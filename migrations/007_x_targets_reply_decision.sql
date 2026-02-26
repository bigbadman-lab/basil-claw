-- Audit columns for whitelist reply decision on x_targets.
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE x_targets ADD COLUMN IF NOT EXISTS reply_decision TEXT;
ALTER TABLE x_targets ADD COLUMN IF NOT EXISTS reply_score     REAL;
ALTER TABLE x_targets ADD COLUMN IF NOT EXISTS reply_reason    TEXT;
