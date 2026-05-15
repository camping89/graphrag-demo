"""Phân tích tài liệu để inject context vào chunks trước khi extract entities.

Hỗ trợ tài liệu lớn (100+ trang) với 3 kỹ thuật:

  Tier 1 — Stratified sampling: lấy mẫu beginning + middle + end để
           LLM detect structure xuyên suốt tài liệu (không chỉ intro).
  Tier 2 — Per-section context: regex detect section headers với offset,
           gán mỗi chunk vào section tương ứng, prefix mang context LOCAL
           (section title + parent) thay vì chỉ global.
  Tier 3 — Multi-subject: tài liệu lớn (audit, hợp đồng, sách) có nhiều
           chủ thể ngang hàng. DocumentContext giữ list subjects (1-5).

Quy trình:
  1. Gọi `analyze_document(full_text)` 1 lần — LLM phân tích sample
  2. `detect_sections_regex(full_text)` quét header theo regex
  3. Mỗi chunk: gọi `context.section_at(offset)` → ra Section
  4. `context.to_chunk_prefix(section)` → prefix có cả global + local
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from langchain_openai import ChatOpenAI

from .config import Config


# Chars per region khi stratified sampling (beginning/middle/end)
SAMPLE_PER_REGION = 2500
# Max subjects detect — đủ cho contracts/audit (thường 3-5 bên)
MAX_SUBJECTS = 5
# Cap số section đưa vào prefix outline để không phình prompt
MAX_SECTIONS_IN_OUTLINE = 12


@dataclass
class Section:
    """Một section trong tài liệu, có offset để map ngược về chunk."""

    title: str
    start_offset: int  # offset trong full_text
    end_offset: int    # offset của section kế tiếp (hoặc len(full_text))
    level: int = 1     # 1 = top-level header, 2 = subsection, ...
    parent_title: Optional[str] = None


@dataclass
class DocumentContext:
    """Metadata cấp tài liệu — global + sections list để lookup local."""

    subjects: List[str] = field(default_factory=list)  # ["Pham Tuyen"] hoặc ["OpenAI", "Schellman"]
    doc_type: str = "(unknown)"
    description: str = ""
    sections: List[Section] = field(default_factory=list)

    @property
    def subject(self) -> str:
        """Backward compat — trả về subject đầu tiên (hoặc placeholder)."""
        return self.subjects[0] if self.subjects else "(unknown)"

    def section_at(self, offset: int) -> Optional[Section]:
        """Tìm section chứa offset cho trước. None nếu không match."""
        for s in self.sections:
            if s.start_offset <= offset < s.end_offset:
                return s
        return None

    def _resolve_parents(self) -> None:
        """Tính parent_title cho mỗi section dựa trên level + thứ tự."""
        stack: List[Section] = []
        for s in self.sections:
            while stack and stack[-1].level >= s.level:
                stack.pop()
            s.parent_title = stack[-1].title if stack else None
            stack.append(s)

    def to_chunk_prefix(self, section: Optional[Section] = None) -> str:
        """Build prefix mang global + local context cho 1 chunk.

        Args:
            section: section chứa chunk này. None nếu không xác định được.
        """
        subjects_str = ", ".join(f'"{s}"' for s in self.subjects) or '"(unknown)"'
        plural = "the primary entities" if len(self.subjects) > 1 else "the primary entity"

        outline = ""
        if self.sections:
            top_titles = [s.title for s in self.sections if s.level == 1][:MAX_SECTIONS_IN_OUTLINE]
            if top_titles:
                outline = f"Top-level sections in document: {', '.join(top_titles)}.\n"

        section_block = ""
        if section:
            parent = f" (parent: \"{section.parent_title}\")" if section.parent_title else ""
            section_block = (
                f"\n=== LOCAL SECTION CONTEXT ===\n"
                f"Current section: \"{section.title}\"{parent}, level {section.level}.\n"
                f"Entities in this chunk belong to or relate to this section's topic. "
                f"Add section-derived attributes (date ranges, subject of section, scope) "
                f"to extracted entities when relevant.\n"
            )

        return (
            f"=== DOCUMENT CONTEXT (do not extract as entities) ===\n"
            f"Document type: {self.doc_type}.\n"
            f"Subjects ({plural}): {subjects_str}.\n"
            f"Summary: {self.description}\n"
            f"{outline}"
            f"{section_block}"
            f"\n"
            f"EXTRACTION RULES (apply strictly):\n"
            f"1. EVERY entity MUST have at least 1 attribute. Empty attributes are "
            f"forbidden — derive sensible defaults from this chunk + section context if "
            f"explicit info missing (e.g. for tech: category/language; orgs: industry/role; "
            f"dates: ISO format YYYY-MM).\n"
            f"2. CONNECT entities to subjects: for each entity mentioned here, evaluate "
            f"whether it relates to any of {subjects_str} and emit a relationship "
            f"(works_at, skilled_in, recipient_of, contributed_to, authored_by, "
            f"audited_by, partnered_with, located_in, etc.). Do NOT leave subjects "
            f"disconnected from entities they're related to.\n"
            f"3. RESPECT hierarchy: when this chunk is under a section header, attach "
            f"section-derived attributes (period, scope, focus) to entities within.\n"
            f"4. USE canonical names consistently across chunks (e.g. \"Veek Co., Ltd\" "
            f"not \"Veek\"; \"Pham Tuyen\" not \"Tuyen\").\n"
            f"5. EXTRACT EVERY DISTINCT IDENTIFIER as a separate entity. Any "
            f"alphanumeric code, numbered reference, version tag, section marker, "
            f"requirement number, item code, or similar structured label is its OWN "
            f"entity — even if many appear together. NEVER merge multiple identifiers "
            f"into a single generic parent entity. Each identifier becomes a node "
            f"whose description/text becomes attributes, with relationships to what "
            f"it references or modifies.\n"
            f"6. EXTRACT EVERY NAMED PROPER NOUN as a separate entity: persons, "
            f"organizations, products, places, systems, frameworks, regulations, "
            f"technologies, standards. Use the entity's exact name as _id. Do NOT "
            f"collapse multiple named items into a single generic category entity "
            f"(e.g. if 4 vendors are listed, output 4 vendor entities, not 1 "
            f"\"Vendors\" concept).\n"
            f"7. PREFER granularity over generalization. If a chunk mentions N "
            f"distinct identifiers/names, the chunk should produce N entities — not "
            f"a single summary entity. Generic category entities are allowed only "
            f"when the source text itself uses that category as a referenceable "
            f"concept (e.g. \"the security team\").\n"
            f"\n"
            f"=== CHUNK CONTENT ===\n"
        )


# ─── Regex section detection ─────────────────────────────────────────────
# Patterns phổ biến cho header trong PDF/text được trích xuất:
#
#   - Markdown đã convert: `# Title`, `## Sub`
#   - Numbered: `1. Title`, `1.1 Title`, `2.3.4 Sub`
#   - ALL CAPS HEADER (line riêng, < 80 chars)
#   - Title Case Header trên dòng riêng (heuristic mạnh hơn — chỉ active cho
#     line ngắn (< 60 chars) đứng giữa 2 blank line)

_HEADER_PATTERNS = [
    # (regex, group_for_title, level_func, kind_label)
    (
        re.compile(r"^(#{1,6})\s+([^\n]+?)$", re.MULTILINE),
        lambda m: (len(m.group(1)), m.group(2).strip()),
        "markdown",
    ),
    (
        re.compile(r"^(\d+(?:\.\d+){0,4})\.?\s+([A-Z][^\n]{2,80})$", re.MULTILINE),
        lambda m: (m.group(1).count(".") + 1, f"{m.group(1)} {m.group(2).strip()}"),
        "numbered",
    ),
    (
        # ALL CAPS line, 2-10 words, on its own line
        re.compile(r"(?m)^([A-Z][A-Z0-9 \-&,/]{4,80})$"),
        lambda m: (1, m.group(1).strip().title()),
        "allcaps",
    ),
]


def detect_sections_regex(full_text: str) -> List[Section]:
    """Phát hiện section boundaries bằng regex. Đủ dùng cho 80% docs.

    Output: list Section sắp xếp theo offset, không overlap.
    Section cuối kéo end_offset tới hết tài liệu.
    """
    matches: list[tuple[int, str, int]] = []
    seen_offsets: set[int] = set()

    for regex, extract, _kind in _HEADER_PATTERNS:
        for m in regex.finditer(full_text):
            if m.start() in seen_offsets:
                continue
            level, title = extract(m)
            if not title or len(title) > 120:
                continue
            matches.append((m.start(), title, level))
            seen_offsets.add(m.start())

    matches.sort(key=lambda x: x[0])

    # Build Section list với end_offset = start của section kế tiếp
    sections: list[Section] = []
    for i, (offset, title, level) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(full_text)
        sections.append(Section(title=title, start_offset=offset, end_offset=end, level=level))

    return sections


# ─── Stratified sampling + LLM analysis ──────────────────────────────────


def _stratified_sample(full_text: str) -> str:
    """Lấy mẫu 3 vùng: đầu / giữa / cuối — bao quát structure cho doc lớn."""
    n = len(full_text)
    s = SAMPLE_PER_REGION
    if n <= 3 * s:
        return full_text
    middle_start = n // 2 - s // 2
    return (
        "=== BEGINNING ===\n"
        + full_text[:s]
        + "\n\n=== MIDDLE ===\n"
        + full_text[middle_start : middle_start + s]
        + "\n\n=== END ===\n"
        + full_text[-s:]
    )


def _strip_code_fence(text: str) -> str:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group() if m else text


def _locate_titles_in_text(
    full_text: str, titles: list[tuple[str, int]]
) -> List[Section]:
    """Tìm offset của mỗi title trong full_text, build Section list.

    Args:
        titles: list of (title_text, level). Order theo thứ tự xuất hiện kỳ vọng.

    Trả về sections sắp xếp theo offset thực tế, không trùng.
    """
    found: list[tuple[int, str, int]] = []
    cursor = 0
    for title, level in titles:
        # Search case-insensitive bắt đầu từ cursor để giữ thứ tự
        lower_full = full_text.lower()
        lower_title = title.lower()
        offset = lower_full.find(lower_title, cursor)
        if offset < 0:
            # Thử search từ đầu (LLM có thể đảo thứ tự)
            offset = lower_full.find(lower_title, 0)
        if offset < 0:
            continue
        found.append((offset, title, level))
        cursor = offset + len(title)

    # Sắp xếp theo offset thực tế (LLM có thể đảo thứ tự)
    found.sort(key=lambda x: x[0])
    sections: list[Section] = []
    for i, (offset, title, level) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(full_text)
        sections.append(Section(title=title, start_offset=offset, end_offset=end, level=level))
    return sections


def analyze_document(cfg: Config, full_text: str) -> DocumentContext:
    """Phân tích tài liệu để build DocumentContext.

    Quy trình:
      1. detect_sections_regex(full_text) → sections candidates (rẻ, không LLM)
      2. Stratified sample → 1 LLM call cho subjects + doc_type + description
         + section list (vì regex bỏ sót Title Case header)
      3. _locate_titles_in_text() tìm offset cho mỗi title LLM đưa
      4. Merge regex + LLM sections (dedupe theo offset gần nhau)
    """
    regex_sections = detect_sections_regex(full_text)
    sample = _stratified_sample(full_text)

    regex_hint = ""
    if regex_sections:
        top = [s.title for s in regex_sections if s.level == 1][:15]
        regex_hint = (
            f"\nRegex pre-detected possible sections (you may include them "
            f"in your output if accurate): {top}\n"
        )

    # Analyze chạy trong build pipeline → dùng extraction_model (NHANH).
    llm = ChatOpenAI(model=cfg.extraction_model, api_key=cfg.openai_api_key)
    prompt = f"""You are analyzing a document via stratified samples (beginning/middle/end).
