# Retrieval System

## Overview

Basil’s retrieval is hybrid: it combines vector similarity over chunk embeddings with schema-aware filtering, source-type weighting, diversity limits, optional lexical gating for named entities, and distance thresholds. The goal is to return a small, ordered set of chunks (default 6) that are both semantically relevant and suitable for grounding replies (e.g. policy or stance content), while avoiding over-representation of a single source and suppressing weak matches.

The pipeline is: embed the user query → fetch a candidate pool from Postgres (pgvector) → weight by source type and optional entity match → sort by adjusted distance → apply diversity (max 2 chunks per source) → apply global and per-chunk distance thresholds → return the filtered list. All distance semantics are “lower is better.”

---

## Vector Similarity

Chunks are ranked by **cosine distance** between the query embedding and the chunk embedding. The implementation uses pgvector’s `<=>` operator for cosine distance. Results are ordered by this distance in **ascending** order so that the smallest (best) distances come first.

The query vector is passed as a parameter and cast to `vector` in SQL (e.g. `(%s)::vector`) so that the database receives a proper vector type rather than a numeric array. If execution with cosine distance fails (e.g. unsupported in the environment), the code rolls back the transaction and retries with L2 distance (`<->`) as a fallback; the rest of the pipeline is unchanged.

---

## Embedding Model Consistency

Embeddings are stored with a model identifier (e.g. the name of the embedding model used at ingest time). Retrieval only considers rows that match the current embedding model so that distances are comparable: mixing embeddings from different models would make distance ordering meaningless.

The implementation discovers the model column dynamically via `information_schema.columns` on the `embeddings` table. It looks for the first of: `model`, `embedding_model`, or `embed_model`. If one of these exists, the SQL adds a predicate `AND e.<model_col> = %s` and binds the current `EMBED_MODEL` (e.g. from env). If none of these columns exist, no model filter is applied and all embedding rows are candidates.

---

## Candidate Pool

The database query does not return the final k chunks directly. It returns a larger **candidate pool** of size `candidate_limit = 20`, ordered by raw vector distance. Over-fetching is used because:

1. **Source weighting** changes effective rank (canon penalty, non-canon bonus, optional entity bonus).
2. **Diversity** drops some candidates (max 2 per source).
3. **Thresholding** may drop the weakest matches.

So the final list is produced by reranking and filtering this pool in Python. If the pool were only k rows, diversity and thresholding could leave too few (or zero) chunks.

---

## Source Weighting

Raw cosine (or L2) distance is adjusted by source type before sorting and selection:

- **Canon:** `adjusted_distance = raw_distance + 0.05`. Canon chunks are demoted so that policy and stance material from URLs and summaries are preferred when distances are close.
- **Non-canon:** `adjusted_distance = raw_distance - 0.02`. Chunks from non-canon sources (e.g. URL, summary, pdf) get a small bonus.

Ordering is by `adjusted_distance` ascending. This weighting does not change the semantics of “lower is better”; it only shifts the numeric values so that non-canon sources are favoured when raw distances are similar.

---

## Diversity Enforcement

After weighting and sorting, the code selects chunks in order of adjusted distance but enforces a **maximum of 2 chunks per source** (by `source_title`). Once a source has contributed 2 chunks, further chunks from that source are skipped until the next source is considered. Selection stops when k chunks have been chosen or the candidate list is exhausted.

This avoids replies being dominated by a single long document and encourages a mix of sources in the context window.

---

## Named-Entity Lexical Anchoring

Vector search alone can perform poorly on short, vague opinion prompts (e.g. “what do you think about Elon Musk?”) because the query is underspecified and many chunks may have similar generic embedding distance. The system adds an optional **lexical gate** for certain named entities.

**When it applies:** The query text (lowercased) is checked for the presence of specific terms (e.g. “elon”, “musk”). If any of these terms appear, a SQL predicate is added so that only rows where the **source title** or **chunk text** (via ILIKE) contains at least one of those terms are returned. This restricts the candidate pool to chunks that lexically mention the entity.

**Entity bonus:** In addition, if the query contains the entity terms and a chunk’s source title or chunk text also contains them, a fixed **entity bonus** (e.g. 0.18) is subtracted from that chunk’s adjusted distance, further promoting entity-relevant chunks in the ranking.

Together, lexical gating and the entity bonus improve precision for “opinion about X” queries when a dedicated source (e.g. a stance summary for X) exists.

---

## Thresholding & Rejection

Two constants control how weak matches are treated:

- **BEST_MATCH_MAX (e.g. 0.85):** After weighting and diversity, the **best** (smallest) adjusted distance among the selected chunks is compared to this value. If it is **greater** than BEST_MATCH_MAX, retrieval is considered to have failed: no chunks are returned, and the caller typically gets an empty context. This avoids grounding replies on a best match that is still too far in embedding space.
- **KEEP_MATCH_MAX (e.g. 0.92):** Among the chunks that passed diversity, any chunk whose adjusted distance is **greater** than KEEP_MATCH_MAX is dropped. The remaining chunks are returned in the same order. This trims the tail of weak matches while keeping stronger ones.

So: first check that the best match is good enough (reject entirely if not); then filter out any selected chunk that is above KEEP_MATCH_MAX. The returned list is at most k chunks, each with adjusted distance ≤ KEEP_MATCH_MAX, and only non-empty if the best adjusted distance was ≤ BEST_MATCH_MAX.

---

## Failure Modes

Realistic ways retrieval can underperform or return no chunks:

1. **Poorly chunked source:** Chunks that are too large or cross topic boundaries can have uninformative embeddings. Queries may then match on a broad chunk instead of the most relevant passage, or the best distance may stay high and trigger rejection.
2. **Missing entity keywords:** Named-entity lexical anchoring depends on the query and the stored text sharing the same keywords (e.g. “elon”, “musk”). If the source uses different spelling, a nickname, or a title only, the lexical gate will not include it and vector search alone may not rank it first.
3. **Embedding drift:** If the embedding model or version changes between ingest and retrieval without re-ingesting, distances are not comparable. Model filtering mitigates this when the model column is present and set correctly; otherwise, mixed or stale embeddings can distort ranking.
4. **Threshold miscalibration:** If BEST_MATCH_MAX or KEEP_MATCH_MAX are too low for the typical distance distribution (e.g. after a model or corpus change), valid matches may be rejected or filtered out. If they are too high, weak or irrelevant chunks may be returned and used for grounding.
