# Component: PDF Loading & Chunking

> File: `src/pdf_loader.py` (120 lines)
> Vai trò: Đọc PDF, cắt thành chunks vừa với context window LLM, đính kèm
> document context để extraction chất lượng cao.

## 🎯 Mục đích

Chuyển PDF (binary) thành **list các `Document` (text chunks)** sẵn sàng đưa
vào LLM extraction. Quan trọng nhất: **không để chunks "vô hồn"** —
mỗi chunk phải mang đầy đủ context (subject, section) để LLM hiểu nó là
mảnh thuộc tài liệu gì.

## 📥 Đầu vào

```python
pdf_path: Path              # đường dẫn tới file PDF (PyPDFLoader yêu cầu path)
cfg: Config                 # để gọi LLM analyze document
chunk_size: int = 1500      # chars/chunk
chunk_overlap: int = 200    # chars chồng lấp giữa chunks liên tiếp
```

## 📤 Đầu ra

```python
Tuple[List[Document], DocumentContext]
```

- `List[Document]`: chunks đã được prepend context prefix, metadata phong phú
- `DocumentContext`: metadata cấp tài liệu (subjects, sections, type)

## 🔌 2 hàm public

### `load_pdf_chunks(pdf_path, chunk_size, chunk_overlap) -> List[Document]`

Hàm thô — chỉ load + chunk, **không inject context**. Dùng cho debug hoặc
khi không cần context (legacy).

### `load_pdf_chunks_with_context(pdf_path, cfg, chunk_size, chunk_overlap)`

Hàm chính (recommended). Pipeline:
1. Gọi `load_pdf_chunks` để có chunks thô
2. Build `full_text` bằng cách join chunks
3. Gọi `document_context.analyze_document()` → `DocumentContext`
4. Map mỗi chunk → section (qua offset trong full_text)
5. Prepend context prefix vào `page_content` của mỗi chunk
6. Trả về `(chunks, context)`

## 🧬 Cơ chế

### Bước 1: Load PDF

```python
loader = PyPDFLoader(str(pdf_path))
pages = loader.load()
```

`PyPDFLoader` trả về **1 Document/page**, mỗi `Document.page_content` chứa
text raw của page đó, `metadata` chứa `source` (path), `page` (số trang).

**Yêu cầu**: PDF không bị mã hoá AES, hoặc có `cryptography>=3.1` để decrypt.

### Bước 2: Chunk text

```python
splitter = RecursiveCharacterTextSplitter(
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    separators=["\n\n", "\n", ". ", " ", ""],
)
chunks = splitter.split_documents(pages)
```

`RecursiveCharacterTextSplitter` cố gắng cắt theo separator phân cấp:
1. Đầu tiên thử cắt theo `\n\n` (đoạn văn)
2. Nếu vẫn quá dài → cắt theo `\n` (dòng)
3. Sau đó `. ` (câu)
4. Cuối cùng theo `" "` (từ) hoặc `""` (ký tự)

→ Đảm bảo chunks không cắt giữa câu/từ khi có thể.

### Bước 3: Compute chunk offset trong full_text

```python
full_text = "\n\n".join(c.page_content for c in chunks)

cursor = 0
for c in chunks:
    snippet = c.page_content[:200]
    offset = full_text.find(snippet, cursor) if snippet else cursor
    cursor = offset + len(snippet)
```

**Lý do cần offset**: Để map chunk vào section. `DocumentContext.sections`
chứa `start_offset` / `end_offset` của mỗi section trong full_text. Cần
offset của chunk để tìm section chứa nó.

**Heuristic snippet**: dùng 200 chars đầu của chunk làm key search. Tránh
substring đơn giản vì có thể trùng nhiều chỗ.

### Bước 4: Inject context prefix

```python
section = context.section_at(offset)        # find section containing this chunk
prefix = context.to_chunk_prefix(section)   # build text prefix
c.page_content = prefix + c.page_content
```

Sau bước này, `page_content` của mỗi chunk trông như:

```
=== DOCUMENT CONTEXT (do not extract as entities) ===
Document type: CV / Resume.
Subjects (the primary entity): "Pham Tuyen".
Summary: Resume of Frontend Engineer...
Top-level sections: Professional Summary, Technical Skills, Professional Experience.

=== LOCAL SECTION CONTEXT ===
Current section: "Frontend Developer (Freelance)" (parent: "Professional Experience"), level 2.
Entities in this chunk belong to or relate to this section's topic.

EXTRACTION RULES:
1. EVERY entity MUST have at least 1 attribute...
2. ALWAYS create a relationship from the subject ("Pham Tuyen")...
3. RESPECT hierarchy: when this chunk is under a section header...
4. USE canonical names consistently...

=== CHUNK CONTENT ===
[original chunk text...]
```

Khi LLM extract entities từ chunk này, nó:
- Biết tài liệu là **CV** của **Pham Tuyen**
- Biết đoạn này thuộc section "Frontend Developer (Freelance)"
- Bị **ép** fill attributes, tạo edges về subject

