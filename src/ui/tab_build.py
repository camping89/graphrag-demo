"""Tab Build — upload PDF, chọn collection, build knowledge graph."""

from __future__ import annotations

import dataclasses
import tempfile
import threading
import time
import traceback
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

from src.entity_embedder import (
    backfill_embeddings,
    ensure_vector_index,
)
from src.entity_normalizer import (
    apply_merge_plans,
    find_merge_candidates,
)
from src.graph_builder import DEFAULT_MAX_WORKERS, MAX_RETRIES, build_graph
from src.pdf_loader import (
    load_pdf_chunks_with_context,
    pdf_stats,
    recommend_chunk_params,
)
from src.ui.shared import (
    count_entities,
    get_config,
    list_collections,
    slugify_collection_name,
)


@st.cache_data(show_spinner=False)
def _pdf_stats_cached(path_str: str, mtime: float) -> dict:
    """Cache stats theo (path, mtime) — file mới upload → key mới, recompute."""
    return pdf_stats(Path(path_str))


def _choose_pdf_source() -> Path | None:
    """User upload PDF qua file_uploader. Trả về Path hợp lệ hoặc None.

    Sau khi upload → phân tích pages/chars + show đề xuất chunk params
    với nút "Áp dụng" để pre-fill input bên dưới.
    """
    st.markdown("**📄 Bước 1: Upload PDF**")
    uploaded = st.file_uploader(
        "Chọn file PDF",
        type=["pdf"],
        help="Upload tài liệu để build knowledge graph. "
             "Có thể upload nhiều PDF cùng domain (vào cùng collection) để merge entities.",
    )
    if uploaded is None:
        return None

    # PyPDFLoader yêu cầu path → lưu uploaded file ra temp dir
    tmp_dir = Path(tempfile.gettempdir()) / "graphrag-uploads"
    tmp_dir.mkdir(exist_ok=True)
    target = tmp_dir / uploaded.name
    target.write_bytes(uploaded.getvalue())
    st.success(f"✅ Đã upload: {uploaded.name} ({uploaded.size / 1024:.1f} KB)")

    # Phân tích PDF + show đề xuất
    _render_pdf_analysis(target)
    return target


def _render_pdf_analysis(pdf_path: Path) -> None:
    """Hiển thị stats PDF + đề xuất chunk params với nút áp dụng."""
    try:
        stats = _pdf_stats_cached(str(pdf_path), pdf_path.stat().st_mtime)
    except Exception as exc:
        st.warning(f"Không phân tích được PDF: {exc}")
        return

    rec = recommend_chunk_params(stats["total_chars"])

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("📑 Pages", stats["n_pages"])
    col_b.metric("📝 Total chars", f"{stats['total_chars']:,}")
    col_c.metric("📊 Avg chars/page", f"{stats['avg_chars_per_page']:,.0f}")

    def _apply_recommendation():
        st.session_state["chunk_size_input"] = rec["chunk_size"]
        st.session_state["chunk_overlap_input"] = rec["overlap"]

    col_rec, col_btn = st.columns([3, 1], vertical_alignment="center")
    col_rec.info(
        f"💡 **Đề xuất**: `chunk_size={rec['chunk_size']}`, "
        f"`overlap={rec['overlap']}` → ~**{rec['est_chunks']} chunks**. "
        f"{rec['reason']}"
    )
    col_btn.button(
        "✨ Áp dụng đề xuất",
        on_click=_apply_recommendation,
        help="Pre-fill Bước 3 với chunk_size + overlap đề xuất.",
        use_container_width=True,
    )


