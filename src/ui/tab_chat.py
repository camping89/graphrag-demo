"""Tab Chat — hỏi đáp với knowledge graph trên active collection."""

from __future__ import annotations

import streamlit as st

from src.ui.shared import active_collection, get_query_engine


def _render_assistant_inline(prompt: str, collection: str) -> dict | None:
    """Render chat bubble assistant: hiển thị loading → thay bằng câu trả lời.

    Trả về dict {content, related, anchors, used_vector} để caller lưu vào history,
    hoặc None nếu lỗi (đã hiển thị error trong bubble).
    """
    with st.chat_message("assistant"):
        # Placeholder loading hiện ngay khi assistant bubble xuất hiện
        answer_slot = st.empty()
        answer_slot.markdown("💭 *Đang xử lý...*")

        status_slot = st.empty()
        with status_slot.status("Đang xử lý", expanded=False) as status:
            try:
                status.update(label="🔍 Extract entities từ câu hỏi...")
                engine = get_query_engine(collection)
                status.update(label="🕸️ Duyệt knowledge graph...")
                result = engine.ask(prompt)
            except Exception as exc:  # pragma: no cover
                status.update(label=f"❌ Lỗi: {exc}", state="error")
                answer_slot.error(f"Lỗi: {exc}")
                return None

        # Clear status box → chỉ giữ lại câu trả lời
        status_slot.empty()
        answer_slot.markdown(result.answer)

        mode_badge = "🧬 Hybrid (Vector + Graph)" if result.used_vector_search \
            else "🕸️ Graph-only"
        st.caption(f"Mode: {mode_badge} · Anchors: {result.anchor_entities or '—'}")

        if result.related_entities:
            with st.expander("🔗 Entities liên quan trong graph"):
                st.write(result.related_entities)

    return {
        "content": result.answer,
        "related": result.related_entities,
        "anchors": result.anchor_entities,
        "used_vector": result.used_vector_search,
    }


def render_chat_tab() -> None:
    st.subheader("Bước 2 — Hỏi đáp với knowledge graph")
    st.info(
        "🎯 **Yêu cầu**: đã build graph (xem tab **1️⃣ Build Graph**) "
        "hoặc đã có collection sẵn trong MongoDB. "
        "Chọn collection ở **sidebar** để switch giữa các knowledge base."
    )
    with st.expander("💡 Gợi ý dạng câu hỏi"):
        st.markdown(
            "- *Tài liệu này nói về chủ đề/hệ thống nào?*\n"
            "- *Các thực thể chính trong tài liệu là gì?*\n"
            "- *Mối quan hệ giữa X và Y là gì?*\n"
            "- *Liệt kê các bên liên quan và vai trò của họ.*"
        )

    active = active_collection()
    st.caption(f"🗄️ Đang hỏi collection: `{active}`")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # ── Layout: 2 container placeholders ────────────────────────────────
    # history_slot ở trên, input_slot ở dưới — Streamlit giữ position
    # theo thứ tự khai báo, không theo thứ tự ghi nội dung.
    history_slot = st.container()
    input_slot = st.container()

    with input_slot:
        prompt = st.chat_input("Nhập câu hỏi của bạn...")

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
                    with st.expander("🔗 Entities liên quan trong graph"):
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
