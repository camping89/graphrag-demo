"""Rebuild embeddings cho tất cả entities trong 1 collection (CLI).

Khi nào cần:
  - Sau khi normalize merge entities (attrs/rels đổi → embedding stale)
  - Khi đổi embedding model trong .env
  - Khi muốn force re-embed mọi entity

Usage:
  python scripts/rebuild-embeddings.py --collection openai_2025_soc_2_type_2_report
  python scripts/rebuild-embeddings.py --collection my_kb --force
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.entity_embedder import backfill_embeddings, ensure_vector_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild embeddings for entities.")
    parser.add_argument("--collection", required=True, help="Collection name")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed all entities (default: skip entities with embedding)",
    )
    args = parser.parse_args()

    base_cfg = load_config()
    run_cfg = dataclasses.replace(base_cfg, mongodb_collection=args.collection)

    print(f"[1/2] Ensuring Atlas Vector Search index on `{args.collection}`...")
    index_name = ensure_vector_index(run_cfg, args.collection)
    print(f"      Index: `{index_name}`\n")

    print(f"[2/2] Computing embeddings (force={args.force})...")
    t0 = time.time()
    last_pct = [-1]

    def on_prog(done: int, total: int) -> None:
        pct = int(done / total * 100)
        if pct != last_pct[0] and pct % 5 == 0:
            last_pct[0] = pct
            elapsed = time.time() - t0
            eta = (elapsed / done) * (total - done) if done else 0
            print(f"      {done}/{total} ({pct}%) — elapsed {elapsed:.0f}s, ETA ~{eta:.0f}s")

    count = backfill_embeddings(
        run_cfg, args.collection, progress_callback=on_prog, force=args.force
    )
    print(f"\n[DONE] Embedded {count} entities in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()
