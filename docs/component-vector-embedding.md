# Component: Vector Embedding & Hybrid Mode

> File: `src/entity_embedder.py` (212 lines)
> Vai trò: Tính vector embeddings cho mỗi entity, lưu vào MongoDB, tạo Atlas
> Vector Search index. Là nền tảng cho **Hybrid (Vector + Graph)** retrieval.

## 🎯 Mục đích

GraphRAG truyền thống dựa vào **exact match** trên `_id` để traverse graph:

```
Hỏi "Tuyen"  →  $graphLookup _id="Tuyen"  →  KHÔNG MATCH  →  rỗng
```

Trong graph chỉ có `"Pham Tuyen"`, không có `"Tuyen"` → fail.

**Vector embeddings** giải quyết bằng semantic similarity:

```
Hỏi "Tuyen"  →  embed query  →  $vectorSearch  →  Top-K match
  → ["Pham Tuyen" (0.92), "Tuyen Pham" (0.89), ...]
```

Sau đó dùng các entities này làm anchors cho graph traversal.

## 🏗️ 3 chức năng chính

### 1. `backfill_embeddings(cfg, collection_name, ...)` — compute & save
### 2. `ensure_vector_index(cfg, collection_name)` — tạo Atlas index
### 3. `vector_search_entities(cfg, collection, query, k=5)` — query time

## 📐 `entity_to_text(entity)` — embed text construction

Embedding chất lượng phụ thuộc vào text input. Module convert entity dict
thành text dùng cấu trúc:

```python
def entity_to_text(entity):
    parts = [f"Name: {entity['_id']}"]
    if entity.get("type"):
        parts.append(f"Type: {entity['type']}")

    attrs = entity.get("attributes") or {}
    if attrs:
        flat = {k: v[0] if isinstance(v, list) and len(v) == 1 else v
                for k, v in attrs.items()}
        parts.append(f"Attributes: {json.dumps(flat)}")

    rels = entity.get("relationships") or {}
    targets = rels.get("target_ids", [])
    if targets:
        parts.append(f"Related to: {', '.join(str(t) for t in targets[:20])}")

    return "\n".join(parts)
```

Output ví dụ cho `Pham Tuyen`:

```
Name: Pham Tuyen
Type: Person
Attributes: {"title": "Frontend Engineer", "experience_years": "1.5+"}
Related to: AIAIVN, React.js, Next.js, Angular, ...
```

**Vì sao gộp cả targets**? Để vector của `Pham Tuyen` mang **ngữ cảnh quan hệ**.
Hỏi `"frontend engineer AIAIVN"` → `Pham Tuyen` xếp cao vì embedding chứa cả 2 keyword.

## 🔄 `backfill_embeddings(cfg, collection_name, ...)`

```python
def backfill_embeddings(cfg, collection_name, progress_callback=None, force=False):
    embedder = make_embedder(cfg)
    client = MongoClient(cfg.mongodb_uri)
    coll = client[cfg.mongodb_db][collection_name]

    # Chỉ embed entities CHƯA có embedding (trừ khi force=True)
    query = {} if force else {EMBEDDING_FIELD: {"$exists": False}}
    entities = list(coll.find(query))

    for idx, entity in enumerate(entities, start=1):
        text = entity_to_text(entity)
        vector = embedder.embed_query(text)
        coll.update_one(
            {"_id": entity["_id"]},
            {"$set": {EMBEDDING_FIELD: vector}},
        )
        progress_callback(idx, total)
```

**Idempotent**: chạy lại sẽ skip entities đã có `embedding` field. `force=True`
để re-compute toàn bộ (sau khi đổi model hoặc thay logic `entity_to_text`).

**Chi phí**:
- text-embedding-3-small: $0.02 / 1M tokens
- 1 entity ~ 100 tokens → ~$0.000002/entity
- Collection 100 entities → ~$0.0002

→ Cực rẻ. Có thể chạy thoải mái.

## 📍 `ensure_vector_index(cfg, collection_name)`

