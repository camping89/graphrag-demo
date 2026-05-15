"""Streamlit Web UI cho GraphRAG demo.

Chạy:
    streamlit run app.py

Logic được tách thành các module trong src/ui/ để giữ file này ngắn gọn.
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
    "Demo end-to-end: PDF → Knowledge Graph (MongoDB Atlas) → "
    "Hỏi đáp bằng LLM với context có cấu trúc."
)

# Workflow guide cho người mới — nhắc thứ tự thao tác đúng
with st.expander("📖 Workflow cho tài liệu mới (lần đầu dùng)", expanded=False):
    st.markdown(
        """
        Với một tài liệu PDF mới, làm theo thứ tự sau:

        **1️⃣ Build Graph** — Upload PDF và build knowledge graph
        - Bước này gọi LLM extract entities + relationships → lưu vào MongoDB
        - Khuyến nghị dùng `Giới hạn số chunk = 20` để test rẻ trước, sau đó build full
        - Mất vài phút tới vài chục phút tuỳ độ dài PDF

        **2️⃣ Chat** — Hỏi đáp với knowledge graph vừa build
        - Chọn `Active collection` ở sidebar để switch giữa các knowledge base
        - LLM sẽ duyệt graph (qua `$graphLookup`) để trả lời

        **3️⃣ Visualize** (tuỳ chọn) — Xem đồ thị tri thức trực quan
        - Render HTML interactive: kéo, zoom, xem entity + relationship

        > 💡 **Nếu chỉ muốn hỏi đáp** với collection đã có sẵn trong MongoDB
        > → bỏ qua bước 1, vào thẳng tab Chat.
        """
    )

# Tabs theo thứ tự workflow: Build → Chat → Visualize
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
