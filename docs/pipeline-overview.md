# GraphRAG Pipeline — Tổng quan kiến trúc & data flow

> Document tổng hợp toàn bộ pipeline. Đọc trước khi đi vào các file chi tiết
> theo component.

## 📂 Cấu trúc thư mục

```
graphrag-demo/
├── app.py                       # Streamlit entry point (81 lines, có sync st.secrets)
├── requirements.txt             # Dependencies
├── .env                         # MONGODB_URI + OPENAI_API_KEY (bắt buộc)
├── docs/                        # ← Bạn đang đọc thư mục này
│   ├── pipeline-overview.md     # File này
│   ├── component-*.md           # 1 file/component
│   ├── graphrag-explained.md    # Lý thuyết GraphRAG
│   └── graphrag-mongodb.md      # MongoDB GraphRAG
├── src/
│   ├── version.py               # __version__ — bump mỗi lần fix/ship
│   ├── config.py                # Load env vars → Config dataclass (2 models)
│   ├── pdf_loader.py            # PDF → chunks; pdf_stats + recommend_chunk_params
│   ├── document_context.py      # Phân tích doc → DocumentContext
│   ├── graph_builder.py         # build_graph + retry + parallel + cancel
│   ├── entity_embedder.py       # Vector embeddings + Atlas index
│   ├── entity_normalizer.py     # Merge duplicate entities (post-build)
│   ├── query_engine.py          # Hybrid retrieval + diversify + RAG
│   ├── visualizer.py            # networkx + pyvis HTML render
│   └── ui/                      # Streamlit UI modules
│       ├── shared.py            # Cached resources + helpers
│       ├── sidebar.py           # Version badge + active collection selector
│       ├── tab_build.py         # Tab 1: build + auto-normalize + embeddings
│       ├── tab_chat.py          # Tab 2: chat with KG
│       └── tab_visualize.py     # Tab 3: HTML visualization
└── scripts/
    ├── build-graph.py           # CLI: build graph từ PDF
    ├── visualize-graph.py       # CLI: render HTML
    ├── debug-query.py           # CLI: inspect collection
    ├── normalize-collection.py  # CLI: merge duplicate entities (dry-run / --apply)
    └── rebuild-embeddings.py    # CLI: re-embed entities (sau normalize)
```

## 🎯 8 component chính

| # | Component | File | Mô tả ngắn |
|---|-----------|------|------------|
| 1 | [PDF Loading & Chunking](component-pdf-loading.md) | `pdf_loader.py` | PDF → Documents + đề xuất chunk_size theo size |
| 2 | [Document Context Detection](component-document-context.md) | `document_context.py` | Phân tích chủ thể, type, sections, hierarchy |
| 3 | [Entity Extraction (Build)](component-entity-extraction.md) | `graph_builder.py` | LLM extract entities + relationships → MongoDB |
| 4 | [Vector Embedding](component-vector-embedding.md) | `entity_embedder.py` | OpenAI embeddings → Atlas Vector Search |
| 5 | [Entity Normalizer](component-entity-normalizer.md) | `entity_normalizer.py` | Merge duplicate entities (canonical key) |
| 6 | [Query Engine (Hybrid)](component-query-engine.md) | `query_engine.py` | Vector + Graph + diversify retrieval + RAG |
| 7 | [Visualization](component-visualization.md) | `visualizer.py` | networkx graph → pyvis HTML interactive |
| 8 | [Web UI (Streamlit)](component-web-ui.md) | `app.py`, `src/ui/*` | 3-tab interactive demo |

## 🌐 Data flow tổng quan

