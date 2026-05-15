# GraphRAG với MongoDB là gì?

> Ghi chú học tập về cách triển khai GraphRAG trên MongoDB Atlas — so sánh với Microsoft GraphRAG (xem [graphrag-explained.md](./graphrag-explained.md)).

## 1. Khái niệm cơ bản

**GraphRAG với MongoDB** là cách triển khai GraphRAG sử dụng **MongoDB Atlas** làm nơi lưu trữ knowledge graph. Đây là tích hợp chính thức giữa MongoDB và **LangChain** (qua thư viện `langchain-mongodb` từ phiên bản 0.5.0 trở lên), đã **GA (General Availability)** trong 2025.

**Điểm cốt lõi**: MongoDB lưu các **thực thể (nodes)** dưới dạng documents thông thường, và các **mối quan hệ (edges)** dưới dạng các trường tham chiếu trong chính document đó. Khi truy vấn, MongoDB dùng **`$graphLookup`** — một aggregation stage có sẵn — để duyệt đồ thị.

## 2. Kiến trúc hoạt động

### Indexing (Xây graph)

```
Tài liệu → LLM (extract entities + relationships) → MongoDB collection
                                                       ↓
                                              Mỗi entity = 1 document
                                              Relationships = các fields tham chiếu
```

Component chính: **`MongoDBGraphStore`** trong LangChain

- Tự động gửi prompt cho LLM để trích xuất thực thể và mối quan hệ
- Lưu vào MongoDB dưới dạng document có cấu trúc
- Khi thêm document mới: tự động tìm entity đã tồn tại để update, hoặc tạo mới

### Query (Trả lời câu hỏi)

```
Câu hỏi → LLM extract entity từ câu hỏi
              ↓
       MongoDB $graphLookup (đi sâu vào graph)
              ↓
       Tìm các entity liên kết + relationships
              ↓
       Gửi context lại cho LLM → câu trả lời cuối
```

## 3. `$graphLookup` hoạt động thế nào?

Đây là "trái tim" của GraphRAG trên MongoDB — một aggregation stage **đệ quy**:

1. Bắt đầu từ một giá trị (`startWith`) trong document đầu vào
2. So sánh với field `connectToField` ở các document khác trong collection
3. Khi match, lấy giá trị từ `connectFromField` rồi tiếp tục tìm
4. Lặp đệ quy đến khi không còn match hoặc đạt `maxDepth`
5. Trả về mảng các document đã match trong field `as`

→ Đây là cách MongoDB "đi theo các mũi tên" trong graph mà không cần graph database chuyên dụng (như Neo4j).

## 4. So sánh: Microsoft GraphRAG vs MongoDB GraphRAG

Đây là khác biệt **quan trọng nhất** so với GraphRAG nguyên bản của Microsoft:

| Tiêu chí                  | **Microsoft GraphRAG**                | **MongoDB GraphRAG**                          |
|---------------------------|---------------------------------------|-----------------------------------------------|
| **Cách tiếp cận**         | Community-based (phân cụm + tóm tắt)  | Entity-based (thuần thực thể + quan hệ)       |
| **Community detection**   | Có — phát hiện cộng đồng tự động      | Không có sẵn                                  |
| **Community summaries**   | Có — tóm tắt từng cụm bằng LLM        | Không                                         |
| **Global Search (Map-Reduce)** | Có — mạnh cho câu hỏi tổng thể   | Không có sẵn                                  |
| **Cơ chế tìm kiếm**       | Local / Global / DRIFT search         | `$graphLookup` traversal + Vector search      |
| **Storage**               | Files (Parquet, JSON...)              | MongoDB documents                             |
| **Tích hợp**              | GraphRAG library (Python)             | LangChain (`MongoDBGraphStore`)               |
| **Chi phí indexing**      | Cao (do tóm tắt cộng đồng)            | Thấp hơn (chỉ extract entity + relations)     |
| **Vector + Graph trong 1 DB** | Phải dùng nhiều store             | **Có — unified platform**                     |
| **Phù hợp cho**           | Câu hỏi tổng thể, phân tích narrative | QA dựa trên quan hệ, multi-hop reasoning      |

### Sự khác biệt then chốt

> **Microsoft GraphRAG = Graph + Community Hierarchy + LLM Summaries**
> **MongoDB GraphRAG = Graph + $graphLookup traversal**

- Microsoft GraphRAG **phức tạp hơn**, có nhiều tầng tóm tắt → trả lời được câu hỏi kiểu "toàn bộ tài liệu này nói về gì?"
- MongoDB GraphRAG **đơn giản hơn**, tập trung vào duyệt quan hệ → trả lời câu hỏi kiểu "X và Y liên quan thế nào?" cực kỳ tốt

## 5. Ưu điểm chính của MongoDB GraphRAG

### a) Unified Platform (1 DB cho tất cả)