def _choose_collection(base_cfg, pdf_path: Path | None) -> str:
    """UI chọn/nhập collection name, trả về slug đã chuẩn hoá.

    Khi tạo collection mới + đã upload file → có nút "Suggest từ tên file"
    để pre-fill ô input với tên file đã slugify (VD: PhamTuyen_CV.pdf → pham_tuyen_cv).
    """
    st.markdown("**🗄️ Bước 2: Chọn knowledge base (collection)**")
    existing = []
    try:
        existing = list_collections()
    except Exception:
        pass

    mode = st.radio(
        "Mode:",
        ["Merge vào collection có sẵn", "Tạo collection mới"],
        horizontal=True,
    )

    if mode.startswith("Merge") and existing:
        options = sorted(set(existing) | {base_cfg.mongodb_collection})
        default_idx = options.index(base_cfg.mongodb_collection) \
            if base_cfg.mongodb_collection in options else 0
        return st.selectbox("Collection có sẵn", options=options, index=default_idx)

    # --- Tạo collection mới: text input + nút Suggest ---
    def _apply_suggestion():
        # Callback chạy TRƯỚC khi script rerun → được phép sửa session_state
        # cho widget key đang dùng (Streamlit cho phép trong on_click).
        if pdf_path is not None:
            st.session_state["new_collection_input"] = slugify_collection_name(
                pdf_path.stem
            )

    # Khởi tạo lần đầu
    if "new_collection_input" not in st.session_state:
        st.session_state["new_collection_input"] = base_cfg.mongodb_collection

    # vertical_alignment="bottom" → button căn với đáy input (không lệch label)
    col_input, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    raw_name = col_input.text_input(
        "Tên collection mới",
        key="new_collection_input",
        help="Sẽ được chuẩn hoá thành slug a-z/0-9/_.",
    )
    col_btn.button(
        "💡 Suggest từ tên file",
        on_click=_apply_suggestion,
        disabled=pdf_path is None,
        help="Pre-fill ô bên trái bằng tên file PDF (đã slugify).",
        use_container_width=True,
    )

    slug = slugify_collection_name(raw_name)
    st.caption(f"→ Slug: `{slug}`")
    return slug


def _chunk_params() -> tuple[int, int, int, int]:
    """UI tham số chunking — trả về (chunk_size, overlap, limit_chunks, max_workers).

    Dùng `key=` để nút "Áp dụng đề xuất" có thể ghi session_state pre-fill.
    """
    st.markdown("**⚙️ Bước 3: Tham số chunk**")
    # Khởi tạo default 1 lần — sau đó user/button update qua session_state
    st.session_state.setdefault("chunk_size_input", 1500)
    st.session_state.setdefault("chunk_overlap_input", 200)

    col1, col2 = st.columns(2)
    chunk_size = col1.number_input(
        "Chunk size", 500, 5000, step=100, key="chunk_size_input"
    )
    chunk_overlap = col2.number_input(
        "Overlap", 0, 1000, step=50, key="chunk_overlap_input"
    )

    col3, col4 = st.columns(2)
    limit = col3.number_input(
        "Giới hạn số chunk (0 = full PDF)",
        min_value=0, max_value=10000, value=20, step=5,
        help="Đặt giá trị nhỏ để test rẻ. Đặt 0 để chạy full.",
    )
    max_workers = col4.number_input(
        "Parallel workers",
        min_value=1, max_value=20, value=DEFAULT_MAX_WORKERS, step=1,
        help="Số chunk extract song song. Cao = nhanh hơn nhiều nhưng dễ hit "
             "OpenAI rate limit. 5-10 thường tối ưu. 1 = tuần tự (debug).",
    )
    return int(chunk_size), int(chunk_overlap), int(limit), int(max_workers)


