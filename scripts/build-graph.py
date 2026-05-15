"""CLI: load PDF -> chunk -> build knowledge graph trong MongoDB Atlas.

Usage:
    python scripts/build-graph.py --pdf PATH [--chunk-size N] [--overlap N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Cho phép chạy script trực tiếp mà không cần cài package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.graph_builder import DEFAULT_MAX_WORKERS, build_graph
from src.pdf_loader import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    load_pdf_chunks_with_context,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build GraphRAG knowledge graph từ PDF.")
    parser.add_argument("--pdf", type=Path, required=True, help="Đường dẫn tới file PDF")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument(
        "--limit-chunks",
        type=int,
        default=None,
        help="Giới hạn số chunk để test nhanh (mặc định: dùng tất cả).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Số chunk song song (default {DEFAULT_MAX_WORKERS}). 1 = sequential.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    pdf_path = args.pdf

    print(f"[1/3] Load + analyze PDF: {pdf_path}")
    chunks, doc_ctx = load_pdf_chunks_with_context(
        pdf_path, cfg, args.chunk_size, args.overlap
    )
    top_sections = [s.title for s in doc_ctx.sections if s.level == 1][:10]
    print(f"      -> {len(chunks)} chunks")
    print(f"      -> Subjects: {doc_ctx.subjects}")
    print(f"      -> Type:     {doc_ctx.doc_type}")
    print(f"      -> Sections ({len(doc_ctx.sections)} total, top {len(top_sections)}): {top_sections}")

    if args.limit_chunks:
        chunks = chunks[: args.limit_chunks]
        print(f"      -> giới hạn còn {len(chunks)} chunks (test mode)")

    print(f"[2/3] Kết nối MongoDB: db={cfg.mongodb_db}, collection={cfg.mongodb_collection}")
    print(f"[3/3] Extract entities ({args.workers} workers song song)...")
    start = time.time()
    build_graph(cfg, chunks, max_workers=args.workers)
    elapsed = time.time() - start

    print(f"\nXong! Mất {elapsed:.1f}s. Mở MongoDB Atlas UI để xem collection.")


if __name__ == "__main__":
    main()
