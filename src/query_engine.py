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

import json
import re
from dataclasses import dataclass
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_mongodb.graphrag.graph import MongoDBGraphStore

from .config import Config
from .entity_embedder import VECTOR_INDEX_NAME, vector_search_entities
from .graph_builder import make_extraction_model, make_graph_store, make_query_model


_AGGREGATION_DETECT_PROMPT = """Determine if the question asks to COUNT or ENUMERATE entities by category/type in a knowledge graph. Works for any language — translate intent if needed.

Examples:
- "How many Controls are there?" → {{"is_count": true, "category": "Control"}}
- "List all subservice organizations" → {{"is_count": true, "category": "subservice organization"}}
- "Name every Control Objective" → {{"is_count": true, "category": "Control Objective"}}
- "What types of policies exist?" → {{"is_count": true, "category": "Policy"}}
- "Who audited the report?" → {{"is_count": false, "category": null}}
- "What is CC6.1?" → {{"is_count": false, "category": null}}
- "Explain MFA" → {{"is_count": false, "category": null}}

Return ONLY valid JSON with keys "is_count" (bool) and "category" (string or null). No prose, no markdown fence.

Question: {question}"""


# Giới hạn số entity gửi cho LLM trong RAG prompt — tránh blow context.
# Tăng lên 80 vì graph dày (CV/audit có 90+ entities). Anchors phải luôn
# được giữ — xem _sort_for_context() để hiểu thứ tự ưu tiên.
MAX_ENTITIES_IN_CONTEXT = 100


# Hint chèn trước câu hỏi của user khi gọi RAG — fix lỗi LLM đảo chiều
# quan hệ trong context SOC 2/audit. Vd: `Service --provided_by--> Okta`
# có nghĩa Okta CUNG CẤP service, KHÔNG phải service cung cấp Okta.
# Đây là pattern thường gặp với subservice orgs (Azure, Snowflake, Okta).
_DIRECTION_HINT = (
    "IMPORTANT — When interpreting relationships, follow edge DIRECTION strictly. "
    "Example: `A --provided_by--> B` means B provides to A (NOT A provides to B). "
    "Subservice Organizations (Azure, Snowflake, Okta, WorkOS, etc.) are "
    "EXTERNAL providers to OpenAI, NOT internal services of OpenAI. "
    "When asked 'who provides X', identify the entity at the destination of "
    "`provided_by` / `provides_service_to` / `hosts` edges.\n\n"
    "User question: "
)


def _centrality(ent: dict) -> int:
    """Đếm số outgoing relationships — proxy cho 'độ quan trọng' của entity."""
    rels = ent.get("relationships") or {}
    if isinstance(rels, dict):
        return len(rels.get("target_ids", []))
    return 0


