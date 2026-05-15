# GraphRAG × MongoDB Demo

Demo end-to-end **GraphRAG với MongoDB Atlas + OpenAI**. Upload PDF bất kỳ
qua Web UI để build knowledge graph và hỏi đáp.

> Đọc tài liệu khái niệm:
> - [docs/graphrag-explained.md](docs/graphrag-explained.md) — GraphRAG tổng quát
> - [docs/graphrag-mongodb.md](docs/graphrag-mongodb.md) — GraphRAG với MongoDB

## 🏗️ Kiến trúc

```
PDF ──► chunk text ──► LLM extract entities + relationships
                                   │
                                   ▼
                          MongoDB Atlas
                       (knowledge graph)
                                   │
                  ┌────────────────┼────────────────┐
                  ▼                ▼                ▼
            Streamlit Web UI   $graphLookup      pyvis HTML
              (Chat tab)       (query time)     visualization
```

## 📁 Cấu trúc dự án

```
graphrag-demo/
├── app.py                          # Streamlit Web UI (chat + build + visualize)
├── requirements.txt
├── .env.example                    # template config
├── docs/
│   ├── graphrag-explained.md
│   └── graphrag-mongodb.md
├── src/
│   ├── config.py                   # load env vars
│   ├── pdf_loader.py               # PDF → chunks
│   ├── graph_builder.py            # MongoDBGraphStore wrapper
│   ├── query_engine.py             # chat_response wrapper
│   └── visualizer.py               # networkx + pyvis
└── scripts/
    ├── build-graph.py              # CLI: build graph from PDF
    └── visualize-graph.py          # CLI: render HTML
```

## 🚀 Cài đặt

### 1. Tạo MongoDB Atlas cluster (miễn phí)

1. Đăng ký tại [cloud.mongodb.com](https://cloud.mongodb.com)
2. Tạo **M0 Free cluster** (chọn region gần nhất)
3. Vào **Network Access** → Add IP Address → **Allow Access From Anywhere** (chỉ cho demo)
4. Vào **Database Access** → tạo user/password
5. Bấm **Connect → Drivers → Python** → copy connection string
   - VD: `mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority`

### 2. Lấy OpenAI API Key

- Truy cập [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- Đảm bảo tài khoản có credits (mua tối thiểu $5 nếu là tài khoản mới)

### 3. Setup môi trường

```powershell
# Tạo venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Cài deps
pip install -r requirements.txt

# Tạo .env từ template
Copy-Item .env.example .env
# Sửa .env: điền MONGODB_URI và OPENAI_API_KEY
```

## 🎯 Cách chạy

### Option A — Dùng Web UI (khuyến nghị)

```powershell
streamlit run app.py
```

Sau đó mở [http://localhost:8501](http://localhost:8501):
1. Tab **🔨 Build Graph** → upload file PDF → chọn collection → bấm "Build graph"
   - Khuyến nghị bắt đầu với `Giới hạn số chunk = 20` để test nhanh
   - Set `0` để chạy full PDF (~ vài phút tuỳ độ dài)
2. Tab **💬 Chat** → hỏi đáp với knowledge graph
3. Tab **🕸️ Visualize** → xem đồ thị tri thức interactive

### Option B — Dùng CLI

```powershell
# Build graph từ PDF (bắt buộc --pdf)
python scripts/build-graph.py --pdf "path\to\your.pdf" --limit-chunks 20

# Visualize ra HTML
python scripts/visualize-graph.py --out out/graph.html
# Mở out/graph.html trong browser
```

## 🔍 Cách hệ thống hoạt động

1. **Load PDF** (`src/pdf_loader.py`): Dùng `PyPDFLoader` parse PDF
   thành các page, rồi `RecursiveCharacterTextSplitter` cắt thành
   chunks ~1500 ký tự với overlap 200.

2. **Build Graph** (`src/graph_builder.py`): `MongoDBGraphStore` gửi
   từng chunk cho LLM (GPT-5) qua prompt chuẩn — LLM trả về danh
   sách entity + relationship → lưu vào MongoDB. Mỗi entity là 1
   document, các trường relationship trỏ tới `_id` của entity khác.

3. **Query** (`src/query_engine.py`): `chat_response` tự động:
   - Extract entity từ câu hỏi
   - Dùng `$graphLookup` của MongoDB để duyệt graph, tìm các entity
     liên kết và mối quan hệ
   - Gửi context đã thu thập + câu hỏi cho LLM → câu trả lời tự nhiên

4. **Visualize** (`src/visualizer.py`): Đọc collection từ Mongo →
   build `networkx.DiGraph` → render thành HTML bằng `pyvis`.

## 💡 Câu hỏi demo gợi ý

Tuỳ nội dung PDF bạn upload, ví dụ với báo cáo audit:

- *"Báo cáo này nói về hệ thống nào?"*
- *"Auditor là ai và scope của audit là gì?"*
- *"Liệt kê các tiêu chí đánh giá."*
- *"Có những control nào liên quan đến access management?"*
- *"Mối quan hệ giữa các bên liên quan trong báo cáo?"*
- *"Có vấn đề (exception) nào được tìm thấy không?"*

## ⚠️ Lưu ý chi phí

- **Build graph**: gọi LLM cho mỗi chunk → tốn token. Một PDF ~50-100 trang
  ước tính $1–3 USD nếu chạy full với GPT-5.
- **Query**: rẻ hơn nhiều — chỉ vài cents/câu hỏi.
- Demo có flag `--limit-chunks` / "Giới hạn số chunk" để test rẻ trước.

## 🐛 Troubleshooting

**Lỗi `ServerSelectionTimeoutError`**: kiểm tra IP đã được whitelist
trong MongoDB Atlas Network Access chưa.

**Lỗi `AuthenticationFailed`**: kiểm tra user/password trong
connection string khớp với Database Access user.

**Streamlit không tìm thấy `src`**: chạy `streamlit run app.py` từ
**root** của dự án (`C:\w\_me\graphrag-demo`), không phải từ thư mục con.

**Build chậm**: bình thường — mỗi chunk = 1 lần gọi LLM. Dùng
`--limit-chunks 10` để test nhanh.

## 📚 Tham khảo

- [MongoDB Docs — GraphRAG with MongoDB and LangChain](https://www.mongodb.com/docs/atlas/ai-integrations/langchain/graph-rag/)
- [MongoDB Blog — GraphRAG with MongoDB Atlas](https://www.mongodb.com/company/blog/graphrag-mongodb-atlas-integrating-knowledge-graphs-with-llms)
- [langchain-mongodb GitHub](https://github.com/langchain-ai/langchain-mongodb)
