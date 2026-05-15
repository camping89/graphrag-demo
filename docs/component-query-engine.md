# Component: Query Engine (Hybrid Retrieval)

> File: `src/query_engine.py` (196 lines)
> Vai trò: Hybrid Vector + Graph retrieval. Trả lời câu hỏi natural language
> bằng cách traverse knowledge graph có augmented với semantic search.

## 🎯 Mục đích

Đây là **trái tim của Phase 2 (Query)**. Module này:

1. Extract entity names từ câu hỏi (LLM)
2. Vector search top-K semantic similar entities (nếu hybrid mode active)
3. Merge anchors → graph traversal qua `$graphLookup`
4. Sort context theo priority → cap → strip embedding
5. Gửi cho LLM với RAG prompt → câu trả lời

## 📥 Đầu vào & Đầu ra

```python
# Đầu vào
cfg: Config
store: Optional[MongoDBGraphStore]  # truyền hoặc tự tạo
question: str

# Đầu ra
QueryResult:
    answer: str                  # câu trả lời tự nhiên
    related_entities: List[str]  # IDs các entity đã traverse
    anchor_entities: List[str]   # anchors khởi đầu
    used_vector_search: bool     # True = Hybrid, False = Graph-only
```

## 🏛️ Class `GraphRAGQueryEngine`

```python
class GraphRAGQueryEngine:
    def __init__(self, cfg, store=None):
        self._cfg = cfg
        self._store = store or make_graph_store(cfg)

    def ask(self, question: str) -> QueryResult:
        anchors, used_vector = self._gather_anchor_entities(question)
        related_ids = self._traverse_graph(anchors)
        response = self._chat_with_custom_anchors(question, anchors)
        return QueryResult(...)
```

## 🔀 2 modes operation

### Graph-only mode
- Trigger: Atlas Vector Search index không tồn tại trên collection
- Flow: `extract_entity_names(question)` → traverse → RAG
- Yếu khi tên trong câu hỏi ≠ tên trong graph

### Hybrid mode (default khi available)
- Trigger: Atlas Vector Search index tồn tại
- Flow: `extract_entity_names + vector_search → merge → traverse → RAG`
- Mạnh hơn với name mismatch, semantic queries

`_check_vector_index()` kiểm tra mỗi lần `ask()` (KHÔNG cache instance-level
vì Streamlit có thể giữ instance cũ sau khi user tạo index ngoài app).

## 🔍 Step-by-step: `ask(question)`

### Step 1: `_gather_anchor_entities(question, vector_k=10)`

```python
def _gather_anchor_entities(self, question, vector_k=10):
    # 1a. extract_entity_names — LLM call (luôn chạy)
    extracted = self._store.extract_entity_names(question) or []
    # extracted = ["Pham Tuyen", "June 2025", "AIAIVN"]

    used_vector = False
    if self._check_vector_index():
        # 1b. Vector search (nếu hybrid mode)
        semantic = vector_search_entities(self._cfg, ..., question, k=vector_k)
        # semantic = ["Pham Tuyen", "Veek Co., Ltd", "Tuyen", "AIAIVN", ...]
        used_vector = bool(semantic)

        # 1c. Merge dedup, giữ thứ tự
        seen = set()
        merged = []
        for name in [*extracted, *semantic]:
            if name and name not in seen:
                seen.add(name)
                merged.append(name)
        return merged, used_vector

    return extracted, False
```

**Vì sao merge cả 2?**
- `extract_entity_names` → keywords from query (LLM hiểu intent)
- Vector search → semantic similar (handle name mismatch)
- Cả 2 cộng dồn → robust anchors

**Edge: `extracted` thường chứa các từ generic** (vd "June 2025") không match
entity trong graph. Đó là OK — sau bước traverse sẽ bị filter (không có
`_id` matching → không đóng góp).

### Step 2: Graph traversal

```python
related_docs = self._store.related_entities(anchors)
related_ids = [doc.get("_id", "") for doc in related_docs if doc.get("_id")]
```

