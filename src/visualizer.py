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
    """Lấy danh sách target entity ID từ trường relationships.

    Schema MongoDBGraphStore:
      {"target_ids": [...], "types": [...], "attributes": [...]}
    → CHỈ `target_ids` mới là entity IDs. `types` là edge type (string),
    `attributes` là dict — KHÔNG được coi như node, nếu không sẽ tạo ra
    fake hub nodes (vd "supported_by", "maps_to") hút hàng nghìn edges,
    làm vis-network simulation không stabilize → browser lag.
    """
    if not relationships:
        return []

    if isinstance(relationships, dict):
        target_ids = relationships.get("target_ids", [])
        if isinstance(target_ids, list):
            return [str(v) for v in target_ids if v]
        return []

    # Legacy: list các edge object [{target, type}, ...] — giữ cho backward compat
    if isinstance(relationships, list):
        out: list[str] = []
        for edge in relationships:
            if isinstance(edge, dict):
                target = edge.get("target") or edge.get("to") or edge.get("_id")
                if target:
                    out.append(str(target))
            elif edge:
                out.append(str(edge))
        return out
    return []


def fetch_entities(cfg: Config, limit: int = DEFAULT_MAX_NODES) -> list[Dict[str, Any]]:
    """Đọc top-N entity QUAN TRỌNG NHẤT từ collection.

    Sort theo số relationships descending → entity hub (kết nối nhiều) lên đầu.
    Tránh bias "80 entity đầu PDF" của natural order trong `find()`.

    Cũng strip `embedding` field — 1536 floats/entity ko cần cho visualize, chỉ
    tốn RAM và làm document lớn vô ích.
    """
    client = MongoClient(cfg.mongodb_uri)
    try:
        coll = client[cfg.mongodb_db][cfg.mongodb_collection]
        pipeline = [
            {"$addFields": {
                "_rel_count": {"$size": {"$ifNull": ["$relationships.target_ids", []]}}
            }},
            {"$sort": {"_rel_count": -1}},
            {"$limit": limit},
            {"$project": {"_rel_count": 0, "embedding": 0}},
        ]
        return list(coll.aggregate(pipeline))
    finally:
        client.close()


def build_networkx_graph(entities: Iterable[Dict[str, Any]]) -> nx.DiGraph:
    """Convert danh sách entity dict thành networkx DiGraph.

    Chỉ giữ edges trỏ tới node ĐÃ được fetch — tránh networkx tự tạo "skeleton
    nodes" cho target ngoài limit, làm graph phồng lên 5-10× số node fetched.
    """
    entities = list(entities)
    graph = nx.DiGraph()

    fetched_ids: set[str] = set()
    for entity in entities:
        node_id = str(entity.get("_id", ""))
        if not node_id:
            continue
        fetched_ids.add(node_id)
        etype = entity.get("type", "Entity")
        graph.add_node(node_id, label=node_id, title=f"Type: {etype}", group=etype)

    for entity in entities:
        source = str(entity.get("_id", ""))
        if not source:
            continue
        for target in _extract_targets(entity.get("relationships")):
            if target and target != source and target in fetched_ids:
                graph.add_edge(source, target)

    return graph


def render_html(graph: nx.DiGraph, output_path: Path) -> Path:
    """Render networkx graph thành HTML file bằng pyvis.

    Cấu hình physics: stabilize sau 200 iterations rồi DỪNG hẳn — tránh
    browser chạy simulation vô tận với graph nhiều edge → CPU 100% mãi.
    """
    net = Network(
        height="800px",
        width="100%",
        directed=True,
        bgcolor="#1a1a1a",
        font_color="#ffffff",
        notebook=False,
    )
    net.from_nx(graph)
    # Limit simulation: stabilize 200 iter rồi tắt physics → graph "đông cứng",
    # user vẫn drag/zoom được, nhưng CPU không cháy.
    net.set_options("""{
      "physics": {
        "enabled": true,
        "stabilization": {"enabled": true, "iterations": 200, "fit": true},
        "barnesHut": {
          "gravitationalConstant": -8000,
          "centralGravity": 0.3,
          "springLength": 120,
          "springConstant": 0.04,
          "damping": 0.5
        },
        "minVelocity": 0.75
      },
      "interaction": {"hover": true, "tooltipDelay": 200},
      "edges": {"smooth": {"enabled": false}}
    }""")

    output_path.parent.mkdir(parents=True, exist_ok=True)
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
