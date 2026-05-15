"""Wrapper truy vấn knowledge graph trong MongoDB Atlas.

Hỗ trợ 2 mode:

  1. **Graph-only** (mặc định): chỉ dùng extract_entity_names (LLM extract entity
     tên từ câu hỏi) → match exact `_id` trong graph → $graphLookup traversal.
     Nhanh, rẻ — nhưng yếu khi tên entity trong câu hỏi không khớp `_id`.

  2. **Hybrid (Vector + Graph)**: kết hợp:
       - extract_entity_names (exact match)
       - vector_search_entities (semantic match — giải quyết "Tuyen" ≈ "Pham Tuyen")
     → gộp anchor entities → $graphLookup traversal.
     Mode này tự kích hoạt khi collection đã có embeddings + vector index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from langchain_mongodb.graphrag.graph import MongoDBGraphStore

from .config import Config
from .entity_embedder import VECTOR_INDEX_NAME, vector_search_entities
from .graph_builder import make_graph_store, make_query_model


# Giới hạn số entity gửi cho LLM trong RAG prompt — tránh blow context.
# Tăng lên 80 vì graph dày (CV/audit có 90+ entities). Anchors phải luôn
# được giữ — xem _sort_for_context() để hiểu thứ tự ưu tiên.
MAX_ENTITIES_IN_CONTEXT = 80


def _diversify_truncate(
    entities: list[dict], max_n: int, anchors: list[str]
) -> list[dict]:
    """Cap entities về `max_n`, ưu tiên đa dạng `type` để LLM có context rộng.

    Vấn đề: nếu chỉ sort theo priority rồi cap đầu list → có thể 80 slot toàn
    cùng 1 type (vd "Concept"), miss các Organization/Control quan trọng.

    Strategy:
      1. Anchor entities LUÔN giữ (priority cao nhất)
      2. Non-anchor: round-robin theo type (mỗi type ≥ 1 slot)
      3. Sau khi mỗi type có 1 entity, fill thêm theo priority cũ
    """
    anchor_set = set(anchors)
    anchor_ents = [e for e in entities if e.get("_id", "") in anchor_set]
    rest = [e for e in entities if e.get("_id", "") not in anchor_set]

    selected = list(anchor_ents[:max_n])
    slots = max_n - len(selected)
    if slots <= 0:
        return selected

    # Group non-anchors theo type, giữ thứ tự priority trong mỗi group
    by_type: dict[str, list[dict]] = {}
    for e in rest:
        by_type.setdefault(e.get("type") or "(no_type)", []).append(e)

    # Round-robin pick: mỗi vòng lấy 1 entity / type
    while slots > 0 and any(by_type.values()):
        for tk in list(by_type.keys()):
            if not by_type[tk]:
                continue
            selected.append(by_type[tk].pop(0))
            slots -= 1
            if slots == 0:
                break

    return selected


def _sort_for_context(entities: list[dict], anchors: list[str]) -> list[dict]:
    """Sắp entities theo độ ưu tiên cho LLM context window:
      1. Anchor entities (do user/vector search trỏ vào) — quan trọng nhất,
         carry edge attributes có thông tin temporal/relationship.
      2. depth=0 entities (original từ $graphLookup, không bị traverse expand)
      3. depth=1 (neighbors trực tiếp của anchors)
      4. depth=2+ (xa hơn)

    Bug fix: trước đây `related[:N]` cắt mất anchor entities vì $graphLookup
    trả về order không deterministic → LLM không có thông tin từ anchor docs.
    """
    anchor_set = set(anchors)

    def priority(ent: dict) -> tuple:
        eid = ent.get("_id", "")
        depth = ent.get("depth")
        # Tier 1: chính là anchor (user/vector trỏ trực tiếp)
        if eid in anchor_set:
            return (0, 0)
        # Tier 2: depth=0 (original từ pipeline, không qua traversal)
        if depth is None:
            return (1, 0)
        # Tier 3+: sắp theo depth tăng dần
        return (2, depth)

    return sorted(entities, key=priority)


@dataclass
class QueryResult:
    """Bao bọc câu trả lời + danh sách entity được sử dụng làm context."""

    answer: str
    related_entities: list[str]
    anchor_entities: list[str]
    used_vector_search: bool


class GraphRAGQueryEngine:
    """Wrapper truy vấn với hybrid vector+graph fallback."""

    def __init__(self, cfg: Config, store: Optional[MongoDBGraphStore] = None):
        self._cfg = cfg
        # Query path → dùng query_model (CHẤT LƯỢNG) cho cả extract entity từ
        # câu hỏi + RAG response. Build path tạo store riêng với extraction_model.
        self._store = store or make_graph_store(cfg, chat_model=make_query_model(cfg))

    @property
    def store(self) -> MongoDBGraphStore:
        return self._store

    def _check_vector_index(self) -> bool:
        """Có Atlas Vector Search index trên collection không?

        KHÔNG cache kết quả ở instance — vì engine bị Streamlit cache_resource
        giữ lại qua nhiều rerun; nếu cache giá trị này, sau khi user tạo index
        sẽ vẫn báo False. Trade-off: 1 Mongo call mỗi ask() — chi phí không đáng kể.
        """
        try:
            indexes = list(self._store.collection.list_search_indexes())
            return any(idx.get("name") == VECTOR_INDEX_NAME for idx in indexes)
        except Exception:
            return False

    def _gather_anchor_entities(
        self, question: str, vector_k: int = 10
    ) -> tuple[list[str], bool]:
        """Thu thập danh sách anchor entities cho graph traversal.

        Returns:
            (anchor_ids, used_vector_search)
        """
        # Bước 1: extract tên entity từ câu hỏi (LLM call) — luôn chạy
        try:
            extracted = self._store.extract_entity_names(question) or []
        except Exception:
            extracted = []

        # Bước 2: nếu có vector index → mở rộng anchors qua semantic search
        used_vector = False
        if self._check_vector_index():
            try:
                semantic = vector_search_entities(
                    self._cfg,
                    self._store.collection.name,
                    question,
                    k=vector_k,
                )
                used_vector = bool(semantic)
                # Gộp 2 nguồn, giữ thứ tự, không duplicate
                seen = set()
                merged: list[str] = []
                for name in [*extracted, *semantic]:
                    if name and name not in seen:
                        seen.add(name)
                        merged.append(name)
                return merged, used_vector
            except Exception:
                pass

        return extracted, used_vector

    def ask(self, question: str) -> QueryResult:
        """Hỏi 1 câu, trả về câu trả lời + entities làm context.

        Hybrid mode tự động:
          - Có vector index → extract names + vector search → traverse
          - Không có → chỉ extract names → traverse (graph-only)
        """
        anchors, used_vector = self._gather_anchor_entities(question)

        related_ids: list[str] = []
        if anchors:
            try:
                related_docs = self._store.related_entities(anchors)
                related_ids = [
                    doc.get("_id", "") for doc in related_docs if doc.get("_id")
                ]
            except Exception:
                related_ids = []

        # LUÔN dùng custom anchors path nếu có anchors — để áp dụng:
        #   - Diversify truncation (đa dạng type, tránh dồn 1 type)
        #   - Strip embedding field (không blow context)
        #   - Sort priority anchor → depth=0 → depth=N
        # Chỉ fallback về built-in chat_response khi KHÔNG có anchor nào
        # (edge case: câu hỏi quá generic, extract_entity_names trả rỗng).
        if anchors:
            response = self._chat_with_custom_anchors(question, anchors)
        else:
            response = self._store.chat_response(question)

        answer_text = getattr(response, "content", str(response))
        return QueryResult(
            answer=answer_text,
            related_entities=related_ids,
            anchor_entities=anchors,
            used_vector_search=used_vector,
        )

    def _chat_with_custom_anchors(self, question: str, anchors: list[str]):
        """Gọi RAG prompt với context lấy từ anchors do hybrid retrieval cung cấp.

        Thay thế similarity_search() nội bộ vốn chỉ dùng extract_entity_names.
        Hai chỗ cẩn thận:
          1. Strip field `embedding` (1536 floats) — không cần cho LLM, sẽ blow context
          2. Giới hạn số entity gửi cho LLM để tránh quá context window
        """
        from langchain_mongodb.graphrag.prompts import rag_prompt

        related_entities = self._store.related_entities(anchors)
        # 1. Sort theo priority (anchor → depth=0 → depth=N) — anchors luôn đầu
        sorted_entities = _sort_for_context(related_entities, anchors)
        # 2. Diversify truncate — đảm bảo entities phủ nhiều type
        #    (tránh case 80 slot toàn cùng 1 type, miss anchors khác type)
        diversified = _diversify_truncate(
            sorted_entities, MAX_ENTITIES_IN_CONTEXT, anchors
        )
        # 3. Strip embedding field — không cần cho LLM, tránh blow context
        cleaned = []
        for ent in diversified:
            ent_copy = {k: v for k, v in ent.items() if k != "embedding"}
            cleaned.append(ent_copy)

        chain = rag_prompt | self._store.entity_extraction_model
        return chain.invoke(
            dict(
                query=question,
                related_entities=cleaned,
                entity_schema=self._store.entity_schema,
            )
        )
