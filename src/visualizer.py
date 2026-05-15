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


def _tier_for_degree(degree: int, max_degree: int) -> tuple[int, int]:
    """Phân tier dựa trên degree relative so với max trong graph hiện tại.

    Trả về (tier_number, size_px). Tier 1 = super-hub, Tier 5 = leaf.

    Dùng tỷ lệ thay vì threshold cố định → adapt theo từng collection
    (collection nhỏ 100 entities và lớn 3000 entities đều có 5 tier rõ rệt).
    """
    if max_degree <= 0:
        return 5, 12
    ratio = degree / max_degree
    if ratio >= 0.50:
        return 1, 50  # Super-hub — trung tâm của graph
    if ratio >= 0.20:
        return 2, 35  # Major hub
    if ratio >= 0.08:
        return 3, 25  # Connector
    if ratio >= 0.03:
        return 4, 18  # Mid
    return 5, 12      # Leaf


def build_networkx_graph(entities: Iterable[Dict[str, Any]]) -> nx.DiGraph:
    """Convert entity dict thành networkx DiGraph với 5 tier size rời rạc.

    - Chỉ giữ edges trỏ tới fetched nodes (no skeleton)
    - Phân node thành 5 tier theo % degree → size rời rạc (50/35/25/18/12 px)
      → user nhìn ra ngay "cấp" của entity, không phải gradient mờ ảo
    - Tooltip kèm tier number + rel count cho hover
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
        graph.add_node(node_id, label=node_id, group=etype, _etype=etype)

    for entity in entities:
        source = str(entity.get("_id", ""))
        if not source:
            continue
        for target in _extract_targets(entity.get("relationships")):
            if target and target != source and target in fetched_ids:
                graph.add_edge(source, target)

    # Pass 2: tính degree + phân tier (cần biết max_degree trước)
    degrees = {n: graph.in_degree(n) + graph.out_degree(n) for n in graph.nodes}
    max_deg = max(degrees.values()) if degrees else 0

    for node_id, deg in degrees.items():
        tier, size = _tier_for_degree(deg, max_deg)
        etype = graph.nodes[node_id].pop("_etype", "Entity")
        graph.nodes[node_id]["size"] = size
        graph.nodes[node_id]["title"] = (
            f"Tier {tier} · {deg} relationships\nType: {etype}"
        )

    return graph


def render_html(graph: nx.DiGraph, output_path: Path) -> Path:
    """Render networkx graph thành HTML file bằng pyvis.

    Layout strategy:
      - forceAtlas2Based solver: tốt hơn barnesHut cho dense graph
        (spread nodes đều hơn, ít hairball)
      - avoidOverlap=1: cấm nodes chồng lên nhau
      - Node size scale theo `value` (= degree) → hub to, leaf nhỏ
      - Labels chỉ hiện khi node đủ to (drawThreshold) → ít overlap text
      - Stabilize 250 iter rồi dừng physics (graph "đông cứng")
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
    net.set_options("""{
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "stabilization": {"enabled": true, "iterations": 250, "fit": true},
        "forceAtlas2Based": {
          "gravitationalConstant": -100,
          "centralGravity": 0.005,
          "springLength": 200,
          "springConstant": 0.18,
          "damping": 0.4,
          "avoidOverlap": 1
        },
        "minVelocity": 0.75
      },
      "nodes": {
        "shape": "dot",
        "scaling": {
          "label": {
            "enabled": true,
            "min": 10,
            "max": 22,
            "drawThreshold": 16,
            "maxVisible": 30
          }
        },
        "font": {"size": 12, "strokeWidth": 3, "strokeColor": "#000"}
      },
      "edges": {
        "smooth": {"enabled": false},
        "color": {"opacity": 0.35},
        "width": 0.5
      },
      "interaction": {"hover": true, "tooltipDelay": 200, "navigationButtons": true}
    }""")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output_path), open_browser=False, notebook=False)

    # Post-process: inject JS to freeze physics permanently after stabilization.
    # vis-network không tự tắt physics → sau stabilize, nodes vẫn jitter nhẹ
    # khi user drag. Listen `stabilizationIterationsDone` rồi set physics:false
    # để nodes "đông cứng" hẳn (user vẫn drag được, nhưng KHÔNG còn animation).
    freeze_js = (
        "network.on('stabilizationIterationsDone', function () {"
        "  network.setOptions({ physics: false });"
        "});"
    )
    html = output_path.read_text(encoding="utf-8")
    # Pyvis HTML kết thúc network init bằng `return network;`. Inject TRƯỚC line này.
    if "return network;" in html:
        html = html.replace("return network;", freeze_js + "\n  return network;")
        output_path.write_text(html, encoding="utf-8")

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
