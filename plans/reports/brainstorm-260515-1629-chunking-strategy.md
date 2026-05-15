# Brainstorm: Chunking Strategy cho GraphRAG

**Date:** 2026-05-15 16:29 (Asia/Bangkok)
**Branch:** main
**Status:** Brainstorm xong, chưa implement. Chờ user xác nhận để tạo plan.

---

## Bối cảnh

Fixed-size chunking hiện tại (`RecursiveCharacterTextSplitter`, 1000-1500 chars, overlap 150-200) gây 2 lỗi đối nghịch:

| Vấn đề                           | Hậu quả                                                                                                     |
|----------------------------------|-------------------------------------------------------------------------------------------------------------|
| Section dài cross nhiều chunks   | LLM extract entity mất cross-reference; relationship rời rạc; entity duplicate vì context khác giữa 2 chunk |
| Section ngắn nằm trong chunk lớn | Chunk nhiễu nhiều topic → LLM gộp entities sai; vector embedding "trộn" → retrieval kém precision           |

`document_context.py` đã *biết* offset của sections (regex + LLM detect) nhưng KHÔNG dùng để cắt chunk — chỉ inject section title vào prefix. Đó là lãng phí lớn nhất hiện tại.

---

## User constraints (đã confirm)

- Mục tiêu: cải thiện **CẢ** entity extraction (build) **VÀ** retrieval (query)
- Chi phí LLM: không phải vấn đề
- Schema: cho phép thay đổi (thêm field/collection)

---

## 6 Options đã đánh giá

### A. Section-as-Chunk (Structural-First)
Mỗi `Section` → 1 chunk. Section dài (> max_chars) split tiếp theo paragraph `\n\n`, inject section title + parent vào prefix.

- ✅ Đơn giản, tận dụng infrastructure sẵn có
- ✅ Loại bỏ "section ngắn bị nhiễu"
- ⚠️ Section quá dài (50+ trang) vẫn split → vấn đề cũ còn nhẹ
- ⚠️ Doc không có header → degrade về fixed-size

### B. Semantic Chunking (Embedding-driven boundary)
Cắt theo sentence, embed, detect boundary tại điểm "drop similarity" (LangChain `SemanticChunker`).

- ✅ Agnostic format, work cả khi không có header
- ❌ Không giải quyết "section dài cross chunks"
- ⚠️ Sai boundary với list/table/code/dialogue

### C. Parent-Child / Hierarchical (Parent-Document Pattern)
- **Parent** = section (5-8k chars) → LLM extract entities với full context
- **Child** = 300-600 chars semantic chunks → vector search precision
- Query: hit child → expand lên parent

- ✅ **Giải quyết cả 2 vấn đề**
- ✅ Standard pattern (LangChain `ParentDocumentRetriever`)
- ⚠️ Storage gấp 2; retrieval logic phức tạp hơn

### D. LLM-Driven Chunking (Smart Cutter)
LLM đọc section, trả về `[{start, end, summary}]`.

- ✅ Chất lượng cao nhất
- ✅ Bonus: summary mỗi chunk
- ⚠️ Đắt: +1 LLM call/section
- ⚠️ LLM output variance, cần validate offset

### E. Late Chunking (Jina AI / BGE-M3)
Embed full section bằng long-context model, mean-pool token embeddings theo chunk boundary.

- ✅ Retrieval cross-reference rất mạnh
- ❌ **Không giúp LLM extract entity** (LLM vẫn nhận text chunk gốc)
- ⚠️ OpenAI embedding API không expose token embeddings → cần Jina v3

### F. Propositional / RAPTOR (Dense X)
LLM rewrite document thành propositions độc lập + summary tree.

- ✅ Chunk độc lập tuyệt đối về context
- ❌ RẤT đắt (10× build cost)
- ❌ Mất nuance, distort số liệu/format gốc

---

## Bảng so sánh