Tạo Atlas Vector Search index nếu chưa có. **Idempotent** — gọi nhiều lần OK.

```python
def ensure_vector_index(cfg, collection_name):
    coll = client[cfg.mongodb_db][collection_name]

    # Check existing
    existing = list(coll.list_search_indexes())
    for idx in existing:
        if idx.get("name") == VECTOR_INDEX_NAME:
            return VECTOR_INDEX_NAME  # đã có → skip

    # Tạo mới
    coll.create_search_index({
        "name": VECTOR_INDEX_NAME,
        "type": "vectorSearch",
        "definition": {
            "fields": [{
                "type": "vector",
                "path": EMBEDDING_FIELD,
                "numDimensions": 1536,
                "similarity": "cosine",
            }]
        },
    })

    # Đợi index build (Atlas build async)
    for _ in range(30):
        indexes = list(coll.list_search_indexes())
        target = next((i for i in indexes if i.get("name") == VECTOR_INDEX_NAME), None)
        if target and target.get("status") in ("READY", "STEADY"):
            break
        time.sleep(2)

    return VECTOR_INDEX_NAME
```

**Schema explained**:
- `name: "entity_vector_index"` — unique identifier
- `type: "vectorSearch"` — Atlas's vector search index type
- `path: "embedding"` — field chứa vector trong document
- `numDimensions: 1536` — match với text-embedding-3-small output
- `similarity: "cosine"` — cosine similarity (most common cho text embeddings)

**Wait for build**: Atlas build index async, có thể mất 10-60s. Code poll
status mỗi 2s, max 60s. Sau đó index có thể chưa READY hoàn toàn nhưng có thể query.

## 🔍 `vector_search_entities(cfg, collection_name, query_text, k=5)`

Search top-K entity IDs semantic similar với query.

```python
def vector_search_entities(cfg, collection_name, query_text, k=5, num_candidates=50):
    embedder = make_embedder(cfg)
    query_vector = embedder.embed_query(query_text)

    coll = client[cfg.mongodb_db][collection_name]
    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX_NAME,
                "path": EMBEDDING_FIELD,
                "queryVector": query_vector,
                "numCandidates": num_candidates,
                "limit": k,
            }
        },
        {
            "$project": {
                "_id": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
    results = list(coll.aggregate(pipeline))
    return [str(r["_id"]) for r in results]
```

**Pipeline breakdown**:
1. `$vectorSearch` stage:
   - `queryVector`: embedding của question
   - `numCandidates: 50` — Atlas search 50 candidates trước
   - `limit: k` — return top K (mặc định 5, query engine dùng 10)
2. `$project` lấy `_id` + similarity score (debug)

**numCandidates vs k**:
- `numCandidates` = số entity Atlas xét xem (broader search)
- `k` = số final trả về
- Tỉ lệ 5:1 đến 10:1 thường tốt — k=5 → numCandidates=50

## 📊 Cấu trúc data sau backfill

```json
{
  "_id": "Pham Tuyen",
  "type": "Person",
  "attributes": { ... },
  "relationships": { ... },
  "embedding": [           ← FIELD MỚI
    0.0123, -0.0234, 0.0456, 0.0789, ...
    // 1536 floats total
  ]
}
```

Mỗi embedding = 1536 floats × 8 bytes = **~12 KB/entity**. Collection 100 entities
= ~1.2 MB embeddings. Atlas Free tier cluster đủ chứa hàng nghìn entities.

## ⚙️ Constants

```python
VECTOR_INDEX_NAME = "entity_vector_index"
EMBEDDING_FIELD = "embedding"
EMBEDDING_DIM = 1536    # match text-embedding-3-small
```

## 🚦 Trạng thái Hybrid mode

Query engine check vector index tồn tại:

```python
def _check_vector_index(self) -> bool:
    indexes = list(self._store.collection.list_search_indexes())
    return any(idx.get("name") == VECTOR_INDEX_NAME for idx in indexes)
```

- Index tồn tại → Hybrid mode active
- Không có → Graph-only mode (fallback)

