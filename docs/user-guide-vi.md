# Hướng dẫn sử dụng — GraphRAG × MongoDB Demo

## 1. Demo này là gì?

**GraphRAG × MongoDB Demo** là một web app cho phép:

1. **Upload 1 file PDF** (báo cáo, hợp đồng, audit, CV, ...)
2. **Tự động trích xuất** các thực thể (entity) và mối quan hệ thành 1 đồ thị tri thức (knowledge graph) lưu trong MongoDB
3. **Hỏi đáp tự nhiên** với tài liệu — bot trả lời dựa trên đồ thị, không phải search keyword

Khác biệt với chatbot thông thường:
- Không "đoán" từ training data — chỉ dùng thông tin trong PDF của bạn
- Hiểu được quan hệ giữa các thực thể (vd Schellman → audit → OpenAI Inc.)
- Trả lời có ground truth (có thể trace lại nguồn entity)

## 2. Truy cập

URL chính: `https://eve-graphrag-demo.streamlit.app/`

**Deep links** (mở thẳng tab cụ thể):
| URL               | Mở tab                     |
|-------------------|----------------------------|
| `/?tab=build`     | Build Graph (tab mặc định) |
| `/?tab=chat`      | Chat — hỏi đáp             |
| `/?tab=visualize` | Visualize — xem đồ thị     |

Trên góc trên trái có **App version** (vd `v0.6.1`) — nếu app vừa update mà reload không thấy đổi → bấm `R` để rerun, hoặc menu góc phải `Clear cache → Rerun`.

## 3. Tổng quan giao diện

### Sidebar (cột trái)
- **App version**: bump mỗi lần code update
- **DB**: tên database MongoDB
- **Extraction model**: LLM dùng cho phase build (mặc định `gpt-5-mini` — nhanh, rẻ)
- **Query model**: LLM dùng cho Chat (mặc định `gpt-5` — chất lượng cao)
- **Active collection**: dropdown chọn knowledge base — cốt lõi để switch giữa các bộ tài liệu khác nhau

### 3 tabs chính
1. **1️⃣ Build Graph** — Upload PDF, build đồ thị tri thức (lần đầu cho 1 tài liệu mới)
2. **2️⃣ Chat** — Hỏi đáp với tri thức đã build (use case chính)
3. **3️⃣ Visualize** — Render đồ thị thành HTML tương tác

---

## 4. Use case 1: Hỏi đáp với tài liệu có sẵn (use case chính)

Demo đã build sẵn collection `openai_2025_soc_2_type_2_report` — báo cáo SOC 2 Type 2 của OpenAI (124 trang, ~3000 entities). Người dùng có thể hỏi ngay không cần upload mới.

### Bước 1: Vào tab Chat
- Click `2️⃣ Chat` hoặc mở URL `?tab=chat`

### Bước 2: Verify active collection
- Nhìn sidebar bên trái → `Active collection` phải là `openai_2025_soc_2_type_2_report`
- Nếu không phải, chọn lại từ dropdown

### Bước 3: Đặt câu hỏi
Gõ vào ô `Type your question...` ở dưới cùng và nhấn Enter.

**Mẫu câu hỏi đã test (đều trả lời chính xác)**:

| Loại           | Câu hỏi mẫu                                             | Kết quả expected                                                              |
|----------------|---------------------------------------------------------|-------------------------------------------------------------------------------|
| Basic facts    | "Báo cáo SOC 2 này là của công ty nào, do ai audit?"    | OpenAI Inc. — Schellman & Company, LLC                                        |
| Liệt kê        | "Liệt kê tất cả subservice organizations của OpenAI"    | Azure, Snowflake, Okta, WorkOS, ...                                           |
| Specific       | "Snowflake cung cấp gì cho OpenAI?"                     | Data warehouse + role + responsibilities                                      |
| Control        | "Control CC6.1 nói về gì?"                              | Logical access control + related entities                                     |
| Role           | "AICPA có vai trò gì trong báo cáo này?"                | Standards body cho SOC 2 framework                                            |
| Structure      | "Báo cáo có cấu trúc thế nào?"                          | 5 sections + nội dung mỗi section                                             |
| Policy         | "Information Security Policy có những gì? Ai maintain?" | Scope/coverage + Management owns                                              |
| Trust criteria | "Báo cáo đánh giá các tiêu chí Trust Services nào?"     | Security/Availability/Confidentiality/Privacy (excludes Processing Integrity) |
| Count          | "Có bao nhiêu Control Objective trong báo cáo?"         | 7 Control Objectives + tên chi tiết                                           |

