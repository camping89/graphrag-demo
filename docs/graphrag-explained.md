# GraphRAG là gì? Giải thích dễ hiểu từ A-Z

> Ghi chú học tập về GraphRAG — tổng hợp từ Microsoft Research, Memgraph, Neo4j, Fluree và các nguồn khác (cập nhật 2025-2026).

## 1. Bắt đầu từ "RAG" trước đã

Trước khi hiểu GraphRAG, cần hiểu **RAG (Retrieval-Augmented Generation)** — kỹ thuật giúp các mô hình AI như ChatGPT trả lời chính xác hơn dựa trên dữ liệu riêng.

**Ví dụ dễ hình dung:** Bạn hỏi ChatGPT về tài liệu nội bộ công ty. ChatGPT không biết, vì nó chưa từng "đọc" tài liệu đó. RAG hoạt động như sau:

1. Cắt nhỏ tài liệu thành từng đoạn (chunks)
2. Khi bạn hỏi, hệ thống tìm những đoạn **giống nhất** với câu hỏi
3. Đưa các đoạn đó cho AI để AI trả lời dựa trên chúng

Đây là **RAG truyền thống** (Vector RAG). Giống như một thủ thư đi tìm những trang sách có từ khóa giống câu hỏi.

## 2. Vấn đề của RAG truyền thống

RAG truyền thống có những điểm yếu lớn:

- **Không "nối các điểm" được**: Nếu câu trả lời cần ghép nhiều mảnh thông tin từ nhiều tài liệu khác nhau → bó tay
- **Chỉ tìm theo độ giống bề mặt**: Tìm văn bản có vẻ giống câu hỏi, chứ không hiểu **mối quan hệ** giữa các sự vật
- **Không trả lời được câu hỏi tổng thể**: Kiểu "Toàn bộ tài liệu này nói về chủ đề chính gì?"

**Ví dụ kinh điển**: Bạn hỏi *"Alice đã làm dự án nào với những người làm việc dưới quyền Bob?"*

- RAG truyền thống: Tìm được tài liệu về Alice, tài liệu về Bob, nhưng **không biết cách kết nối** chúng → AI sẽ đoán mò → dễ bịa (hallucination)

## 3. GraphRAG là gì?

**GraphRAG = Graph + RAG**, do Microsoft Research phát triển. Thay vì chỉ lưu các đoạn văn bản, nó xây dựng một **bản đồ tri thức (knowledge graph)** từ dữ liệu.

**Hình dung đơn giản:**

- RAG truyền thống = Một đống thẻ ghi chú trộn lẫn, tìm bằng từ khóa
- GraphRAG = Một **sơ đồ mạng nhện** mà mỗi điểm là một "thực thể" (người, công ty, sản phẩm, khái niệm...), các đường nối là **mối quan hệ** giữa chúng (làm việc với, thuộc về, gây ra...)

**Ví dụ trực quan:**

```
[Alice] --làm dự án--> [Project X] <--làm dự án-- [Charlie]
                                                      |
                                                  báo cáo cho
                                                      v
                                                   [Bob]
```

Giờ AI có thể đi theo các mũi tên để trả lời chính xác câu hỏi phức tạp ở trên.

## 4. Nguyên lý hoạt động (2 giai đoạn)

### Giai đoạn 1: Indexing (Xây dựng bản đồ tri thức) — làm sẵn từ trước

1. **Cắt nhỏ tài liệu** thành các đoạn (TextUnits)
2. **Dùng LLM trích xuất** ra các thực thể (người, địa điểm, sự kiện, khái niệm...) và mối quan hệ
3. **Xây đồ thị** kết nối tất cả thực thể lại
4. **Phát hiện cộng đồng (community)** — gom các nhóm thực thể có liên hệ chặt thành cụm. VD: tất cả nhân vật trong cùng bộ phận = một "cộng đồng"
5. **Tóm tắt từng cộng đồng** bằng LLM → tạo ra "community summary" cho mỗi cụm

Kết quả: Một bản đồ tri thức có **nhiều tầng**, từ chi tiết (từng thực thể) đến tổng quát (tóm tắt cả cụm).

### Giai đoạn 2: Query (Trả lời câu hỏi)

GraphRAG có 3 cách tìm kiếm chính:

**a) Local Search (Tìm kiếm cục bộ)** — cho câu hỏi cụ thể về 1 thực thể

- Xác định thực thể trong câu hỏi → đi theo các đường nối trong đồ thị để gom ngữ cảnh xung quanh
- VD: "Sản phẩm X có những tính năng gì?"

**b) Global Search (Tìm kiếm toàn cục)** — cho câu hỏi tổng thể

- Dùng kỹ thuật **Map-Reduce**:
  - **Map**: Gửi câu hỏi đến từng cộng đồng, mỗi cộng đồng trả lời một phần
  - **Reduce**: Tổng hợp tất cả thành 1 câu trả lời cuối
- VD: "Chủ đề chính của toàn bộ tài liệu này là gì?"

**c) DRIFT Search (lai)** — kết hợp cả hai, bắt đầu từ tổng quan rồi đi vào chi tiết

## 5. Ứng dụng thực tế