`MongoDBGraphStore.related_entities(anchors)` internally chạy
`$graphLookup` aggregation:

```javascript
db.coll.aggregate([
    {$match: {_id: {$in: anchors}}},
    {$graphLookup: {
        from: "coll",
        startWith: "$relationships.target_ids",
        connectFromField: "relationships.target_ids",
        connectToField: "_id",
        as: "connections",
        maxDepth: 2,
        depthField: "depth"
    }},
    // flatten + dedupe
])
```

→ Đi đệ quy từ anchors qua `target_ids` đến độ sâu `maxDepth=2` mặc định.

### Step 3: Decide RAG path

```python
if used_vector and anchors:
    response = self._chat_with_custom_anchors(question, anchors)
else:
    response = self._store.chat_response(question)
```

**`store.chat_response()` mặc định**:
- Internal: `similarity_search(question)` → `extract_entity_names + related_entities`
- Limitation: chỉ dùng `extract_entity_names`, không có vector search

**`_chat_with_custom_anchors()` — custom path**:
- Dùng anchors đã merge (extract + vector)
- Sort entities + strip embedding
- Gọi rag_prompt với cleaned context

### Step 4: `_chat_with_custom_anchors(question, anchors)`

```python
def _chat_with_custom_anchors(self, question, anchors):
    related_entities = self._store.related_entities(anchors)

    # Sort theo priority → anchors trước, depth=0, depth=1+...
    sorted_entities = _sort_for_context(related_entities, anchors)

    # Strip embedding + cap
    cleaned = []
    for ent in sorted_entities[:MAX_ENTITIES_IN_CONTEXT]:  # 80
        cleaned.append({k: v for k, v in ent.items() if k != "embedding"})

    # RAG prompt
    chain = rag_prompt | self._store.entity_extraction_model
    return chain.invoke({
        "query": question,
        "related_entities": cleaned,
        "entity_schema": self._store.entity_schema,
    })
```

## 🎯 `_sort_for_context` — context window curation

**Bug fix quan trọng**: `related_entities` MongoDB trả về order không
deterministic. Naive `related[:80]` có thể cắt mất các anchor entity quan
trọng nhất (chứa edge attributes với date, role, period info).

```python
def _sort_for_context(entities, anchors):
    anchor_set = set(anchors)

    def priority(ent):
        eid = ent.get("_id", "")
        depth = ent.get("depth")

        # Tier 1: anchor (user/vector trỏ thẳng)
        if eid in anchor_set:
            return (0, 0)
        # Tier 2: depth=0 (original từ pipeline, không qua traversal)
        if depth is None:
            return (1, 0)
        # Tier 3+: depth ascending
        return (2, depth)

    return sorted(entities, key=priority)
```

**3 tier**:

1. **Anchor entities** (priority 1) — Pham Tuyen, Veek, ... do user/vector chỉ định.
   Chúng carry edge attributes quý giá (period, role).
2. **depth=0** — entities từ `$original` projection (cũng là anchors implicit).
3. **depth=1, 2, ...** — entities reached qua traversal, càng xa càng ít quan trọng.

Sau sort: cap [:80] đảm bảo anchors LUÔN có trong context.

## 📊 Constants

```python
MAX_ENTITIES_IN_CONTEXT = 80   # cap entities gửi LLM
                                # Quá thấp (40) → cắt mất anchors
                                # Quá cao (500) → blow context window
```

## 🔗 RAG prompt (từ langchain_mongodb)

```
## Context
You are a meticulous analyst tasked with extracting information in the form
of knowledge graphs comprised of entities (nodes) and their relationships (edges).

Based on the user input (query), you have already retrieved information from
the knowledge graph in the form of a list of entities known to be related to those
in the Query.

From the context retrieved alone, please respond to the Query.
Your response should be a string of concise prose.

## Entity Schema
{entity_schema}

## Entities Found to be Related to Query
{related_entities}

[Human]: {query}
```

LLM nhận:
- Schema (MongoDB validator JSON) — giúp parse entities dict
- List entities (đã sort + strip embedding)
- Query gốc của user

