"""Sidebar — display config and pick the active collection."""

from __future__ import annotations

import streamlit as st

from src.ui.shared import get_config, list_collections
from src.version import __version__


def render_sidebar() -> None:
    """Render the full sidebar: config info + active collection picker."""
    with st.sidebar:
        st.title("⚙️ Configuration")

        # Version badge — bump in src/version.py whenever code ships.
        # User reloads Streamlit and checks this to confirm fresh code.
        st.caption(f"🏷️ App version: **v{__version__}**")

        try:
            cfg = get_config()
        except RuntimeError as err:
            st.error(str(err))
            st.stop()

        st.success("Loaded .env")
        st.code(
            f"DB:               {cfg.mongodb_db}\n"
            f"Extraction model: {cfg.extraction_model}\n"
            f"Query model:      {cfg.query_model}",
            language="text",
        )

        st.markdown("---")
        st.subheader("🗄️ Active collection")
        st.caption("Pick a collection for Chat & Visualize.")

        try:
            existing = list_collections()
        except Exception as exc:  # pragma: no cover
            existing = []
            st.warning(f"Could not list collections: {exc}")

        # Always include the default collection in the list
        options = sorted(set(existing) | {cfg.mongodb_collection})
        default_idx = (
            options.index(cfg.mongodb_collection)
            if cfg.mongodb_collection in options
            else 0
        )

        active = st.selectbox("Collection", options=options, index=default_idx)
        if st.button("🔄 Refresh list"):
            list_collections.clear()
            st.rerun()

        st.session_state["active_collection"] = active