| Lĩnh vực                          | Ví dụ ứng dụng                                                                                                              |
|-----------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| **Y tế**                          | Bệnh viện phân tích hồ sơ bệnh nhân, kết hợp lịch sử điều trị + tài liệu nghiên cứu → chẩn đoán bệnh hiếm đạt 95% chính xác |
| **Luật**                          | Luật sư hỏi "Cho tôi xem tất cả vụ án trích dẫn Vụ X về 'force majeure' trong 5 năm qua mà phán quyết đã bị lật ngược"      |
| **Tài chính**                     | Nền tảng reView của Neo4j giúp giảm 50% khối lượng công việc của analyst                                                    |
| **Hỗ trợ khách hàng**             | Chatbot trả lời "Tại sao đơn hàng của tôi bị trễ?" bằng cách lần theo mối quan hệ giữa đơn hàng → nhà cung cấp → quy trình  |
| **Dược phẩm**                     | Tích hợp bài báo nghiên cứu + dữ liệu thử nghiệm lâm sàng + quy định pháp lý vào hệ thống tra cứu                           |
| **Quản lý tri thức doanh nghiệp** | Tìm thông tin rải rác giữa các phòng ban, email, tài liệu nội bộ                                                            |
| **Chuỗi cung ứng**                | Phát hiện các phụ thuộc ẩn giữa nhà cung cấp                                                                                |
| **Phát hiện gian lận**            | Lần theo các mối quan hệ tài chính bất thường                                                                               |

### Benchmark số liệu (2025)

- FalkorDB benchmark: Vector RAG đạt **~0%** trên schema-bound queries phức tạp, Graph RAG đạt **>90%**
- Diffbot KG-LM benchmark: Graph RAG vượt RAG truyền thống **>50%** trong các bài toán multi-hop QA

## 6. So sánh nhanh

| Tiêu chí                    | RAG truyền thống  | GraphRAG                              |
|-----------------------------|-------------------|---------------------------------------|
| Cách lưu                    | Vector embeddings | Đồ thị tri thức + tóm tắt cộng đồng   |
| Câu hỏi đơn giản            | Tốt               | Tốt                                   |
| Câu hỏi nối nhiều thông tin | **Kém**           | **Mạnh**                              |
| Câu hỏi tổng thể            | Kém               | **Rất mạnh**                          |
| Khả năng giải thích nguồn   | Trung bình        | Tốt (truy ngược được)                 |
| Chi phí xây dựng            | Thấp              | **Cao** (LazyGraphRAG đã giải quyết)  |
| Chi phí truy vấn            | Thấp              | Cao hơn (LazyGraphRAG rẻ hơn 700 lần) |

## 7. Cập nhật mới: LazyGraphRAG (2024-2025)

Microsoft giới thiệu **LazyGraphRAG** — "lười biếng" theo nghĩa **không tóm tắt trước**, mà chỉ làm khi cần:

- Chi phí indexing **chỉ bằng 0.1%** GraphRAG đầy đủ
- Chi phí truy vấn **rẻ hơn 700 lần**
- Chất lượng câu trả lời tương đương Global Search

## 8. Khi nào nên dùng GraphRAG?

**Bài test đơn giản** (Connectivity Test): Câu hỏi của người dùng có cần kết nối **3 mảnh thông tin trở lên** từ các tài liệu khác nhau không?

- **Có** → Dùng GraphRAG
- **Không** → RAG truyền thống đã đủ (rẻ và đơn giản hơn)

## 9. Cách tiếp cận lai (Hybrid)

Trong thực tế, hệ thống tốt nhất thường **kết hợp cả hai**:

1. **Vector search** để tìm "vùng" liên quan trong knowledge base
2. **Graph traversal** để mở rộng ngữ cảnh, đi theo mối quan hệ
3. **Enriched context** đưa cho LLM để sinh câu trả lời

→ Tương lai không phải Vector **hoặc** Graph, mà là Vector **cộng với** Graph.

## Tóm lại

> **RAG cho AI quyền truy cập thông tin. GraphRAG cho AI khả năng hiểu mối liên hệ.**
>
> RAG trả lời câu hỏi với đúng **dữ liệu**. GraphRAG trả lời với đúng **ngữ cảnh**.

GraphRAG không phải "viên đạn bạc" cho mọi bài toán, nhưng khi dữ liệu có nhiều **mối quan hệ phức tạp** và cần **suy luận nhiều bước**, nó vượt trội hơn hẳn — đặc biệt với câu hỏi tổng hợp, phân tích trên toàn bộ tập tài liệu.

---

## Nguồn tham khảo

- [Project GraphRAG - Microsoft Research](https://www.microsoft.com/en-us/research/project/graphrag/)
- [GraphRAG: Unlocking LLM discovery on narrative private data](https://www.microsoft.com/en-us/research/blog/graphrag-unlocking-llm-discovery-on-narrative-private-data/)
- [LazyGraphRAG sets a new standard for GraphRAG quality and cost](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)
- [GraphRAG Documentation - Microsoft](https://microsoft.github.io/graphrag/)
- [GitHub - microsoft/graphrag](https://github.com/microsoft/graphrag)
- [GraphRAG: Nâng Tầm RAG với Đồ Thị Tri Thức - SIU AI Lab](https://ailab.siu.edu.vn/article/74/graphrag-nang-tam-rag-voi-o-thi-tri-thuc)
- [RAG vs GraphRAG: Shared Goal & Key Differences - Memgraph](https://memgraph.com/blog/rag-vs-graphrag)
- [GraphRAG vs. Vector RAG - Fluree](https://flur.ee/fluree-blog/graphrag-vs-vector-rag-when-knowledge-graphs-outperform-semantic-search/)
- [GraphRAG Use Cases - Lettria](https://www.lettria.com/blogpost/rag-use-cases-discover-4-uses-of-graphrag)
- [Graph RAG Guide 2025: Architecture, Implementation & ROI - Salfati Group](https://salfati.group/topics/graph-rag)
- [4 Real-World Success Stories Where GraphRAG Beats Standard RAG - Memgraph](https://memgraph.com/blog/graphrag-vs-standard-rag-success-stories)
- [What is GraphRAG: Complete guide 2026 - Meilisearch](https://www.meilisearch.com/blog/graph-rag)