→ LLM lookup info trong context để answer.

## 🧪 Test thủ công

```python
from src.config import load_config
from src.query_engine import GraphRAGQueryEngine
import dataclasses

cfg = load_config()
cfg2 = dataclasses.replace(cfg, mongodb_collection="phamtuyen_frontend_2026")
engine = GraphRAGQueryEngine(cfg2)

result = engine.ask("Pham Tuyen làm việc ở đâu trong June 2025 - March 2026?")
print(f"Mode: {'Hybrid' if result.used_vector_search else 'Graph-only'}")
print(f"Anchors: {result.anchor_entities}")
print(f"Related: {len(result.related_entities)}")
print(f"\nAnswer: {result.answer}")
```

Expected output:
```
Mode: Hybrid
Anchors: ['Pham Tuyen', 'AIAIVN', 'June 2025', ...]
Related: 60
Answer: Trong giai đoạn 06/2025–03/2026, Pham Tuyen làm việc tại AIAIVN.
```

## 🚦 Failure modes

| Failure | Handling |
|---------|----------|
| `extract_entity_names` LLM fail | Empty extracted, vẫn dùng vector search |
| Vector search Atlas index error | Try-except → fallback `used_vector=False` |
| `related_entities` rỗng (anchors all miss) | `related_ids=[]`, vẫn gọi RAG (LLM sẽ trả "no data") |
| LLM RAG context overflow | Đã cap 80 entities + strip embedding → hiếm |
| Network drop | Exception bubble up, caller (UI) catch hiển thị error |

## 📈 Performance

| Step | Time | Note |
|------|------|------|
| `extract_entity_names` | ~1-3s | 1 LLM call (small) |
| `_check_vector_index` | ~50ms | 1 Mongo `list_search_indexes` |
| `vector_search_entities` | ~300-500ms | embed + Atlas search |
| `related_entities` | ~200-500ms | $graphLookup (depends on graph size) |
| `_sort_for_context` | < 10ms | in-memory sort |
| RAG chain.invoke | ~3-8s | LLM với context 60-80 entities |
| **Total `ask()`** | **~5-15s** | dominated by LLM calls |

## 🔗 Tương tác với component khác

| Component | Hướng | Tương tác |
|-----------|-------|-----------|
| `config.py` | nhận | `Config` |
| `graph_builder.py` | dùng | `make_graph_store()` |
| `entity_embedder.py` | dùng | `vector_search_entities()`, check `VECTOR_INDEX_NAME` |
| `langchain_mongodb` | dùng | `MongoDBGraphStore`, `rag_prompt` |
| `ui/tab_chat.py` | gọi | `engine.ask(prompt)` |

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| Caching: same question → same answer (TTL 5 min) | Low | Medium |
| Streaming RAG response (token-by-token) | Medium | High UX |
| Re-rank related entities by relevance score | Medium | High |
| Multi-hop reasoning (decompose complex queries) | High | High |
| Custom RAG prompt với chain-of-thought | Low | Medium |
| Cite source chunks trong answer | Medium | High |
| Detect "no info" vs hallucination | Medium | High |

## 📚 References

- `src/query_engine.py:22-31` — `QueryResult` dataclass
- `src/query_engine.py:34-39` — constants
- `src/query_engine.py:42-72` — `_sort_for_context`
- `src/query_engine.py:75-93` — `__init__`, `_check_vector_index`
- `src/query_engine.py:96-130` — `_gather_anchor_entities`
- `src/query_engine.py:133-170` — `ask`
- `src/query_engine.py:172-196` — `_chat_with_custom_anchors`
- LangChain source: [langchain_mongodb/graphrag/graph.py](https://github.com/langchain-ai/langchain-mongodb)
- Atlas docs: [$vectorSearch](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-stage/)

## 🔗 Linked components

- [Entity Extraction](component-entity-extraction.md) — provides knowledge graph data
- [Vector Embedding](component-vector-embedding.md) — provides vector search
- [Web UI tab_chat](component-web-ui.md) — consumer
- [Pipeline Overview](pipeline-overview.md)
