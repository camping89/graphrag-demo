# Component: Entity Extraction (Build Graph)

> File: `src/graph_builder.py` (176 lines)
> Vai trò: Convert text chunks thành knowledge graph trong MongoDB Atlas.
> Mỗi chunk → LLM extract entities + relationships → upsert vào collection.

## 🎯 Mục đích

Đây là **trái tim của Phase 1 (Build)**. Module này:
1. Khởi tạo `MongoDBGraphStore` (LangChain integration)
2. Đưa từng chunk cho LLM để trích xuất entities + edges
3. Lưu/merge vào MongoDB collection
4. Hỗ trợ parallel processing + retry exponential backoff

## 📥 Đầu vào & Đầu ra

```python
# Đầu vào
cfg: Config                              # MongoDB URI, OpenAI key, chat_model
chunks: List[Document]                   # đã có context prefix (từ pdf_loader)
chat_model: Optional[ChatOpenAI] = None  # override default
progress_callback: Optional[Callable[[int, int], None]]
max_workers: int = 5                     # parallel concurrency
failed_callback: Optional[Callable[[int, int], None]]

# Đầu ra
MongoDBGraphStore                        # collection đã được populate
```

**Side effect chính**: Documents được **upsert** vào
`db[cfg.mongodb_db][cfg.mongodb_collection]`. Mỗi entity = 1 MongoDB document.

## 🧩 3 hàm public

### `make_chat_model(cfg) -> ChatOpenAI`
Khởi tạo LLM client. Tách ra để dễ override (mock test, swap model).

### `make_graph_store(cfg, chat_model=None) -> MongoDBGraphStore`
Khởi tạo `MongoDBGraphStore` từ `langchain-mongodb`. Tham số:
- `connection_string`: Mongo URI
- `database_name` + `collection_name`
- `entity_extraction_model`: LLM dùng cho extraction

### `build_graph(cfg, chunks, ..., max_workers=5)` — hàm chính

3 execution paths tuỳ tham số:

| max_workers | progress_callback | Path |
|-------------|-------------------|------|
| ≤ 1 | None | Path 1: `store.add_documents(chunks)` — 1 batch call (nhanh nhất) |
| ≤ 1 | Có | Path 2: sequential loop, retry per-chunk, progress sau mỗi chunk |
| > 1 (default 5) | bất kỳ | Path 3: ThreadPoolExecutor, retry per-chunk, atomic counter |

## 🔧 Path 1: Batch call (fastest)

```python
store.add_documents(chunks)
```

`MongoDBGraphStore.add_documents()` (LangChain) internally:
1. Lặp qua từng `Document`
2. Gửi cho LLM với prompt `entity_prompt` (langchain_mongodb default)
3. Parse JSON response → list entities
4. Upsert vào MongoDB qua `update_one(_id, $set, upsert=True)`

→ Đơn giản nhưng KHÔNG có progress update — không phù hợp cho UI.

## 🔄 Path 2: Sequential với progress

```python
for idx, chunk in enumerate(chunks, start=1):
    err = _add_with_retry(store, chunk)
    if err:
        errors_seq.append((idx, err))
    progress_callback(idx, total)
```

Mỗi chunk được xử lý qua `_add_with_retry()` (retry 4 lần với exponential backoff).
Progress callback sau mỗi chunk — UI cập nhật progress bar real-time.

## ⚡ Path 3: Parallel (DEFAULT, recommended)

```python
counter_lock = threading.Lock()
counter = {"done": 0}
errors: list[tuple[int, str]] = []

def _process_one(idx_and_chunk):
    idx, chunk = idx_and_chunk
    err = _add_with_retry(store, chunk)
    if err:
        with counter_lock:
            errors.append((idx, err))
    with counter_lock:
        counter["done"] += 1
        done_now = counter["done"]
    if progress_callback:
        progress_callback(done_now, total)

with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
    list(pool.map(_process_one, enumerate(chunks)))
```

**Thread safety guarantees:**
- `pymongo.Collection.update_one` — atomic upsert, thread-safe
- `OpenAI client` — thread-safe (connection pool internal)
- `counter_lock` — bảo vệ counter atomic increment
- `errors` list mutation — protected by lock

**Tốc độ thực tế** (CV 5 chunks, gpt-5):
- max_workers=1: ~5×40s = 200s
- max_workers=5: ~50-60s (5 workers song song)
- max_workers=10: ~30-40s (đẩy gần rate limit)

