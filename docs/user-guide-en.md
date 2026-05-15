# User Guide — GraphRAG × MongoDB Demo

## 1. What is this demo?

**GraphRAG × MongoDB Demo** is a web app that lets you:

1. **Upload a PDF** (report, contract, audit, CV, etc.)
2. **Automatically extract** entities and relationships into a knowledge graph stored in MongoDB
3. **Ask natural-language questions** against the document — the bot answers from the graph, not from keyword search

How it differs from a regular chatbot:
- No "guessing" from training data — answers come from your PDF only
- Understands relationships between entities (e.g. Schellman → audit → OpenAI Inc.)
- Grounded answers (you can trace which entities the answer came from)

## 2. Access

Main URL: `https://eve-graphrag-demo.streamlit.app/`

**Deep links** (open a specific tab directly):

| URL               | Opens tab                |
|-------------------|--------------------------|
| `/?tab=build`     | Build Graph (default)    |
| `/?tab=chat`      | Chat — Q&A               |
| `/?tab=visualize` | Visualize — graph viewer |

The top-left sidebar shows the **App version** (e.g. `v0.6.3`). If you just received an update and reload doesn't seem to apply, hit `R` to rerun.

## 3. UI overview

### Sidebar (left column)
- **App version**: bumped on each code update
- **DB**: MongoDB database name
- **Extraction model**: LLM used during build (default `gpt-5-mini` — fast, cheap)
- **Query model**: LLM used during Chat (default `gpt-5` — high quality)
- **Active collection**: dropdown to pick the knowledge base — switch between document sets here

### 3 main tabs
1. **1️⃣ Build Graph** — Upload PDF, build the knowledge graph (first-time setup for a new document)
2. **2️⃣ Chat** — Ask questions against the built knowledge (primary use case)
3. **3️⃣ Visualize** — Render the graph into interactive HTML

---

## 4. Use case 1: Q&A against an existing collection (primary use case)

A demo collection `openai_2025_soc_2_type_2_report` is pre-built — OpenAI's SOC 2 Type 2 report (124 pages, ~3000 entities). You can ask questions right away without uploading anything.

### Step 1: Open the Chat tab
- Click `2️⃣ Chat` or use URL `?tab=chat`

### Step 2: Verify the active collection
- Look at the sidebar → `Active collection` should be `openai_2025_soc_2_type_2_report`
- If not, pick it from the dropdown

### Step 3: Ask a question
Type into the `Type your question...` input at the bottom and press Enter.

**Sample questions (all verified to answer correctly)**:

| Kind           | Sample question                                                    | Expected answer                                                               |
|----------------|--------------------------------------------------------------------|-------------------------------------------------------------------------------|
| Basic facts    | "Which company is this SOC 2 report for, and who audited it?"      | OpenAI Inc. — Schellman & Company, LLC                                        |
| Listing        | "List all subservice organizations of OpenAI"                      | Azure, Snowflake, Okta, WorkOS, ...                                           |
| Specific       | "What does Snowflake provide for OpenAI?"                          | Data warehouse + role + responsibilities                                      |
| Control        | "What is Control CC6.1 about?"                                     | Logical access control + related entities                                     |
| Role           | "What is AICPA's role in this report?"                             | Standards body for the SOC 2 framework                                        |
| Structure      | "What is the structure of this report?"                            | 5 sections + their contents                                                   |
| Policy         | "What does the Information Security Policy cover? Who maintains it?" | Scope/coverage + Management owns                                            |
| Trust criteria | "Which Trust Services Criteria are evaluated in this report?"      | Security/Availability/Confidentiality/Privacy (excludes Processing Integrity) |
| Count          | "How many Control Objectives are in the report?"                   | 7 Control Objectives + their names                                            |

### Step 4: Read the result
After pressing Enter, you'll see:

1. **The answer** — text response to your question
2. **Mode badge** beneath the answer:
   - `🧬 Hybrid (Vector + Graph)` — best mode (embeddings present)
   - `🕸️ Graph-only` — basic mode (no embeddings)
3. **Anchors** — entities the bot used as starting points for graph traversal
4. **🔗 Related entities in graph** (expander) — entities in the LLM's context

### Step 5: Ask follow-ups
You can ask multiple questions in a row — but the bot has **no memory** between turns (each question is an independent query). To carry context, combine info into one prompt.

---

## 5. Use case 2: Upload a new PDF and build the graph

Use this when you want to try a different document (CV, contract, another report).

### Step 1: Open the Build Graph tab

### Step 2: Upload the PDF
- Click `Choose PDF file` → pick the file from your machine
- After upload, the app auto-analyzes:
  - Page count
  - Total characters
  - Recommended `chunk_size` and `overlap` based on document length

→ Click `✨ Apply recommendation` to auto-fill the recommended params.

