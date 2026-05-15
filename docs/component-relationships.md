# Component Relationships — Mối quan hệ & hỗ trợ giữa các thành phần

> Document tổng hợp **cách các component giao tiếp, phụ thuộc, và bổ trợ
> lẫn nhau**. Đọc cùng với [pipeline-overview.md](pipeline-overview.md).

## 🗺️ Map tổng các components

```
                    ┌─────────────────────────────────────────┐
                    │           Foundation Layer              │
                    │   config.py (Config dataclass)          │
                    └─────────────────┬───────────────────────┘
                                      │ load_config()
                  ┌───────────────────┼───────────────────────┐
                  │                   │                       │
        ┌─────────▼────────┐  ┌───────▼──────────┐  ┌─────────▼─────────┐
        │  Data Ingestion  │  │  Extraction      │  │  Query            │
        │                  │  │                  │  │                   │
        │  pdf_loader      │  │  graph_builder   │  │  query_engine     │
        │  document_context│  │  entity_embedder │  │                   │
        └─────────┬────────┘  └───────┬──────────┘  └─────────┬─────────┘
                  │                   │                       │
                  └─────────┬─────────┴───────────┬───────────┘
                            │                     │
                            ▼                     ▼
                    ┌───────────────┐     ┌──────────────────┐
                    │ MongoDB Atlas │     │   visualizer     │
                    │  (data store) │◄────┤  (export HTML)   │
                    └───────────────┘     └──────────────────┘
                            ▲                     │
                            │                     │
                            └──────────┬──────────┘
                                       │
                                       ▼
                            ┌─────────────────────┐
                            │   UI Layer (ui/*)   │
                            │                     │
                            │  app.py             │
                            │  ├─ sidebar         │
                            │  ├─ tab_build       │
                            │  ├─ tab_chat        │
                            │  └─ tab_visualize   │
                            │                     │
                            │  shared.py (cache)  │
                            └─────────────────────┘
```

## 📦 Layers & responsibilities

### Layer 0: **Foundation**
| Module | Responsibility |
|--------|----------------|
| `config.py` | Centralized env loading, `Config` dataclass |
| `.env` | Persistent config (MONGODB_URI) |

### Layer 1: **Data Ingestion**
| Module | Input | Output |
|--------|-------|--------|
| `pdf_loader.py` | PDF file | `(chunks, DocumentContext)` |
| `document_context.py` | full_text | `DocumentContext` |

### Layer 2: **Extraction & Storage**
| Module | Input | Output |
|--------|-------|--------|
| `graph_builder.py` | chunks | MongoDB writes (entities) |
| `entity_embedder.py` | collection | MongoDB updates (embedding field) + Atlas Vector Index |

### Layer 3: **Query**
| Module | Input | Output |
|--------|-------|--------|
| `query_engine.py` | question | `QueryResult(answer, anchors, related, mode)` |
| `visualizer.py` | collection | HTML file |

### Layer 4: **Presentation**
| Module | Role |
|--------|------|
| `app.py` | Entry point + tab routing |
| `ui/shared.py` | Cached resources |
| `ui/sidebar.py` | Global state (active collection) |
| `ui/tab_build.py` | Phase 1 UI |
| `ui/tab_chat.py` | Phase 2 UI |
| `ui/tab_visualize.py` | Phase 3 UI |

## 🔄 Tương tác chi tiết theo data flow

### Phase 1: Build Graph

