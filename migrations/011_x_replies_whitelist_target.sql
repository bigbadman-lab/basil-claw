-- Whitelist replies in x_replies: target_tweet_id (unique), source; allow mention_tweet_id NULL.
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS / DROP IF EXISTS).

ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS target_tweet_id TEXT;
ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS source          TEXT DEFAULT 'mention';
CREATE UNIQUE INDEX IF NOT EXISTS x_replies_target_tweet_id_key ON x_replies (target_tweet_id) WHERE target_tweet_id IS NOT NULL;
ALTER TABLE x_replies ALTER COLUMN mention_tweet_id DROP NOT NULL;
ALTER TABLE x_replies DROP CONSTRAINT IF EXISTS x_replies_mention_tweet_id_fkey;