def render_build_tab() -> None:
    st.subheader("Bước 1 — Build knowledge graph từ PDF")
    st.info(
        "🎯 **Đây là bước đầu tiên với mọi tài liệu mới.** "
        "Sau khi build xong → sang tab **2️⃣ Chat** để hỏi đáp."
    )

    st.warning(
        "Thao tác này sẽ gọi LLM cho từng chunk → có thể mất vài phút và "
        "tốn API credits. Mỗi lần build sẽ **merge** entities vào collection."
    )

    base_cfg = get_config()
    pdf_path = _choose_pdf_source()
    collection_name = _choose_collection(base_cfg, pdf_path)
    chunk_size, chunk_overlap, limit_chunks, max_workers = _chunk_params()

    st.markdown("---")
    # Dùng placeholder để có thể replace button bằng version disabled
    # ngay sau khi click — tránh user click nhiều lần khi build đang chạy
    button_slot = st.empty()
    clicked = button_slot.button(
        "🚀 Build graph",
        type="primary",
        disabled=pdf_path is None,
    )

    # Result slot — nằm NGAY dưới button để user thấy message tức thì sau rerun
    result_slot = st.empty()
    last_error = st.session_state.pop("last_build_error", None)
    if last_error:
        result_slot.error(
            f"❌ **Build thất bại**\n\n"
            f"**Lỗi:** `{last_error['type']}: {last_error['message']}`\n\n"
            f"**Khi nào:** sau {last_error['elapsed']:.1f}s từ khi bấm Build.\n\n"
            f"**Traceback (dán cho dev):**\n```\n{last_error['traceback']}\n```"
        )
    else:
        last = st.session_state.pop("last_build_result", None)
        if last:
            _render_build_result(result_slot, last)

    if clicked:
        # Thay button bằng phiên bản disabled trước khi vào build loop
        button_slot.button(
            "⏳ Đang build...",
            type="primary",
            disabled=True,
            key="build_btn_disabled",
        )
        run_cfg = dataclasses.replace(base_cfg, mongodb_collection=collection_name)

        with st.spinner(f"Load + analyze PDF: {pdf_path.name}"):
            chunks, doc_ctx = load_pdf_chunks_with_context(
                pdf_path, run_cfg, chunk_size, chunk_overlap
            )
            st.info(f"Đã load {len(chunks)} chunks.")

            subjects_md = ", ".join(f"`{s}`" for s in doc_ctx.subjects) or "—"
            top_sections = [s.title for s in doc_ctx.sections if s.level == 1][:10]
            sections_md = ", ".join(f"`{s}`" for s in top_sections) or "—"
            total_sections = len(doc_ctx.sections)

            st.success(
                f"📑 **Document context detected:**\n\n"
                f"- **Subjects** ({len(doc_ctx.subjects)}): {subjects_md}\n"
                f"- **Type**: `{doc_ctx.doc_type}`\n"
                f"- **Description**: {doc_ctx.description}\n"
                f"- **Sections detected**: {total_sections} total "
                f"({len(top_sections)} top-level): {sections_md}"
            )

            # Cho user xem prefix mẫu để verify chất lượng injection
            with st.expander("🔍 Xem prefix sẽ inject vào chunks"):
                sample_section = doc_ctx.sections[0] if doc_ctx.sections else None
                st.code(doc_ctx.to_chunk_prefix(sample_section), language="text")

            if limit_chunks > 0:
                chunks = chunks[:limit_chunks]
                st.info(f"Giới hạn xuống {len(chunks)} chunks (test mode).")

        start = time.time()
        status_box = st.empty()
        error_box = st.empty()  # live last-error box (cập nhật khi 1 chunk fail)
        progress_bar = st.progress(0, text="Khởi tạo...")
        total = len(chunks)

        # Lưu fail count + last error để hiển thị live
        live_state = {"failed": 0, "last_error": "", "last_idx": 0}

        def on_progress(done: int, total: int) -> None:
            """Update progress bar + ETA dựa trên tốc độ trung bình hiện tại."""
            pct = done / total
            elapsed = time.time() - start
            avg_per_chunk = elapsed / done
            eta = avg_per_chunk * (total - done)
            fail_suffix = (
                f" — ⚠️ {live_state['failed']} fail"
                if live_state["failed"] else ""
            )
            progress_bar.progress(
                pct,
                text=(
                    f"Chunk {done}/{total} ({pct * 100:.1f}%) — "
                    f"elapsed {elapsed:.0f}s, ETA ~{eta:.0f}s{fail_suffix}"
                ),
            )

        status_box.info(
            f"Đang extract entities ({max_workers} workers song song) → "
            f"`{run_cfg.mongodb_db}.{collection_name}`..."
        )

        def on_failed(failed: int, total_chunks: int) -> None:
            live_state["failed"] = failed

        def on_error(idx: int, err_msg: str) -> None:
            """Cập nhật error_box LIVE khi 1 chunk fail (sau retries)."""
            live_state["last_idx"] = idx
            live_state["last_error"] = err_msg
            # Hiển thị error rõ + gợi ý — fail rate cao => model/rate issue
            fail_rate_hint = ""
            if live_state["failed"] >= 3:
                fail_rate_hint = (
                    "\n\n🚨 **Nhiều chunks đang fail liên tục.** Khả năng: "
                    "OpenAI rate limit (gpt-5 TPM thấp) HOẶC model không "
                    "tồn tại. **Khuyến nghị**: kill Streamlit (Ctrl+C terminal), "
                    "set `OPENAI_CHAT_MODEL=gpt-4o` trong `.env`, build lại."
                )
            error_box.error(
                f"⚠️ **{live_state['failed']} chunks failed** (sau {MAX_RETRIES} retries).\n\n"
                f"**Chunk #{idx}** error:\n```\n{err_msg}\n```"
                + fail_rate_hint
            )

        # ScriptRunContext của Streamlit gắn vào worker threads:
        # nếu không có, mọi call st.* (kể cả progress_bar.progress) từ thread
        # sẽ silently no-op → UI kẹt ở "Khởi tạo..." dù chunks đã chạy.
        ctx = get_script_run_ctx()

        def _attach_ctx() -> None:
            add_script_run_ctx(threading.current_thread(), ctx)

        try:
            build_graph(
                run_cfg,
                chunks,
                progress_callback=on_progress,
                max_workers=max_workers,
                failed_callback=on_failed,
                error_callback=on_error,
                thread_initializer=_attach_ctx,
            )
        except Exception as exc:
            # Lưu chi tiết error vào session_state để giữ qua rerun
            # (nếu không, st.error sẽ bị xoá khi st.rerun() fire ngay sau đó)
            st.session_state["last_build_error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
                "traceback": traceback.format_exc()[-2000:],  # last 2k chars
                "elapsed": time.time() - start,
            }
            progress_bar.empty()
            status_box.empty()
            st.rerun()

        progress_bar.empty()
        status_box.empty()
        # Giữ error_box nếu có fail — để user thấy summary sau khi rerun
        if live_state["failed"] == 0:
            error_box.empty()

        # AUTO-NORMALIZE: ngay sau build, gộp các entity duplicate (canonical
        # name match: "Document Section" ↔ "DocumentSection"). Đây là quality
        # step — luôn đáng làm, không cần user bấm tay.
        norm_result = {"merged_groups": 0, "deleted_entities": 0}
        try:
            with st.spinner("🔀 Auto-normalize duplicate entities..."):
                plans = find_merge_candidates(run_cfg, collection_name)
                if plans:
                    norm_result = apply_merge_plans(run_cfg, collection_name, plans)
        except Exception as exc:
            # Không fail build vì normalize lỗi — chỉ log
            print(f"[auto-normalize] skip do lỗi: {exc}")

        # Đếm entities thật trong collection sau build + normalize.
        try:
            entity_count = count_entities(collection_name)
        except Exception:
            entity_count = -1

        # Lưu kết quả build vào session_state để hiển thị sau khi rerun
        st.session_state["last_build_result"] = {
            "total": total,
            "elapsed": time.time() - start,
            "db": run_cfg.mongodb_db,
            "collection": collection_name,
            "failed": live_state["failed"],
            "entity_count": entity_count,
            "merged_groups": norm_result["merged_groups"],
            "deleted_dupes": norm_result["deleted_entities"],
        }
        # Reset cached engine + collections list để Chat/Visualize thấy collection mới
        st.cache_resource.clear()
        list_collections.clear()
        # Rerun → button quay về enabled, success message sẽ hiển thị ở dưới
        st.rerun()

    # --- Hybrid mode: backfill embeddings cho collection ---
    st.markdown("---")
    _render_embeddings_section(base_cfg)


