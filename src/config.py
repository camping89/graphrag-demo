"""Tải và validate biến môi trường cho demo GraphRAG.

Sử dụng python-dotenv để đọc file .env trong project root. Tất cả các
module khác lấy config qua hàm load_config() ở đây — tránh lặp logic
đọc env và tránh hard-code giá trị.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    """Container chứa toàn bộ cấu hình runtime.

    Hai LLM tách biệt theo workload:
      - extraction_model: gọi cho mỗi chunk khi build graph + analyze
        document context. Cần NHANH + RẺ + TPM cao (chạy 5-10 song song)
        → gpt-4o / gpt-4.1 phù hợp.
      - query_model: gọi 1 lần cho mỗi câu hỏi chat. Cần CHẤT LƯỢNG cao
        cho reasoning + tổng hợp context → gpt-5 phù hợp.
    """

    mongodb_uri: str
    mongodb_db: str
    mongodb_collection: str
    openai_api_key: str
    extraction_model: str
    query_model: str
    embedding_model: str

    @property
    def chat_model(self) -> str:
        """Backward compat — code cũ dùng chat_model thì trả query_model."""
        return self.query_model


def _require(name: str) -> str:
    """Đọc env var bắt buộc, raise nếu thiếu."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Thiếu biến môi trường bắt buộc: {name}. "
            f"Hãy copy .env.example sang .env và điền giá trị."
        )
    return value


def load_config() -> Config:
    """Tải cấu hình từ .env và env vars hệ thống.

    Migration: nếu chỉ có `OPENAI_CHAT_MODEL` (single model legacy) thì
    dùng cho cả 2 vai trò để tương thích ngược.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    # Defaults theo OpenAI API model names (cập nhật 2026-05-15):
    #   - gpt-4o, gpt-4.1 family: RETIRED 13/02/2026.
    #   - gpt-5 / gpt-5-mini / gpt-5-nano: still in API (legacy nhưng vẫn dùng).
    #   - gpt-5.1, gpt-5.4, gpt-5.5: mới hơn nhưng tuỳ tier mới có access.
    # Build (extraction) cần TPM cao + nhanh → gpt-5-mini.
    # Query cần chất lượng → gpt-5 (user thường có sẵn access).
    legacy_chat = os.getenv("OPENAI_CHAT_MODEL")  # fallback nếu chưa migrate
    extraction = os.getenv("OPENAI_EXTRACTION_MODEL") or legacy_chat or "gpt-5-mini"
    query = os.getenv("OPENAI_QUERY_MODEL") or legacy_chat or "gpt-5"

    return Config(
        mongodb_uri=_require("MONGODB_URI"),
        mongodb_db=os.getenv("MONGODB_DB", "graphrag_demo"),
        mongodb_collection=os.getenv("MONGODB_COLLECTION", "soc2_entities"),
        openai_api_key=_require("OPENAI_API_KEY"),
        extraction_model=extraction,
        query_model=query,
        embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
    )