```
UI (tab_build)
  │
  │ 1. User upload PDF + chọn collection + nhấn Build
  │
  ▼
load_pdf_chunks_with_context(pdf_path, cfg, ...)
  │
  ├─► load_pdf_chunks(pdf_path)
  │     └─► PyPDFLoader + RecursiveCharacterTextSplitter
  │           └─► returns List[Document]
  │
  ├─► analyze_document(cfg, full_text)
  │     ├─► detect_sections_regex(full_text)
  │     ├─► _stratified_sample(full_text)
  │     ├─► ChatOpenAI.invoke(prompt)  ← LLM CALL 1
  │     ├─► _locate_titles_in_text(full_text, llm_titles)
  │     └─► returns DocumentContext(subjects, sections, ...)
  │
  └─► For each chunk:
        ├─► context.section_at(offset) → Section
        ├─► context.to_chunk_prefix(section)
        └─► chunk.page_content = prefix + content

  returns (chunks, doc_ctx)
  │
  ▼
build_graph(cfg, chunks, max_workers=5, ...)
  │
  ├─► make_graph_store(cfg)
  │     └─► MongoDBGraphStore(connection_string, db, collection, ...)
  │
  └─► ThreadPoolExecutor(max_workers=5):
        For each chunk in parallel:
          _add_with_retry(store, chunk, max_retries=4):
            attempt 1..N:
              try:
                store.add_documents([chunk])  ← LLM CALL per chunk
                  └─► langchain_mongodb extracts entities/relationships
                      └─► pymongo update_one upsert  ← MONGODB WRITE
              catch RateLimit:
                sleep(exponential backoff)
                retry

  returns MongoDBGraphStore
  │
  ▼
[Optional] backfill_embeddings(cfg, collection, ...)
  │
  ├─► ensure_vector_index(cfg, collection)
  │     ├─► check existing index
  │     ├─► if missing: coll.create_search_index(...)  ← ATLAS API
  │     └─► poll until READY
  │
  └─► For each entity not yet embedded:
        ├─► entity_to_text(entity)
        ├─► embedder.embed_query(text)  ← OPENAI API
        └─► coll.update_one(_id, $set: {embedding: vector})

  returns count of embedded entities
```

### Phase 2: Query

```
UI (tab_chat)
  │
  │ 1. User nhập câu hỏi + Enter
  │
  ▼
get_query_engine(collection_name)
  │
  └─► cached → reuse hoặc tạo mới:
        GraphRAGQueryEngine(cfg)
          └─► self._store = make_graph_store(cfg)

  returns engine
  │
  ▼
engine.ask(question)
  │
  ├─► _gather_anchor_entities(question, vector_k=10)
  │     │
  │     ├─► store.extract_entity_names(question)  ← LLM CALL (qua langchain_mongodb)
  │     │     └─► returns ["Pham Tuyen", "Veek Co.", ...]
  │     │
  │     ├─► _check_vector_index()
  │     │     └─► coll.list_search_indexes()  ← MONGO QUERY
  │     │
  │     └─► [If hybrid] vector_search_entities(...)
  │           ├─► embedder.embed_query(question)  ← OPENAI EMBED
  │           └─► coll.aggregate([$vectorSearch])  ← ATLAS QUERY
  │
  │     returns (anchors, used_vector)
  │
  ├─► store.related_entities(anchors)
  │     └─► coll.aggregate([$match, $graphLookup, ...])  ← MONGO TRAVERSAL
  │           returns list[entity_dict]
  │
  └─► _chat_with_custom_anchors(question, anchors)
        │
        ├─► _sort_for_context(related, anchors)
        │     └─► priority: anchors > depth=0 > depth=N
        │
        ├─► cap [:80] + strip "embedding" field
        │
        └─► rag_prompt | entity_extraction_model
              └─► chain.invoke({query, related_entities, schema})  ← LLM CALL
                    returns AIMessage

  returns QueryResult(answer, anchors, related, used_vector)
```

### Phase 3: Visualize

```
UI (tab_visualize)
  │
  │ 1. User chỉnh slider max_nodes, nhấn Render
  │
  ▼
visualize_graph(cfg, output_path, max_nodes=200)
  │
  ├─► fetch_entities(cfg, limit=200)
  │     └─► coll.find({}, limit=200)  ← MONGO READ
  │
  ├─► build_networkx_graph(entities)
  │     ├─► For each entity: add_node(_id, label, group=type)
  │     └─► For each edge: add_edge(source, target)
  │
  └─► render_html(graph, output_path)
        ├─► pyvis.Network(directed, ...)
        ├─► net.from_nx(graph)
        ├─► net.repulsion(node_distance=180)
        └─► net.write_html(path)

  returns Path
  │
  ▼
UI: components.html(html_content, height=820)
```

