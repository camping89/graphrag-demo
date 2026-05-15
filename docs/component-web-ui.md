# Component: Web UI (Streamlit)

> Files: `app.py` (81 lines, có sync `st.secrets`) + `src/ui/` (5 modules)
> Vai trò: Giao diện web tương tác cho user — 3 tabs cho 3 phase
> (Build, Chat, Visualize). Sidebar hiển thị version + 2 model.

## 🎯 Mục đích

Cung cấp **interface user-friendly** để demo end-to-end pipeline mà không
cần code/CLI. Streamlit lý tưởng vì:

- Code Python thuần, không cần frontend
- Hot reload khi save file
- Built-in widgets (chat, file uploader, progress bar)
- Caching cho expensive resources (Mongo client, query engine)

## 📂 Cấu trúc

```
app.py                          # entry point + secrets sync + tab routing (81 lines)
src/ui/
├── shared.py                   # cached resources + helpers
├── sidebar.py                  # version badge + 2 models + active collection
├── tab_build.py                # Tab 1: Build Graph + PDF analysis + auto-normalize
├── tab_chat.py                 # Tab 2: Chat
└── tab_visualize.py            # Tab 3: Visualize
```

## 🚪 `app.py` — entry point

```python
import os
import streamlit as st

# Streamlit Cloud lưu secrets trong st.secrets (TOML), KHÔNG tự inject env vars.
# load_config() đọc qua os.getenv() → cần đoạn này để work cả local (.env)
# lẫn cloud (st.secrets). Local rỗng → no-op.
try:
    for _key, _val in st.secrets.items():
        if isinstance(_val, str):
            os.environ.setdefault(_key, _val)
except (FileNotFoundError, Exception):
    pass

st.set_page_config(page_title="GraphRAG × MongoDB Demo", layout="wide")

render_sidebar()

st.title("🕸️ GraphRAG × MongoDB Demo")
st.caption("Demo end-to-end: PDF → Knowledge Graph → Chat")

with st.expander("📖 Workflow cho tài liệu mới (lần đầu dùng)"):
    st.markdown("""1️⃣ Build → 2️⃣ Chat → 3️⃣ Visualize ...""")

tab_build, tab_chat, tab_graph = st.tabs([
    "1️⃣ Build Graph", "2️⃣ Chat", "3️⃣ Visualize",
])

with tab_build:     render_build_tab()
with tab_chat:      render_chat_tab()
with tab_graph:     render_visualize_tab()
```

**Deployment note**: trên Streamlit Cloud, điền secrets dạng TOML:
```toml
MONGODB_URI = "mongodb+srv://..."
MONGODB_DB = "graphrag_demo"
OPENAI_API_KEY = "sk-..."
OPENAI_EXTRACTION_MODEL = "gpt-5-mini"
OPENAI_QUERY_MODEL = "gpt-5"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
```

## 🧠 `src/ui/shared.py` — cached resources

Cached resources tránh khởi tạo lại Mongo connection / query engine mỗi rerun.

### `get_config()` — load .env once
```python
@st.cache_resource(show_spinner=False)
def get_config() -> Config:
    return load_config()
```

### `get_query_engine(collection_name)` — engine per collection
```python
@st.cache_resource(show_spinner="Đang kết nối MongoDB...")
def get_query_engine(collection_name: str) -> GraphRAGQueryEngine:
    base = get_config()
    cfg = dataclasses.replace(base, mongodb_collection=collection_name)
    return GraphRAGQueryEngine(cfg)
```

Cache key = `collection_name` → mỗi collection có engine riêng cached.

### `list_collections()` — Mongo collection names
```python
@st.cache_data(ttl=10, show_spinner=False)
def list_collections() -> list[str]:
    cfg = get_config()
    client = MongoClient(cfg.mongodb_uri)
    return sorted(client[cfg.mongodb_db].list_collection_names())
```

TTL 10s → user thấy collection mới sau khi build, không phải refresh tay.

### `active_collection()` + `slugify_collection_name()`