### Step 3: Pick a collection
- **Create new collection**: type a name (e.g. `my_contract_2026`), or click `💡 Suggest from filename` to auto-generate from the file name
- **Merge into existing collection**: pick from the dropdown (use this to add a new document to an existing knowledge base — same-name entities will be merged)

### Step 4: Configure params (pre-filled from the recommendation)
- **Chunk size**: characters per chunk
- **Overlap**: overlap characters between consecutive chunks
- **Chunk limit**: default `0` = process the full PDF. Set a small number (e.g. `20`) for a cheap test build first if cost is a concern
- **Parallel workers**: chunks processed in parallel (default 5 — avoid >10 due to OpenAI rate limits)

### Step 5: Click `🚀 Build graph`
- This takes a few minutes to tens of minutes depending on PDF length
- Progress bar shows: chunks processed, %, ETA, failure count (if any)
- After build, the app **automatically**:
  1. **Normalizes duplicates** — merges entities with the same canonical name (e.g. `Information Security Policy` ≡ `information security policy`)
  2. Shows a summary: chunks, entity count, elapsed, errors (if any)

### Step 6: Build embeddings (optional, recommended)
After the build finishes, scroll down to the `🧬 Hybrid Vector + Graph` section and click `🧬 Build embeddings + vector index`.

→ This enables Hybrid mode: Chat handles natural-language questions much better (no need to match entity names exactly).

### Step 7: Switch to the Chat tab
- Pick the new collection in the sidebar
- Ask questions as in section 4

---

## 6. Use case 3: Visualize the graph

Use this to **visualize** the knowledge graph — see entities and the relationships between them.

### Step 1: Open the Visualize tab
### Step 2: Make sure the active collection is correct
### Step 3: Set `Max entities` — the maximum number of entities to display
- Default: 80
- Recommended ≤ 150 for smooth browser performance
- \> 200 may lag on low-spec laptops

### Step 4: Click `🎨 Render graph HTML`
After a few seconds you'll see the interactive graph:
- **Drag** entities around
- **Zoom** with scroll
- **Click** an entity to highlight it
- **Hover** for tooltips (type + relationship count + tier)

### Visual structure
- Entities are split into **5 tiers** by centrality:
  - **Tier 1** (50px, largest): super-hubs (e.g. `Schellman & Company`, `OpenAI Inc.`)
  - **Tier 2** (35px): major hubs
  - **Tier 3** (25px): connectors
  - **Tier 4** (18px): mid-tier
  - **Tier 5** (12px): leaf entities
- Node color follows `type` (Organization, Control, Policy, ...)
- Arrows show relationship direction (A → B means A has a relationship pointing to B)

---

## 7. Tips for effective use

### ✅ Do
- **Be specific** with entity names: "What is Schellman & Company?" beats "Tell me about the auditor"
- **One question at a time** — the bot doesn't remember previous turns
- **Verify the active collection** in the sidebar before asking
- **Try the sample questions** in section 4 first to get a feel for the bot's behavior before asking your own

### ❌ Avoid
- **Overly generic prompts** ("Tell me everything") — the bot will fall back to vague summaries
- **Deep inferential questions** ("What would happen if Okta failed?") — the bot answers from the graph only, no reasoning beyond
- **Math-heavy questions** ("How many controls × policies?") — the bot retrieves; it doesn't compute

---

## 8. Known limitations

| Limitation                                                                       | Workaround                                         |
|----------------------------------------------------------------------------------|----------------------------------------------------|
| The bot can be slow (5-15s) on complex questions                                 | Wait, don't spam Enter                             |
| The bot replies in English if the collection is English (even for non-EN inputs) | Ask in English for consistency                     |
| Exact counting needs intent detection — may miss unusual phrasings               | Use standard wording: "How many...", "List all..." |
| Build calls the LLM → consumes API credits                                       | Use a small chunk limit when testing               |
| Visualize lags with > 200 nodes                                                  | Lower the `Max entities` slider                    |

---

## 9. FAQ

**Q: Why can't the bot answer my question?**
- Check the active collection actually has data on the topic (sidebar)
- Be more specific, use exact entity names
- Verify the mode badge says `Hybrid` — if it says `Graph-only`, build embeddings

**Q: How long does building the graph take?**
- Small docs (< 20 pages): ~2-5 minutes
- Medium (20-100 pages): 10-30 minutes
- Large (> 100 pages): 30-60 minutes (the 124-page SOC 2 demo: ~25 minutes)

**Q: Does this cost API credits?**
- Build: ~$0.50 for 124 pages (gpt-5-mini for extraction)
- Chat: ~$0.01-0.05 per question (gpt-5 for RAG)
- Embeddings: ~$0.005 for 3000 entities

**Q: Can I delete a collection?**
- Not yet supported in the UI — delete it directly in the MongoDB Atlas dashboard

**Q: Does the bot save chat history?**
- No. Each session is independent. Refreshing the page clears the history.