## 🤝 Dependencies matrix

| Module ↓ depends on → | config | pdf_loader | document_context | graph_builder | entity_embedder | query_engine | visualizer | ui/shared |
|---|---|---|---|---|---|---|---|---|
| **pdf_loader** | ✅ | — | ✅ | | | | | |
| **document_context** | ✅ | | — | | | | | |
| **graph_builder** | ✅ | | | — | | | | |
| **entity_embedder** | ✅ | | | | — | | | |
| **query_engine** | ✅ | | | ✅ | ✅ | — | | |
| **visualizer** | ✅ | | | | | | — | |
| **ui/shared** | ✅ | | | | | ✅ | | — |
| **ui/sidebar** | | | | | | | | ✅ |
| **ui/tab_build** | | ✅ | | ✅ | ✅ | | | ✅ |
| **ui/tab_chat** | | | | | | ✅ | | ✅ |
| **ui/tab_visualize** | ✅ | | | | | | ✅ | ✅ |

→ Dependency graph **không có cycle**. config là root, ui là leaves.

## 🧬 Data contracts giữa các component

### Contract 1: `pdf_loader` → `graph_builder`

```python
# pdf_loader trả về:
chunks: List[Document]
# - chunks[i].page_content: text với context prefix prepended
# - chunks[i].metadata = {
#     source: path,
#     page: int,
#     chunk_id: int,
#     document_subjects: List[str],
#     document_type: str,
#     section: Optional[str],
# }

# graph_builder consume:
build_graph(cfg, chunks=chunks, ...)
```

### Contract 2: `document_context` → `pdf_loader`

```python
# document_context exports:
@dataclass
class DocumentContext:
    subjects: List[str]
    doc_type: str
    description: str
    sections: List[Section]

    def section_at(offset: int) -> Optional[Section]
    def to_chunk_prefix(section: Optional[Section]) -> str

# pdf_loader sử dụng:
ctx = analyze_document(cfg, full_text)
section = ctx.section_at(chunk_offset)
prefix = ctx.to_chunk_prefix(section)
```

### Contract 3: `graph_builder` ↔ MongoDB

```python
# Entity document schema:
{
    "_id": str,                       # canonical entity name
    "type": str,                      # Person, Organization, Project, ...
    "attributes": {field: [values]},  # entity facts
    "relationships": {
        "target_ids": [str, ...],
        "types": [str, ...],
        "attributes": [{...}, ...]    # edge attributes (period, role, ...)
    },
    "embedding": [float, ...]         # 1536 floats, ADDED by entity_embedder
}
```

### Contract 4: `entity_embedder` → `query_engine`

```python
# entity_embedder defines:
VECTOR_INDEX_NAME = "entity_vector_index"
EMBEDDING_FIELD = "embedding"

# query_engine reads:
def _check_vector_index(self) -> bool:
    return any(idx["name"] == VECTOR_INDEX_NAME for idx in coll.list_search_indexes())

# entity_embedder exports:
def vector_search_entities(cfg, collection_name, query_text, k=5) -> list[str]
```

### Contract 5: `query_engine` → UI

```python
@dataclass
class QueryResult:
    answer: str
    related_entities: list[str]
    anchor_entities: list[str]
    used_vector_search: bool

# UI consume:
result = engine.ask(question)
st.markdown(result.answer)
if result.used_vector_search:
    st.caption("Mode: 🧬 Hybrid")
```

## 🎭 Patterns hỗ trợ lẫn nhau

### Pattern 1: **Context injection** chống "vô hồn"