def _render_build_result(slot, last: dict) -> None:
    """Render success/warning ngay dưới button Build.

    Hiển thị rõ:
      - Số chunks đã process
      - Số entities thực tế trong collection (sau dedup upsert)
      - Tỷ lệ chunk fail (nếu có) + gợi ý fix
      - Giải thích vì sao entities có thể ít hơn chunks (dedup)
    """
    total = last["total"]
    failed = last.get("failed", 0)
    entities = last.get("entity_count", -1)
    elapsed = last["elapsed"]
    coll = f"`{last['db']}.{last['collection']}`"
    merged_groups = last.get("merged_groups", 0)
    deleted_dupes = last.get("deleted_dupes", 0)

    entity_line = (
        f"- **Entities trong collection** (sau dedup): **{entities}**\n"
        if entities >= 0 else ""
    )
    norm_line = (
        f"- **Auto-normalize**: merge {merged_groups} groups, "
        f"xoá {deleted_dupes} duplicate entities\n"
        if merged_groups > 0 else ""
    )
    dedup_hint = (
        "\n\n💡 *Lưu ý*: số entities < số chunks là **bình thường** — "
        "MongoDBGraphStore upsert theo tên entity, nên các entity trùng tên "
        "trong nhiều chunks sẽ **merge** thành 1 row."
    )

    if failed > 0:
        slot.warning(
            f"⚠️ **Build xong nhưng có lỗi.** {failed}/{total} chunks **thất bại** "
            f"sau retries (rate limit/timeout).\n\n"
            f"- Chunks processed: {total - failed}/{total}\n"
            f"{entity_line}"
            f"{norm_line}"
            f"- Thời gian: {elapsed:.1f}s\n"
            f"- Collection: {coll}\n\n"
            f"👉 **Khuyến nghị**: giảm `Parallel workers` xuống 3 và build lại "
            f"để bù chunks fail (chỉ merge thêm vào, không trùng)."
            + dedup_hint
        )
    else:
        slot.success(
            f"✅ **Build xong!**\n\n"
            f"- Chunks processed: **{total}**\n"
            f"{entity_line}"
            f"{norm_line}"
            f"- Thời gian: {elapsed:.1f}s\n"
            f"- Collection: {coll}\n\n"
            f"👉 Sang tab **2️⃣ Chat** để hỏi đáp."
            + dedup_hint
        )


