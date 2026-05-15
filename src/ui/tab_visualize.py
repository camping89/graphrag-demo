"""Tab Visualize — render the knowledge graph into interactive HTML."""

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
    """Count total entities in collection — used as a UI hint."""
    client = MongoClient(cfg.mongodb_uri)
    try:
        return client[cfg.mongodb_db][collection_name].estimated_document_count()
    finally:
        client.close()


def render_visualize_tab() -> None:
    st.subheader("Step 3 (optional) — Visualize the knowledge graph")
    st.info(
        "🎯 **Optional**: render the knowledge graph to an interactive HTML "
        "to visualize entities + relationships. "
        "Requires a graph already built in **1️⃣ Build Graph**."
    )
    active = active_collection()
    cfg = get_config()
    try:
        total_in_db = _count_entities(cfg, active)
    except Exception:
        total_in_db = None

    if total_in_db is not None:
        st.caption(
            f"🗄️ Collection: `{active}` — **{total_in_db} entities** in DB."
        )
    else:
        st.caption(f"🗄️ Visualizing collection: `{active}`")

    max_nodes = st.slider(
        "Max entities to display (keep render fast, avoid lag)",
        min_value=20,
        max_value=300,
        value=80,
        step=10,
        help="Limits how many entities are FETCHED from MongoDB for the HTML "
             "render. Not the total entity count in DB. "
             "Recommended ≤ 150 for smooth browser; > 200 may lag on low-spec laptops.",
    )

    if st.button("🎨 Render graph HTML"):
        run_cfg = dataclasses.replace(get_config(), mongodb_collection=active)
        with st.spinner("Reading from Mongo and rendering HTML..."):
            try:
                path = visualize_graph(run_cfg, GRAPH_HTML_PATH, max_nodes=max_nodes)
                st.success(f"Rendered: {path.resolve()}")
            except Exception as exc:
                st.error(f"Render failed: {exc}")
                return

    if GRAPH_HTML_PATH.exists():
        st.markdown("---")
        html = GRAPH_HTML_PATH.read_text(encoding="utf-8")
        components.html(html, height=820, scrolling=True)
    else:
        st.info("No HTML yet — click **Render graph HTML** above.")