```
document_context detect subject/section
                    │
                    ▼
        pdf_loader prepend prefix to chunks
                    │
                    ▼
       graph_builder LLM extract với prefix
                    │
                    ▼
        Resulting entities có attributes + edges đầy đủ
                    │
                    ▼
         query_engine có rich context to retrieve
```

### Pattern 2: **Hybrid retrieval** chống name mismatch

```
entity_embedder compute embeddings + Atlas index
                    │
                    ▼
       query_engine vector_search complement extract_entity_names
                    │
                    ▼
        anchors gồm cả exact match + semantic match
                    │
                    ▼
        $graphLookup từ richer anchor set → đầy đủ context
                    │
                    ▼
            LLM answer chính xác hơn
```

### Pattern 3: **Retry + parallel** cho throughput

```
graph_builder ThreadPoolExecutor(max_workers=5)
                    │
                    ▼
        Mỗi worker chạy _add_with_retry
                    │
                    ▼
        429 trigger exponential backoff (2s, 4s, 8s, 16s)
                    │
                    ▼
        Failed callback notify UI nếu retry exhausted
                    │
                    ▼
        UI hiển thị warning, user biết phải rebuild missing chunks
```

### Pattern 4: **Cache + rerun** cho UX

```
ui/shared cached resources (engine, config)
                    │
                    ▼
        ui/tab_chat: prompt → append history → st.rerun()
                    │
                    ▼
        Streamlit fresh frame → user thấy question NGAY
                    │
                    ▼
        Run 2: process pending → engine.ask (slow)
                    │
                    ▼
        Render answer → append history → done
```

## 🔌 Inter-component communication mechanisms

### Mechanism 1: Direct function call
- `pdf_loader.load_pdf_chunks_with_context` → `document_context.analyze_document`
- `query_engine.ask` → `entity_embedder.vector_search_entities`
- Most common, simple, type-safe

### Mechanism 2: Shared MongoDB collection
- `graph_builder` writes
- `entity_embedder` updates (add embedding field)
- `query_engine` reads (related_entities, vector_search)
- `visualizer` reads (fetch_entities)
- Indirection layer — components không biết về nhau

### Mechanism 3: Streamlit session_state
- `chat_history` shared trong tab_chat
- `active_collection` shared cross-tabs (sidebar + tab_chat + tab_visualize)
- `last_build_result` persist qua rerun

### Mechanism 4: Streamlit cache_resource/cache_data
- Cross-rerun persistence
- `get_query_engine` cached theo collection name
- `list_collections` cached với TTL 10s

### Mechanism 5: Callback functions
- `progress_callback(done, total)` từ build_graph → UI progress bar
- `failed_callback(failed, total)` cho error surface
- `on_progress` cho backfill embeddings

## 🌐 External dependencies

| External | Used by | Purpose |
|----------|---------|---------|
| **MongoDB Atlas** | graph_builder, entity_embedder, query_engine, visualizer | Persistent storage |
| **OpenAI API** | document_context, graph_builder, entity_embedder, query_engine | LLM + embeddings |
| **PyPDF (cryptography)** | pdf_loader | PDF parsing (encrypted support) |
| **LangChain framework** | All extraction/query modules | Wrappers cho LLM + Mongo |
| **langchain-mongodb** | graph_builder, query_engine | `MongoDBGraphStore` |
| **pyvis + networkx** | visualizer | HTML graph render |
| **Streamlit** | All ui modules | Web framework |

## 🧩 Module coupling analysis

### Tight coupling (good — these MUST work together)
- `pdf_loader` ↔ `document_context`: pdf_loader injects context prefix using document_context's API
- `query_engine` ↔ `entity_embedder`: query_engine checks vector index existence, calls vector_search