## 🔁 Retry mechanism

### `_is_retryable(exc) -> bool`

Heuristic dựa trên error message:

```python
_RETRYABLE_KEYWORDS = (
    "rate", "429", "too many", "overloaded",
    "timeout", "503", "502", "504"
)

def _is_retryable(exc):
    msg = str(exc).lower()
    return any(k in msg for k in _RETRYABLE_KEYWORDS)
```

### `_add_with_retry(store, chunk, max_retries=4) -> Optional[str]`

```python
def _add_with_retry(store, chunk, max_retries=4):
    for attempt in range(max_retries + 1):
        try:
            store.add_documents([chunk])
            return None  # success
        except Exception as exc:
            if attempt == max_retries or not _is_retryable(exc):
                return str(exc)[:200]
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
    return last_err
```

**Exponential backoff**:
- attempt 0: 0s (ngay lần đầu)
- attempt 1: 2-3s
- attempt 2: 4-5s
- attempt 3: 8-9s
- attempt 4 (max): give up → log

**Jitter**: `random.uniform(0, 1)` thêm 0-1s noise → tránh "thundering herd"
(tất cả workers retry cùng lúc → đợt 429 thứ 2).

## 📦 Schema entity trong MongoDB

Sau khi build, mỗi entity là 1 document:

```json
{
  "_id": "Pham Tuyen",              // entity name (unique ID)
  "type": "Person",                  // entity type
  "attributes": {                    // facts riêng của entity
    "title": ["Frontend Engineer"],
    "summary": ["..."],
    "experience_years": ["1.5+"],
    "email": ["..."]
  },
  "relationships": {                 // outgoing edges (mảng song song)
    "target_ids": ["AIAIVN", "React.js", ...],
    "types":      ["works_at", "skilled_in", ...],
    "attributes": [
      {"period": ["2025-06 to 2026-03"], "role": ["Frontend Engineer"]},
      {"category": ["Frontend Framework"]},
      ...
    ]
  },
  "embedding": [0.012, -0.034, ...]  // 1536 floats (sau backfill)
}
```

**Đặc điểm**:
- `_id` là canonical name (string) — dễ search, là edge target
- `attributes` là `{field: [values]}` — array để cho phép multi-value (vd: nhiều
  industry tags cho 1 company)
- `relationships` là 3 mảng song song (target_ids[i], types[i], attributes[i] đồng bộ)
- `embedding` được add sau bởi `entity_embedder` (optional)

## 🧠 Entity extraction LLM prompt (từ langchain_mongodb)

`langchain_mongodb.graphrag.prompts.entity_prompt` (đã được pre-defined):

```
You are an expert in entity extraction...

Schema:
{entity_schema}

Examples:
{entity_extraction_examples}

Extract entities and relationships from:
{input_document}
```

Trong demo này, **`input_document`** đã được **inject context prefix** từ
`pdf_loader.load_pdf_chunks_with_context` → mỗi chunk LLM thấy:

```
=== DOCUMENT CONTEXT ===
Subject: "Pham Tuyen"
Current section: "Frontend Developer (Veek)"
RULES:
1. EVERY entity MUST have attributes...
2. ALWAYS create relationship to subject...
...

=== CHUNK CONTENT ===
[actual text]
```

→ LLM extract có ngữ cảnh đầy đủ + bắt buộc tuân quy tắc.

## 🔄 Entity merge logic (LangChain handle)

Khi `add_documents()` chạy chunk N và chunk N+1 cùng nhắc "Pham Tuyen":
- Chunk N: tạo entity `Pham Tuyen` với `attributes.email`
- Chunk N+1: LLM tạo entity `Pham Tuyen` với `attributes.summary`
- LangChain: gọi `update_one({_id: "Pham Tuyen"}, {$addToSet: ...}, upsert=True)`
- → MongoDB merge attributes (no duplicate), relationships append

**Implication**: entities được "giàu lên" theo thời gian khi nhiều chunks
cùng mention. Đây chính là cách multi-document graph hoạt động: build PDF 2
vào cùng collection → entities merge với entities từ PDF 1.

## 🎚️ Constants

