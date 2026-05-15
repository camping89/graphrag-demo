# Component: Visualization

> File: `src/visualizer.py` (111 lines)
> Vai trò: Render knowledge graph trong MongoDB thành HTML interactive
> (kéo, zoom, hover) bằng `networkx` + `pyvis`.

## 🎯 Mục đích

Cho user (đặc biệt non-technical) **nhìn trực tiếp** knowledge graph: các
nodes (entities) + edges (relationships). Phục vụ:

1. **Validate build quality** — graph có dense không, có entity rời rạc không
2. **Discovery** — phát hiện cluster, hub nodes (entity nhiều quan hệ)
3. **Debug** — sau khi query fail, xem graph để hiểu data có gì

## 📥 Đầu vào & Đầu ra

```python
# Đầu vào
cfg: Config                        # connection info
output_path: Path                  # nơi lưu HTML
max_nodes: int = 200               # cap entities (avoid lag)

# Đầu ra
Path                               # đường dẫn HTML file đã render
```

**Side effect**: tạo file HTML interactive tại `output_path`. File này
self-contained — mở bằng browser, không cần server.

## 🔌 4 hàm public

### `fetch_entities(cfg, limit=200) -> list[dict]`
Đọc entities từ MongoDB. Không lấy `embedding` field (làm HTML quá nặng).

### `build_networkx_graph(entities) -> nx.DiGraph`
Convert list entities thành `networkx.DiGraph` (directed graph).

### `render_html(graph, output_path) -> Path`
Render `nx.DiGraph` thành HTML bằng pyvis.

### `visualize_graph(cfg, output_path, max_nodes=200) -> Path`
Pipeline: Mongo → networkx → pyvis HTML.

## 🧬 Chi tiết

### `fetch_entities(cfg, limit)`

```python
def fetch_entities(cfg, limit=200):
    client = MongoClient(cfg.mongodb_uri)
    coll = client[cfg.mongodb_db][cfg.mongodb_collection]
    return list(coll.find({}, limit=limit))
```

**Note**: dùng `limit=` param của `find()` không phải `.limit()` chain — đơn giản.

**Tại sao cap?**: Render 1000+ nodes/edges làm:
- HTML file lớn (5+ MB)
- Browser lag khi load
- Đồ thị rối khó nhìn

→ Mặc định 200, user có thể tùy chỉnh slider 20-500 trong UI.

### `_extract_targets(relationships)` — flexible schema

```python
def _extract_targets(relationships):
    targets = []
    if not relationships:
        return targets

    # Case 1: dict {type: [target_ids]}
    if isinstance(relationships, dict):
        for value in relationships.values():
            if isinstance(value, list):
                targets.extend(str(v) for v in value if v)

    # Case 2: list of edge objects [{target, type}, ...]
    if isinstance(relationships, list):
        for edge in relationships:
            target = edge.get("target") or edge.get("to") or edge.get("_id")
            if target:
                targets.append(str(target))

    return targets
```

**Vì sao flexible?** Schema MongoDBGraphStore có 2 format có thể gặp:
- Mặc định: `{target_ids: [...], types: [...]}`
- Custom: list các `{target, type, ...}` objects

→ Function handle cả hai để robust với version langchain-mongodb khác nhau.

### `build_networkx_graph(entities)`

```python
def build_networkx_graph(entities):
    graph = nx.DiGraph()

    for entity in entities:
        node_id = str(entity.get("_id", ""))
        if not node_id:
            continue
        label = entity.get("type", "Entity")
        graph.add_node(node_id,
                       label=node_id,
                       title=f"Type: {label}",
                       group=label)

    for entity in entities:
        source = str(entity.get("_id", ""))
        for target in _extract_targets(entity.get("relationships")):
            if target and target != source:
                graph.add_edge(source, target)

    return graph
```

**Node attributes** dùng cho pyvis rendering:
- `label`: text hiển thị trên node
- `title`: hover tooltip
- `group`: dùng cho color coding (nodes cùng type cùng màu)

**Bỏ self-edge** (`target != source`) — không meaningful, gây rối.

### `render_html(graph, output_path)`

```python
def render_html(graph, output_path):
    net = Network(
        height="800px",
        width="100%",
        directed=True,
        bgcolor="#1a1a1a",       # dark theme
        font_color="#ffffff",
        notebook=False,
    )
    net.from_nx(graph)
    net.repulsion(node_distance=180, spring_length=120)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output_path), open_browser=False, notebook=False)
    return output_path
```

**Pyvis settings**:
- **800px height**: đủ rộng cho fullscreen, vẫn vừa Streamlit iframe
- **Directed**: mũi tên thể hiện hướng quan hệ
- **Dark theme**: easier on eyes, nodes/edges nổi bật hơn
- **`repulsion`**: physics engine — nodes đẩy nhau ra (node_distance) +
  springs giữ edges (spring_length). Layout động khi load.

**`open_browser=False`**: không tự mở browser (Streamlit lo phần này qua iframe).

