"""Tab Chat — Q&A against the knowledge graph on the active collection."""

from __future__ import annotations

import streamlit as st

from src.ui.shared import active_collection, get_query_engine


def _render_assistant_inline(prompt: str, collection: str) -> dict | None:
    """Render the assistant chat bubble: show loading → replace with answer.

    Returns dict {content, related, anchors, used_vector} for the caller to
    store in history, or None on error (error already shown in the bubble).
    """
    with st.chat_message("assistant"):
        # Loading placeholder shown immediately when the assistant bubble appears
        answer_slot = st.empty()
        answer_slot.markdown("💭 *Thinking...*")

        status_slot = st.empty()
        with status_slot.status("Processing", expanded=False) as status:
            try:
                status.update(label="🔍 Extracting entities from question...")
                engine = get_query_engine(collection)
                status.update(label="🕸️ Traversing knowledge graph...")
                result = engine.ask(prompt)
            except Exception as exc:  # pragma: no cover
                status.update(label=f"❌ Error: {exc}", state="error")
                answer_slot.error(f"Error: {exc}")
                return None

        # Clear status box → only the answer remains
        status_slot.empty()
        answer_slot.markdown(result.answer)

        mode_badge = "🧬 Hybrid (Vector + Graph)" if result.used_vector_search \
            else "🕸️ Graph-only"
        st.caption(f"Mode: {mode_badge} · Anchors: {result.anchor_entities or '—'}")

        if result.related_entities:
            with st.expander("🔗 Related entities in graph"):
                st.write(result.related_entities)

    return {
        "content": result.answer,
        "related": result.related_entities,
        "anchors": result.anchor_entities,
        "used_vector": result.used_vector_search,
    }


def render_chat_tab() -> None:
    st.subheader("Step 2 — Q&A against the knowledge graph")
    st.info(
        "🎯 **Requirement**: graph must be built (see tab **1️⃣ Build Graph**) "
        "or a collection must already exist in MongoDB. "
        "Pick a collection in the **sidebar** to switch between knowledge bases."
    )
    with st.expander("💡 Sample questions"):
        st.markdown(
            "- *What topic/system is this document about?*\n"
            "- *What are the main entities in the document?*\n"
            "- *What is the relationship between X and Y?*\n"
            "- *List the involved parties and their roles.*"
        )

    active = active_collection()
    st.caption(f"🗄️ Querying collection: `{active}`")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # ── Layout: 2 container placeholders ────────────────────────────────
    # history_slot ở trên, input_slot ở dưới — Streamlit giữ position
    # theo thứ tự khai báo, không theo thứ tự ghi nội dung.
    history_slot = st.container()
    input_slot = st.container()

    with input_slot:
        prompt = st.chat_input("Type your question...")

    # ── Tách thành 2 script run để show câu hỏi NGAY ────────────────────
    # Run 1: prompt vừa submit → append history + set pending → st.rerun()
    # Run 2: render history (user msg đã có) → process pending → render answer
    # Mục đích: run 2 không bị engine.ask() block trước khi DOM flush user msg.
    if prompt:
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        st.session_state["_pending_question"] = prompt
        st.rerun()

    # Render history (luôn chạy ở run 2 với user msg mới nhất đã append)
    with history_slot:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                related = msg.get("related") or []
                if related:
                    with st.expander("🔗 Related entities in graph"):
                        st.write(related)

        # Process pending (chỉ có ở run 2 sau khi user vừa submit)
        pending = st.session_state.pop("_pending_question", None)
        if pending:
            assistant_msg = _render_assistant_inline(pending, active)
            if assistant_msg:
                st.session_state.chat_history.append({
                    "role": "assistant",
                    **assistant_msg,
                })
