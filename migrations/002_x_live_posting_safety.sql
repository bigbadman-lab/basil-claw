-- X live posting: idempotency, claim-then-post, kill switch, audit.
-- Run after 001. Safe to run multiple times (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

-- ---------- A) Unique keys ----------
-- Mention: tweet_id is already UNIQUE on x_mentions (schema.sql). Enforce explicitly:
CREATE UNIQUE INDEX IF NOT EXISTS x_mentions_tweet_id_key ON x_mentions (tweet_id);

-- One reply row per mention (prevents duplicate replies / double-post):
CREATE UNIQUE INDEX IF NOT EXISTS x_replies_mention_tweet_id_key ON x_replies (mention_tweet_id);

-- ---------- B) New columns ----------
-- x_replies: claim tracking and error for atomic claim-then-post
ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS post_claimed_at TIMESTAMPTZ;
ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS post_claimed_by  TEXT;
ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS error_text        TEXT;

-- x_cursor: kill switch (posting only when env + this flag are both enabled)
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS posting_enabled BOOLEAN NOT NULL DEFAULT false;

-- ---------- C) Advisory lock (optional but recommended) ----------
-- Use in application: SELECT pg_try_advisory_xact_lock(1234567890) AS acquired;
-- Key 1234567890 = single global lock for "mentions run". If acquired = false, exit (another process has the lock).
-- No DDL needed; use in same transaction as the run or at start of run_once.