```
                    ┌──────────────────────────────────────────┐
                    │   PDF input (CV, audit, contract, ...)   │
                    └──────────────────┬───────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  PHASE 1: BUILD GRAPH (tab "1️⃣ Build Graph" — chạy 1 lần)            │
│                                                                       │
│  1. pdf_loader.load_pdf_chunks_with_context()                         │
│       a. PyPDFLoader → pages → RecursiveCharacterTextSplitter         │
│       b. document_context.analyze_document(): LLM analyze sample      │
│          → DocumentContext(subjects, doc_type, sections)              │
│       c. detect_sections_regex() + _locate_titles_in_text()           │
│          → List[Section] với offset                                   │
│       d. Map mỗi chunk → section, prepend context_prefix              │
│                                                                       │
│  2. graph_builder.build_graph()                                       │
│       ThreadPoolExecutor(max_workers=5):                              │
│         For each chunk in parallel:                                   │
│           _add_with_retry(store, chunk):                              │
│             store.add_documents([chunk])                              │
│              ├── LLM extract entities + relationships (gpt-5)         │
│              └── upsert vào MongoDB collection                        │
│       → Collection có 50-100 entities, mỗi entity có:                 │
│           _id, type, attributes, relationships {target_ids, types}    │
│                                                                       │
│  3. entity_embedder.backfill_embeddings()  [optional, hybrid mode]    │
│       a. ensure_vector_index() → tạo Atlas Vector Search index        │
│       b. Với mỗi entity:                                              │
│           entity_to_text() → text representation                      │
│           OpenAIEmbeddings.embed_query() → vector 1536-dim            │
│           update_one() → save embedding vào document                  │
└──────────────────────────────────────────────────────────────────────┘

                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  PHASE 2: QUERY (tab "2️⃣ Chat" — chạy nhiều lần)                     │
│                                                                       │
│  User question → query_engine.ask(question):                          │
│                                                                       │
│  1. _gather_anchor_entities(question, vector_k=10):                   │
│       a. store.extract_entity_names(question) [LLM]                   │
│          → ["Pham Tuyen", "Veek Co., Ltd"]                            │
│       b. vector_search_entities(question, k=10) [Atlas $vectorSearch] │
│          → top-10 entities semantic similarity                        │
│       c. Merge dedup → anchors list                                   │
│                                                                       │
│  2. store.related_entities(anchors) [MongoDB $graphLookup]            │
│       → traversal đệ quy từ anchors → 50-90 entities reached          │
│                                                                       │
│  3. _sort_for_context(related, anchors)                               │
│       → anchors (priority 1) trước, rồi depth=0, depth=1+, depth=N    │
│                                                                       │
│  4. Cap [:MAX_ENTITIES_IN_CONTEXT=80] + strip embedding field         │
│                                                                       │
│  5. rag_prompt chain.invoke({query, related_entities, schema}) [LLM]  │
│       → answer string                                                 │
└──────────────────────────────────────────────────────────────────────┘

                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  PHASE 3: VISUALIZE (tab "3️⃣ Visualize" — optional)                  │
│                                                                       │
│  1. visualizer.fetch_entities() → top N entities từ Mongo             │
│  2. build_networkx_graph() → nx.DiGraph với nodes + edges             │
│  3. pyvis.Network.from_nx() → render_html() → out/graph.html          │
└──────────────────────────────────────────────────────────────────────┘
```

## 🔗 Mối quan hệ giữa các component (dependency graph)

```
                          config.py
                              ▲
                              │ (load_config)
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
  pdf_loader        document_context      entity_embedder
        │                     ▲                     ▲
        │                     │ (uses)              │ (uses)
        │                     │                     │
        └──────► uses ────────┘                     │
                              ▲                     │
                              │                     │
                              │           (vector search)
                              │                     │
                          pdf_loader.load_pdf_chunks_with_context
                              │
                              ▼
                      graph_builder.build_graph
                              │
                              ▼ (writes MongoDB)
                      MongoDB Atlas Collection
                              ▲
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
       query_engine    entity_embedder   visualizer
              │       (writes vectors)         │
              ▼                                ▼
       ChatModel reply                   HTML interactive
              │                                │
              ▼                                ▼
       ┌──────────────────────────────────────────┐
       │            ui/ (Streamlit)                │
       │   tab_chat   tab_build   tab_visualize    │
       └──────────────────────────────────────────┘
                       ▲
                       │
                     app.py
```

## 📊 Data structures chính (data contracts)

