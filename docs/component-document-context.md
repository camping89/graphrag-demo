# Component: Document Context Detection

> File: `src/document_context.py` (343 lines)
> Vai trò: Phân tích cấu trúc tài liệu trước khi extract entities — phát hiện
> subject(s), doc_type, description, sections với offset & hierarchy.

## 🎯 Mục đích

Giải bài toán **"entities vô hồn"** mà GraphRAG truyền thống gặp phải:
LLM extract chunk-by-chunk độc lập, mất ngữ cảnh toàn tài liệu → 68%+
entities có `attributes` & `edges` rỗng.

Module này thực hiện **1 LLM call duy nhất** trước build, phân tích:

1. **Subjects** (1-5 chủ thể chính) → ép LLM link entities về subject khi extract
2. **Doc type** (CV, Audit, Contract, ...) → LLM suy ra attributes phù hợp
3. **Description** (2 câu) → grounding cho LLM
4. **Sections** với offset & hierarchy → mỗi chunk biết section nó thuộc

## 🧬 Tier 1+2+3 Architecture

### Tier 1: Stratified sampling
Doc 100+ trang → 3000 chars đầu không bao quát structure. Sampling 3 vùng:

```python
SAMPLE_PER_REGION = 2500   # chars per region

def _stratified_sample(full_text: str) -> str:
    n = len(full_text)
    if n <= 3 * 2500:
        return full_text
    middle = n // 2 - 1250
    return (
        "=== BEGINNING ===\n" + full_text[:2500] +
        "\n=== MIDDLE ===\n"  + full_text[middle:middle+2500] +
        "\n=== END ===\n"     + full_text[-2500:]
    )
```

→ LLM thấy cả intro + nội dung giữa + conclusion. Hiểu structure xuyên suốt.

### Tier 2: Per-section context với offset

```python
@dataclass
class Section:
    title: str
    start_offset: int       # offset trong full_text
    end_offset: int         # = start_offset của section kế (hoặc len(full_text))
    level: int = 1          # 1 = top-level, 2 = sub-section
    parent_title: Optional[str] = None
```

**Hybrid detection** — kết hợp 2 source:
1. **Regex** (rẻ, không LLM): bắt `# Title`, `1.1. Title`, ALL CAPS HEADER
2. **LLM**: bắt Title Case header (vd "Professional Summary") mà regex bỏ sót

Khi merge, ưu tiên regex cho exactness, bổ sung LLM cho Title Case.

### Tier 3: Multi-subject

```python
subjects: List[str]   # 1-5 chủ thể
```

| Loại doc | Subjects |
|----------|----------|
| CV | `["Pham Tuyen"]` |
| Audit report | `["OpenAI Inc.", "Schellman & Co"]` |
| Contract | `["Party A", "Party B", "Party C"]` |
| Whitepaper | `["GraphRAG", "Microsoft Research"]` |

→ Tránh ép entity về 1 subject sai khi doc đa chủ thể.

## 🔑 Classes & Functions

### `Section` dataclass
```python
@dataclass
class Section:
    title: str
    start_offset: int
    end_offset: int
    level: int = 1
    parent_title: Optional[str] = None
```

### `DocumentContext` dataclass

```python
@dataclass
class DocumentContext:
    subjects: List[str] = field(default_factory=list)
    doc_type: str = "(unknown)"
    description: str = ""
    sections: List[Section] = field(default_factory=list)
```

**Methods:**

- `subject` (property): backward compat, trả về `subjects[0]`
- `section_at(offset)`: tìm `Section` chứa offset cho trước
- `_resolve_parents()`: tính `parent_title` cho mỗi section dựa trên level + thứ tự (stack-based)
- `to_chunk_prefix(section)`: build text prefix để prepend vào chunk

### `to_chunk_prefix(section)` — output

Đây là **prompt engineering** trọng yếu. Output:

```
=== DOCUMENT CONTEXT (do not extract as entities) ===
Document type: CV / Resume.
Subjects (the primary entity): "Pham Tuyen".
Summary: Resume of Frontend Engineer outlining work experience...
Top-level sections in document: Professional Summary, Technical Skills, ...

=== LOCAL SECTION CONTEXT ===
Current section: "Frontend Developer (Freelance)" (parent: "Professional Experience"), level 2.
Entities in this chunk belong to or relate to this section's topic.
Add section-derived attributes (date ranges, subject of section, scope)
to extracted entities when relevant.

EXTRACTION RULES (apply strictly):
1. EVERY entity MUST have at least 1 attribute. Empty attributes are
forbidden — derive sensible defaults from this chunk + section context...
2. CONNECT entities to subjects: for each entity mentioned here,
evaluate whether it relates to any of "Pham Tuyen" and emit a
relationship (works_at, skilled_in, recipient_of, ...).
3. RESPECT hierarchy: when this chunk is under a section header...
4. USE canonical names consistently across chunks...

=== CHUNK CONTENT ===
```

