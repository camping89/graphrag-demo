"""Streamlit Web UI for the GraphRAG demo.

Run:
    streamlit run app.py

Logic is split into modules under src/ui/ to keep this file short.
"""

from __future__ import annotations

import os

import streamlit as st

# Sync Streamlit secrets sang env vars — Streamlit Cloud lưu secrets trong
# st.secrets (TOML), không tự inject thành env vars. load_config() đọc
# qua os.getenv() nên cần đoạn này để work cả local (.env) lẫn cloud.
# Local dev có .env → st.secrets rỗng → no-op. Cloud có st.secrets → populate.
try:
    for _key, _val in st.secrets.items():
        if isinstance(_val, str):
            os.environ.setdefault(_key, _val)
except (FileNotFoundError, Exception):
    pass  # Local dev không có secrets.toml — bỏ qua


from src.ui.sidebar import render_sidebar
from src.ui.tab_build import render_build_tab
from src.ui.tab_chat import render_chat_tab
from src.ui.tab_visualize import render_visualize_tab


st.set_page_config(page_title="GraphRAG × MongoDB Demo", layout="wide")

render_sidebar()

st.title("🕸️ GraphRAG × MongoDB Demo")
st.caption(
    "End-to-end demo: PDF → Knowledge Graph (MongoDB Atlas) → "
    "Q&A via LLM with structured context."
)

# Workflow guide for first-time users
with st.expander("📖 Workflow for a new document (first-time use)", expanded=False):
    st.markdown(
        """
        For a new PDF document, follow this order:

        **1️⃣ Build Graph** — Upload PDF and build the knowledge graph
        - This step calls the LLM to extract entities + relationships → stored in MongoDB
        - Recommended: set `Chunk limit = 20` for a cheap test first, then build full
        - Takes a few minutes to tens of minutes depending on PDF length

        **2️⃣ Chat** — Ask questions against the freshly built knowledge graph
        - Pick `Active collection` in the sidebar to switch between knowledge bases
        - The LLM traverses the graph (via `$graphLookup`) to answer

        **3️⃣ Visualize** (optional) — Interactive view of the knowledge graph
        - Render an HTML graph: drag, zoom, inspect entities + relationships

        > 💡 **If you only want to query** an existing collection in MongoDB
        > → skip step 1 and go straight to the Chat tab.
        """
    )

# Tabs in workflow order: Build → Chat → Visualize
tab_build, tab_chat, tab_graph = st.tabs([
    "1️⃣ Build Graph",
    "2️⃣ Chat",
    "3️⃣ Visualize",
])

with tab_build:
    render_build_tab()

with tab_chat:
    render_chat_tab()

with tab_graph:
    render_visualize_tab()