Helpers cho session_state + name sanitization.

## 📌 `src/ui/sidebar.py` — version badge + collection selector

Sidebar luôn hiển thị bên trái mọi tab. 4 chức năng:

1. **Version badge** — `🏷️ App version: vX.Y.Z` (đọc `src/version.py`).
   Bump version mỗi khi fix bug → user reload thấy version mới = biết code đã update,
   chưa update = vẫn dùng cache cũ.

2. **Show config** (read-only):
   ```
   DB:               graphrag_demo
   Extraction model: gpt-5-mini
   Query model:      gpt-5
   ```

3. **Active collection dropdown** — chọn collection để Chat & Visualize:
   ```python
   options = sorted(set(existing) | {cfg.mongodb_collection})
   active = st.selectbox("Collection", options=options, ...)
   st.session_state["active_collection"] = active
   ```

4. **Refresh button** — clear `list_collections` cache khi cần thấy collection mới ngay:
   ```python
   if st.button("🔄 Refresh danh sách"):
       list_collections.clear()
       st.rerun()
   ```

## 🔨 `src/ui/tab_build.py` — Build Graph tab

Tab phức tạp nhất. 4 sections chính:

### A. Upload PDF + Phân tích đề xuất

```python
uploaded = st.file_uploader("Chọn file PDF", type=["pdf"])
if uploaded:
    target.write_bytes(uploaded.getvalue())

    # Phân tích nhanh (cache theo path + mtime)
    stats = _pdf_stats_cached(str(target), target.stat().st_mtime)
    rec = recommend_chunk_params(stats["total_chars"])
    # Show: 124 pages, ~380k chars → đề xuất 1000/150 (est 380 chunks)
    if st.button("✅ Áp dụng đề xuất"):
        st.session_state["chunk_size_input"] = rec["chunk_size"]
        st.session_state["overlap_input"] = rec["overlap"]
```

### B. Chọn collection + tham số

- Radio: "Merge vào collection có sẵn" hoặc "Tạo collection mới"
- Button **💡 Suggest từ tên file** → pre-fill input qua `on_click` callback
- Tham số: chunk_size, overlap (pre-fill từ đề xuất), limit chunks, max_workers

### C. Build action

```python
# Worker thread cần attach Streamlit ScriptRunContext để gọi st.session_state
ctx = get_script_run_ctx()
def _init_worker():
    add_script_run_ctx(threading.current_thread(), ctx)

# Cancel event — user bấm Stop → các chunk chưa chạy bị skip
cancel_event = threading.Event()

# Error storage — live update + survive st.rerun
errors_state = {"failed": 0, "details": []}
def on_error(idx, err):
    errors_state["details"].append((idx, err))
    error_box.write(...)  # render ngay

build_graph(
    run_cfg, chunks,
    progress_callback=on_progress,
    failed_callback=on_failed,
    error_callback=on_error,
    thread_initializer=_init_worker,
    cancel_event=cancel_event,
    max_workers=max_workers,
)

# Auto-normalize NGAY sau build — không phải nút riêng
plans = find_merge_candidates(run_cfg, collection)
if plans:
    apply_merge_plans(run_cfg, collection, plans)
    st.info(f"🔀 Merged {len(plans)} duplicate groups")

# Persist kết quả (để survive st.rerun) — không nuke bằng st.error()
st.session_state["last_build_result"] = {
    "ok": True, "n_entities": n, "failed": errors_state["failed"], ...
}
st.session_state["last_build_traceback"] = tb  # nếu fail
st.cache_resource.clear()
st.rerun()
```

**Build result message** render BELOW button (không nuke bởi rerun):
- ✅ Success → `Build xong! N entities, M chunks failed`
- ❌ Fail → expander chứa traceback đầy đủ

### D. Build embeddings section (Hybrid mode)