**Tâm điểm**: 4 EXTRACTION RULES ép LLM:
- Rule 1: Không để attributes rỗng
- Rule 2: Phải link về subject
- Rule 3: Dùng section context
- Rule 4: Naming consistency (tránh "Tuyen" vs "Pham Tuyen" duplicate)

## 🔍 Regex section detection

```python
_HEADER_PATTERNS = [
    # Markdown: # Title, ## Sub
    (re.compile(r"^(#{1,6})\s+([^\n]+?)$", re.MULTILINE), markdown_extract, "markdown"),

    # Numbered: 1.2.3 Title
    (re.compile(r"^(\d+(?:\.\d+){0,4})\.?\s+([A-Z][^\n]{2,80})$", re.MULTILINE),
     numbered_extract, "numbered"),

    # ALL CAPS: "EXECUTIVE SUMMARY"
    (re.compile(r"(?m)^([A-Z][A-Z0-9 \-&,/]{4,80})$"),
     allcaps_extract, "allcaps"),
]
```

Mỗi pattern có:
1. **Regex** với `re.MULTILINE` để match đầu/cuối dòng
2. **Extract function** tách title + level
3. **Kind label** để debug

Sau khi match, dedupe theo offset (cùng 1 vị trí không match 2 pattern).

## 🤖 LLM analysis

```python
prompt = """Analyze a document via stratified samples...

Identify:
1. subjects: 1-5 PRIMARY entities. Use canonical names.
2. doc_type: short label
3. description: 2-sentence summary
4. sections: list of section headers with level (1=top, 2=sub)

Output STRICT JSON: {"subjects": [...], "doc_type": "...", ...}
"""

response = llm.invoke(prompt)
data = json.loads(_strip_code_fence(response.content))
```

**`_strip_code_fence`**: extract JSON object từ output, robust với markdown
wrapping `` ```json ... ``` ``.

**Failure mode**: nếu LLM trả về malformed JSON → fallback `DocumentContext("(unknown)", ...)`. Build vẫn tiếp tục.

## 🗺️ Locate LLM section titles trong full_text

LLM cho biết **tên** sections nhưng không biết offset. Module tìm offset
bằng case-insensitive substring search:

```python
def _locate_titles_in_text(full_text, titles):
    found = []
    cursor = 0
    for title, level in titles:
        offset = full_text.lower().find(title.lower(), cursor)
        if offset < 0:
            offset = full_text.lower().find(title.lower(), 0)  # search từ đầu
        if offset >= 0:
            found.append((offset, title, level))
            cursor = offset + len(title)

    found.sort(key=lambda x: x[0])
    return [Section(...) for (offset, title, level) in found]
```

**Thứ tự**:
1. Search forward từ cursor → giữ thứ tự document
2. Nếu không tìm thấy forward, search từ đầu (LLM có thể đảo thứ tự)
3. Sort lại theo offset thực tế

## ⚙️ Constants

```python
SAMPLE_PER_REGION = 2500           # chars per region (3 regions tổng = 7500)
MAX_SUBJECTS = 5                   # cap số subjects
MAX_SECTIONS_IN_OUTLINE = 12       # cap số section đưa vào prefix
```

## 🔁 Workflow trong `analyze_document`

```python
def analyze_document(cfg, full_text):
    # 1. Regex detect — fast, no LLM
    regex_sections = detect_sections_regex(full_text)

    # 2. Stratified sample
    sample = _stratified_sample(full_text)

    # 3. LLM call — analyze sample + return subjects + doc_type + sections
    llm = ChatOpenAI(model=cfg.chat_model, api_key=cfg.openai_api_key)
    response = llm.invoke(prompt_with_sample)
    data = json.loads(_strip_code_fence(response.content))

    # 4. Locate LLM titles trong full_text → Section objects
    llm_sections = _locate_titles_in_text(full_text, llm_titles)

    # 5. Merge regex + LLM, dedupe theo offset gần nhau
    merged = list(regex_sections)
    for ls in llm_sections:
        if any(abs(rs.start_offset - ls.start_offset) < 20 for rs in regex_sections):
            continue
        merged.append(ls)
    merged.sort(key=lambda s: s.start_offset)

    # 6. Refresh end_offset (start của section kế)
    for i, s in enumerate(merged):
        s.end_offset = merged[i+1].start_offset if i+1 < len(merged) else len(full_text)

    # 7. Build DocumentContext + resolve parent hierarchy
    ctx = DocumentContext(subjects=..., doc_type=..., description=..., sections=merged)
    ctx._resolve_parents()
    return ctx
```