- Documents + Vector + Graph **trong cùng một database**
- Không cần đồng bộ giữa nhiều store khác nhau (Pinecone + Neo4j + MongoDB...)
- Giảm độ phức tạp vận hành, tránh "data synchronization challenges"

### b) Cập nhật động dễ dàng

MongoDB excels at updates of nested data structures → khi thêm document mới, có thể tự động:

- Tìm entity đã tồn tại → update
- Hoặc tạo entity mới
- Tích lũy attributes và relationships qua thời gian

### c) Hybrid Search dễ dàng

Có thể kết hợp trong 1 pipeline:

- `$vectorSearch` — tìm theo độ giống ngữ nghĩa
- `$graphLookup` — duyệt theo quan hệ
- `$search` — full-text search

## 6. Ví dụ thực tế

### Case study 2025: Legal Contracts GraphRAG (Radixia)

- **Stack**: AWS Lambda + API Gateway + S3 + MongoDB Atlas + GPT-4.1 Mini
- **Flow**: Upload PDF → extract text → GPT-4.1 phân tích entity + relationship → lưu vào MongoDB → query bằng GraphRAG
- **Use case**: Trả lời câu hỏi kiểu "Điều khoản X trong hợp đồng A liên quan thế nào với điều khoản Y trong hợp đồng B?"

### Tutorial chính thức của MongoDB

- Dataset: Sherlock Holmes từ Wikipedia
- Chia chunks → load qua `MongoDBGraphStore.add_documents()`
- Query bằng `chat_response()`
- Visualize bằng networkx + pyvis

## 7. Prerequisites khi triển khai

- Atlas cluster phiên bản **6.0.11, 7.0.2 trở lên**
- IP address phải được thêm vào Atlas project's access list
- MongoDB Community/Enterprise cluster đã cài **Search và Vector Search**
- OpenAI API Key (hoặc LLM provider khác) có credits

## 8. Tradeoffs (Điểm yếu cần biết)

- **Latency cao hơn**: Graph traversal sâu → response chậm
- **Scalability thách thức**: Khi knowledge base lớn, traversal có thể nặng
- **Không có community summaries**: Không mạnh cho câu hỏi "tổng thể"
- **Phức tạp cấu hình**: Phải chọn retrieval strategy phù hợp (keyword, entity-based, semantic on first node...)

## 9. Khi nào nên dùng MongoDB GraphRAG?

**Chọn MongoDB GraphRAG khi:**

- ✅ Bạn đã dùng MongoDB cho dữ liệu chính → tránh thêm DB mới
- ✅ Cần kết hợp Vector + Graph + Document trong 1 hệ thống
- ✅ Câu hỏi chủ yếu là **relationship-based** ("X liên hệ với Y thế nào?")
- ✅ Dữ liệu thay đổi thường xuyên, cần update động

**Chọn Microsoft GraphRAG khi:**

- ✅ Cần phân tích **narrative** sâu (báo cáo, nghiên cứu dài)
- ✅ Câu hỏi mang tính tổng thể, tổng hợp
- ✅ Cần community detection tự động

## Tóm tắt 1 dòng

> **MongoDB GraphRAG** là phiên bản GraphRAG "đơn giản hóa" tập trung vào **entity + relationship traversal** qua `$graphLookup`, tận dụng việc MongoDB hỗ trợ cả document, vector và graph trong **một database duy nhất** — khác với Microsoft GraphRAG vốn phức tạp hơn với community detection và hierarchical summaries.

---

## Nguồn tham khảo

- [GraphRAG with MongoDB Atlas: Integrating Knowledge Graphs with LLMs - MongoDB Blog](https://www.mongodb.com/company/blog/graphrag-mongodb-atlas-integrating-knowledge-graphs-with-llms)
- [GraphRAG with MongoDB and LangChain - Official Docs](https://www.mongodb.com/docs/atlas/ai-integrations/langchain/graph-rag/)
- [Now GA: GraphRAG with MongoDB Atlas and LangChain](https://www.mongodb.com/products/updates/now-ga-graphrag-with-mongodb-atlas-and-langchain/)
- [Knowledge Graph RAG Using MongoDB - Medium](https://medium.com/mongodb/knowledge-graph-rag-using-mongodb-1346e953064c)
- [Building a Knowledge Base and Visualization Graphs for RAG With MongoDB](https://www.mongodb.com/developer/products/atlas/mongodb-knowledge-base-rag-visualization/)
- [Building a GraphRAG for Legal Contracts with MongoDB Atlas - Radixia](https://blog.radixia.ai/building-a-graphrag-for-legal-contracts-with-mongodb-atlas/)
- [Navigating the Nuances of GraphRAG vs. RAG - Foojay](https://foojay.io/today/navigating-the-nuances-of-graphrag-vs-rag/)
- [MongoDB Vector Search Overview](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/)
