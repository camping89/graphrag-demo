"""Load file PDF nguồn và chia thành các chunk văn bản.

Dùng PyPDFLoader để parse PDF (mỗi page = 1 Document), sau đó dùng
RecursiveCharacterTextSplitter để cắt thành các chunk nhỏ hơn — đủ
để LLM extract entity chính xác mà không vượt context window.

Hỗ trợ 2 chế độ:
  - load_pdf_chunks()              — load thuần (legacy / debug)
  - load_pdf_chunks_with_context() — analyze + inject DOCUMENT CONTEXT
    vào đầu mỗi chunk để LLM extract chất lượng cao hơn (recommended)
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import Config
from .document_context import (
    DocumentContext,
    analyze_document,
)


# Tham số chunk: chọn theo hướng dẫn của MongoDB GraphRAG tutorial,
# cân bằng giữa độ chi tiết entity và chi phí gọi LLM.
DEFAULT_CHUNK_SIZE = 1500
DEFAULT_CHUNK_OVERLAP = 200


def pdf_stats(pdf_path: Path) -> dict:
    """Đếm pages + tổng chars của PDF (không chunk, không LLM).

    Dùng để hiển thị thông tin nhanh + tính đề xuất chunk params.
    """
    loader = PyPDFLoader(str(pdf_path))
    pages = loader.load()
    total_chars = sum(len(p.page_content) for p in pages)
    return {
        "n_pages": len(pages),
        "total_chars": total_chars,
        "avg_chars_per_page": total_chars / max(1, len(pages)),
    }


def recommend_chunk_params(total_chars: int) -> dict:
    """Đề xuất chunk_size + overlap dựa trên kích thước tài liệu.

    Ưu tiên CHUNK NHỎ để LLM extract granular: mỗi chunk chứa ít entity
    → LLM tách rõ từng entity thay vì gộp thành concept tổng quát.

    Logic theo size (không hard-code domain):
      - Doc nhỏ   (<50k chars):       chunk 1200 / overlap 180
      - Doc vừa-lớn (50k-400k chars): chunk 1000 / overlap 150 — granular nhất
      - Doc rất lớn (>400k chars):    chunk 1200 / overlap 180 — cân rate limit

    Trade-off: chunk nhỏ = nhiều LLM call (đắt 1.5-2×) nhưng coverage entity
    cao hơn ~10× cho doc có nhiều identifier/proper noun rải rác.
    """
    if total_chars < 50_000:
        cs, ov = 1200, 180
        reason = "Doc nhỏ — chunk vừa phải đủ context."
    elif total_chars < 400_000:
        cs, ov = 1000, 150
        reason = (
            "Doc vừa-lớn — chunk nhỏ ép LLM tách từng identifier/proper noun "
            "thành entity riêng thay vì gộp generic."
        )
    else:
        cs, ov = 1200, 180
        reason = "Doc rất lớn — cân giữa granular và rate limit OpenAI."

    est_chunks = max(1, (total_chars - ov) // (cs - ov))
    return {
        "chunk_size": cs,
        "overlap": ov,
        "est_chunks": est_chunks,
        "reason": reason,
    }


def load_pdf_chunks(
    pdf_path: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Document]:
    """Load PDF, chia thành chunks và trả về danh sách Document.

    Args:
        pdf_path: đường dẫn tuyệt đối tới file PDF.
        chunk_size: số ký tự tối đa trong mỗi chunk.
        chunk_overlap: số ký tự chồng lấp giữa 2 chunk liên tiếp,
            giúp giữ ngữ cảnh xuyên ranh giới chunk.

    Returns:
        Danh sách Document — mỗi cái có metadata chứa source và page.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file PDF: {pdf_path}")

    loader = PyPDFLoader(str(pdf_path))
    pages = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(pages)

    # Gắn thêm chỉ số chunk để dễ truy nguồn khi debug
    for idx, doc in enumerate(chunks):
        doc.metadata["chunk_id"] = idx

    return chunks


def load_pdf_chunks_with_context(
    pdf_path: Path,
    cfg: Config,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> Tuple[List[Document], DocumentContext]:
    """Load PDF, phân tích context, inject GLOBAL + LOCAL context vào mỗi chunk.

    Hỗ trợ tài liệu lớn (100+ trang):
      1. Load + chunk PDF
      2. Build full_text (joined) → đưa cho analyze_document để:
         - Stratified sample 3 vùng → LLM detect subjects (1-5), doc_type, description
         - Regex detect sections với offset
      3. Với mỗi chunk: tìm offset trong full_text → tìm Section chứa nó
         → prefix mang context LOCAL (section title + parent) + GLOBAL
      4. → LLM extract entities có ngữ cảnh đầy đủ + bắt buộc link & fill attrs

    Returns:
        (chunks_with_context_prefix, context)
    """
    chunks = load_pdf_chunks(pdf_path, chunk_size, chunk_overlap)
    if not chunks:
        return chunks, DocumentContext(subjects=[], doc_type="(empty)", description="")

    # Build full text với joiner để tái tạo gần đúng tài liệu gốc.
    # RecursiveCharacterTextSplitter dùng separator hierarchy ["\n\n", "\n", ...]
    # nên dùng "\n\n" để rejoin là xấp xỉ ổn nhất.
    full_text = "\n\n".join(c.page_content for c in chunks)

    # Phân tích — 1 LLM call cho doc_type/subjects/description + regex sections
    context = analyze_document(cfg, full_text)

    # Map mỗi chunk về section: scan tuần tự với cursor (tránh O(n²) substring search)
    cursor = 0
    for c in chunks:
        snippet = c.page_content[:200]  # 200 chars đầu chunk làm key search
        offset = full_text.find(snippet, cursor) if snippet else cursor
        if offset < 0:
            offset = cursor  # fallback nếu không tìm được (rare)

        section = context.section_at(offset)
        prefix = context.to_chunk_prefix(section)
        c.page_content = prefix + c.page_content
        c.metadata["document_subjects"] = context.subjects
        c.metadata["document_type"] = context.doc_type
        c.metadata["section"] = section.title if section else None

        cursor = offset + len(snippet)  # advance để chunk sau search forward

    return chunks, context
