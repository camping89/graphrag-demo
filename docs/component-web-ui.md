# Component: Web UI (Streamlit)

> Files: `app.py` (38 lines) + `src/ui/` (5 modules, 642 lines tổng)
> Vai trò: Giao diện web tương tác cho user — 3 tabs cho 3 phase
> (Build, Chat, Visualize).

## 🎯 Mục đích

Cung cấp **interface user-friendly** để demo end-to-end pipeline mà không
cần code/CLI. Streamlit lý tưởng vì:

- Code Python thuần, không cần frontend
- Hot reload khi save file
- Built-in widgets (chat, file uploader, progress bar)
- Caching cho expensive resources (Mongo client, query engine)

## 📂 Cấu trúc

```
app.py                          # entry point + tab routing (38 lines)
src/ui/
├── shared.py                   # cached resources + helpers (49 lines)
├── sidebar.py                  # active collection selector (50 lines)
├── tab_build.py                # Tab 1: Build Graph (358 lines)
├── tab_chat.py                 # Tab 2: Chat (110 lines)
└── tab_visualize.py            # Tab 3: Visualize (75 lines)
```

## 🚪 `app.py` — entry point

```python
st.set_page_config(page_title="GraphRAG × MongoDB Demo", layout="wide")

render_sidebar()

st.title("🕸️ GraphRAG × MongoDB Demo")
st.caption("Demo end-to-end: PDF → Knowledge Graph → Chat")

# Workflow guide expander
with st.expander("📖 Workflow cho tài liệu mới (lần đầu dùng)"):
    st.markdown("""1️⃣ Build → 2️⃣ Chat → 3️⃣ Visualize ...""")

# 3 tabs theo workflow order
tab_build, tab_chat, tab_graph = st.tabs([
    "1️⃣ Build Graph",
    "2️⃣ Chat",
    "3️⃣ Visualize",
])

with tab_build:     render_build_tab()
with tab_chat:      render_chat_tab()
with tab_graph:     render_visualize_tab()
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

## 📌 `src/ui/sidebar.py` — collection selector

Sidebar luôn hiển thị bên trái mọi tab. 3 chức năng:

1. **Show config** (read-only):
   ```
   DB:         graphrag_demo
   Chat model: gpt-5
   ```

2. **Active collection dropdown** — chọn collection để Chat & Visualize:
   ```python
   options = sorted(set(existing) | {cfg.mongodb_collection})
   active = st.selectbox("Collection", options=options, ...)
   st.session_state["active_collection"] = active
   ```

3. **Refresh button** — clear `list_collections` cache khi cần thấy collection mới ngay:
   ```python
   if st.button("🔄 Refresh danh sách"):
       list_collections.clear()
       st.rerun()
   ```

## 🔨 `src/ui/tab_build.py` — Build Graph tab

Tab phức tạp nhất (358 lines). 3 sections chính:

### A. Build graph từ PDF

**3 bước UI**:

1. **Bước 1: Upload PDF**
   ```python
   uploaded = st.file_uploader("Chọn file PDF", type=["pdf"])
   if uploaded:
       tmp_dir = Path(tempfile.gettempdir()) / "graphrag-uploads"
       target = tmp_dir / uploaded.name
       target.write_bytes(uploaded.getvalue())
   ```

2. **Bước 2: Chọn collection**
   - Radio: "Merge vào collection có sẵn" hoặc "Tạo collection mới"
   - Text input với button **💡 Suggest từ tên file**:
     ```python
     def _apply_suggestion():
         st.session_state["new_collection_input"] = slugify_collection_name(pdf_path.stem)

     col_btn.button("💡 Suggest từ tên file",
                    on_click=_apply_suggestion,
                    disabled=pdf_path is None)
     ```

3. **Bước 3: Tham số** (chunk size, overlap, limit chunks, parallel workers)

**Build action**:
```python
button_slot = st.empty()
clicked = button_slot.button("🚀 Build graph", disabled=pdf_path is None)

if clicked:
    # Disable button ngay
    button_slot.button("⏳ Đang build...", disabled=True, key="build_btn_disabled")

    # Load + analyze
    chunks, doc_ctx = load_pdf_chunks_with_context(pdf_path, run_cfg, ...)

    # Show detected context
    st.success(f"📑 Subjects: {doc_ctx.subjects}, Type: {doc_ctx.doc_type}, ...")

    # Build with progress
    progress_bar = st.progress(0, text="Khởi tạo...")
    def on_progress(done, total):
        progress_bar.progress(done/total, text=f"Chunk {done}/{total} ...")

    build_graph(run_cfg, chunks, progress_callback=on_progress, max_workers=max_workers, ...)

    # Persist result + rerun → button enable lại
    st.session_state["last_build_result"] = {...}
    st.cache_resource.clear()
    st.rerun()
```

### B. Build embeddings section (Hybrid mode)

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

### C. Error surface

Sau khi build, nếu có chunks fail (retry exhausted) → hiển thị warning:
```python
if failed > 0:
    st.warning(f"⚠️ Build xong với {failed}/{total} chunks thất bại...")
```

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
