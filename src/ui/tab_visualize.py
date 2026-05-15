"""Tab Visualize — render knowledge graph thành HTML interactive."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from pymongo import MongoClient

from src.ui.shared import active_collection, get_config
from src.visualizer import visualize_graph


GRAPH_HTML_PATH = Path("out/graph.html")


def _count_entities(cfg, collection_name: str) -> int:
    """Đếm tổng số entity trong collection — dùng cho UI hint."""
    client = MongoClient(cfg.mongodb_uri)
    try:
        return client[cfg.mongodb_db][collection_name].estimated_document_count()
    finally:
        client.close()


def render_visualize_tab() -> None:
    st.subheader("Bước 3 (tuỳ chọn) — Visualize knowledge graph")
    st.info(
        "🎯 **Tuỳ chọn**: render đồ thị tri thức ra HTML interactive "
        "để nhìn trực quan các entity + relationship. "
        "Cần đã build graph ở **1️⃣ Build Graph** trước."
    )
    active = active_collection()
    cfg = get_config()
    try:
        total_in_db = _count_entities(cfg, active)
    except Exception:
        total_in_db = None

    if total_in_db is not None:
        st.caption(
            f"🗄️ Collection: `{active}` — **{total_in_db} entities** trong DB."
        )
    else:
        st.caption(f"🗄️ Đang visualize collection: `{active}`")

    max_nodes = st.slider(
        "Số entity hiển thị tối đa (để render nhanh, không bị lag)",
        min_value=20,
        max_value=300,
        value=80,
        step=10,
        help="Giới hạn số entity FETCH từ MongoDB để render HTML. "
             "Không phải tổng số entity trong DB. "
             "Khuyến nghị ≤ 150 để browser mượt; > 200 có thể lag với laptop yếu.",
    )

    if st.button("🎨 Render graph HTML"):
        run_cfg = dataclasses.replace(get_config(), mongodb_collection=active)
        with st.spinner("Đang đọc Mongo và render HTML..."):
            try:
                path = visualize_graph(run_cfg, GRAPH_HTML_PATH, max_nodes=max_nodes)
                st.success(f"Đã render: {path.resolve()}")
            except Exception as exc:
                st.error(f"Render thất bại: {exc}")
                return

    if GRAPH_HTML_PATH.exists():
        st.markdown("---")
        html = GRAPH_HTML_PATH.read_text(encoding="utf-8")
        components.html(html, height=820, scrolling=True)
    else:
        st.info("Chưa có file HTML — bấm nút **Render graph HTML** ở trên.")