```python
st.subheader("🧬 Hybrid Vector + Graph (tuỳ chọn)")
target_coll = st.selectbox("Collection cần compute embeddings", ...)
force = st.checkbox("Re-compute ngay cả entity đã có embedding")

if st.button("🧬 Build embeddings + vector index"):
    with st.spinner("Đảm bảo Atlas Vector Search index tồn tại..."):
        ensure_vector_index(run_cfg, target_coll)
    count = backfill_embeddings(run_cfg, target_coll,
                                 progress_callback=on_progress,
                                 force=force)
```

> Section "Normalize duplicates" trong UI đã bị bỏ — auto-normalize trong build flow
> đã đủ. Manual normalize giờ chỉ chạy qua `scripts/normalize-collection.py`.

## 💬 `src/ui/tab_chat.py` — Chat tab

UX-critical. Pattern tránh được:
1. **Input nằm dưới cùng** (sticky bottom of tab)
2. **Câu hỏi hiện ngay** khi Enter (không đợi engine)

### Layout pattern: 2 container placeholders

```python
history_slot = st.container()    # vị trí trên
input_slot = st.container()      # vị trí dưới

with input_slot:
    prompt = st.chat_input("Nhập câu hỏi...")

if prompt:
    # Run 1: append + rerun để show user msg ngay
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    st.session_state["_pending_question"] = prompt
    st.rerun()

# Run 2: render history (đã có user msg) + process pending
with history_slot:
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    pending = st.session_state.pop("_pending_question", None)
    if pending:
        assistant_msg = _render_assistant_inline(pending, active)
        ...
```

### Loading state với `st.status`

```python
with st.chat_message("assistant"):
    answer_slot = st.empty()
    answer_slot.markdown("💭 *Đang xử lý...*")

    status_slot = st.empty()
    with status_slot.status("Đang xử lý", expanded=False) as status:
        status.update(label="🔍 Extract entities từ câu hỏi...")
        engine = get_query_engine(collection)
        status.update(label="🕸️ Duyệt knowledge graph...")
        result = engine.ask(prompt)

    status_slot.empty()  # ← ẩn status khi xong
    answer_slot.markdown(result.answer)

    # Mode badge
    mode_badge = "🧬 Hybrid (Vector + Graph)" if result.used_vector_search else "🕸️ Graph-only"
    st.caption(f"Mode: {mode_badge} · Anchors: {result.anchor_entities}")
```

### Session state schema

```python
st.session_state.chat_history = [
    {"role": "user", "content": "Tuyen làm ở đâu?"},
    {"role": "assistant", "content": "...", "related": [...], "anchors": [...], "used_vector": True},
    ...
]
st.session_state["_pending_question"] = "..."  # transient
st.session_state["active_collection"] = "phamtuyen_..."
```

## 🕸️ `src/ui/tab_visualize.py` — Visualize tab

Đơn giản (75 lines):

```python
active = active_collection()
total_in_db = _count_entities(cfg, active)
st.caption(f"🗄️ Collection: `{active}` — **{total_in_db} entities** trong DB.")

max_nodes = st.slider("Số entity hiển thị tối đa", 20, 500, 150, ...)

if st.button("🎨 Render graph HTML"):
    run_cfg = dataclasses.replace(get_config(), mongodb_collection=active)
    path = visualize_graph(run_cfg, GRAPH_HTML_PATH, max_nodes=max_nodes)
    st.success(f"Đã render: {path.resolve()}")

if GRAPH_HTML_PATH.exists():
    html = GRAPH_HTML_PATH.read_text(encoding="utf-8")
    components.html(html, height=820, scrolling=True)
```

**Key UX**: HTML render xong được embed inline trong tab qua
`streamlit.components.v1.html` — user thấy graph ngay, không cần mở file ngoài.

## 🎨 Streamlit patterns đã áp dụng

### 1. **Cached resources** (`@st.cache_resource`)
- Heavy objects (Mongo client, query engine) → cache cho session
- TTL 10s cho `list_collections` để pick up changes

### 2. **Session state cho persistent state**
- `chat_history` survives reruns
- `active_collection` shared across tabs
- `last_build_result` persists qua rerun để show success message

