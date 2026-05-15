# Getting Started — Hands-on Guide

> Hướng dẫn từng bước cho người **lần đầu** chạy demo. Mục tiêu: từ zero
> tới có graph + chat hoạt động trong ~30 phút.

## 📋 Checklist trước khi bắt đầu

- [ ] **Python 3.10+** đã cài (project test với 3.14)
- [ ] **MongoDB Atlas account** (free tier OK) — [sign up](https://cloud.mongodb.com/)
- [ ] **OpenAI API key** + balance ≥ $5 — [get key](https://platform.openai.com/api-keys)
- [ ] PDF nguồn để test (CV, báo cáo, hợp đồng — bất kỳ)

## 🚀 Setup (~10 phút)

### Bước 1: Clone & cài deps

```powershell
cd C:\w\_me\graphrag-demo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Bước 2: Tạo Atlas cluster

1. Đăng nhập [cloud.mongodb.com](https://cloud.mongodb.com)
2. Tạo cluster **M0 (Free Forever)** — chọn region gần nhất
3. **Network Access** → Add IP → `0.0.0.0/0` (allow all — chỉ cho demo)
4. **Database Access** → tạo user (lưu password!)
5. **Connect** → Drivers → Python → copy connection string

Ví dụ URI:
```
mongodb+srv://myuser:mypassword@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority
```

### Bước 3: Cấu hình `.env`

Tạo file `.env` trong root project:

```env
MONGODB_URI=mongodb+srv://myuser:mypassword@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority
```

**Optional** (override defaults):
```env
MONGODB_DB=my_db
MONGODB_COLLECTION=my_kb
OPENAI_EXTRACTION_MODEL=gpt-5-mini       # NHANH + RẺ cho build (N chunks)
OPENAI_QUERY_MODEL=gpt-5                 # CHẤT LƯỢNG cho chat (1 lần/Q)
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

> **Migration**: nếu bạn đã có `OPENAI_CHAT_MODEL=gpt-5` từ phiên bản cũ → vẫn work
> (backward compat). Code sẽ dùng giá trị này cho cả 2 vai trò extraction + query.

**`OPENAI_API_KEY`** — chỉ cần điền nếu chưa có sẵn trong env máy:
```env
OPENAI_API_KEY=sk-...
```

**Deploy lên Streamlit Cloud**: dùng `st.secrets` (TOML) thay vì `.env`:
```toml
MONGODB_URI = "mongodb+srv://..."
OPENAI_API_KEY = "sk-..."
OPENAI_EXTRACTION_MODEL = "gpt-5-mini"
OPENAI_QUERY_MODEL = "gpt-5"
```
`app.py` tự sync `st.secrets` → `os.environ` lúc startup.

### Bước 4: Kiểm tra setup

```powershell
.\.venv\Scripts\python.exe -c "from src.config import load_config; cfg=load_config(); print(f'OK. DB={cfg.mongodb_db}, OpenAI key length={len(cfg.openai_api_key)}')"
```

Nếu in `OK. DB=graphrag_demo, OpenAI key length=...` → setup xong.

## 🎮 First run — build graph từ CV

### Cách A: Web UI (recommend)

```powershell
streamlit run app.py
```

→ Mở browser http://localhost:8501

**Trong tab "1️⃣ Build Graph":**

1. **Bước 1**: Upload file PDF (CV/báo cáo của bạn)
   - UI tự phân tích pages + tổng chars → đề xuất `chunk_size/overlap` phù hợp
   - Bấm **✅ Áp dụng đề xuất** để pre-fill tham số bên dưới
2. **Bước 2**: chọn "Tạo collection mới" → bấm **💡 Suggest từ tên file** → tự fill
3. **Bước 3** (tham số đã pre-fill từ đề xuất, có thể chỉnh):
   - Limit chunks: **`5`** (test rẻ lần đầu, sau đó tăng lên)
   - Workers: `5`
4. Bấm **🚀 Build graph**
5. Đợi 1-2 phút → thấy banner "✅ Build xong! N entities"
6. **Auto-normalize** chạy ngay sau build → merge duplicate entities (vd 3 variant
   của "Information Security Policy" → 1 entity gộp đầy đủ).
7. (Optional) Bấm **🧬 Build embeddings** để enable Hybrid mode khi chat

**Sang tab "2️⃣ Chat":**

1. Sidebar bên trái → chọn collection vừa build
2. Hỏi câu đầu tiên, vd:
   - *"Tài liệu này nói về ai/cái gì?"*
   - *"Liệt kê các thực thể chính"*
3. Đợi 5-10s → thấy câu trả lời + badge `🕸️ Graph-only`

### Cách B: CLI

```powershell
.\.venv\Scripts\python.exe scripts/build-graph.py --pdf "your.pdf" --limit-chunks 5
```

## ✨ Nâng cấp lên Hybrid Mode (semantic search)

Sau khi build xong, embedding entities để query bằng tên mơ hồ:

**Trong tab "1️⃣ Build Graph"** → kéo xuống section **🧬 Hybrid Vector + Graph**:

1. Chọn collection
2. Bấm **🧬 Build embeddings + vector index**
3. Đợi ~30-60s (1 LLM embed call/entity, ~$0.0002/entity)

→ Sau đó tab Chat hiển thị badge `🧬 Hybrid (Vector + Graph)` thay vì `🕸️ Graph-only`.

**Thử ngay**: hỏi câu với tên KHÔNG khớp exact (vd "Tuyen" thay vì "Pham Tuyen") → giờ tìm được.

## 🕸️ Visualize graph

Tab "3️⃣ Visualize" → chọn collection → slider max nodes → bấm **🎨 Render**.

→ Thấy đồ thị interactive (kéo, zoom, hover). Nodes cùng type cùng màu.

## 🧪 Build full PDF (sau khi test OK)

Khi đã verify pipeline hoạt động:

- **Web UI**: tab Build → đặt **"Giới hạn số chunk = 0"** → build lại
- **CLI**: bỏ flag `--limit-chunks`:
  ```powershell
  python scripts/build-graph.py --pdf "your.pdf" --workers 5
  ```

**Cost ước tính**:

| Doc size | Chunks | Time (5 workers, gpt-5) | Cost |
|----------|--------|--------------------------|------|
| 4 trang | 5 | ~1-2 phút | ~$0.5 |
| 30 trang | 80 | ~10-15 phút | ~$5-8 |
| 100 trang | 300 | ~30-50 phút | ~$15-25 |

## 🔍 Kiểm tra data trong MongoDB

**MongoDB Compass** (recommend):
1. Download [Compass](https://www.mongodb.com/products/compass)
2. Connect với URI từ `.env`
3. Browse `graphrag_demo` → `your_collection`
4. Click vào document → xem `_id`, `attributes`, `relationships`, `embedding`

**Hoặc CLI**:
```powershell
.\.venv\Scripts\python.exe scripts/debug-query.py your_collection
```

→ In ra danh sách entities + sample doc.

## 📚 Đọc tiếp

Sau khi chạy thành công lần đầu:

1. [pipeline-overview.md](pipeline-overview.md) — hiểu kiến trúc
2. [component-relationships.md](component-relationships.md) — components tương tác thế nào
3. [graphrag-explained.md](graphrag-explained.md) — lý thuyết GraphRAG
4. Đọc từng [component-*.md](.) khi cần đi sâu

## 🆘 Gặp lỗi?

Xem [troubleshooting.md](troubleshooting.md) — list các lỗi phổ biến + cách fix.

## ❓ FAQ nhanh

**Q: Tôi không có $5 OpenAI credit?**
A: Đổi `OPENAI_CHAT_MODEL=gpt-4o-mini` trong `.env` — rẻ hơn 50× so với gpt-5.
Build 30-page PDF chỉ tốn ~$0.20.

**Q: Có cần biết MongoDB không?**
A: Không. Demo tự tạo database/collection tự động. Mongo chỉ là storage.

**Q: Có thể chạy hoàn toàn local (không cloud)?**
A: Cần adapt:
- MongoDB → cài Mongo local + Atlas Search add-on (phức tạp)
- OpenAI → swap sang Ollama (local LLM)
- Demo này hiện chỉ support Atlas + OpenAI.

**Q: Build cùng PDF nhiều lần?**
A: Vào cùng collection → entities được merge (không duplicate). Vào collection
khác → tạo knowledge base riêng.

**Q: Có thể upload nhiều PDF cùng domain không?**
A: Có. Build từng cái vào cùng collection → entities common (như tên người,
tổ chức) sẽ merge → graph càng giàu càng nhiều docs.