### Bước 4: Đọc kết quả
Sau khi bấm Enter, sẽ thấy 3 thành phần:

1. **Câu trả lời** — text trả lời câu hỏi
2. **Mode badge** dưới câu trả lời:
   - `🧬 Hybrid (Vector + Graph)` — chế độ tốt nhất (đã có embeddings)
   - `🕸️ Graph-only` — chế độ cơ bản (chưa embed)
3. **Anchors** — danh sách entity bot đã dùng làm điểm bắt đầu để traverse graph
4. **🔗 Related entities in graph** (expander) — danh sách entity nằm trong context khi LLM trả lời

### Bước 5: Hỏi tiếp
Có thể hỏi liên tiếp — bot không nhớ context cũ giữa các câu (mỗi câu là 1 query độc lập). Để bot có context lịch sử, gộp câu hỏi vào 1 prompt.

---

## 5. Use case 2: Upload PDF mới và build graph

Dùng khi muốn thử với 1 tài liệu khác (CV, hợp đồng, báo cáo khác).

### Bước 1: Vào tab Build Graph

### Bước 2: Upload PDF
- Click `Choose PDF file` → chọn file từ máy
- Sau khi upload, hệ thống tự phân tích:
  - Số trang
  - Tổng số ký tự
  - Đề xuất `chunk_size` và `overlap` phù hợp với độ dài

→ Bấm `✨ Apply recommendation` để tự điền tham số đề xuất.

### Bước 3: Chọn collection
- **Tạo collection mới**: gõ tên (vd `my_contract_2026`) hoặc bấm `💡 Suggest from filename` để tự generate từ tên file
- **Merge vào collection có sẵn**: chọn từ dropdown (nếu muốn add tài liệu mới vào knowledge base hiện có — sẽ merge entities cùng tên)

### Bước 4: Set tham số (đã pre-fill từ đề xuất)
- **Chunk size**: số ký tự / chunk
- **Overlap**: ký tự chồng lấp giữa 2 chunks
- **Chunk limit**: mặc định `0` = chạy full PDF. Đặt số nhỏ (vd `20`) cho 1 lần test rẻ trước nếu lo về chi phí
- **Parallel workers**: số chunk xử lý song song (mặc định 5 — không nên >10 vì OpenAI rate limit)

### Bước 5: Bấm `🚀 Build graph`
- Quá trình mất vài phút đến vài chục phút tuỳ độ dài PDF
- Progress bar hiển thị: số chunk đã xử lý, % hoàn thành, ETA, số chunk fail (nếu có)
- Sau build xong, hệ thống **tự động**:
  1. **Normalize duplicates** — gộp các entity trùng tên (vd "Information Security Policy" với "information security policy")
  2. Show summary: số chunks, số entities, thời gian, có lỗi không

### Bước 6: Build embeddings (optional, recommended)
Sau khi build graph xong, scroll xuống section `🧬 Hybrid Vector + Graph` và bấm `🧬 Build embeddings + vector index`.

→ Enables hybrid mode: chat sẽ tốt hơn với câu hỏi natural language (không cần khớp tên entity chính xác).

### Bước 7: Sang tab Chat hỏi đáp
- Chọn collection mới ở sidebar
- Hỏi như mục 4

---

## 6. Use case 3: Visualize đồ thị

Dùng để **trực quan hóa** đồ thị tri thức — thấy entities và relationships giữa chúng.

