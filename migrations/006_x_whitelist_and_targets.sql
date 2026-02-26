-- Whitelist accounts, per-user whitelist cursor, and targets for whitelist reply flow.
-- Safe to run multiple times (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS x_whitelist_accounts (
  handle    TEXT NOT NULL UNIQUE,
  user_id   TEXT NOT NULL UNIQUE PRIMARY KEY,
  enabled   BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS x_whitelist_cursor (
  user_id   TEXT PRIMARY KEY,
  since_id  TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS x_targets (
  tweet_id        TEXT PRIMARY KEY,
  source          TEXT NOT NULL,
  author_user_id  TEXT NOT NULL,
  author_handle   TEXT,
  tweet_text      TEXT NOT NULL,
  tweet_created_at TIMESTAMPTZ NOT NULL,
  inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_json        JSONB
);
