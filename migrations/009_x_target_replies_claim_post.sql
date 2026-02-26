-- Claim and post tracking for x_target_replies (whitelist drafts).
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE x_target_replies ADD COLUMN IF NOT EXISTS post_claimed_at  TIMESTAMPTZ;
ALTER TABLE x_target_replies ADD COLUMN IF NOT EXISTS post_claimed_by   TEXT;
ALTER TABLE x_target_replies ADD COLUMN IF NOT EXISTS reply_tweet_id    TEXT;
ALTER TABLE x_target_replies ADD COLUMN IF NOT EXISTS posted_at        TIMESTAMPTZ;
ALTER TABLE x_target_replies ADD COLUMN IF NOT EXISTS error_text       TEXT;