| Option                 | Build cost | Solve section dài | Solve section ngắn nhiễu | Improve retrieval | Complexity  |
|------------------------|------------|-------------------|--------------------------|-------------------|-------------|
| **A** Section-as-Chunk | ≈ same     | ⚠️ Partial         | ✅ Yes                    | ⚠️ Mild            | Low         |
| **B** Semantic         | +5-10%     | ❌ No              | ⚠️ Partial                | ⚠️ Mild            | Medium      |
| **C** Parent-Child     | +20-30%    | ✅ Yes             | ✅ Yes                    | ✅ Yes             | Medium-High |
| **D** LLM-driven       | +100-200%  | ✅ Yes             | ✅ Yes                    | ✅ Yes             | High        |
| **E** Late chunking    | +30% embed | ❌ No (extract)    | ⚠️ Partial                | ✅ Very high       | High        |
| **F** Propositional    | +500-1000% | ✅ Yes             | ✅ Yes                    | ✅ Very high       | Very High   |

---

## 🎯 Recommendation: Hybrid A + C + small D

```
BUILD PIPELINE
1. analyze_document() → DocumentContext (đã có)
2. Cho mỗi section:
   ├─ Nếu len ≤ MAX_PARENT (5000): parent = section
   └─ Nếu len > MAX_PARENT:
       → paragraph split với section context inject
       → fallback LLM split (D) nếu paragraph vẫn quá dài
3. PARENT chunks → LLM extract entities (full context)
4. CHILD chunks (300-600 chars semantic split) → embed cho vector search
   Lưu parent_id reference

QUERY PIPELINE
1. Vector search trên CHILD chunks
2. Expand mỗi hit lên PARENT (dedupe by parent_id)
3. Feed parent text + entity graph cho LLM trả lời
```

**Lý do hybrid thắng từng option:**
- A solo: section dài vẫn break → fix bằng paragraph split + section prefix (`to_chunk_prefix` đã có)
- C solo: cần chiến lược chọn parent → A làm strategy cho parent
- D nhẹ: chỉ gọi LLM-split khi section thực sự dài (rare), cost manageable

**Schema thay đổi tối thiểu (mới):**
```
chunks collection:
  - _id, text, embedding
  - parent_id (null nếu là parent)
  - section_title, document_id, offset
  - level: "parent" | "child"
  - summary (optional, có khi dùng D)
```

---

## File trong scope (codebase hiện tại)

- `src/pdf_loader.py` — core chunking logic (sẽ rewrite phần `load_pdf_chunks_with_context`)
- `src/document_context.py` — đã có `Section` + offset, **REUSE**
- `src/graph_builder.py` — entity extraction nhận chunks (parent)
- `src/entity_embedder.py` — embedding logic (chuyển sang embed child chunks)
- `src/query_engine.py` — retrieval logic (thêm child→parent expand)
- MongoDB schema — thêm `chunks` collection hoặc field `parent_id`

---

## Unresolved questions

1. Paragraph split (simpler, free) hay LLM split (chất lượng, +cost) cho section quá dài? Đề xuất default paragraph, fallback LLM khi paragraph cho ra chunk > threshold.
2. MAX_PARENT size lý tưởng? Đề xuất 5000-6000 chars (≈1500 tokens).
3. Child chunk có overlap không? Đề xuất no (parent expand đã đủ context).
4. Doc không có section detect được — fallback? Đề xuất B (semantic chunking) làm parent fallback, hoặc synthetic sections 5000 chars.
5. Migration: rebuild graph cũ hay chỉ apply cho doc mới? (cần quyết trước khi implement)
6. Child chunks có cần entity-link không, hay chỉ là vector search aid?

---

## Next steps (cho session sau)

1. **Quyết** các unresolved questions ở trên (đặc biệt #1, #2, #5)
2. Chạy `/ck:plan` với report này làm input → tạo phase breakdown
3. Plan kỳ vọng:
   - Phase 01: refactor `pdf_loader.py` — section-aware parent chunking
   - Phase 02: paragraph/LLM split cho section dài
   - Phase 03: child chunk generation + schema update
   - Phase 04: rewrite `entity_embedder` embed child chunks
   - Phase 05: query expand child→parent trong `query_engine`
   - Phase 06: test end-to-end + benchmark vs baseline

---

## Quick reference khi quay lại

- **Đã chọn approach:** Hybrid A + C + D nhẹ
- **Chưa decide:** 6 unresolved questions ở trên
- **Action item tiếp theo:** trả lời 6 câu hỏi → `/ck:plan` để break thành phases
