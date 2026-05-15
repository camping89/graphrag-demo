"""Xây dựng knowledge graph trong MongoDB Atlas từ các Document chunks.

Sử dụng MongoDBGraphStore (langchain-mongodb >= 0.5.0). Class này tự
gửi prompt cho LLM để extract entities + relationships, rồi lưu vào
MongoDB collection. Khi add document mới: tự động merge vào entity
đã tồn tại hoặc tạo entity mới.

Documentation: https://www.mongodb.com/docs/atlas/ai-integrations/langchain/graph-rag/
"""

from __future__ import annotations

import concurrent.futures
import random
import threading
import time
from typing import Callable, List, Optional

from langchain_core.documents import Document
from langchain_mongodb.graphrag.graph import MongoDBGraphStore
from langchain_openai import ChatOpenAI

from .config import Config


# Callback signature: (index_processed, total) -> None
# Dùng để báo tiến độ build cho UI (progress bar). index_processed bắt đầu từ 1.
ProgressCallback = Callable[[int, int], None]

# Default số worker song song khi build — balance giữa tốc độ và rate limit OpenAI.
# Account tier 1: ~500 RPM cho gpt-5 → 5-10 worker là sweet spot.
DEFAULT_MAX_WORKERS = 5

# Retry config khi gặp 429 (rate limit) hoặc lỗi tạm thời từ OpenAI.
# Exponential backoff: delay = 2^attempt + jitter (0..1s)
# attempt=0 → ngay, attempt=1 → ~2s, attempt=2 → ~4s, attempt=3 → ~8s
MAX_RETRIES = 4

# Substring trong error message → coi là retryable.
# Bao gồm:
#   - Rate limit / quota: rate, 429, too many, overloaded
#   - Network/upstream: timeout, 503, 502, 504
#   - JSON parse fail từ LLM output (transient — lần sau có thể gen valid):
#     "expecting property name", "expecting value", "unterminated string",
#     "json", "invalid \\escape", "extra data"
_RETRYABLE_KEYWORDS = (
    "rate", "429", "too many", "overloaded", "timeout", "503", "502", "504",
    "expecting", "json", "unterminated", "invalid \\escape", "extra data",
)


def _is_retryable(exc: Exception) -> bool:
    """Heuristic: error message chứa keyword retryable → yes."""
    msg = str(exc).lower()
    return any(k in msg for k in _RETRYABLE_KEYWORDS)


def _add_with_retry(
    store: MongoDBGraphStore,
    chunk: Document,
    max_retries: int = MAX_RETRIES,
) -> Optional[str]:
    """Thêm 1 chunk với exponential backoff khi gặp 429/rate limit.

    Returns:
        None nếu success, error message (truncated) nếu fail sau retries.
    """
    last_err: Optional[str] = None
    for attempt in range(max_retries + 1):
        try:
            store.add_documents([chunk])
            return None
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:200]
            if attempt == max_retries or not _is_retryable(exc):
                return last_err
            # Exponential backoff + jitter để tránh thundering herd
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
    return last_err


def make_extraction_model(cfg: Config) -> ChatOpenAI:
    """Model NHANH dùng cho build pipeline (extract entity từ chunk + analyze doc)."""
    return ChatOpenAI(
        model=cfg.extraction_model,
        api_key=cfg.openai_api_key,
    )


def make_query_model(cfg: Config) -> ChatOpenAI:
    """Model CHẤT LƯỢNG dùng cho query (chat RAG + extract entity từ câu hỏi)."""
    return ChatOpenAI(
        model=cfg.query_model,
        api_key=cfg.openai_api_key,
    )


# Backward compat — code cũ gọi make_chat_model() coi như cần extraction model
make_chat_model = make_extraction_model


def make_graph_store(
    cfg: Config,
    chat_model: Optional[ChatOpenAI] = None,
) -> MongoDBGraphStore:
    """Khởi tạo MongoDBGraphStore kết nối tới Atlas cluster.

    Default model = extraction_model (vì store dùng cho build). Query engine
    sẽ truyền query_model riêng qua tham số chat_model.
    """
    llm = chat_model or make_extraction_model(cfg)

    return MongoDBGraphStore(
        connection_string=cfg.mongodb_uri,
        database_name=cfg.mongodb_db,
        collection_name=cfg.mongodb_collection,
        entity_extraction_model=llm,
    )