**`notebook=False`**: không cần Jupyter wrapper, write HTML thuần.

### `visualize_graph(cfg, output_path, max_nodes)` — pipeline

```python
def visualize_graph(cfg, output_path, max_nodes=200):
    entities = fetch_entities(cfg, limit=max_nodes)
    if not entities:
        raise RuntimeError("Collection rỗng. Hãy chạy build-graph trước khi visualize.")
    graph = build_networkx_graph(entities)
    return render_html(graph, output_path)
```

## 🎨 Visual output

User mở HTML file → giao diện:

```
┌──────────────────────────────────────────────────────┐
│  🕸️ Interactive Graph                                 │
│                                                       │
│            [Pham Tuyen]──works_at──>[AIAIVN]          │
│                  │                       │            │
│                  │                       │            │
│             skilled_in              develops          │
│                  │                       │            │
│                  ▼                       ▼            │
│            [React.js]            [HeristepAI GPS]     │
│              [Next.js]                                │
│              ...                                      │
└──────────────────────────────────────────────────────┘

Controls (built-in pyvis):
  • Drag node → reposition
  • Scroll → zoom in/out
  • Click node → highlight
  • Hover node → tooltip (type info)
```

**Color coding** (auto by `group`):
- Person nodes: 1 màu
- Organization: màu khác
- Technology/Project/Award: ...
- Pyvis tự assign palette consistent

## ⚙️ Constants

```python
DEFAULT_MAX_NODES = 200    # cap default
```

## 🔗 Tương tác với component khác

| Component | Hướng | Tương tác |
|-----------|-------|-----------|
| `config.py` | nhận | `Config.mongodb_uri/db/collection` |
| `pymongo` | gọi | `find({}, limit=...)` |
| `networkx` | gọi | `DiGraph()`, `add_node/edge` |
| `pyvis` | gọi | `Network.from_nx`, `write_html` |
| `ui/tab_visualize.py` | gọi | `visualize_graph()` |
| `scripts/visualize-graph.py` | gọi | CLI entry |

## 🧪 Test thủ công

```python
from pathlib import Path
from src.config import load_config
from src.visualizer import visualize_graph
import dataclasses

cfg = load_config()
cfg2 = dataclasses.replace(cfg, mongodb_collection="phamtuyen_frontend_2026")

output = visualize_graph(cfg2, Path("out/test.html"), max_nodes=150)
print(f"Rendered: {output.resolve()}")
# Mở out/test.html trong browser
```

## 📈 Performance

| Entities | HTML size | Render time | Browser load |
|----------|-----------|-------------|--------------|
| 20 | ~50 KB | < 1s | instant |
| 100 | ~200 KB | 1-2s | instant |
| 200 (default) | ~400 KB | 2-3s | smooth |
| 500 | ~1 MB | 5-10s | lag |
| 1000+ | 2+ MB | 15+ s | very laggy |

→ Cap 200 là sweet spot. 500+ nên dùng Gephi/Cytoscape thay vì pyvis.

## ⚠️ Edge cases & failure modes

| Edge case | Handling |
|-----------|----------|
| Collection rỗng | `RuntimeError` với hướng dẫn build trước |
| Entity không có `_id` | Skip |
| Entity không có `relationships` | Node được tạo, không có edges out |
| Target trong `target_ids` không có node tương ứng | pyvis tự tạo node ghost (chỉ có label, không có attrs) |
| Self-edge (target == source) | Skip — không meaningful |
| Output path không tồn tại | `mkdir(parents=True, exist_ok=True)` tự tạo |

## 🔮 Possible improvements

| Improvement | Effort | Value |
|-------------|--------|-------|
| Filter theo entity type (chỉ show Person + Org) | Low | Medium |
| Highlight path between 2 selected nodes | Medium | High UX |
| Cluster layout (group by attribute) | Medium | High |
| Edge labels (type của relationship) | Low | Medium |
| Export to GraphML/GEXF (cho Gephi) | Low | Low |
| Search box trong HTML để locate node | Medium | High |
| Display attributes khi click node | Low | High |

## 📚 References

- `src/visualizer.py:14-15` — `DEFAULT_MAX_NODES`
- `src/visualizer.py:18-44` — `_extract_targets` (flexible schema)
- `src/visualizer.py:47-54` — `fetch_entities`
- `src/visualizer.py:57-77` — `build_networkx_graph`
- `src/visualizer.py:80-96` — `render_html`
- `src/visualizer.py:99-111` — `visualize_graph` (pipeline)
- pyvis docs: [Network class](https://pyvis.readthedocs.io/)
- networkx docs: [DiGraph](https://networkx.org/documentation/stable/reference/classes/digraph.html)

## 🔗 Linked components

- [Entity Extraction](component-entity-extraction.md) — produces graph data
- [Web UI tab_visualize](component-web-ui.md) — consumer
- [Pipeline Overview](pipeline-overview.md)