```python
DEFAULT_MAX_WORKERS = 5    # sweet spot cho OpenAI tier 1+
MAX_RETRIES = 4            # 4 retries = max delay 8-16s, đủ cover 429 transient

_RETRYABLE_KEYWORDS = (
    "rate", "429", "too many", "overloaded",
    "timeout", "503", "502", "504"
)
```

## 🚨 Error handling

| Error type | Action |
|------------|--------|
| `RateLimitError` (429) | Retry với exponential backoff |
| `TimeoutError` | Retry |
| 5xx server errors | Retry |
| `AuthenticationError` | Không retry — fail fast, log full error |
| Network drop | Retry (timeout keyword match) |
| Malformed LLM response | Không retry — log, skip chunk |

**Failed callback**: Sau khi build xong, nếu có chunks fail → callback nhận
`(failed_count, total)` → UI hiển thị warning.

## 🔗 Tương tác với component khác

| Component | Hướng | Tương tác |
|-----------|-------|-----------|
| `config.py` | nhận | `Config` |
| `pdf_loader.py` | nhận | List[Document] với context prefix |
| `langchain_mongodb.graphrag.MongoDBGraphStore` | gọi | extract + write |
| `langchain_openai.ChatOpenAI` | gọi | LLM provider |
| `pymongo` | indirect | qua `MongoDBGraphStore` |
| `query_engine.py` | dùng | `make_graph_store` để init engine |
| `entity_embedder.py` | follow-up | sau build có thể chạy backfill embeddings |
| `ui/tab_build.py` | gọi | qua progress + failed callbacks |
| `scripts/build-graph.py` | gọi | CLI entry |

## 🧪 Test thủ công

```python
from src.config import load_config
from src.graph_builder import build_graph
from src.pdf_loader import load_pdf_chunks_with_context
from pathlib import Path

cfg = load_config()
chunks, ctx = load_pdf_chunks_with_context(Path("doc.pdf"), cfg)

def on_progress(done, total):
    print(f"  {done}/{total}")

def on_failed(failed, total):
    print(f"  WARNING: {failed} chunks failed")

store = build_graph(
    cfg, chunks,
    progress_callback=on_progress,
    max_workers=5,
    failed_callback=on_failed,
)

print(f"Collection: {store.collection.name}")
print(f"Count: {store.collection.count_documents({})}")
```

## 📈 Performance characteristics

| Doc | Chunks | Workers | Time | Cost (gpt-5) |
|-----|--------|---------|------|---------------|
| CV 4p | 5 | 5 | ~50s | ~$0.5 |
| Audit 30p | 80 | 5 | ~15 min | ~$8 |
| Audit 30p | 10 | 10 | ~7 min | ~$8 |
| Book 200p | 600 | 5 | ~3 hours | ~$50 |
| Book 200p | 10 | 10 | ~1.5 hours | ~$50 |

→ Cost scale tuyến tính với số chunks. Time scale 1/N với workers (giới hạn
bởi rate limit).

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| Auto-throttle workers khi liên tục 429 (giảm `max_workers` dynamic) | Medium | Medium |
| Batch chunks 2-3 cùng LLM call (nếu chunk nhỏ) | High | Low — phá symmetry |
| Streaming progress (giảm Streamlit re-render) | Low | Low |
| Custom entity_prompt với schema explicit | Medium | High — quality |
| Use Claude/Gemini cho extraction (multi-provider) | Medium | Low — diversity |

## 📚 References

- `src/graph_builder.py:24-37` — constants
- `src/graph_builder.py:40-50` — `_is_retryable`
- `src/graph_builder.py:53-77` — `_add_with_retry`
- `src/graph_builder.py:80-92` — `make_chat_model`, `make_graph_store`
- `src/graph_builder.py:95-176` — `build_graph` (3 paths)
- LangChain docs: [MongoDBGraphStore](https://www.mongodb.com/docs/atlas/ai-integrations/langchain/graph-rag/)
- LangChain source: [graph.py](https://github.com/langchain-ai/langchain-mongodb)

## 🔗 Linked components

- [PDF Loading](component-pdf-loading.md) — produces chunks
- [Document Context](component-document-context.md) — provides DocumentContext used in prefix
- [Vector Embedding](component-vector-embedding.md) — optional follow-up after build
- [Query Engine](component-query-engine.md) — uses `make_graph_store` to read graph
- [Pipeline Overview](pipeline-overview.md)