### Loose coupling (good — interchangeable)
- `graph_builder` ↔ MongoDB: chỉ thông qua langchain_mongodb wrapper, có thể swap với Neo4j integration
- `visualizer` ↔ MongoDB: dùng pymongo trực tiếp, có thể swap với cypher cho Neo4j
- UI ↔ backend: nhận qua direct call hoặc cached resources, dễ swap UI framework

### Independent (good — testable separately)
- `document_context.analyze_document` thuần input/output, unit-testable
- `entity_embedder.entity_to_text` không có side effects
- `visualizer.build_networkx_graph` thuần in-memory operation

## 🚦 Critical paths

### Critical path 1: Build pipeline
```
PDF → load → analyze_document (1 LLM) → chunks with prefix
    → build_graph (N LLM, parallel) → MongoDB
    → backfill_embeddings (M LLM) → MongoDB updates + Atlas index
```
Failure points: PDF parse, LLM rate limit, MongoDB write, Atlas index build.

### Critical path 2: Query pipeline
```
Question → extract_entity_names (1 LLM) → anchors
        → vector_search (1 embed + 1 Atlas) → enrich anchors
        → related_entities (1 $graphLookup) → context
        → sort + cap → rag_prompt (1 LLM) → answer
```
~3 LLM calls per query. Failure points: LLM, Atlas vector search, Mongo traversal.

### Critical path 3: Build → Query
```
Build → entities trong Mongo → Query reads
```
Sự đồng bộ qua `st.cache_resource.clear()` sau build.

## 🛡️ Defense-in-depth chống lỗi

| Layer | Defense |
|-------|---------|
| L0 (Foundation) | `_require()` raise nếu thiếu env vars |
| L1 (Ingestion) | PyPDFLoader try/except, document_context fallback `(unknown)` |
| L2 (Extraction) | Retry exponential backoff, failed_callback surface |
| L3 (Query) | Try/except quanh vector_search → fallback Graph-only |
| L4 (UI) | Try/except quanh `engine.ask`, show error trong chat bubble |

## 🔮 Tương lai có thể thay đổi cấu trúc

| Scenario | Affected components | Impact |
|----------|---------------------|--------|
| Đổi từ MongoDB → Neo4j | graph_builder, query_engine, entity_embedder, visualizer | High — rewrite layer 2-3 |
| Đổi từ OpenAI → Claude | config, graph_builder, query_engine | Low — chỉ swap ChatOpenAI |
| Multi-tenancy (per-user collection) | config, ui/shared, sidebar | Medium — thêm tenant ID |
| Streaming chat response | query_engine, tab_chat | Medium — async/yield pattern |
| Cloud deployment (FastAPI thay Streamlit) | All ui/* | High — rewrite UI |
| Knowledge graph completion (auto-add missing edges) | New module + graph_builder | High — thêm phase post-build |

## 📊 Component metrics

| Module | LOC | LLM calls/run | DB calls/run | Public API |
|--------|-----|---------------|--------------|------------|
| config.py | 54 | 0 | 0 | `load_config()` |
| pdf_loader.py | 120 | 1 (indirect) | 0 | 2 functions |
| document_context.py | 343 | 1 | 0 | 1 function + 2 classes |
| graph_builder.py | 176 | N (per chunk) | N writes | 3 functions |
| entity_embedder.py | 212 | M (per entity) | M writes + 1 search | 4 functions |
| query_engine.py | 196 | 3 per query | 2-3 per query | 1 class |
| visualizer.py | 111 | 0 | 1 read | 4 functions |
| ui/*.py | ~642 | 0 | 0 (delegate) | render functions |
| **Total** | **~1854** | | | |

## 📚 Đọc thêm

- [Pipeline Overview](pipeline-overview.md) — architecture chi tiết
- [PDF Loading](component-pdf-loading.md)
- [Document Context](component-document-context.md)
- [Entity Extraction](component-entity-extraction.md)
- [Vector Embedding](component-vector-embedding.md)
- [Query Engine](component-query-engine.md)
- [Visualization](component-visualization.md)
- [Web UI](component-web-ui.md)