def _diversify_truncate(
    entities: list[dict], max_n: int, anchors: list[str]
) -> list[dict]:
    """Cap entities về `max_n`, hybrid hub + type diversity.

    Vấn đề: graph có thể có 200+ distinct types với 80 slots — pure round-robin
    không kịp pick các hub entities ở types late.

    Strategy 3-pass:
      1. Anchor entities LUÔN giữ
      2. Top 1/3 slots: hub entities by centrality desc — bắt buộc các Provider
         lớn (Okta, Azure, Snowflake) vào context bất kể type
      3. Còn lại: round-robin theo type, within-type sort by centrality
    """
    anchor_set = set(anchors)
    anchor_ents = [e for e in entities if e.get("_id", "") in anchor_set]
    rest = [e for e in entities if e.get("_id", "") not in anchor_set]

    selected = list(anchor_ents[:max_n])
    slots = max_n - len(selected)
    if slots <= 0:
        return selected

    # Pass 2: top hubs by centrality (1/2 remaining slots) — đảm bảo
    # các Provider/Major actor không bị bỏ rơi vì round-robin không kịp
    # khi graph có hàng trăm distinct types.
    rest_sorted = sorted(rest, key=_centrality, reverse=True)
    hub_quota = max(1, slots // 2)
    hub_picked = rest_sorted[:hub_quota]
    hub_ids = {e.get("_id") for e in hub_picked}
    selected.extend(hub_picked)
    slots -= len(hub_picked)
    if slots <= 0:
        return selected

    # Pass 3: round-robin theo type cho slots còn lại, within-type sort by centrality
    remaining = [e for e in rest if e.get("_id") not in hub_ids]
    by_type: dict[str, list[dict]] = {}
    for e in remaining:
        by_type.setdefault(e.get("type") or "(no_type)", []).append(e)
    for t in by_type:
        by_type[t].sort(key=_centrality, reverse=True)

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

    def _expand_anchors_with_neighbors(
        self, anchors: list[str], max_extra: int = 8
    ) -> list[str]:
        """Mở rộng anchors qua 1 hop graph để bắt thêm entity QUAN TRỌNG.

        Vấn đề: vector search + extract_entity_names ưu tiên entity match
        keyword câu hỏi (Controls/Concepts), bỏ sót hub entities (Org providers,
        major actors) thường là câu trả lời cho "ai cung cấp / ai làm X".

        Fix type-agnostic: lấy neighbors qua 1 hop, sort theo `centrality`
        (số relationships) descending, lấy top N. Hub neighbors thường là
        các actor/provider chính bất kể domain (audit, CV, contract, medical...).

        Vd:
          - Audit doc: top hub = Schellman, OpenAI Inc., HITRUST, Azure, Okta
          - CV: top hub = Pham Tuyen, Veek, AIAIVN
          - Contract: top hub = Party A, Party B, governing-law entity
        → Generic cho mọi loại tài liệu.
        """
        if not anchors:
            return anchors

        try:
            neighbors = self._store.related_entities(anchors)
        except Exception:
            return anchors

        seen = set(anchors)
        scored: list[tuple[int, str]] = []
        for n in neighbors:
            nid = n.get("_id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            # Centrality = số outgoing relationships của neighbor
            rels = n.get("relationships") or {}
            targets = rels.get("target_ids", []) if isinstance(rels, dict) else []
            scored.append((len(targets), nid))

        # Sort theo centrality desc → hub entities lên đầu
        scored.sort(key=lambda x: x[0], reverse=True)
        return anchors + [nid for _, nid in scored[:max_extra]]

    def ask(self, question: str) -> QueryResult:
        """Hỏi 1 câu, trả về câu trả lời + entities làm context.

        Hybrid mode tự động:
          - Có vector index → extract names + vector search → traverse
          - Không có → chỉ extract names → traverse (graph-only)
        """
        anchors, used_vector = self._gather_anchor_entities(question)
        # Multi-hop expansion: bắt thêm Organization providers thường bị
        # vector search miss khi câu hỏi nói về "ai cung cấp X"
        anchors = self._expand_anchors_with_neighbors(anchors)

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

    def _detect_aggregation_category(self, question: str) -> Optional[str]:
        """LLM-based detect câu hỏi đếm/liệt kê → trả category, None nếu không.

        Dùng extraction_model (gpt-5-mini) thay query_model để tiết kiệm cost.
        Language-agnostic: hiểu mọi phrasing thay vì regex hard-code.
        """
        try:
            prompt = ChatPromptTemplate.from_template(_AGGREGATION_DETECT_PROMPT)
            detector = make_extraction_model(self._cfg)
            result = (prompt | detector).invoke({"question": question})
            text = getattr(result, "content", str(result)).strip()
            # Strip markdown fence nếu LLM trả ```json ... ```
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            data = json.loads(text)
            if data.get("is_count") and data.get("category"):
                cat = str(data["category"]).strip()
                if len(cat) >= 3:
                    return cat
        except Exception:
            pass
        return None

    def _enrich_query(self, question: str) -> str:
        """Augment query với direction hint + aggregation data (nếu có).

        Đối với counting/listing question: fetch ALL entities matching type
        từ MongoDB trực tiếp (không qua vector/graph) → inject vào prompt
        để LLM trả lời số chính xác. Fix RAG counting weakness.
        """
        prefix = _DIRECTION_HINT
        category = self._detect_aggregation_category(question)
        if category:
            try:
                regex = re.escape(category)
                # Pass 1: thử match `type` field — strict, ưu tiên.
                type_matches = list(
                    self._store.collection.find(
                        {"type": {"$regex": regex, "$options": "i"}},
                        {"_id": 1, "type": 1},
                    ).limit(200)
                )
                # Pass 2: fallback match `_id` chỉ KHI không có type match —
                # case user hỏi về identifier pattern (vd "CC6.x", "P6.x").
                if type_matches:
                    matching = type_matches
                else:
                    matching = list(
                        self._store.collection.find(
                            {"_id": {"$regex": regex, "$options": "i"}},
                            {"_id": 1, "type": 1},
                        ).limit(200)
                    )
            except Exception:
                matching = []
            if matching:
                listing = "\n".join(
                    f"  {i + 1}. {m['_id']} (type: {m.get('type', '?')})"
                    for i, m in enumerate(matching)
                )
                prefix += (
                    f"AGGREGATION CONTEXT — direct database enumeration:\n"
                    f"Exactly {len(matching)} entities match '{category}' "
                    f"in the knowledge graph:\n{listing}\n\n"
                    f"Use this exact count/list when answering. "
                    f"Do NOT rely on context entities below for counting "
                    f"(they are filtered for relevance, not completeness).\n\n"
                )
        return prefix + question

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
                query=self._enrich_query(question),
                related_entities=cleaned,
                entity_schema=self._store.entity_schema,
            )
        )
