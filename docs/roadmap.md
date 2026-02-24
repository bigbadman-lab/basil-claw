# Roadmap

## Current State (v0.x)

- **Postgres + pgvector** configured. Tables: sources, chunks, embeddings; optional x_mentions, x_replies, x_cursor. Embeddings table supports model column for filtering; vector dimension and index (e.g. ivfflat cosine) are schema-dependent.
- **Ingestion pipeline** in `ingest/run_ingest.py`: canon (single markdown), URL list (HTML fetch), summary markdown (`ingest/sources/summaries/*.md`), and local PDFs in `ingest/sources/`. Schema-adaptive column detection; re-ingest per source replaces that source’s chunks and embeddings (delete then insert). Chunking is paragraph-based (max_chars ~1200, overlap 200); embeddings via single model (env-configured). DRY_RUN=1 skips DB entirely.
- **Hybrid retrieval** in `ingest/test_reply.py`: cosine distance (pgvector `<=>`), optional L2 fallback; model filter (model/embedding_model/embed_model); candidate_limit 20 then rerank; source-type weighting (canon +0.05, non-canon −0.02); entity bonus for matching entity terms; diversity (max 2 chunks per source_title); named-entity lexical anchor (SQL ILIKE on query terms e.g. elon/musk) when query contains those terms.
- **Threshold rejection:** BEST_MATCH_MAX and KEEP_MATCH_MAX; if best adjusted distance > BEST_MATCH_MAX, return empty; then drop chunks with adjusted distance > KEEP_MATCH_MAX.
- **Intent classifier:** Regex/keyword-based; four intents (casual, policy_question, abuse_bait, other). Retrieval runs only for policy_question and other.
- **Reply generation:** Canon + context block (retrieved chunks) + user tweet + intent passed to chat model; prompt rules: 1–2 sentences, max 240 chars, no hashtags, no bullet points, no links unless asked, do not invent facts, canon voice.
- **Test-only execution:** No live X loop. `test_reply.py` runs a single test tweet locally; no posting, no mention fetch, no cursor updates.

---

## Phase 1 — Stabilisation

- Deduplicate ingestion per source (ensure one logical source per url/path/locator; avoid duplicate source rows across runs when identity is the same).
- True dry-run mode: already present (DRY_RUN=1 exits before DB); document and optionally extend (e.g. “shadow” run that would post but does not).
- Remove legacy PDF noise from retrieval (e.g. exclude or downweight certain source_types in retrieval if PDFs prove noisy; or improve PDF chunking/sanitisation).
- Tighten threshold calibration (tune BEST_MATCH_MAX / KEEP_MATCH_MAX against labelled or sampled queries; document chosen values and rationale).
- Structured logging of retrieval decisions (log intent, candidate count, best/kept distances, rejection reason when applicable).
- Store retrieved chunk IDs (and optionally source titles) in x_replies (e.g. citations_json or dedicated columns) for audit and debugging.

---

## Phase 2 — Reply Engine Refactor

- Extract reply engine into a dedicated module (e.g. `reply_engine.py`) from `test_reply.py`; keep retrieval and intent in shared or separate modules as appropriate.
- Introduce structured **ReplyResult** object (reply text, intent, retrieved chunk ids, model, flags such as rejected/fallback).
- Add audit logging (every reply attempt logged with inputs, retrieval result, and outcome).
- Improve entity detection: generalise beyond hardcoded (“elon”, “musk”) to configurable or discovered entity terms (e.g. from stance file names or a small registry).
- Introduce soft fallback logic for weak matches (e.g. when best distance is above BEST_MATCH_MAX, optionally return a single “no position yet” or clarifying-question reply instead of empty context; configurable).

---

## Phase 3 — Automation Layer

- Implement X mention loop: poll or stream mentions, de-duplicate by tweet_id, process in order.
- Cursor tracking via **x_cursor** (e.g. since_id) so each mention is processed once and restarts resume correctly.
- Rate limiting (per-account and/or per-conversation limits; backoff when hitting API limits).
- Retry handling for transient failures (embedding API, DB, X API).
- Moderation layer before posting (content checks, block list, or human-in-the-loop option; decision logged in x_replies).
- Dry-run “shadow mode” for live testing: run the full loop and log intended replies to x_replies without posting to X.

---

## Phase 4 — Retrieval & Knowledge Improvements

- Cross-source deduplication (detect near-duplicate chunks across sources; optionally merge or suppress duplicates at ingest or retrieval).
- Smarter chunking (semantic boundaries: sentence or paragraph-aware; avoid splitting mid-thought; consider model-based segmentation if needed).
- Hybrid BM25 + vector scoring (combine lexical and vector signals for ranking; requires BM25 index or equivalent).
- Dynamic threshold calibration (e.g. per-intent or per-entity thresholds; or adaptive based on score distribution).
- Source-type weighting refinement (revisit canon vs summary vs url weights based on retrieval quality metrics).

---

## Phase 5 — Observability & Governance

- Reply audit dashboard (inspect replies, retrieval inputs, and outcomes; filter by date, intent, source).
- Retrieval metrics (e.g. candidate counts, rejection rate, distance distributions; export for analysis).
- Embedding drift detection (compare distances or rankings after model change; alert or block if drift exceeds a threshold).
- Versioned canon (store canon text with version or timestamp; ability to roll back or A/B prompt variants).
- Versioned stance files (track which stance file version was ingested; support re-ingest of single source and version metadata).

---

## Long-Term Direction

- **Multi-agent extension:** Separate agents or routing for different intents or topics; shared retrieval layer.
- **Topic-specialised stance packs:** Ingest and tag stance content by topic; retrieval or routing selects relevant pack.
- **Structured policy graph:** Represent policies and positions as a graph; retrieval or reasoning over graph in addition to or instead of free-text chunks.
- **Fine-tuning vs retrieval evaluation:** Compare fine-tuned small model + retrieval vs larger model + retrieval for cost, latency, and quality.
- **Public transparency report:** Publish high-level stats (e.g. reply volume, intent mix, retrieval rejection rate) and design choices for accountability.