def _render_embeddings_section(base_cfg) -> None:
    """UI để compute vector embeddings + tạo Atlas Vector Search index.

    Tách thành function riêng để giữ render_build_tab() ngắn gọn.
    Bước này biến collection từ "graph-only" → "hybrid vector + graph".
    """
    st.subheader("🧬 Hybrid Vector + Graph (tuỳ chọn)")
    st.markdown(
        "Sau khi build graph, **compute embeddings** để hỏi đáp bằng câu "
        "tự nhiên — không cần khớp chính xác tên entity trong graph. "
        "Bước này gọi OpenAI embedding API cho từng entity (rẻ — ~$0.0001/entity)."
    )

    try:
        existing = list_collections()
    except Exception:
        existing = [base_cfg.mongodb_collection]
    options = sorted(set(existing) | {base_cfg.mongodb_collection})
    default_idx = (
        options.index(base_cfg.mongodb_collection)
        if base_cfg.mongodb_collection in options
        else 0
    )
    target_coll = st.selectbox(
        "Collection cần compute embeddings",
        options=options,
        index=default_idx,
        key="embed_target_collection",
    )
    force = st.checkbox(
        "Re-compute ngay cả entity đã có embedding (force refresh)",
        value=False,
    )

    last_embed = st.session_state.pop("last_embed_result", None)
    if last_embed:
        st.success(
            f"✅ Đã embed **{last_embed['count']} entities** trong "
            f"`{last_embed['collection']}` ({last_embed['elapsed']:.1f}s). "
            f"Vector index: `{last_embed['index']}`. "
            f"Giờ hỏi đáp đã có hybrid mode!"
        )

    btn_slot = st.empty()
    clicked = btn_slot.button("🧬 Build embeddings + vector index", type="secondary")
    if not clicked:
        return

    btn_slot.button(
        "⏳ Đang build embeddings...",
        disabled=True,
        key="embed_btn_disabled",
    )

    run_cfg = dataclasses.replace(base_cfg, mongodb_collection=target_coll)
    start = time.time()
    progress = st.progress(0, text="Khởi tạo embedding model...")

    def on_progress(done: int, total: int) -> None:
        pct = done / total
        elapsed = time.time() - start
        eta = (elapsed / done) * (total - done)
        progress.progress(
            pct,
            text=f"Entity {done}/{total} ({pct * 100:.1f}%) — ETA ~{eta:.0f}s",
        )

    try:
        with st.spinner("Đảm bảo Atlas Vector Search index tồn tại..."):
            index_name = ensure_vector_index(run_cfg, target_coll)
        count = backfill_embeddings(
            run_cfg, target_coll, progress_callback=on_progress, force=force
        )
    except Exception as exc:
        st.session_state["last_build_error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc()[-2000:],
            "elapsed": time.time() - start,
        }
        progress.empty()
        st.rerun()

    progress.empty()
    st.session_state["last_embed_result"] = {
        "count": count,
        "collection": target_coll,
        "index": index_name,
        "elapsed": time.time() - start,
    }
    st.cache_resource.clear()  # query engine cache reset → load lại có hybrid
    st.rerun()
