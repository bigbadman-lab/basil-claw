-- Add posted_at to x_replies. When reply_tweet_id is set, posted_at should be set to the time of post.
ALTER TABLE x_replies ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ NULL;