### 3. **Container placeholders cho layout**
- `st.empty()` cho dynamic replace (button enabled → disabled)
- `st.container()` cho ordered slots (history above, input below)

### 4. **`st.rerun()` cho instant UX**
- Sau submit chat → rerun để show user msg ngay
- Sau build → rerun để reset button state

### 5. **`on_click` callbacks**
- Modify session_state cho widget keys (Streamlit cho phép trong callback)
- Vd: suggest button pre-fill text input

### 6. **`st.status` cho progress**
- Multi-step async operations (extract → traverse → answer)
- Expandable nếu user muốn xem chi tiết

## 🔗 Tương tác với component khác

| UI Module | Dùng |
|-----------|------|
| `app.py` | tất cả tab modules + sidebar |
| `sidebar.py` | `get_config`, `list_collections` |
| `tab_build.py` | `get_config`, `load_pdf_chunks_with_context`, `build_graph`, `backfill_embeddings`, `ensure_vector_index` |
| `tab_chat.py` | `active_collection`, `get_query_engine` |
| `tab_visualize.py` | `active_collection`, `get_config`, `visualize_graph` |
| `shared.py` | `Config`, `GraphRAGQueryEngine`, `MongoClient` |

## 🧪 Cách chạy

```powershell
cd C:\w\_me\graphrag-demo
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

→ Mở browser http://localhost:8501

## 📈 Performance characteristics

| Action | Time |
|--------|------|
| Page load đầu tiên | ~2-3s (cache cold) |
| Page load sau (cache warm) | < 500ms |
| Chat first question (cache miss) | engine.ask time + ~1s init |
| Chat subsequent | engine.ask time only |
| Build button click → first progress | ~5s (PDF analyze) |
| Switch tab | instant (Streamlit re-render only) |

## ⚠️ Streamlit quirks đã handle

| Quirk | Fix trong code |
|-------|----------------|
| `st.cache_resource` giữ instance cũ sau khi user tạo Vector Index ngoài | `_check_vector_index` không cache instance-level |
| `st.chat_input` trong tab không sticky bottom theo default | Dùng 2 `st.container()` placeholders để control order |
| Submit prompt không hiện ngay vì engine block | Pattern `st.rerun()` tách 2 script runs |
| `st.status` "✅ Xong" treo trên UI | Bọc trong `st.empty()` slot, gọi `.empty()` sau khi xong |
| Modify widget key sau khi widget render | Dùng `on_click` callback, OR set BEFORE widget render |
| Disable button trong khi run | `st.empty()` placeholder, replace bằng disabled version |

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| Theme customization (logo, brand colors) | Low | Low |
| Multi-language UI (i18n) | Medium | Low cho demo |
| Export chat history → MD/PDF | Medium | High |
| Inline entity preview khi click related entity | Medium | High UX |
| Compare 2 collections side-by-side | High | Medium |
| Re-build button per collection (drop + rebuild) | Low | Medium |
| Settings page (override defaults, switch model) | Medium | Low |

## 📚 References

- `app.py:1-38` — entry point
- `src/ui/shared.py:14-49` — cached resources + helpers
- `src/ui/sidebar.py:1-50` — sidebar
- `src/ui/tab_build.py:1-358` — build tab (PDF + embeddings)
- `src/ui/tab_chat.py:1-110` — chat tab
- `src/ui/tab_visualize.py:1-75` — visualize tab
- Streamlit docs: [API Reference](https://docs.streamlit.io/develop/api-reference)
- Streamlit: [Caching](https://docs.streamlit.io/develop/concepts/architecture/caching)

## 🔗 Linked components

Tất cả backend modules — UI là consumer:
- [PDF Loading](component-pdf-loading.md)
- [Document Context](component-document-context.md)
- [Entity Extraction](component-entity-extraction.md)
- [Vector Embedding](component-vector-embedding.md)
- [Query Engine](component-query-engine.md)
- [Visualization](component-visualization.md)
- [Pipeline Overview](pipeline-overview.md)
