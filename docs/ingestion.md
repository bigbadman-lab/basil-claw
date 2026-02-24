# Ingestion Pipeline

## Overview

The ingestion pipeline loads content from configured sources, chunks it into semantic blocks, generates embeddings via a single embedding model, and writes to three Postgres tables: **sources**, **chunks**, and **embeddings**. The pipeline is implemented in `ingest/run_ingest.py`. It is schema-adaptive: column names for sources, chunks, and embeddings are discovered at runtime from `information_schema`, and only existing columns are used. Re-ingesting a source replaces that source’s chunks and embeddings (delete then insert) so that repeated runs do not duplicate rows for the same source.

---

## Supported Source Types

| Type      | Description | Locator / identity |
|-----------|-------------|---------------------|
| **canon** | Single markdown file defining Basil’s persona and rules. | Fixed path: `ingest/sources/basil_canon.md`. |
| **url**   | HTML pages fetched from a list of URLs. | URL string. Title from page `<title>` or URL. |
| **summary** | Markdown files in a dedicated directory. | Path under `ingest/sources/summaries/`. Title = basename without extension. |
| **pdf**   | Local PDF files in `ingest/sources/`. | File path. Title = basename without extension. |

Canon is loaded first, then URLs from a file, then summary markdown (if the directory exists), then PDFs. All source text is sanitized (NUL and other control characters removed) before chunking and storage.

---

## Source Discovery

- **Canon:** Path is fixed (`CANON_PATH`). File must exist and be non-empty.
- **URLs:** Read from `restore_britain_urls.txt` (one URL per line; blank lines and `#`-prefixed lines ignored).
- **Summary markdown:** Directory `ingest/sources/summaries/` is listed; any file with a `.md` extension (case-insensitive) is loaded. Empty files are skipped.
- **PDFs:** Directory `ingest/sources/` is listed; any file with a `.pdf` extension is loaded.

No recursive directory walk is performed. Only the listed paths and the two directories above are used.

---

## Text Loading

- **Canon:** Read as UTF-8, stripped, then sanitized.
- **URL:** Fetched with HTTP; HTML is parsed with BeautifulSoup. Script, style, and noscript elements are removed. Text is extracted, collapsed to single newlines, sanitized. Title comes from `<title>` or the URL.
- **Summary:** Read as UTF-8, stripped. No sanitization is applied in the current code to the raw summary text before appending to the doc list (downstream, chunk text is sanitized before insert).
- **PDF:** pypdf `PdfReader` extracts text per page; non-empty pages are stripped and joined with double newlines. Result is sanitized.

All loaded content is held in memory as a `SourceDoc` (source_type, locator, title, text) before chunking.

---

## Chunking Strategy

Chunking is paragraph-based and designed to keep semantic units together for retrieval.

- **Unit:** Paragraphs (split on newlines; blank paragraphs discarded).
- **Target size:** Chunks are built to around **max_chars = 1200** characters (configurable).
- **Overlap:** **overlap = 200** characters: when a chunk is closed, the last 200 characters of that chunk are carried into the next chunk as a prefix to preserve context across boundaries.
- **Output:** Each chunk is a single string (paragraphs joined by newlines). Empty chunks are dropped.

Chunks are then sanitized again (NUL and control characters removed) before insertion. This keeps the stored text safe for Postgres and avoids indexing noise. The approach favours retrieval precision by preserving whole paragraphs and using overlap rather than arbitrary sentence or character splits.

For policy and stance content, **summary markdown** (curated, structured) is preferred over raw PDF extraction when both exist: summaries are usually cleaner and chunk more predictably.

---

## Embedding Generation

- **Model:** One embedding model is used for the whole run, from env (e.g. `EMBEDDING_MODEL`, default `text-embedding-3-small`).
- **API:** OpenAI Embeddings API; `encoding_format="float"`.
- **Batching:** Texts are sent in batches of 96. Responses are merged in index order so that the i-th chunk gets the i-th embedding.
- **Input:** The list of chunk strings (after sanitization) for the current source. Each source is fully chunked and embedded before moving to the next.

---

## Database Writes

Writes occur in order: **sources** → **chunks** → **embeddings**, inside a single transaction (no autocommit).

1. **sources:** One row per `SourceDoc`. Columns are chosen from schema: e.g. source_type (or type), title, url/path/locator, raw text (raw_text/text/content/body), content hash (content_sha256/content_hash/hash/sha256). On conflict on url/path/locator (whichever exists), the row is updated and the same id is reused.
2. **Re-ingest cleanup:** Before inserting chunks for that source, existing embeddings that reference chunks of this source are deleted, then all chunks for this source are deleted. This avoids unique constraint violations on (source_id, chunk_index) and keeps one set of chunks per source.
3. **chunks:** One row per chunk. At least source_id and a text column (text/content/body/chunk_text) are required. Optional: chunk_index (or index), hash column (chunk_sha256 etc.). Chunk text is the sanitized string. `execute_values` is used for bulk insert; chunk ids are returned in order.
4. **embeddings:** One row per chunk. Columns: chunk_id, vector column (embedding or vector), and optionally model. Each row gets the same embedding model identifier (see below). `execute_values` is used.

The transaction is committed only after all sources are processed. On any exception, the transaction is rolled back.

---

## Embedding Model Metadata

If the **embeddings** table has a **model** column, the pipeline writes the current `EMBED_MODEL` value (e.g. `text-embedding-3-small`) into that column for every inserted embedding row. If the column does not exist, no model value is written.

Storing the model name matters for retrieval: the retrieval layer can filter by `model = current_embedding_model` so that only embeddings produced by the same model are compared. Mixing embeddings from different models would make distance ordering unreliable.

---

## Dry-Run Behaviour

Dry-run is controlled by the environment variable **DRY_RUN**: when `DRY_RUN=1`, the script behaves as follows:

1. Load all sources (canon, URLs, summaries, PDFs) as usual.
2. Print a short list of loaded sources (source_type, title, locator, character count).
3. **Return immediately.** No database connection is opened and no data is written.

So when dry-run is enabled, the pipeline does **not** connect to Postgres and does **not** write to the database. It is safe to run with `DRY_RUN=1` to verify source discovery and text loading without touching the store.

---

## Known Limitations

- **No cross-source deduplication:** Identical or near-identical content from different sources (e.g. same article at two URLs) is not deduplicated. Each source produces its own rows in sources/chunks/embeddings. Re-ingestion replaces only that source’s chunks and embeddings.
- **Re-ingestion replaces per source:** For a given source (same url/path/locator), re-running ingest deletes that source’s chunks and embeddings then inserts the new set. There is no merge or versioning; the new set fully replaces the old.
- **Large PDFs discouraged:** PDF text extraction is linear in page count and stored in memory. Very large PDFs can be slow and memory-heavy. Prefer converting long PDFs to structured summary markdown and ingesting that instead when possible.
