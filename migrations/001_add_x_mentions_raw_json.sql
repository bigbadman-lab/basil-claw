-- Add raw_json to x_mentions for audit storage. Safe to run multiple times.
ALTER TABLE x_mentions
ADD COLUMN IF NOT EXISTS raw_json JSONB;
