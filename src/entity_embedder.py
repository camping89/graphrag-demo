"""Hybrid Vector + Graph search — module xử lý embeddings cho entities.

Lý do tồn tại: GraphRAG yếu khi tên entity trong câu hỏi không match exact
với `_id` trong graph (VD: hỏi "Tuyen" trong khi graph có "Pham Tuyen").
Vector search semantic giải quyết issue này bằng cách so sánh ý nghĩa.

Workflow:
  1. backfill_embeddings()  → tính embedding cho từng entity, lưu vào field `embedding`
  2. ensure_vector_index()  → tạo Atlas Vector Search index trên field này
  3. vector_search_entities() → query bằng `$vectorSearch` → trả về top-k entity _ids

Sau đó query_engine.py kết hợp với extract_entity_names() để mở rộng anchor.

Tham khảo:
  - Atlas Vector Search: https://www.mongodb.com/docs/atlas/atlas-vector-search/
  - pymongo create_search_index: https://pymongo.readthedocs.io/
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from langchain_openai import OpenAIEmbeddings
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from .config import Config


VECTOR_INDEX_NAME = "entity_vector_index"
EMBEDDING_FIELD = "embedding"
# text-embedding-3-small trả về vector 1536 dim
EMBEDDING_DIM = 1536


# Callback signature: (processed, total) → None — báo tiến độ cho UI
ProgressCallback = Callable[[int, int], None]


def make_embedder(cfg: Config) -> OpenAIEmbeddings:
    """Khởi tạo embeddings client dùng chung cho backfill + query."""
    return OpenAIEmbeddings(
        model=cfg.embedding_model,
        api_key=cfg.openai_api_key,
    )


def entity_to_text(entity: dict[str, Any]) -> str:
    """Convert entity document → text để embed.

    Gồm:
      - _id (tên entity, quan trọng nhất)
      - type (Person, Organization, ...)
      - attributes (nội dung)
      - target_ids (entity liên kết — giúp embed mang ngữ cảnh quan hệ)
    """
    parts = [f"Name: {entity.get('_id', '')}"]
    if entity.get("type"):
        parts.append(f"Type: {entity['type']}")

    attrs = entity.get("attributes") or {}
    if attrs:
        # attrs có format {field: [values]} → flatten
        flat = {k: v[0] if isinstance(v, list) and len(v) == 1 else v
                for k, v in attrs.items()}
        parts.append(f"Attributes: {json.dumps(flat, ensure_ascii=False)}")

    rels = entity.get("relationships") or {}
    targets = rels.get("target_ids", []) if isinstance(rels, dict) else []
    if targets:
        parts.append(f"Related to: {', '.join(str(t) for t in targets[:20])}")

    return "\n".join(parts)


def backfill_embeddings(
    cfg: Config,
    collection_name: str,
    progress_callback: Optional[ProgressCallback] = None,
    force: bool = False,
) -> int:
    """Tính + lưu embedding cho mọi entity trong collection.

    Args:
        force: nếu False, skip entities đã có embedding để tiết kiệm cost.
               Đặt True khi muốn re-compute toàn bộ.

    Returns:
        Số entities đã được update.
    """
    embedder = make_embedder(cfg)
    client = MongoClient(cfg.mongodb_uri)
    try:
        coll = client[cfg.mongodb_db][collection_name]

        query = {} if force else {EMBEDDING_FIELD: {"$exists": False}}
        entities = list(coll.find(query))
        total = len(entities)
        if total == 0:
            return 0

        updated = 0
        for idx, entity in enumerate(entities, start=1):
            text = entity_to_text(entity)
            vector = embedder.embed_query(text)
            coll.update_one(
                {"_id": entity["_id"]},
                {"$set": {EMBEDDING_FIELD: vector}},
            )
            updated += 1
            if progress_callback:
                progress_callback(idx, total)

        return updated
    finally:
        client.close()


def ensure_vector_index(cfg: Config, collection_name: str) -> str:
    """Tạo Atlas Vector Search index nếu chưa có. Idempotent.

    Returns:
        Tên index. Raise nếu cluster không hỗ trợ vector search.
    """
    client = MongoClient(cfg.mongodb_uri)
    try:
        coll = client[cfg.mongodb_db][collection_name]

        # Check existing search indexes
        try:
            existing = list(coll.list_search_indexes())
        except OperationFailure as exc:
            raise RuntimeError(
                "Cluster không hỗ trợ Atlas Search/Vector Search. "
                "Cần Atlas M0+ với Search enabled. Chi tiết: " + str(exc)
            )

        for idx in existing:
            if idx.get("name") == VECTOR_INDEX_NAME:
                return VECTOR_INDEX_NAME

        # Tạo mới — schema theo format Atlas Vector Search hiện hành
        coll.create_search_index(
            {
                "name": VECTOR_INDEX_NAME,
                "type": "vectorSearch",
                "definition": {
                    "fields": [
                        {
                            "type": "vector",
                            "path": EMBEDDING_FIELD,
                            "numDimensions": EMBEDDING_DIM,
                            "similarity": "cosine",
                        }
                    ]
                },
            }
        )

        # Index cần vài giây để Atlas finish building — đợi (best-effort)
        for _ in range(30):
            indexes = list(coll.list_search_indexes())
            target = next((i for i in indexes if i.get("name") == VECTOR_INDEX_NAME), None)
            if target and target.get("status") in ("READY", "STEADY"):
                break
            time.sleep(2)

        return VECTOR_INDEX_NAME
    finally:
        client.close()


def vector_search_entities(
    cfg: Config,
    collection_name: str,
    query_text: str,
    k: int = 5,
    num_candidates: int = 50,
) -> list[str]:
    """Trả về top-k entity _ids tương đồng ngữ nghĩa với query_text.

    Dùng $vectorSearch aggregation stage của MongoDB Atlas.
    """
    embedder = make_embedder(cfg)
    query_vector = embedder.embed_query(query_text)

    client = MongoClient(cfg.mongodb_uri)
    try:
        coll = client[cfg.mongodb_db][collection_name]
        pipeline = [
            {
                "$vectorSearch": {
                    "index": VECTOR_INDEX_NAME,
                    "path": EMBEDDING_FIELD,
                    "queryVector": query_vector,
                    "numCandidates": num_candidates,
                    "limit": k,
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        results = list(coll.aggregate(pipeline))
        return [str(r["_id"]) for r in results]
    finally:
        client.close()