### Config (`src/config.py`)
```python
@dataclass(frozen=True)
class Config:
    mongodb_uri: str          # connection string Atlas
    mongodb_db: str           # "graphrag_demo"
    mongodb_collection: str   # default collection
    openai_api_key: str
    extraction_model: str     # "gpt-5-mini" — NHANH + RẺ, dùng cho build (N chunks)
    query_model: str          # "gpt-5"      — CHẤT LƯỢNG, dùng cho chat (1 lần/Q)
    embedding_model: str      # "text-embedding-3-small"

    @property
    def chat_model(self) -> str:
        """Backward-compat — trả về query_model cho code cũ."""
```

> **Tại sao tách 2 model?** Build pipeline gọi LLM N lần (N = số chunks, thường 100-500),
> cần model nhanh + rẻ + TPM cao. Query pipeline gọi 1-2 lần/câu hỏi, cần chất lượng
> reasoning + tổng hợp context.

### Document (`langchain_core.documents.Document`)
```python
class Document:
    page_content: str         # text của chunk (sau khi inject context prefix)
    metadata: dict            # {source, page, chunk_id, document_subjects, section, ...}
```

### DocumentContext (`src/document_context.py`)
```python
@dataclass
class DocumentContext:
    subjects: List[str]       # 1-5 chủ thể chính
    doc_type: str             # "CV / Resume", "SOC 2 Audit", ...
    description: str          # 2-câu mô tả
    sections: List[Section]   # với offset, level, parent
```

### Entity (MongoDB document trong collection)
```python
# Lưu dưới dạng JSON trong MongoDB
{
    "_id": "Pham Tuyen",           # entity name = unique identifier
    "type": "Person",              # entity type (Person/Org/Project/...)
    "attributes": {                # facts riêng của entity
        "title": ["Frontend Engineer"],
        "summary": ["..."],
        "experience_years": ["1.5+"]
    },
    "relationships": {             # outgoing edges
        "target_ids": ["AIAIVN", "React.js", ...],
        "types":      ["works_at", "skilled_in", ...],
        "attributes": [{...}, {...}, ...]   # mỗi edge có attrs riêng (period, role)
    },
    "embedding": [0.012, -0.034, ...]  # 1536 floats (sau backfill)
}
```

### QueryResult (`src/query_engine.py`)
```python
@dataclass
class QueryResult:
    answer: str                    # LLM-generated answer
    related_entities: list[str]    # IDs các entity trong traversal
    anchor_entities: list[str]     # anchors dùng để start traversal
    used_vector_search: bool       # True = hybrid mode, False = graph-only
```

## 🧩 Bảng tóm tắt vai trò mỗi component

| Component | Đầu vào | Đầu ra | LLM calls/lần chạy | Phụ thuộc |
|-----------|---------|--------|--------------------|-----------|
| `pdf_loader` | PDF path | `(chunks, context)` | 1 (qua document_context) | document_context |
| `document_context` | Full text | `DocumentContext` | 1 | langchain_openai |
| `graph_builder` | chunks + cfg | MongoDB writes | N (1/chunk) | langchain_mongodb |
| `entity_embedder` | collection | embeddings updated | M (1/entity) | langchain_openai, pymongo |
| `query_engine` | question | `QueryResult` | 2-3 (extract + chat + maybe embed) | graph_builder, entity_embedder |
| `visualizer` | collection | HTML file | 0 | pymongo, networkx, pyvis |
| `ui/*` | user interaction | render UI | 0 | tất cả modules trên |

## ⚙️ Configuration knobs quan trọng

| Knob | Default | Tác động |
|------|---------|----------|
| `chunk_size` | auto theo size (1000-1200) | Mỗi chunk = 1 LLM call extract. Nhỏ = granular, nhiều entity |
| `chunk_overlap` | auto theo size (150-180) | Giữ context xuyên ranh giới chunk |
| `MAX_WORKERS` (build) | 5 | Số chunks parallel. Cao = nhanh, dễ 429 |
| `MAX_RETRIES` | 4 | Exponential backoff khi 429: 2s, 4s, 8s, 16s |
| `vector_k` (query) | 10 | Top-K semantic neighbors khi vector search |
| `MAX_ENTITIES_IN_CONTEXT` | 80 | Cap entities gửi LLM ở RAG step (round-robin theo type) |
| `extraction_model` | gpt-5-mini | Build pipeline — N call/doc, ưu tiên nhanh + rẻ |
| `query_model` | gpt-5 | Chat — 1 call/Q, ưu tiên chất lượng reasoning |
| `embedding_model` | text-embedding-3-small | 1536 dim, đủ cho most use cases |