The document may be LARGE (100+ pages), so use all 3 regions to understand structure.

Identify:
1. **subjects**: list of 1-{MAX_SUBJECTS} PRIMARY entities this document is about. Use canonical names.
   - CV: 1 person (e.g. "Pham Tuyen")
   - Audit report: org audited + auditor (e.g. ["OpenAI Inc.", "Schellman & Co"])
   - Legal contract: all parties (e.g. ["Party A Inc.", "Party B LLC"])
   - Technical paper: core topic/system (e.g. ["GraphRAG", "Microsoft Research"])
2. **doc_type**: short label like "CV / Resume", "SOC 2 Audit Report", "Legal Contract",
   "Technical Whitepaper", "Research Paper", "Privacy Policy", "Annual Report", etc.
3. **description**: 2-sentence summary of what document covers.
4. **sections**: list of section headers found in the document. Look for short Title-Case
   lines that act as section breaks (e.g. "Professional Summary", "Technical Skills",
   "Education"). Output each as {{"title": "...", "level": 1}} where level=1 for
   top-level, 2 for subsections. Output them in document order. Max 30 sections.

Output STRICT JSON, no markdown:
{{"subjects": ["..."], "doc_type": "...", "description": "...",
  "sections": [{{"title": "...", "level": 1}}, ...]}}
{regex_hint}
SAMPLES:
{sample}
"""

    response = llm.invoke(prompt)
    raw = getattr(response, "content", str(response))

    try:
        data = json.loads(_strip_code_fence(raw))
    except (json.JSONDecodeError, AttributeError):
        ctx = DocumentContext(
            subjects=[],
            doc_type="(unknown)",
            description="",
            sections=regex_sections,
        )
        ctx._resolve_parents()
        return ctx

    # Locate LLM-provided section titles trong full_text
    llm_titles = [
        (str(s.get("title", "")), int(s.get("level", 1)))
        for s in data.get("sections", [])
        if s.get("title")
    ][:30]
    llm_sections = _locate_titles_in_text(full_text, llm_titles)

    # Merge với regex sections — ưu tiên regex cho markdown/numbered (chính xác hơn),
    # bổ sung LLM cho Title Case (regex bỏ sót)
    merged = list(regex_sections)
    for ls in llm_sections:
        # Bỏ qua nếu trùng offset với regex (cách nhau < 20 chars)
        if any(abs(rs.start_offset - ls.start_offset) < 20 for rs in regex_sections):
            continue
        merged.append(ls)
    merged.sort(key=lambda s: s.start_offset)
    # Refresh end_offset sau merge
    for i, s in enumerate(merged):
        s.end_offset = merged[i + 1].start_offset if i + 1 < len(merged) else len(full_text)

    ctx = DocumentContext(
        subjects=[str(s) for s in data.get("subjects", []) if s][:MAX_SUBJECTS],
        doc_type=str(data.get("doc_type", "(unknown)")),
        description=str(data.get("description", "")),
        sections=merged,
    )
    ctx._resolve_parents()
    return ctx
