"""CLI: render knowledge graph thành file HTML interactive.

Usage:
    python scripts/visualize-graph.py [--out out/graph.html] [--max-nodes N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.visualizer import DEFAULT_MAX_NODES, visualize_graph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render knowledge graph thành HTML.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("out/graph.html"),
        help="Đường dẫn file HTML output (mặc định: out/graph.html)",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=DEFAULT_MAX_NODES,
        help=f"Số entity tối đa để hiển thị (mặc định: {DEFAULT_MAX_NODES})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()

    print(f"Đọc entities từ MongoDB (max {args.max_nodes})...")
    output_path = visualize_graph(cfg, args.out, max_nodes=args.max_nodes)
    print(f"Xong! Mở file: {output_path.resolve()}")


if __name__ == "__main__":
    main()
