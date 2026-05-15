"""Sidebar — hiển thị config và chọn active collection."""

from __future__ import annotations

import streamlit as st

from src.ui.shared import get_config, list_collections
from src.version import __version__


def render_sidebar() -> None:
    """Render toàn bộ sidebar: thông tin config + chọn active collection."""
    with st.sidebar:
        st.title("⚙️ Cấu hình")

        # Version badge — bump trong src/version.py mỗi lần fix bug.
        # User reload Streamlit nhìn vào đây để biết code đã update chưa.
        st.caption(f"🏷️ App version: **v{__version__}**")

        try:
            cfg = get_config()
        except RuntimeError as err:
            st.error(str(err))
            st.stop()

        st.success("Đã load .env")
        st.code(
            f"DB:               {cfg.mongodb_db}\n"
            f"Extraction model: {cfg.extraction_model}\n"
            f"Query model:      {cfg.query_model}",
            language="text",
        )

        st.markdown("---")
        st.subheader("🗄️ Active collection")
        st.caption("Chọn collection để Chat & Visualize.")

        try:
            existing = list_collections()
        except Exception as exc:  # pragma: no cover
            existing = []
            st.warning(f"Không list được collections: {exc}")

        # Đảm bảo collection mặc định luôn xuất hiện trong list
        options = sorted(set(existing) | {cfg.mongodb_collection})
        default_idx = (
            options.index(cfg.mongodb_collection)
            if cfg.mongodb_collection in options
            else 0
        )

        active = st.selectbox("Collection", options=options, index=default_idx)
        if st.button("🔄 Refresh danh sách"):
            list_collections.clear()
            st.rerun()

        st.session_state["active_collection"] = active