### Bước 5: Metadata phong phú

Mỗi chunk có `metadata` được làm giàu:

```python
c.metadata["document_subjects"] = context.subjects      # ["Pham Tuyen"]
c.metadata["document_type"] = context.doc_type          # "CV / Resume"
c.metadata["section"] = section.title if section else None  # "Frontend Developer..."
c.metadata["chunk_id"] = idx                            # 0, 1, 2, ...
```

→ Sau này nếu cần debug hoặc trace nguồn entity, có thể đi từ chunk_id ngược về vị trí trong PDF.

## 🔁 Default chunk parameters

```python
DEFAULT_CHUNK_SIZE = 1500
DEFAULT_CHUNK_OVERLAP = 200
```

**Tại sao 1500?**
- Đủ cho ~1 đoạn văn dài hoặc 1 section nhỏ
- Không vượt context window LLM (gpt-5 = 200K, gpt-4o-mini = 128K)
- Cân bằng giữa số chunks (= số LLM calls) và density entities/chunk

**Tại sao overlap 200?**
- Một entity ở cuối chunk N có thể được nhắc lại đầu chunk N+1
- Overlap giúp LLM khi extract chunk N+1 vẫn thấy context của chunk N
- 200/1500 ≈ 13% — đủ giữ ngữ cảnh xuyên chunk mà không lãng phí

## 🔗 Tương tác với component khác

| Component | Hướng | Tương tác |
|-----------|-------|-----------|
| `config.py` | nhận | `Config` để gọi LLM trong `analyze_document` |
| `document_context.py` | gọi | `analyze_document()`, `Section.section_at()`, `DocumentContext.to_chunk_prefix()` |
| `graph_builder.py` | gửi cho | chunks (with prefix) → `add_documents()` |
| `ui/tab_build.py` | gọi | qua spinner |
| `scripts/build-graph.py` | gọi | CLI entry point |

## 🐛 Edge cases & failure modes

| Edge case | Handling |
|-----------|----------|
| PDF không tồn tại | `FileNotFoundError` ngay đầu hàm |
| PDF có AES encryption | Cần `cryptography>=3.1` (đã trong requirements.txt) |
| PDF rỗng / 0 pages | Trả về `[], DocumentContext("(empty)", ...)` |
| Chunk snippet không tìm thấy trong full_text | Fallback offset = cursor (rare) |
| LLM analyze_document fail | `DocumentContext("(unknown)", ...)` — fallback an toàn |

## 🧪 Cách test thủ công

```python
from pathlib import Path
from src.config import load_config
from src.pdf_loader import load_pdf_chunks_with_context

cfg = load_config()
chunks, ctx = load_pdf_chunks_with_context(Path("doc.pdf"), cfg)

print(f"Subjects: {ctx.subjects}")
print(f"Sections: {len(ctx.sections)}")
print(f"Chunks: {len(chunks)}")

# Xem prefix của chunk đầu
print(chunks[0].page_content[:1000])

# Xem section assignment
for i, c in enumerate(chunks):
    print(f"Chunk {i}: section={c.metadata.get('section')}")
```

## 📈 Performance characteristics

| Doc size | Chunks | analyze time | total load time |
|----------|--------|--------------|-----------------|
| 4 pages (CV) | 5 | ~5s (1 LLM call) | ~7s |
| 30 pages (audit) | ~80 | ~10s | ~15s |
| 100 pages | ~280 | ~15s | ~25s |

→ Bottleneck là `analyze_document` (1 LLM call). Phần còn lại là I/O + CPU thuần.

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| Smaller chunk_size cho doc nhiều ngắt section ngắn | Low | Medium — bắt edge tốt hơn |
| Section-based splitter (cắt theo header thay vì char count) | Medium | High — mỗi chunk = 1 section logic |
| Streaming chunk processing (yield instead of build list) | Medium | Low — chỉ cần khi doc > 10K chunks |
| Parallel LLM analyze (multi-region simultaneously) | Low | Low — 1 call hiện tại đã đủ |
| Layout-aware parsing với `unstructured.io` thay PyPDF | High | High cho doc phức tạp (tables, columns) |

## 📚 References

- `src/pdf_loader.py:14-23` — imports
- `src/pdf_loader.py:34-67` — `load_pdf_chunks` (basic)
- `src/pdf_loader.py:70-120` — `load_pdf_chunks_with_context` (main)
- LangChain docs: [PyPDFLoader](https://python.langchain.com/docs/integrations/document_loaders/pypdfloader)
- LangChain docs: [RecursiveCharacterTextSplitter](https://python.langchain.com/api_reference/text_splitters/character/langchain_text_splitters.character.RecursiveCharacterTextSplitter.html)

## 🔗 Linked components

- [Document Context Detection](component-document-context.md) — provides `DocumentContext`
- [Entity Extraction (Build Graph)](component-entity-extraction.md) — consumes chunks
- [Pipeline Overview](pipeline-overview.md) — bigger picture
