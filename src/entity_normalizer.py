"""Entity normalization — merge duplicates với canonical name.

Vấn đề: LLM extract qua nhiều chunks → cùng concept với tên khác nhau:
  - `Document Section` vs `DocumentSection`
  - `User Group` vs `UserGroup`
  - `Control Category` vs `ControlCategory`
  → 2-3 rows riêng biệt trong graph, làm yếu traversal.

Giải pháp: post-build normalize step
  1. Group entities cùng `type`
  2. Tính canonical_key = lower(strip_space(_id)) — match insensitive case + space
  3. Trong mỗi group: chọn winner (nhiều attrs/rels nhất)
  4. Merge attrs (union dict), redirect relationships (target_ids), delete losers
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from pymongo import MongoClient

from .config import Config


@dataclass
class MergePlan:
    """Plan merge cho 1 group entities cùng canonical."""

    canonical_id: str       # winner — giữ lại
    losers: list[str]       # bị delete + redirect
    entity_type: str
    total_attrs: int        # tổng attr count (preview)
    total_rels: int         # tổng rel count (preview)


def _canonical_key(name: str) -> str:
    """Chuẩn hoá tên về key so sánh: lower + remove non-alnum.

    Ví dụ:
      "Document Section" → "documentsection"
      "DocumentSection"  → "documentsection"  ✓ match
      "User Group"       → "usergroup"
      "User-Group"       → "usergroup"        ✓ match
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _score(entity: dict) -> tuple[int, int, int]:
    """Score cho winner pick: (n_attrs, n_rels, -len(_id)).

    Ưu tiên entity nhiều attrs/rels nhất.
    Tiebreak: tên ngắn hơn (thường canonical hơn).
    """
    attrs = entity.get("attributes") or {}
    rels = entity.get("relationships") or {}
    targets = rels.get("target_ids", []) if isinstance(rels, dict) else []
    return (
        len(attrs) if isinstance(attrs, dict) else 0,
        len(targets),
        -len(entity.get("_id", "")),
    )


def find_merge_candidates(
    cfg: Config, collection_name: str
) -> list[MergePlan]:
    """Quét collection tìm các group duplicate có thể merge.

    Returns: list MergePlan, mỗi cái = 1 group ≥ 2 entities cùng canonical key.
    """
    client = MongoClient(cfg.mongodb_uri)
    coll = client[cfg.mongodb_db][collection_name]

    # Group theo (type, canonical_key) — chỉ merge trong cùng type
    groups: dict[tuple[str, str], list[dict]] = {}
    for ent in coll.find({}, {"embedding": 0}):
        etype = ent.get("type") or "(no_type)"
        key = _canonical_key(ent.get("_id", ""))
        if not key:
            continue
        groups.setdefault((etype, key), []).append(ent)

    plans: list[MergePlan] = []
    for (etype, _key), members in groups.items():
        if len(members) < 2:
            continue
        # Bỏ qua nếu tất cả members có _id giống hệt nhau (không cần merge)
        ids = {m["_id"] for m in members}
        if len(ids) < 2:
            continue

        # Sort theo score desc — winner = max score
        members.sort(key=_score, reverse=True)
        winner = members[0]
        losers = [m["_id"] for m in members[1:]]

        # Tổng attrs/rels (sau merge) để preview
        total_attrs = sum(
            len(m.get("attributes") or {}) if isinstance(m.get("attributes"), dict) else 0
            for m in members
        )
        total_rels = sum(
            len((m.get("relationships") or {}).get("target_ids", []))
            for m in members
        )

        plans.append(MergePlan(
            canonical_id=winner["_id"],
            losers=losers,
            entity_type=etype,
            total_attrs=total_attrs,
            total_rels=total_rels,
        ))

    client.close()
    return sorted(plans, key=lambda p: (-len(p.losers), p.entity_type))


def apply_merge_plans(
    cfg: Config,
    collection_name: str,
    plans: list[MergePlan],
    progress_callback: Optional[callable] = None,
) -> dict:
    """Thực thi merge: cho mỗi plan, gộp attrs + relationships → xoá losers.

    Returns: {"merged_groups": N, "deleted_entities": M, "redirected_refs": K}
    """
    client = MongoClient(cfg.mongodb_uri)
    coll = client[cfg.mongodb_db][collection_name]

    deleted = 0
    redirected = 0

    for idx, plan in enumerate(plans, start=1):
        if progress_callback:
            progress_callback(idx, len(plans))

        # 1. Load winner + losers
        winner = coll.find_one({"_id": plan.canonical_id})
        if winner is None:
            continue
        loser_docs = list(coll.find({"_id": {"$in": plan.losers}}))

        # 2. Merge attributes (union — winner wins khi conflict key)
        merged_attrs = dict(winner.get("attributes") or {})
        for loser in loser_docs:
            for k, v in (loser.get("attributes") or {}).items():
                if k not in merged_attrs:
                    merged_attrs[k] = v
                elif isinstance(merged_attrs[k], list) and isinstance(v, list):
                    # Union list values + dedupe
                    seen = set(map(str, merged_attrs[k]))
                    merged_attrs[k].extend(x for x in v if str(x) not in seen)

        # 3. Merge relationships — concat then dedupe theo (type, target_id) pair
        winner_rels = winner.get("relationships") or {}
        w_targets = list(winner_rels.get("target_ids", []))
        w_types = list(winner_rels.get("types", []))
        w_attrs = list(winner_rels.get("attributes", []))
        for loser in loser_docs:
            lr = loser.get("relationships") or {}
            for t, tgt, atr in zip(
                lr.get("types", []),
                lr.get("target_ids", []),
                lr.get("attributes", []) or [{} for _ in lr.get("types", [])],
            ):
                w_targets.append(tgt)
                w_types.append(t)
                w_attrs.append(atr)

        # Dedupe theo (type, target) pair — keep first attr
        seen_pairs: set[tuple[str, str]] = set()
        new_targets, new_types, new_attrs = [], [], []
        for t, tgt, atr in zip(w_types, w_targets, w_attrs):
            key = (str(t), str(tgt))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            new_types.append(t)
            new_targets.append(tgt)
            new_attrs.append(atr)

        # 4. Update winner với merged data
        coll.update_one(
            {"_id": plan.canonical_id},
            {"$set": {
                "attributes": merged_attrs,
                "relationships": {
                    "types": new_types,
                    "target_ids": new_targets,
                    "attributes": new_attrs,
                },
            }},
        )

        # 5. Redirect refs: mọi entity nào trỏ tới losers → đổi sang canonical
        for loser_id in plan.losers:
            result = coll.update_many(
                {"relationships.target_ids": loser_id},
                {"$set": {"relationships.target_ids.$[elem]": plan.canonical_id}},
                array_filters=[{"elem": loser_id}],
            )
            redirected += result.modified_count

        # 6. Xoá losers
        del_result = coll.delete_many({"_id": {"$in": plan.losers}})
        deleted += del_result.deleted_count

    client.close()
    return {
        "merged_groups": len(plans),
        "deleted_entities": deleted,
        "redirected_refs": redirected,
    }
