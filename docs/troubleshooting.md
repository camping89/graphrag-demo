# Troubleshooting — Common Errors & Fixes

> Liệt kê các lỗi gặp phải khi chạy demo + cách fix nhanh. Sắp theo phase
> (setup → build → query → visualize → UI).

## 🔧 Setup errors

### `RuntimeError: Thiếu biến môi trường bắt buộc: MONGODB_URI`

**Triệu chứng**: chạy `python -c "from src.config import load_config; load_config()"` fail.

**Nguyên nhân**: `.env` chưa tạo hoặc thiếu `MONGODB_URI`.

**Fix**:
```powershell
Copy-Item .env.example .env
notepad .env
# Điền MONGODB_URI=mongodb+srv://...
```

### `pypdf.errors.DependencyError: cryptography>=3.1 is required for AES algorithm`

**Triệu chứng**: load PDF có encryption (vd báo cáo SOC 2 thật).

**Fix**:
```powershell
.\.venv\Scripts\python.exe -m pip install 'cryptography>=3.1'
```

(Đã trong `requirements.txt`. Lỗi này chỉ xảy ra nếu cài deps incomplete.)

### `UnicodeEncodeError: 'charmap' codec can't encode character` (Windows)

**Triệu chứng**: chạy CLI in ra tiếng Việt fail.

**Fix**: set env var trước:
```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe scripts/build-graph.py ...
```

## 🏗️ Build errors

### `pymongo.errors.ServerSelectionTimeoutError`

**Triệu chứng**: kết nối Mongo timeout 10-30s.

**Nguyên nhân**:
1. **IP chưa whitelist** trong Atlas Network Access
2. URI sai (user/password/cluster name)
3. Network proxy/firewall block port 27017

