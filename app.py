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
        - Upload PDF → app auto-analyzes pages/chars + recommends chunk params
        - Pick or create a `collection` (one knowledge base per topic/document set)
        - Default `Chunk limit = 0` processes the full PDF. Set to a small
          number (e.g. 20) for a cheap test build first if cost is a concern
        - LLM extracts entities + relationships per chunk → stored in MongoDB
        - **Auto-normalize** runs after build: merges duplicate entities (e.g.
          `Information Security Policy` ≡ `information security policy`)
        - Takes a few minutes to tens of minutes depending on PDF length
        - **Then click** `🧬 Build embeddings + vector index` to enable Hybrid
          mode (recommended — much better Q&A quality)

        **2️⃣ Chat** — Ask questions against the knowledge graph
        - Pick `Active collection` in the sidebar to switch between knowledge bases
        - The LLM extracts question entities + (if Hybrid) runs vector search,
          then traverses the graph (`$graphLookup`) to gather context, then answers
        - Mode badge under each answer: `🧬 Hybrid` (best) or `🕸️ Graph-only` (basic)
        - Sample questions for the SOC 2 demo collection:
          - *Who is the audit firm for this SOC 2 report?*
          - *List all subservice organizations of OpenAI*
          - *What does Snowflake provide for OpenAI?*
          - *How many Control Objectives are in the report?*

        **3️⃣ Visualize** (optional) — Interactive HTML view of the knowledge graph
        - Render: drag, zoom, hover for tooltips
        - Entities are sized in 5 tiers by centrality (super-hub → leaf)
        - Recommended max nodes ≤ 150 for smooth browser performance

        > 💡 **Already have data in MongoDB?** Skip step 1, pick the collection
        > in the sidebar, and go straight to the Chat tab.

        > 🔗 **Deep links**: append `?tab=chat` or `?tab=visualize` to the URL
        > to land directly on a specific tab — useful for sharing demo links.
        """
    )

# URL-routed tab navigation — supports deep links like ?tab=chat
# st.tabs doesn't allow programmatic selection, so we use st.radio bound
# to st.query_params for shareable URLs (e.g. when sending demo links).
TABS = {
    "build": "1️⃣ Build Graph",
    "chat": "2️⃣ Chat",
    "visualize": "3️⃣ Visualize",
}
_tab_keys = list(TABS.keys())

_qp_tab = st.query_params.get("tab", "build")
if _qp_tab not in TABS:
    _qp_tab = "build"

current_tab = st.radio(
    "Workflow",
    options=_tab_keys,
    format_func=lambda k: TABS[k],
    horizontal=True,
    index=_tab_keys.index(_qp_tab),
    label_visibility="collapsed",
    key="active_tab",
)

# Sync selection back to URL — user can copy/share the link
if st.query_params.get("tab") != current_tab:
    st.query_params["tab"] = current_tab

st.markdown("---")

if current_tab == "build":
    render_build_tab()
elif current_tab == "chat":
    render_chat_tab()
else:
    render_visualize_tab()
