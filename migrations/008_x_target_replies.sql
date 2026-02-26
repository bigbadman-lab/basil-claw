-- Whitelist reply drafts and skip decisions per target tweet.
-- Safe to run multiple times (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS x_target_replies (
  target_tweet_id TEXT PRIMARY KEY,
  reply_text      TEXT,
  decision        TEXT NOT NULL,   -- drafted | blocked
  block_reason    TEXT,
  source          TEXT NOT NULL DEFAULT 'whitelist',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