## 🌳 Parent resolution (hierarchy)

Stack-based: với mỗi section, pop stack đến khi level cha < level con.

```python
def _resolve_parents(self):
    stack = []
    for s in self.sections:
        while stack and stack[-1].level >= s.level:
            stack.pop()
        s.parent_title = stack[-1].title if stack else None
        stack.append(s)
```

Ví dụ:
```
L1: Professional Experience       (stack: [PE], parent=None)
L2: Frontend Engineer             (stack: [PE, FE], parent="Professional Experience")
L2: HeristepAI GPS                (pop FE, push HeristepAI; parent="Professional Experience")
                                  Wait — same level pops, but the order is:
                                  stack=[PE, FE], current L2 ≥ FE L2 → pop FE → stack=[PE]
                                  parent = "Professional Experience"
L2: Frontend Developer (Veek)     (pop Heristep → parent="Professional Experience")
```

## 🔗 Tương tác với component khác

| Component | Hướng | Tương tác |
|-----------|-------|-----------|
| `config.py` | nhận | `Config.openai_api_key`, `Config.chat_model` |
| `pdf_loader.py` | được gọi | `load_pdf_chunks_with_context` |
| `langchain_openai` | gọi | `ChatOpenAI.invoke()` |

## 🧪 Test trên CV thật

```python
from src.config import load_config
from src.document_context import analyze_document
from pathlib import Path

cfg = load_config()
# Giả sử bạn có full text từ PDF
ctx = analyze_document(cfg, full_text)

# Output:
# subjects: ['Pham Tuyen']
# doc_type: CV / Resume
# description: Resume of Frontend Engineer Pham Tuyen outlining...
# sections: 15 total
#   [L1] 'Professional Summary' @ 119
#   [L1] 'Technical Skills' @ 641
#   [L1] 'Professional Experience' @ 985
#   [L2] 'Frontend Engineer' @ 1009
#   ...
```

## ⚠️ Edge cases

| Edge case | Handling |
|-----------|----------|
| Doc rỗng hoặc < 3*2500 chars | Không sample, dùng full_text trực tiếp |
| LLM return malformed JSON | Fallback `DocumentContext("(unknown)", ...)` với regex sections only |
| LLM gives title không có trong full_text | Skip title đó trong `_locate_titles_in_text` |
| 2 sections cùng title | Mỗi cái dùng offset riêng (forward search) |
| Section title quá dài (> 120 chars) | Skip — không hợp lệ |

## 📊 Performance

| Doc size | Sample size | LLM call time | Total |
|----------|-------------|---------------|-------|
| 4 pages CV | full_text (~3 KB) | ~5-8s | ~8s |
| 30 pages audit | 7.5 KB stratified | ~10-15s | ~16s |
| 100 pages | 7.5 KB stratified | ~10-15s | ~16s |
| 500 pages | 7.5 KB stratified | ~10-15s | ~16s |

→ **O(1) LLM calls cho mọi kích thước doc** (1 call duy nhất). Đây là điểm
mạnh của approach — chi phí cố định, không scale với doc size.

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| LLM-based section detection per-page (cho doc rất lớn, structure phức tạp) | High | Medium |
| Cache `DocumentContext` cho cùng PDF (skip re-analyze) | Low | Low |
| Validate section detection độ chính xác | Medium | Medium |
| Map subject→section (subject nào liên quan section nào) | Medium | High cho audit/contract |

## 📚 References

- `src/document_context.py:21-25` — constants
- `src/document_context.py:28-37` — `Section` dataclass
- `src/document_context.py:40-89` — `DocumentContext`
- `src/document_context.py:92-115` — `_HEADER_PATTERNS` regex
- `src/document_context.py:118-148` — `detect_sections_regex`
- `src/document_context.py:151-185` — `_locate_titles_in_text`
- `src/document_context.py:200-340` — `analyze_document`

## 🔗 Linked components

- [PDF Loading & Chunking](component-pdf-loading.md) — consumer
- [Entity Extraction](component-entity-extraction.md) — chunks với context prefix làm input
- [Pipeline Overview](pipeline-overview.md)
