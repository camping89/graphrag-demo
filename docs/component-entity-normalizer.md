# Component: Entity Normalizer

> File: `src/entity_normalizer.py` (~218 lines)
> Vai trò: Merge duplicate entities trong knowledge graph sau khi build.
> Cùng concept với tên khác nhau (`Document Section` vs `DocumentSection`)
> được gộp về 1 canonical entity, bảo toàn attrs + relationships.

## 🎯 Vấn đề

LLM extract qua N chunks độc lập → cùng 1 concept có thể được đặt tên khác
nhau ở mỗi chunk:

| Variant 1                     | Variant 2                     | Variant 3                     |
|-------------------------------|-------------------------------|-------------------------------|
| `Information Security Policy` | `Information security policy` | `information security policy` |
| `Document Section`            | `DocumentSection`             | `document section`            |
| `User Group`                  | `UserGroup`                   | `user-group`                  |

Mỗi variant → 1 MongoDB document riêng, mỗi cái mang ~20 attrs + ~18 rels.
Khi query hit 1 trong 3 → LLM chỉ thấy 1 phần data.

**Sau normalize**: 1 entity duy nhất với 62 attrs + 55 rels (gộp đầy đủ).

## 🧩 2 hàm public

### `find_merge_candidates(cfg, collection_name) -> list[MergePlan]`

Scan collection → tìm groups duplicate.

**Algorithm**:
1. Group theo `(type, canonical_key)` — chỉ merge trong cùng `type`
2. `canonical_key = re.sub(r"[^a-z0-9]", "", name.lower())` — match insensitive case + space + dấu gạch
3. Group ≥ 2 entities → tạo `MergePlan`
4. Winner = entity có score cao nhất: `(n_attrs, n_rels, -len(_id))`
   - Nhiều attrs/rels nhất; tiebreak tên ngắn hơn

**Returns** `list[MergePlan]` sorted theo `(-len(losers), entity_type)` —
group nhiều duplicate nhất lên đầu.

### `apply_merge_plans(cfg, collection_name, plans, progress_callback) -> dict`

Thực thi merge plans. Cho mỗi plan:

1. **Merge attributes** (union, winner thắng khi key conflict; list values dedupe theo `str()`)
2. **Merge relationships** — concat winner + losers, dedupe theo `(type, target_id)` pair, keep first attr
3. **Update winner** với merged data
4. **Redirect refs** — mọi entity trỏ tới loser → đổi thành canonical (qua `array_filters`)
5. **Delete losers**

**Returns** `{"merged_groups": N, "deleted_entities": M, "redirected_refs": K}`

## 🏛️ Data class

```python
@dataclass
class MergePlan:
    canonical_id: str       # winner — giữ lại
    losers: list[str]       # bị delete + redirect
    entity_type: str
    total_attrs: int        # tổng attr count (preview)
    total_rels: int         # tổng rel count (preview)
```

## 🔍 Canonical key examples

```python
_canonical_key("Document Section")  # → "documentsection"
_canonical_key("DocumentSection")   # → "documentsection"  ✓ match
_canonical_key("document_section")  # → "documentsection"  ✓ match
_canonical_key("Document-Section")  # → "documentsection"  ✓ match
_canonical_key("Documents Section") # → "documentssection" ✗ no match (plural khác)
```

> **Lưu ý**: Plural vs singular KHÔNG match (vd "Policy" vs "Policies"). Đây là
> trade-off có chủ đích — tránh merge nhầm 2 concept khác nhau chỉ vì cùng gốc từ.

## 🚦 Lifecycle integration

### Tự động (UI)
`tab_build.py` gọi `find_merge_candidates` + `apply_merge_plans` **ngay sau khi build xong**,
trước khi backfill embeddings.

→ User không cần thao tác gì thêm. Build → normalize → embed = 1 flow liền mạch.

### Manual CLI
```powershell
# Dry-run preview (mặc định)
python scripts/normalize-collection.py --collection openai_2025_soc_2_type_2_report

# Apply merge
python scripts/normalize-collection.py --collection openai_2025_soc_2_type_2_report --apply

# Tăng top N preview
python scripts/normalize-collection.py --collection my_kb --top 50
```

## ⚠️ Hậu quả phụ — phải re-embed

Sau khi merge: attrs + rels của winner đổi → `entity_to_text()` → embedding stale.

→ Chạy `scripts/rebuild-embeddings.py --collection X --force` để re-compute,
hoặc UI tự gọi `backfill_embeddings(force=True)` sau khi normalize.

## 📊 Performance characteristics

| Operation                                | Cost                           | Time (collection 2000 entities) |
|------------------------------------------|--------------------------------|---------------------------------|
| `find_merge_candidates`                  | 1 MongoDB scan + Python group  | < 2s                            |
| `apply_merge_plans` (200 groups)         | 4 ops/group × 200 = 800 writes | ~5-10s                          |
| **Total normalize**                      |                                | **~10-15s**                     |
| `rebuild-embeddings --force` (2000 ents) | 2000 LLM embedding calls       | ~15 phút                        |

## 🔗 Tương tác với component khác

| Caller                                            | Khi nào gọi                                   |
|---------------------------------------------------|-----------------------------------------------|
| `ui/tab_build.py`                                 | Sau khi build xong, trước backfill embeddings |
| `scripts/normalize-collection.py`                 | User chạy thủ công                            |
| `entity_embedder.backfill_embeddings(force=True)` | Sau normalize để re-embed                     |

## 🚫 Khi nào KHÔNG nên normalize

- Collection mới build chỉ vài chục entities → ít duplicate, ROI thấp
- Bạn chủ đích muốn giữ variants riêng (vd "Customer V1" vs "Customer V2" là 2 entity thật)
- Sau khi đã normalize 1 lần → run lại sẽ no-op (idempotent), nhưng tốn scan time

## 📚 References

- `src/entity_normalizer.py:38-47` — `_canonical_key`
- `src/entity_normalizer.py:50-63` — `_score` (winner pick logic)
- `src/entity_normalizer.py:121-217` — `apply_merge_plans`
- `scripts/normalize-collection.py` — CLI wrapper

## 🔗 Linked components

- [Entity Extraction](component-entity-extraction.md) — sinh ra entities cần normalize
- [Vector Embedding](component-vector-embedding.md) — re-embed sau normalize
- [Query Engine](component-query-engine.md) — hưởng lợi (1 entity gộp = 1 hit đầy đủ)
- [Web UI](component-web-ui.md) — auto-normalize trong tab Build
- [Pipeline Overview](pipeline-overview.md)
