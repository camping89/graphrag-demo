# System Diagrams (Mermaid)

> Các diagram trực quan của pipeline. Render bằng:
> - GitHub auto (nếu push lên GitHub)
> - VS Code: extension `Markdown Preview Mermaid Support`
> - Online: [mermaid.live](https://mermaid.live)
> - Tool nội bộ: `/ck:preview --diagram` (xem [mermaidjs-v11 skill](file:///$HOME/.claude/skills/mermaidjs-v11))

## 1. High-level Architecture

```mermaid
graph TB
    subgraph "External"
        PDF[PDF File]
        OpenAI[OpenAI API]
        Atlas[(MongoDB Atlas)]
    end

    subgraph "Data Ingestion Layer"
        PDFLoader[pdf_loader.py]
        DocCtx[document_context.py]
    end

    subgraph "Extraction Layer"
        GraphBuilder[graph_builder.py]
        EntityEmbedder[entity_embedder.py]
    end

    subgraph "Query Layer"
        QueryEngine[query_engine.py]
        Visualizer[visualizer.py]
    end

    subgraph "Presentation Layer"
        AppPy[app.py]
        Sidebar[ui/sidebar.py]
        TabBuild[ui/tab_build.py]
        TabChat[ui/tab_chat.py]
        TabViz[ui/tab_visualize.py]
        Shared[ui/shared.py]
    end

    PDF --> PDFLoader
    PDFLoader --> DocCtx
    DocCtx --> OpenAI
    PDFLoader --> GraphBuilder
    GraphBuilder --> OpenAI
    GraphBuilder --> Atlas
    EntityEmbedder --> OpenAI
    EntityEmbedder --> Atlas
    QueryEngine --> OpenAI
    QueryEngine --> Atlas
    QueryEngine --> EntityEmbedder
    Visualizer --> Atlas

    AppPy --> Sidebar
    AppPy --> TabBuild
    AppPy --> TabChat
    AppPy --> TabViz
    TabBuild --> PDFLoader
    TabBuild --> GraphBuilder
    TabBuild --> EntityEmbedder
    TabChat --> QueryEngine
    TabViz --> Visualizer
    TabChat -.shared cache.-> Shared
    TabBuild -.shared cache.-> Shared

    classDef external fill:#fce4ec,stroke:#c2185b
    classDef ingestion fill:#e8f5e9,stroke:#388e3c
    classDef extraction fill:#fff3e0,stroke:#f57c00
    classDef query fill:#e3f2fd,stroke:#1976d2
    classDef ui fill:#f3e5f5,stroke:#7b1fa2

    class PDF,OpenAI,Atlas external
    class PDFLoader,DocCtx ingestion
    class GraphBuilder,EntityEmbedder extraction
    class QueryEngine,Visualizer query
    class AppPy,Sidebar,TabBuild,TabChat,TabViz,Shared ui
```

## 2. Phase 1: Build Graph (sequence diagram)

```mermaid
sequenceDiagram
    participant U as User
    participant UI as ui/tab_build
    participant Loader as pdf_loader
    participant Ctx as document_context
    participant LLM as OpenAI
    participant Builder as graph_builder
    participant Mongo as MongoDB Atlas
    participant Embed as entity_embedder

    U->>UI: Upload PDF + chọn collection
    UI->>Loader: load_pdf_chunks_with_context(pdf, cfg)
    Loader->>Loader: PyPDFLoader.load() → pages
    Loader->>Loader: RecursiveCharacterTextSplitter → chunks
    Loader->>Ctx: analyze_document(cfg, full_text)
    Ctx->>Ctx: detect_sections_regex(full_text)
    Ctx->>Ctx: _stratified_sample(full_text)
    Ctx->>LLM: ChatOpenAI.invoke(prompt)
    LLM-->>Ctx: {subjects, doc_type, sections}
    Ctx->>Ctx: _locate_titles_in_text → Section[]
    Ctx-->>Loader: DocumentContext
    Loader->>Loader: Prepend context prefix to each chunk
    Loader-->>UI: (chunks, doc_ctx)

    UI->>UI: Show detected context (subjects, sections)
    UI->>Builder: build_graph(cfg, chunks, max_workers=5)

    par For each chunk in parallel (5 workers)
        Builder->>LLM: store.add_documents([chunk])
        Note over LLM: Extract entities + relationships
        LLM-->>Builder: extracted data
        Builder->>Mongo: update_one(_id, $set, upsert=True)
        Mongo-->>Builder: ack
    end

    Builder-->>UI: MongoDBGraphStore (with progress callbacks)

    opt User opt-in Hybrid Mode
        U->>UI: Bấm "Build embeddings"
        UI->>Embed: ensure_vector_index(cfg, coll)
        Embed->>Mongo: create_search_index(vectorSearch)
        Mongo-->>Embed: index READY

        loop For each entity without embedding
            Embed->>Embed: entity_to_text(entity)
            Embed->>LLM: embed_query(text)
            LLM-->>Embed: vector[1536]
            Embed->>Mongo: update_one($set: {embedding: vector})
        end

        Embed-->>UI: count embedded
    end

    UI-->>U: ✅ Build complete!
```

## 3. Phase 2: Query (sequence diagram)

```mermaid
sequenceDiagram
    participant U as User
    participant UI as ui/tab_chat
    participant Engine as query_engine
    participant Store as MongoDBGraphStore
    participant Embed as entity_embedder
    participant LLM as OpenAI
    participant Mongo as MongoDB Atlas

    U->>UI: Nhập câu hỏi + Enter
    UI->>UI: Append to chat_history
    UI->>UI: st.rerun() (show user msg immediately)

    UI->>Engine: engine.ask(question)
    Engine->>Engine: _gather_anchor_entities(question, k=10)

    Engine->>Store: extract_entity_names(question)
    Store->>LLM: query_prompt | chat_model
    LLM-->>Store: ["Pham Tuyen", "Veek Co.", ...]
    Store-->>Engine: extracted_names

    Engine->>Engine: _check_vector_index()
    Engine->>Mongo: list_search_indexes()
    Mongo-->>Engine: indexes list

    alt Vector index exists (Hybrid mode)
        Engine->>Embed: vector_search_entities(question, k=10)
        Embed->>LLM: embed_query(question)
        LLM-->>Embed: query_vector[1536]
        Embed->>Mongo: aggregate $vectorSearch
        Mongo-->>Embed: top-10 entities
        Embed-->>Engine: semantic anchors
        Engine->>Engine: merge extracted + semantic (dedupe)
    else No vector index (Graph-only)
        Note over Engine: anchors = extracted_names only
    end

    Engine->>Store: related_entities(anchors)
    Store->>Mongo: aggregate $graphLookup
    Mongo-->>Store: list[entity_dict] (50-90 entities)
    Store-->>Engine: related entities

    Engine->>Engine: _sort_for_context(related, anchors)
    Note over Engine: Tier1: anchors, Tier2: depth=0, Tier3+: depth=N

    Engine->>Engine: cap[:80] + strip embedding field

    Engine->>LLM: rag_prompt | chat_model
    Note over LLM: Context: 80 entities + schema + query
    LLM-->>Engine: AIMessage(content)

    Engine-->>UI: QueryResult(answer, anchors, related, used_vector)
    UI->>UI: Render assistant bubble with answer
    UI-->>U: ✅ Câu trả lời
```

## 4. Phase 3: Visualize (sequence diagram)

```mermaid
sequenceDiagram
    participant U as User
    participant UI as ui/tab_visualize
    participant Viz as visualizer
    participant Mongo as MongoDB Atlas

    U->>UI: Chọn collection + max_nodes + bấm Render
    UI->>Viz: visualize_graph(cfg, output_path, max_nodes=200)

    Viz->>Mongo: coll.find({}, limit=200)
    Mongo-->>Viz: entities[]

    Viz->>Viz: build_networkx_graph(entities)
    Note over Viz: For each entity: add_node + add_edges

    Viz->>Viz: render_html(graph, output_path)
    Note over Viz: pyvis Network.from_nx() + repulsion physics

    Viz->>Viz: net.write_html(out/graph.html)
    Viz-->>UI: Path

    UI->>UI: components.html(html_content, height=820)
    UI-->>U: 🕸️ Interactive graph embedded
```

## 5. Data flow — Entity lifecycle

```mermaid
stateDiagram-v2
    [*] --> InChunk: text mentioned

    InChunk --> Extracted: LLM extract<br/>(graph_builder)
    Extracted --> WrittenToMongo: store.add_documents()<br/>upsert

    WrittenToMongo --> Merged: Mention trong chunk khác<br/>→ update_one merges
    Merged --> WrittenToMongo: continue building

    WrittenToMongo --> Embedded: backfill_embeddings()<br/>(optional)

    Embedded --> SearchableSemantic: $vectorSearch ready
    WrittenToMongo --> TraversedGraph: $graphLookup
    SearchableSemantic --> ContextSent: query_engine.ask()
    TraversedGraph --> ContextSent

    ContextSent --> SortedAndCapped: _sort_for_context<br/>cap[:80]
    SortedAndCapped --> StrippedEmbedding: remove embedding field
    StrippedEmbedding --> SentToLLM: rag_prompt
    SentToLLM --> [*]: Used in answer
```

## 6. Component dependency graph

```mermaid
graph LR
    Config[config.py]

    PDFLoader[pdf_loader.py]
    DocCtx[document_context.py]
    Builder[graph_builder.py]
    Embedder[entity_embedder.py]
    Engine[query_engine.py]
    Viz[visualizer.py]

    Shared[ui/shared.py]
    Sidebar[ui/sidebar.py]
    TabBuild[ui/tab_build.py]
    TabChat[ui/tab_chat.py]
    TabViz[ui/tab_visualize.py]
    App[app.py]

    Config --> PDFLoader
    Config --> DocCtx
    Config --> Builder
    Config --> Embedder
    Config --> Engine
    Config --> Viz

    DocCtx --> PDFLoader
    PDFLoader --> Builder
    Builder --> Engine
    Embedder --> Engine

    Config --> Shared
    Engine --> Shared

    Shared --> Sidebar
    Shared --> TabBuild
    Shared --> TabChat
    Shared --> TabViz

    PDFLoader --> TabBuild
    Builder --> TabBuild
    Embedder --> TabBuild
    Engine --> TabChat
    Viz --> TabViz

    Sidebar --> App
    TabBuild --> App
    TabChat --> App
    TabViz --> App

    classDef foundation fill:#fff8e1,stroke:#f57c00
    classDef backend fill:#e8f5e9,stroke:#2e7d32
    classDef ui fill:#e3f2fd,stroke:#1565c0

    class Config foundation
    class PDFLoader,DocCtx,Builder,Embedder,Engine,Viz backend
    class Shared,Sidebar,TabBuild,TabChat,TabViz,App ui
```

## 7. Retry mechanism (exponential backoff)

```mermaid
flowchart TD
    Start([store.add_documents call]) --> Try[Try LLM extraction]
    Try --> Success{Success?}
    Success -->|Yes| Done([Return None])
    Success -->|No| Check{Retryable?<br/>429/timeout/5xx}
    Check -->|No| FailFast([Return error message])
    Check -->|Yes| MaxRetry{attempt < 4?}
    MaxRetry -->|No| Exhausted([Return error - log])
    MaxRetry -->|Yes| Backoff[Sleep 2^attempt + jitter]
    Backoff --> Inc[attempt++]
    Inc --> Try

    classDef success fill:#c8e6c9,stroke:#2e7d32
    classDef fail fill:#ffcdd2,stroke:#c62828
    classDef retry fill:#fff9c4,stroke:#f9a825

    class Done success
    class FailFast,Exhausted fail
    class Backoff,Inc retry
```

## 8. Hybrid retrieval decision tree

```mermaid
flowchart TD
    Start([User question]) --> Extract[extract_entity_names<br/>LLM call]
    Extract --> Names["[names from question]"]

    Names --> CheckIdx{Vector index<br/>exists?}

    CheckIdx -->|No| GraphOnly[Graph-only mode]
    CheckIdx -->|Yes| Vector[vector_search_entities<br/>top-10 semantic]

    Vector --> Merge[Merge dedupe<br/>extracted + semantic]
    Merge --> Hybrid[Hybrid mode]

    GraphOnly --> Traverse[graphLookup traversal]
    Hybrid --> Traverse

    Traverse --> Sort[_sort_for_context<br/>anchors → depth0 → depth1+]
    Sort --> Cap[Cap top 80<br/>strip embedding]
    Cap --> RAG[rag_prompt<br/>LLM call]
    RAG --> Answer([Answer + metadata])

    classDef llm fill:#ffe0b2,stroke:#f57c00
    classDef db fill:#bbdefb,stroke:#1976d2
    classDef logic fill:#dcedc8,stroke:#558b2f

    class Extract,RAG llm
    class Vector,Traverse db
    class Names,Merge,Sort,Cap logic
```

## 9. Context injection structure

```mermaid
graph LR
    subgraph Original["Original Chunk"]
        Text[Frontend Developer<br/>Veek Co., Ltd<br/>May 2024 - January 2025<br/>...]
    end

    subgraph WithContext["After Context Injection"]
        direction TB
        Prefix["=== DOCUMENT CONTEXT ===<br/>Type: CV / Resume<br/>Subject: 'Pham Tuyen'<br/>Sections: Professional Summary, Technical Skills..."]
        Local["=== LOCAL SECTION ===<br/>Current: 'Frontend Developer (Veek)'<br/>Parent: 'Professional Experience'"]
        Rules["EXTRACTION RULES:<br/>1. EVERY entity MUST have attributes<br/>2. ALWAYS link to subject 'Pham Tuyen'<br/>3. Use section context<br/>4. Canonical names"]
        OriginalText[=== CHUNK CONTENT ===<br/>Frontend Developer<br/>Veek Co., Ltd<br/>May 2024 - January 2025...]

        Prefix --> Local
        Local --> Rules
        Rules --> OriginalText
    end

    Original -->|context injection| WithContext
    WithContext -->|to LLM| LLM[Extract entities<br/>with rich context]

    classDef original fill:#fff3e0,stroke:#e65100
    classDef enhanced fill:#e8f5e9,stroke:#2e7d32

    class Original original
    class WithContext,LLM enhanced
```

## 10. Entity schema in MongoDB

```mermaid
classDiagram
    class Entity {
        +String _id
        +String type
        +Attributes attributes
        +Relationships relationships
        +Vector embedding
    }

    class Attributes {
        +Dict[str, List] field_to_values
    }

    class Relationships {
        +List~String~ target_ids
        +List~String~ types
        +List~Dict~ attributes
    }

    class Vector {
        +Float[1536] values
    }

    Entity *-- Attributes
    Entity *-- Relationships
    Entity o-- Vector : optional

    note for Entity "Stored as MongoDB document.\n_id is canonical entity name."
    note for Relationships "3 parallel arrays:\ntarget_ids[i], types[i], attributes[i]\nrepresent 1 edge each."
    note for Vector "Added by entity_embedder.\nDim = 1536 (text-embedding-3-small)."
```

## 11. Streamlit UI state flow

```mermaid
stateDiagram-v2
    [*] --> Idle: Page load

    Idle --> UserSubmit: User types question
    UserSubmit --> Append: Append to chat_history
    Append --> SetPending: Set _pending_question
    SetPending --> Rerun1: st.rerun()
    Rerun1 --> RenderHistory: Render history (with new user msg)
    RenderHistory --> ProcessPending: Pop _pending_question
    ProcessPending --> ShowLoading: Show 💭 Đang xử lý
    ShowLoading --> CallEngine: engine.ask()
    CallEngine --> ShowAnswer: Replace placeholder with answer
    ShowAnswer --> AppendAssistant: Append assistant to history
    AppendAssistant --> Idle: Ready for next question

    note right of CallEngine
        Slow step (~5-15s)
        UI freezes momentarily
        Cache speeds up reuse
    end note

    note right of Rerun1
        Critical for UX:
        User sees question NGAY
        without waiting for engine
    end note
```

## 12. Build progress with parallel workers

```mermaid
gantt
    title Build với 5 workers song song (10 chunks)
    dateFormat X
    axisFormat %s

    section Worker 1
    Chunk 1     :w1c1, 0, 40s
    Chunk 6     :w1c2, after w1c1, 35s

    section Worker 2
    Chunk 2     :w2c1, 0, 45s
    Chunk 7     :w2c2, after w2c1, 40s

    section Worker 3
    Chunk 3     :w3c1, 0, 38s
    Chunk 8     :w3c2, after w3c1, 42s

    section Worker 4
    Chunk 4     :w4c1, 0, 50s
    Chunk 9     :w4c2, after w4c1, 35s

    section Worker 5
    Chunk 5     :w5c1, 0, 42s
    Chunk 10    :w5c2, after w5c1, 38s
```

Sequential build cùng workload sẽ là 400s (10 × 40s avg). Parallel chỉ ~85s.

## 13. RAG context assembly priority

```mermaid
flowchart TB
    AllEntities[91 related entities<br/>from $graphLookup] --> Sort

    Sort{_sort_for_context}

    Sort -->|Tier 1: in anchors list| T1[Anchor entities<br/>Pham Tuyen, Veek, ...]
    Sort -->|Tier 2: depth null| T2[depth=0 entities<br/>original from pipeline]
    Sort -->|Tier 3+: depth >= 1| T3[depth=1+ entities<br/>neighbors via traversal]

    T1 --> Concat[Concat priority order]
    T2 --> Concat
    T3 --> Concat

    Concat --> Cap[Cap top 80]
    Cap --> Strip[Strip embedding field]
    Strip --> Context[Final context<br/>for RAG prompt]

    classDef p1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:3px
    classDef p2 fill:#fff9c4,stroke:#f9a825
    classDef p3 fill:#ffccbc,stroke:#e64a19

    class T1 p1
    class T2 p2
    class T3 p3
```

## 14. Document context detection workflow

```mermaid
flowchart TD
    Doc[Full document text] --> Sample{Size > 7500?}

    Sample -->|Yes| Stratified[Stratified sampling<br/>2500 chars beginning + middle + end]
    Sample -->|No| Full[Use full text]

    Stratified --> RegexDetect[detect_sections_regex<br/>Markdown / Numbered / ALLCAPS]
    Full --> RegexDetect

    RegexDetect --> LLMAnalyze[LLM analyze stratified sample]
    LLMAnalyze --> ExtractData[Extract:<br/>subjects, doc_type,<br/>description, section_titles]

    ExtractData --> LocateTitles[_locate_titles_in_text<br/>find LLM titles in full_text]
    RegexDetect --> Merge

    LocateTitles --> Merge[Merge regex + LLM sections<br/>dedupe by offset proximity]

    Merge --> Sort[Sort by offset]
    Sort --> ResolveParents[_resolve_parents<br/>stack-based hierarchy]
    ResolveParents --> Final[DocumentContext]
```

## Cách export diagrams

### Export PNG/SVG

1. Copy code mermaid khối nào đó
2. Paste vào [mermaid.live](https://mermaid.live)
3. Actions → Download SVG/PNG

### Render trong VS Code

1. Cài extension: "Markdown Preview Mermaid Support" (`bierner.markdown-mermaid`)
2. Open file `.md` → Cmd/Ctrl+Shift+V → preview

### Render trong terminal

```powershell
# Cài mermaid-cli
npm install -g @mermaid-js/mermaid-cli

# Render
mmdc -i docs/diagrams.md -o out/diagrams.png
```

### Render trong project tool

```powershell
# Dùng skill có sẵn /mermaidjs-v11
# Trong Claude Code: gõ /ck:preview --diagram
```

## 📚 Đọc thêm

- [Pipeline Overview](pipeline-overview.md) — text description của architecture
- [Component Relationships](component-relationships.md) — chi tiết phụ thuộc
- [Mermaid Docs](https://mermaid.js.org/intro/)