> `recommend_chunk_params(total_chars)` trong `pdf_loader.py` tự đề xuất:
> - <50k chars → 1200/180
> - 50k-400k → 1000/150 (granular nhất)
> - \>400k → 1200/180 (cân rate limit)

## 🚦 Lifecycle 1 tài liệu (end-to-end)

1. **User upload PDF** qua Streamlit tab Build hoặc CLI
2. **PDF stats + đề xuất chunk** (`pdf_stats` + `recommend_chunk_params`)
   - Đếm pages/chars → suggest chunk_size/overlap → user áp dụng
3. **Load + chunk** (`pdf_loader.load_pdf_chunks`)
4. **Analyze doc** (`document_context.analyze_document` — 1 LLM call extraction_model)
   - Stratified sample 3 vùng → subjects, type, sections
5. **Inject context prefix** vào mỗi chunk (`DocumentContext.to_chunk_prefix`)
6. **Build graph parallel** (`graph_builder.build_graph` — N LLM calls extraction_model)
   - 5 workers song song, retry 429 + JSON parse errors với backoff
   - thread_initializer → attach Streamlit ScriptRunContext cho worker
   - cancel_event → user bấm Stop dừng giữa chừng
7. **Auto-normalize** (`entity_normalizer` — không LLM, chỉ MongoDB)
   - Group duplicates theo canonical key → merge attrs + redirect refs → delete losers
8. **Embed entities** (`entity_embedder.backfill_embeddings`)
   - 1 LLM embedding call/entity (chạy tự động sau normalize, hoặc manual --force)
   - Tạo Atlas Vector Search index
9. **Query** (`query_engine.ask` — query_model)
   - extract_entity_names + vector_search → anchors
   - $graphLookup → reached entities
   - `_diversify_truncate` → cap 80 theo round-robin type → RAG prompt → answer
10. **(Optional) Visualize** (`visualizer.visualize_graph`)
    - Fetch entities + relationships → networkx → pyvis HTML

## 📚 Tài liệu chi tiết

Đi sâu vào từng component:

- [PDF Loading & Chunking](component-pdf-loading.md)
- [Document Context Detection](component-document-context.md)
- [Entity Extraction (Build Graph)](component-entity-extraction.md)
- [Vector Embedding & Hybrid Mode](component-vector-embedding.md)
- [Entity Normalizer](component-entity-normalizer.md)
- [Query Engine (Hybrid Retrieval)](component-query-engine.md)
- [Visualization](component-visualization.md)
- [Web UI (Streamlit)](component-web-ui.md)

## 🔑 Best practices đã apply trong codebase này

1. **Document context injection** trước extract (chống "vô hồn" entities)
2. **Stratified sampling** cho doc lớn 100+ trang
3. **Multi-subject support** cho audit/contract
4. **Section-aware chunking** với offset tracking
5. **PDF-aware chunk recommendation** — auto suggest theo size
6. **Hybrid Vector + Graph** retrieval (giải quyết name mismatch)
7. **Parallel chunk processing** với ThreadPoolExecutor + cancel_event
8. **Exponential backoff retry** cho 429 + JSON parse errors
9. **2-model split** — extraction (nhanh/rẻ) vs query (chất lượng)
10. **Post-build normalize** — merge duplicate entities tự động
11. **Diversified context** — round-robin theo type, không bias 1 loại
12. **Anchor-priority sorting** khi assemble RAG context
13. **Version badge** trong sidebar — biết code đã reload hay vẫn cache cũ
14. **Frozen Config dataclass** + env-based config (`.env` hoặc `st.secrets`)
15. **Modular UI** (mỗi tab = 1 module)