### Bước 1: Vào tab Visualize
### Bước 2: Đảm bảo active collection đúng
### Bước 3: Set `Max entities` — số entity hiển thị tối đa
- Mặc định: 80
- Nên ≤ 150 để browser mượt
- > 200 có thể lag với laptop yếu

### Bước 4: Bấm `🎨 Render graph HTML`
Sau vài giây sẽ thấy đồ thị tương tác:
- Có thể **kéo thả** entity
- **Zoom** bằng scroll
- **Click** vào entity để highlight
- **Hover** xem tooltip (type + số relationships + tier)

### Cấu trúc visualize
- Entity được phân **5 cấp** (tier) theo độ "trung tâm":
  - **Tier 1** (50px, biggest): super-hubs (vd `Schellman & Company`, `OpenAI Inc.`)
  - **Tier 2** (35px): major hubs
  - **Tier 3** (25px): connectors
  - **Tier 4** (18px): mid-tier
  - **Tier 5** (12px): leaf entities
- Màu node theo `type` (Organization, Control, Policy, ...)
- Mũi tên = direction của relationship (A → B nghĩa là A có relationship trỏ tới B)

---

## 7. Tips để sử dụng hiệu quả

### ✅ Nên
- **Hỏi cụ thể**, tên entity chính xác: "What is Schellman & Company?" tốt hơn "Tell me about the auditor"
- **Hỏi từng câu một** — bot không nhớ context giữa câu (mỗi câu là 1 query độc lập)
- **Verify active collection** trước khi hỏi — kiểm tra sidebar
- **Test với câu mẫu** ở mục 4 trước → quen với behavior trước khi hỏi câu mới

### ❌ Tránh
- **Câu quá generic** ("Tell me everything") — bot sẽ ngắt thành câu chung chung
- **Câu hỏi suy luận sâu** ("What would happen if Okta failed?") — bot chỉ trả lời theo data có trong graph, không reasoning ngoài
- **Câu hỏi yêu cầu tính toán** ("How many controls × policies?") — bot không làm math, chỉ retrieve

---

## 8. Các giới hạn đã biết

| Limitation                                                                            | Workaround                                       |
|---------------------------------------------------------------------------------------|--------------------------------------------------|
| Bot có thể trả lời chậm (5-15s) cho câu phức tạp                                      | Đợi, không spam Enter                            |
| Bot trả lời tiếng Anh nếu collection English (kể cả hỏi tiếng Việt)                   | Hỏi tiếng Anh để consistent                      |
| Đếm số chính xác (count queries) yêu cầu detect intent — đôi khi miss với phrasing lạ | Dùng từ khóa chuẩn: "How many...", "List all..." |
| Build LLM call → tốn API credits                                                      | Set chunk limit nhỏ khi test                     |
| Visualize lag khi >200 nodes                                                          | Giảm slider Max entities                         |

---

## 9. FAQ

**Q: Tại sao bot không trả lời được câu hỏi của tôi?**
- Check active collection có data về chủ đề đó không (sidebar)
- Hỏi cụ thể hơn, dùng tên entity chính xác
- Verify mode badge có phải `Hybrid` không — nếu `Graph-only`, hãy build embeddings

**Q: Build graph mất bao lâu?**
- Tài liệu nhỏ (<20 trang): ~2-5 phút
- Trung bình (20-100 trang): 10-30 phút
- Lớn (>100 trang): 30-60 phút (124-trang SOC 2 demo: ~25 phút)

**Q: Có tốn API credits không?**
- Build: ~$0.50 cho 124 trang (gpt-5-mini cho extraction)
- Chat: ~$0.01-0.05 / câu hỏi (gpt-5 cho RAG)
- Embeddings: ~$0.005 cho 3000 entities

**Q: Có thể xóa collection không?**
- Hiện UI chưa hỗ trợ — phải xóa trực tiếp trong MongoDB Atlas dashboard

**Q: Bot có lưu lại chat history không?**
- Không. Mỗi session độc lập, refresh page = mất history