→ User có thể skip embedding nếu muốn pure graph mode.

## 🔗 Tương tác với component khác

| Component | Hướng | Tương tác |
|-----------|-------|-----------|
| `config.py` | nhận | `Config.embedding_model`, `Config.openai_api_key` |
| `langchain_openai.OpenAIEmbeddings` | gọi | `embed_query()` |
| `pymongo` | gọi | `update_one`, `create_search_index`, `aggregate $vectorSearch` |
| `query_engine.py` | gọi | `vector_search_entities()`, check `VECTOR_INDEX_NAME` |
| `ui/tab_build.py` | gọi | section "Hybrid Vector + Graph" |

## 🧪 Test thủ công

```python
from src.config import load_config
from src.entity_embedder import (
    ensure_vector_index,
    backfill_embeddings,
    vector_search_entities,
)

cfg = load_config()
coll = "phamtuyen_frontend_2026"

# Step 1: tạo index
idx_name = ensure_vector_index(cfg, coll)
print(f"Index: {idx_name}")

# Step 2: backfill
def on_progress(done, total):
    print(f"  {done}/{total}")
count = backfill_embeddings(cfg, coll, progress_callback=on_progress)
print(f"Embedded: {count}")

# Step 3: search
results = vector_search_entities(cfg, coll, "Tuyen", k=10)
print(f"Top 10 matches for 'Tuyen': {results}")
# Output ví dụ: ['Pham Tuyen', 'Tuyen Pham', 'AIAIVN', ...]
```

## 📈 Performance

| Operation | Time | Note |
|-----------|------|------|
| `make_embedder` | < 1ms | thuần init client |
| `embed_query` (1 entity) | ~200-500ms | OpenAI API call |
| `backfill 100 entities` | ~30-50s | sequential, có thể parallelize |
| `ensure_vector_index` (lần đầu) | ~10-60s | Atlas build async |
| `ensure_vector_index` (lần 2+) | < 100ms | skip vì existed |
| `vector_search_entities k=10` | ~300-500ms | embed query + Atlas query |

## ⚠️ Edge cases & failure modes

| Edge case | Handling |
|-----------|----------|
| Atlas cluster không support Vector Search | `OperationFailure` → raise với message hướng dẫn |
| Index đã tồn tại với schema khác | Hiện tại không reconcile — phải drop & recreate manually |
| Embedding model trả về dim khác (vd thay model) | Atlas index không accept → fail. Cần `force=True` rebuild |
| Entity quá lớn cho embedding context (> 8K tokens) | text-embedding-3-small handles up to 8192 tokens, `entity_to_text` cap targets[:20] đảm bảo |
| Collection rỗng → backfill | Trả về 0, không lỗi |

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| Parallel backfill (ThreadPoolExecutor như build_graph) | Low | Medium — speed up backfill 5x |
| Embed cả edge attrs vào text (cho richer semantic) | Low | High — temporal queries better |
| Multiple embedding models (multi-vector per entity) | High | Low |
| Hybrid score (vector × graph proximity) trong vector_search | Medium | High |
| Use ada-embedding-3-large (3072 dim) cho quality | Low | Medium |
| Incremental re-embed khi entity update | Medium | Low cho demo |

## 📚 References

- `src/entity_embedder.py:21-29` — constants
- `src/entity_embedder.py:32-37` — `make_embedder`
- `src/entity_embedder.py:40-67` — `entity_to_text`
- `src/entity_embedder.py:70-105` — `backfill_embeddings`
- `src/entity_embedder.py:108-160` — `ensure_vector_index`
- `src/entity_embedder.py:163-212` — `vector_search_entities`
- Atlas docs: [Vector Search](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/)
- OpenAI: [text-embedding-3-small](https://platform.openai.com/docs/guides/embeddings)

## 🔗 Linked components

- [Entity Extraction](component-entity-extraction.md) — tạo entities trước, embed sau
- [Query Engine](component-query-engine.md) — consumer của vector search
- [Pipeline Overview](pipeline-overview.md)
