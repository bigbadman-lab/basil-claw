-- Basil Clawthorne DB schema
-- Enables pgvector and creates tables for RAG + X automation.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- RAG storage ----------

CREATE TABLE IF NOT EXISTS sources (
  id              BIGSERIAL PRIMARY KEY,
  source_type     TEXT NOT NULL,          -- 'url' | 'pdf' | 'md' | 'manual'
  title           TEXT,
  url             TEXT UNIQUE,
  content_sha256  TEXT NOT NULL,
  fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_text_len    INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
  id              BIGSERIAL PRIMARY KEY,
  source_id       BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  chunk_index     INT NOT NULL,
  content         TEXT NOT NULL,
  content_tokens  INT,
  chunk_sha256    TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, chunk_index)
);

-- NOTE: vector dimension must match your embedding model output.
-- We'll start with 1536 for MVP.
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id        BIGINT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  model           TEXT NOT NULL,
  embedding       vector(1536) NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS embeddings_ivfflat_cosine
ON embeddings USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- ---------- X automation + audit ----------

CREATE TABLE IF NOT EXISTS x_mentions (
  id                BIGSERIAL PRIMARY KEY,
  tweet_id          TEXT NOT NULL UNIQUE,
  author_id         TEXT NOT NULL,
  author_username   TEXT,
  text              TEXT NOT NULL,
  created_at        TIMESTAMPTZ,
  received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  conversation_id   TEXT,
  in_reply_to_id    TEXT,
  status            TEXT NOT NULL DEFAULT 'new'   -- new|processing|replied|skipped|failed
);
ALTER TABLE x_mentions ADD COLUMN IF NOT EXISTS raw_json JSONB;

CREATE TABLE IF NOT EXISTS x_replies (
  id                BIGSERIAL PRIMARY KEY,
  mention_tweet_id  TEXT NOT NULL REFERENCES x_mentions(tweet_id) ON DELETE CASCADE,
  reply_tweet_id    TEXT,
  reply_text        TEXT NOT NULL,
  decision          TEXT NOT NULL,               -- posted|blocked|error|skipped
  block_reason      TEXT,
  model             TEXT,
  rag_topk          INT NOT NULL DEFAULT 6,
  citations_json    JSONB,
  moderation_json   JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS raw_json JSONB;

CREATE TABLE IF NOT EXISTS x_cursor (
  id               SMALLINT PRIMARY KEY DEFAULT 1,
  since_id         TEXT,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO x_cursor (id, since_id)
VALUES (1, NULL)
ON CONFLICT (id) DO NOTHING;
