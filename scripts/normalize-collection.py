"""Normalize duplicate entities trong 1 collection (CLI).

Default: dry-run — chỉ scan + in preview, KHÔNG merge.
Cần --apply để thực sự merge.

Usage:
  # Preview duplicates
  python scripts/normalize-collection.py --collection openai_2025_soc_2_type_2_report

  # Apply merge
  python scripts/normalize-collection.py --collection openai_2025_soc_2_type_2_report --apply

  # Limit preview output
  python scripts/normalize-collection.py --collection my_kb --top 30
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.entity_normalizer import (
    apply_merge_plans,
    find_merge_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize duplicate entities in a graph collection.")
    parser.add_argument("--collection", required=True, help="Collection name (e.g. openai_2025_soc_2_type_2_report)")
    parser.add_argument("--apply", action="store_true", help="Actually merge (default: preview only / dry-run)")
    parser.add_argument("--top", type=int, default=20, help="Number of groups to print in preview (default: 20)")
    args = parser.parse_args()

    base_cfg = load_config()
    run_cfg = dataclasses.replace(base_cfg, mongodb_collection=args.collection)

    print(f"[1/2] Scanning `{base_cfg.mongodb_db}.{args.collection}` for duplicates...")
    t0 = time.time()
    plans = find_merge_candidates(run_cfg, args.collection)
    print(f"      Found {len(plans)} duplicate groups in {time.time() - t0:.1f}s.\n")

    if not plans:
        print("[OK] No duplicates found.")
        return

    total_losers = sum(len(p.losers) for p in plans)
    print(f"[Preview] Top {min(args.top, len(plans))} groups (total {total_losers} entities will be merged):\n")
    for i, p in enumerate(plans[: args.top], start=1):
        losers_str = ", ".join(f'"{x}"' for x in p.losers)
        print(f"  {i:3d}. type=`{p.entity_type}`")
        print(f"       keep:  \"{p.canonical_id}\" ({p.total_attrs} attrs, {p.total_rels} rels)")
        print(f"       merge: {losers_str}\n")
    if len(plans) > args.top:
        print(f"  ... and {len(plans) - args.top} more groups (use --top N to see more)\n")

    if not args.apply:
        print(f"[DRY-RUN] Nothing changed. Re-run with --apply to merge.")
        return

    print(f"[2/2] Applying {len(plans)} merge plans...")
    t0 = time.time()

    def on_prog(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"      Progress: {done}/{total} groups merged")

    result = apply_merge_plans(run_cfg, args.collection, plans, progress_callback=on_prog)
    print(f"\n[DONE] in {time.time() - t0:.1f}s:")
    print(f"  - merged_groups:    {result['merged_groups']}")
    print(f"  - deleted_entities: {result['deleted_entities']}")
    print(f"  - redirected_refs:  {result['redirected_refs']}")


if __name__ == "__main__":
    main()
