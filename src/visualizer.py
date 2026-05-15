"""Xuất knowledge graph trong MongoDB ra HTML interactive bằng pyvis.

Đọc trực tiếp từ collection MongoDB (mỗi document = 1 entity, các
trường relationships chứa danh sách entity liên kết). Dùng networkx
để dựng đồ thị, sau đó pyvis render thành HTML có thể mở trong trình
duyệt — kéo thả, zoom được.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

import networkx as nx
from pymongo import MongoClient
from pyvis.network import Network

from .config import Config


# Giới hạn số entity hiển thị để HTML không quá nặng khi graph lớn
DEFAULT_MAX_NODES = 200


def _extract_targets(relationships: Any) -> list[str]:
    """Lấy danh sách target id từ trường relationships (linh hoạt với schema)."""
    targets: list[str] = []
    if not relationships:
        return targets

    # Trường hợp 1: dict gồm {type: [target_id, ...]}
    if isinstance(relationships, dict):
        for value in relationships.values():
            if isinstance(value, list):
                targets.extend(str(v) for v in value if v)
            elif value:
                targets.append(str(value))
        return targets

    # Trường hợp 2: list các edge object [{target, type}, ...]
    if isinstance(relationships, list):
        for edge in relationships:
            if isinstance(edge, dict):
                target = edge.get("target") or edge.get("to") or edge.get("_id")
                if target:
                    targets.append(str(target))
            elif edge:
                targets.append(str(edge))
    return targets


def fetch_entities(cfg: Config, limit: int = DEFAULT_MAX_NODES) -> list[Dict[str, Any]]:
    """Đọc danh sách entity từ MongoDB collection."""
    client = MongoClient(cfg.mongodb_uri)
    try:
        coll = client[cfg.mongodb_db][cfg.mongodb_collection]
        return list(coll.find({}, limit=limit))
    finally:
        client.close()


def build_networkx_graph(entities: Iterable[Dict[str, Any]]) -> nx.DiGraph:
    """Convert danh sách entity dict thành networkx DiGraph."""
    graph = nx.DiGraph()

    for entity in entities:
        node_id = str(entity.get("_id", ""))
        if not node_id:
            continue
        label = entity.get("type", "Entity")
        graph.add_node(node_id, label=node_id, title=f"Type: {label}", group=label)

    for entity in entities:
        source = str(entity.get("_id", ""))
        if not source:
            continue
        for target in _extract_targets(entity.get("relationships")):
            if target and target != source:
                graph.add_edge(source, target)

    return graph


def render_html(graph: nx.DiGraph, output_path: Path) -> Path:
    """Render networkx graph thành HTML file bằng pyvis."""
    net = Network(
        height="800px",
        width="100%",
        directed=True,
        bgcolor="#1a1a1a",
        font_color="#ffffff",
        notebook=False,
    )
    net.from_nx(graph)
    net.repulsion(node_distance=180, spring_length=120)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # pyvis 0.3+ dùng tham số `notebook=False` đã đảm bảo write HTML thuần
    net.write_html(str(output_path), open_browser=False, notebook=False)
    return output_path


def visualize_graph(cfg: Config, output_path: Path, max_nodes: int = DEFAULT_MAX_NODES) -> Path:
    """Pipeline: Mongo → networkx → pyvis HTML."""
    entities = fetch_entities(cfg, limit=max_nodes)
    if not entities:
        raise RuntimeError(
            "Collection rỗng. Hãy chạy build-graph trước khi visualize."
        )
    graph = build_networkx_graph(entities)
    return render_html(graph, output_path)