def build_graph(
    cfg: Config,
    chunks: List[Document],
    chat_model: Optional[ChatOpenAI] = None,
    progress_callback: Optional[ProgressCallback] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    failed_callback: Optional[Callable[[int, int], None]] = None,
    thread_initializer: Optional[Callable[[], None]] = None,
    error_callback: Optional[Callable[[int, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> MongoDBGraphStore:
    """Build knowledge graph từ chunks và lưu vào MongoDB.

    Modes:
      - max_workers=1 + no progress: gọi add_documents 1 lần (nhanh nhất, no progress)
      - max_workers=1 + progress: tuần tự từng chunk (chậm nhất, full progress)
      - max_workers>1: ThreadPoolExecutor song song N chunks cùng lúc (recommend)

    Args:
        max_workers: số chunk extract song song. pymongo + OpenAI client đều
            thread-safe; entity merge dùng update_one upsert nên không conflict.
            Hạn chế bởi rate limit OpenAI — 5-10 thường ổn cho tier 1+.
        thread_initializer: callable chạy 1 lần khi mỗi worker thread khởi tạo.
            Dùng để attach Streamlit ScriptRunContext (hoặc bất kỳ thread-local
            setup nào) — nếu None thì threads chạy plain.

    Notes:
        - Khi lỗi 1 chunk, các chunk khác vẫn tiếp tục — error gom vào log.
        - progress_callback có thể bị gọi out-of-order (chunk 5 xong trước chunk 3)
          nhưng counter atomic → percentage luôn monotonic increasing.
    """
    store = make_graph_store(cfg, chat_model=chat_model)
    total = len(chunks)

    # Path 1: fastest — no progress, no parallel split
    if progress_callback is None and max_workers <= 1:
        store.add_documents(chunks)
        return store

    # Path 2: sequential với progress + retry (max_workers=1)
    if max_workers <= 1:
        errors_seq: list[tuple[int, str]] = []
        for idx, chunk in enumerate(chunks, start=1):
            err = _add_with_retry(store, chunk)
            if err:
                errors_seq.append((idx, err))
            if progress_callback:
                progress_callback(idx, total)
        if errors_seq:
            print(f"[build_graph] {len(errors_seq)}/{total} chunks failed after retries.")
            if failed_callback:
                failed_callback(len(errors_seq), total)
        return store

    # Path 3: parallel với ThreadPoolExecutor (DEFAULT) + retry per chunk
    counter_lock = threading.Lock()
    counter = {"done": 0}
    errors: list[tuple[int, str]] = []

    def _process_one(idx_and_chunk: tuple[int, Document]) -> None:
        idx, chunk = idx_and_chunk
        # Cancellation check: nếu user bấm Stop → skip chunk này
        if cancel_event is not None and cancel_event.is_set():
            with counter_lock:
                counter["done"] += 1
                done_now = counter["done"]
            if progress_callback:
                progress_callback(done_now, total)
            return
        err = _add_with_retry(store, chunk)
        if err:
            with counter_lock:
                errors.append((idx, err))
                fail_count_now = len(errors)
            # Gọi failed_callback TRƯỚC error_callback — error_callback đọc
            # state["failed"] để render, cần update count xong rồi mới render.
            if failed_callback:
                failed_callback(fail_count_now, total)
            if error_callback:
                error_callback(idx, err)
        # Atomic counter update + progress callback
        with counter_lock:
            counter["done"] += 1
            done_now = counter["done"]
        if progress_callback:
            progress_callback(done_now, total)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        initializer=thread_initializer,
    ) as pool:
        list(pool.map(_process_one, enumerate(chunks)))

    if errors:
        # Không raise — partial success vẫn có giá trị. Caller có thể log.
        print(f"[build_graph] {len(errors)}/{total} chunks failed after {MAX_RETRIES} retries:")
        for idx, err in errors[:5]:
            print(f"  - chunk {idx}: {err}")
        if failed_callback:
            failed_callback(len(errors), total)

    return store