**Fix**:
1. Atlas → Network Access → Add IP → `0.0.0.0/0` (cho demo) hoặc IP hiện tại
2. Atlas → Connect → copy lại URI, dán vào `.env`
3. Test bằng [MongoDB Compass](https://www.mongodb.com/products/compass) với cùng URI

### `pymongo.errors.OperationFailure: Authentication failed`

**Triệu chứng**: kết nối Atlas thành công nhưng auth fail.

**Fix**:
1. Atlas → Database Access → check username/password
2. Reset password nếu cần (lưu password mới vào `.env`)
3. Special char trong password phải URL-encode (`@` → `%40`, `:` → `%3A`)

### `openai.RateLimitError: 429 Too Many Requests`

**Triệu chứng**: build chunks bị fail (UI hiển thị warning "N/total chunks failed").

**Nguyên nhân**: vượt rate limit OpenAI account tier.

**Fix**:
- Code đã tự retry 4 lần với exponential backoff. Nếu vẫn fail:
  1. Giảm `max_workers` (Web UI: Bước 3, ô "Parallel workers") từ 5 → 2-3
  2. Đợi 1-2 phút rồi build lại (entities đã build sẽ skip — merge)
  3. Upgrade OpenAI tier (đạt $50 spend → tier 2 với limit cao hơn)

### `OpenAIContextOverflowError: Input tokens exceed limit`

**Triệu chứng**: build 1 chunk fail vì context quá dài.

**Nguyên nhân**:
- `chunk_size` quá lớn (vd 8000+ chars)
- Hoặc document context prefix quá nặng

**Fix**: giảm `chunk_size` (Web UI: Bước 3) về 1500-2000.

### Build chậm bất thường (1 chunk > 60s)

**Nguyên nhân**:
- `extraction_model = gpt-5` inherently slow (long reasoning)
- Workers = 1 (sequential)

**Fix**:
1. Tăng workers lên 5 (Web UI hoặc `--workers 5` CLI)
2. Đổi `OPENAI_EXTRACTION_MODEL=gpt-5-mini` (mặc định) → nhanh hơn ~5× và rẻ hơn nhiều
   Vẫn giữ `OPENAI_QUERY_MODEL=gpt-5` cho chất lượng chat
3. Cân nhắc dùng `--limit-chunks 20` để test trước

### Chunk #N JSON parse fail (`Expecting property name enclosed in double quotes`)

**Triệu chứng**: 1-2 chunk fail với error JSON parse từ output LLM.

**Nguyên nhân**: gpt-5-mini đôi khi trả JSON malformed (escape sai, thiếu quote).

**Fix**:
- Code đã thêm các keyword JSON vào `_RETRYABLE_KEYWORDS` (`expecting`, `json`,
  `unterminated`, `invalid \\escape`, `extra data`) → tự retry 4 lần. Thường lần 2-3 pass.
- Nếu vẫn fail sau 4 lần: rebuild lại chunk đó (build cùng collection, upsert dedupe).

### Worker thread warning: `missing ScriptRunContext`

**Triệu chứng**: log Streamlit báo `Thread 'XXX' missing ScriptRunContext` khi build parallel.

**Nguyên nhân**: ThreadPoolExecutor spawn worker thread không có Streamlit context →
`st.session_state` / `st.write` từ worker fail silently.

**Fix**: code đã có `thread_initializer=add_script_run_ctx(...)` truyền vào `build_graph()`.
Nếu vẫn gặp → đảm bảo `tab_build.py` chưa bị revert về phiên bản cũ.

### "Build xong với N/total chunks thất bại" warning

**Triệu chứng**: UI hiển thị warning sau build.

**Nguyên nhân**: N chunks bị fail sau khi hết retry. Thường do 429 nặng hoặc network unstable.

**Fix**:
1. Build lại cùng collection — chunks đã succeed sẽ skip (merge), chunks fail sẽ retry. Không lo duplicate vì MongoDBGraphStore dùng upsert.
2. Giảm workers nếu fail rate > 10%

### "Build thất bại" hiện ra rồi mất ngay (không có chi tiết)

**Triệu chứng**: bấm Build → flash đỏ "Build thất bại" → biến mất sau rerun.

**Nguyên nhân (đã fix)**: `st.error()` render ra rồi `st.rerun()` ngay sau đó nuke message.

**Fix**: code mới lưu traceback vào `st.session_state["last_build_traceback"]` → survive
rerun, hiển thị trong expander dưới button. Nếu vẫn gặp → kéo xuống cuối tab Build,
mở expander "🔍 Chi tiết lỗi" để xem stack trace đầy đủ.

### UI kẹt "Khởi tạo..." dù DB đã có data

**Nguyên nhân (đã fix)**: worker thread thiếu ScriptRunContext → callback progress
không gọi được `st.write` → progress bar không update.

**Fix**: đảm bảo dùng phiên bản code có `thread_initializer` (xem mục JSON parse retry ở trên).

## 🤖 Query errors

### "Không có thông tin nào trong ngữ cảnh đã truy xuất"

**Triệu chứng**: hỏi câu thông thường nhưng LLM nói không có data.

**Possible causes & fixes**:

| Cause | Fix |
|-------|-----|
| Sai active collection trong sidebar | Sidebar → chọn collection đúng (có data về chủ đề hỏi) |
| Tên trong câu hỏi ≠ tên trong graph (vd "Tuyen" vs "Pham Tuyen") | Bật Hybrid mode — build embeddings (tab Build → 🧬 section) |
| Entity rỗng attributes/edges | Rebuild với context injection (đã có trong code mới) |
| Duplicate entities chia data → mỗi variant ít info | Chạy `scripts/normalize-collection.py --collection X --apply` rồi `rebuild-embeddings.py --force` |
| Collection không có data về chủ đề hỏi | Verify entities bằng `python scripts/debug-query.py <coll>` |
| Streamlit cache stale | Sidebar: Clear cache + Rerun (menu hamburger ☰ → "Clear cache") |

### Câu trả lời hallucinate (sai sự thật)

**Triệu chứng**: LLM trả lời thông tin không có trong PDF.

**Nguyên nhân**:
- LLM "tự bịa" khi context không đủ
- Hoặc context có entities sai (từ LLM extraction kém)

**Fix**:
1. Check anchors hiển thị dưới câu trả lời — có hợp lý không?
2. Expand "🔗 Entities liên quan trong graph" để xem context
3. Rebuild graph với chunk_size nhỏ hơn (1000-1200) → extraction precise hơn —
   dùng `recommend_chunk_params()` trong UI
4. Verify `OPENAI_QUERY_MODEL=gpt-5` (chất lượng cao hơn gpt-5-mini cho reasoning)

### Anchors trong câu trả lời thiếu entity quan trọng

**Triệu chứng**: hỏi về "X" nhưng anchors không có "X".

**Nguyên nhân**:
- Vector search k=10 không bắt được X
- Hoặc X không tồn tại trong graph (build thiếu)

**Fix**:
1. Verify X tồn tại: `python scripts/debug-query.py <coll> X`
2. Nếu có → tăng `vector_k` trong `_gather_anchor_entities` (mặc định 10)
3. Nếu không có → rebuild với chunk chứa X

## 🌐 Atlas Vector Search errors

### `OperationFailure: Cluster không hỗ trợ Atlas Search`

**Triệu chứng**: `ensure_vector_index` fail.

**Nguyên nhân**: cluster phiên bản < 6.0.11 hoặc 7.0.2.

**Fix**:
1. Atlas → Cluster → Configuration → upgrade MongoDB version
2. Hoặc tạo cluster mới với version mới hơn

### Vector index "PENDING" mãi không "READY"

**Triệu chứng**: build embeddings done nhưng query Hybrid mode vẫn không work.

**Fix**:
1. Đợi 1-5 phút (Atlas build async)
2. Atlas UI → Cluster → Search → check status
3. Manually trigger query nhỏ:
   ```python
   from src.entity_embedder import vector_search_entities
   from src.config import load_config
   results = vector_search_entities(load_config(), "your_coll", "test query", k=5)
   print(results)
   ```
4. Nếu lỗi `index not found` → đợi tiếp; lỗi khác → drop index + recreate

### `$vectorSearch needs index ... but index is missing`

**Triệu chứng**: query Hybrid fail.

**Fix**: rebuild index:
```python
from src.entity_embedder import ensure_vector_index
from src.config import load_config
ensure_vector_index(load_config(), "your_coll")
```

## 🕸️ Visualization errors

### `RuntimeError: Collection rỗng. Hãy chạy build-graph trước`

**Fix**: build graph trước (tab Build hoặc CLI), rồi mới visualize.

### HTML file render xong nhưng browser show blank

**Nguyên nhân**: pyvis dùng CDN cho vis-network.js. Nếu offline hoặc CDN block → empty.

**Fix**:
1. Check console browser (F12) xem error JS
2. Cần kết nối internet để load CDN
3. Hoặc download `vis-network.js` về local và sửa `render_html()`

### HTML lag/giật khi load 500+ nodes

**Fix**:
1. Giảm slider `max_nodes` xuống 150-200
2. Hoặc dùng tool chuyên dụng (Gephi, Cytoscape) cho large graphs

## 🎨 Streamlit errors

### "Streamlit không tìm thấy `src`"

**Triệu chứng**: `ModuleNotFoundError: src`.

**Fix**: chạy từ ROOT directory (`graphrag-demo/`), không phải subfolder:
```powershell
cd C:\w\_me\graphrag-demo
streamlit run app.py
```

### Button "🚀 Build graph" kẹt ở "⏳ Đang build..." sau khi xong

**Nguyên nhân**: bug cũ — đã fix. Nếu vẫn gặp:

**Fix**:
1. Refresh browser (Ctrl+Shift+R)
2. Streamlit menu ☰ → "Clear cache" → Rerun

### Status box "✅ Xong" treo trên UI

**Nguyên nhân**: bug cũ — đã fix. Nếu vẫn gặp:

**Fix**: same as button kẹt — clear cache + rerun.

### Câu hỏi không hiển thị ngay khi Enter (đợi 2-3s)

**Nguyên nhân**: bug cũ — đã fix bằng `st.rerun()` pattern. Nếu vẫn gặp:

**Fix**:
1. Restart Streamlit (Ctrl+C terminal, chạy lại `streamlit run app.py`)
2. Clear cache từ menu ☰

### Chat input nằm ở giữa thay vì cuối tab

**Nguyên nhân**: bug cũ — đã fix bằng container placeholder. Nếu vẫn gặp:

**Fix**: hard refresh browser (Ctrl+Shift+R).

### Streamlit cache_resource giữ engine cũ sau khi tạo vector index

**Triệu chứng**: build embeddings xong nhưng tab Chat vẫn show `🕸️ Graph-only`.

**Fix**:
1. Sidebar → bấm **🔄 Refresh danh sách collections** (clear cache)
2. Hoặc menu ☰ → Clear cache → Rerun
3. (Đã fix trong code: `_check_vector_index` không cache giá trị)

## 📊 Performance issues

### Chat trả lời quá chậm (> 30s)

**Possible causes**:
- `query_model = gpt-5` inherently slow (long reasoning steps)
- Graph quá dày → `$graphLookup` traversal lâu
- Context window full (80 entities × edges → nhiều token)

**Fix**:
1. Đổi `OPENAI_QUERY_MODEL=gpt-5-mini` → nhanh hơn ~5× (đánh đổi chất lượng nhẹ)
2. Giảm `MAX_ENTITIES_IN_CONTEXT` trong `src/query_engine.py` từ 80 → 50
3. Limit `vector_k` xuống 5

### MongoDB query chậm

**Possible causes**:
- Collection lớn (1000+ entities)
- Atlas M0 free tier có resource limit

**Fix**:
1. Atlas → Cluster → Performance → check CPU/memory
2. Upgrade lên M10+ cluster nếu workload thật
3. Add index `_id` (Mongo tự tạo, nhưng verify)

## 🐛 Data quality issues

### 68% entities "vô hồn" (empty attributes + relationships)

**Triệu chứng**: visualize thấy nhiều node rỗng.

**Nguyên nhân**: build với code cũ (không có context injection).

**Fix**:
1. Drop collection cũ trong Atlas UI
2. Rebuild với code mới (có context injection tự động)
3. Verify: `python scripts/debug-query.py <coll>` — attributes nên có nội dung

### Duplicate entities (vd "Information Security Policy" và "Information security policy" là 2 nodes)

**Nguyên nhân**: LLM extraction qua N chunks độc lập, viết hoa/khoảng trắng/dấu khác nhau.

**Fix** (auto trong UI từ v0.5+):
1. UI tự `find_merge_candidates` + `apply_merge_plans` ngay sau build → merge variants
   theo canonical key (`re.sub(r"[^a-z0-9]", "", name.lower())`).
2. Manual: `python scripts/normalize-collection.py --collection X --apply`
3. Sau normalize → re-embed: `python scripts/rebuild-embeddings.py --collection X --force`
   (vì attrs/rels đã đổi → embedding cũ stale).

### Pham Tuyen không link tới Veek/AIAIVN

**Nguyên nhân**: LLM bỏ sót edge khi extract chunk-by-chunk.

**Fix**: rebuild với code mới — DocumentContext + prefix sẽ ép LLM tạo edge.

## 🔌 Connection issues

### `dbread MCP không thấy graphrag_demo connection`

**Triệu chứng**: `mcp__dbread__list_connections` không có `graphrag_demo`.

**Nguyên nhân**: MCP server load config lúc Claude Code session start. Thêm connection sau đó cần restart.

**Fix**: restart Claude Code session.

### Mongo connection drops giữa chừng

**Nguyên nhân**: idle timeout từ Atlas (mặc định 30 phút).

**Fix**: code tự reconnect (pymongo handle). Nếu fail:
1. Verify URI có `retryWrites=true&w=majority`
2. Restart Streamlit / Python

## ☁️ Deploy errors (Streamlit Cloud)

### `RuntimeError: Thiếu biến môi trường bắt buộc: MONGODB_URI` trên cloud

**Nguyên nhân**: secrets chưa điền hoặc `app.py` không sync `st.secrets` → `os.environ`.

**Fix**:
1. Streamlit Cloud → app → Settings → **Secrets** → paste TOML:
   ```toml
   MONGODB_URI = "mongodb+srv://..."
   OPENAI_API_KEY = "sk-..."
   OPENAI_EXTRACTION_MODEL = "gpt-5-mini"
   OPENAI_QUERY_MODEL = "gpt-5"
   OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
   ```
2. Verify `app.py` có đoạn `for _key, _val in st.secrets.items(): os.environ.setdefault(...)`
   ở đầu file.

### "Main file does not exist: streamlit_app.py"

**Nguyên nhân**: Streamlit Cloud mặc định tìm `streamlit_app.py`.

**Fix**: deployment settings → main file → đổi sang `app.py`.

### "You do not have admin permissions on GitHub"

**Nguyên nhân**: tài khoản Streamlit Cloud login khác chủ repo.

**Fix** (3 options):
1. Login Streamlit bằng tài khoản GitHub là OWNER của repo
2. Owner add bạn làm admin collaborator của repo
3. Fork repo về tài khoản của bạn rồi deploy fork

### Version v0.X.Y trong sidebar không update sau deploy

**Nguyên nhân**: Streamlit Cloud cache module Python.

**Fix**:
1. App settings → **Reboot app** (force restart)
2. Hoặc bump `src/version.py` → push commit mới → cloud auto re-deploy

## 💬 Khi không tự fix được

1. Check log đầy đủ: chạy CLI thay vì UI để thấy stack trace
2. Inspect data: `python scripts/debug-query.py <coll> <search_regex>`
3. Verify version deps: `pip list | grep -i langchain`
4. Reset state: drop collection trong Atlas → rebuild

## ❓ Câu hỏi không có trong list này?

Xem [pipeline-overview.md](pipeline-overview.md) hoặc đi vào component cụ thể trong [component-*.md](.).
