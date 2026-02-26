-- Standalone backoff: next_allowed_at (skip until this time) and last two error timestamps.
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE x_standalone_state ADD COLUMN IF NOT EXISTS next_allowed_at TIMESTAMPTZ;
ALTER TABLE x_standalone_state ADD COLUMN IF NOT EXISTS last_standalone_error_at TIMESTAMPTZ;
ALTER TABLE x_standalone_state ADD COLUMN IF NOT EXISTS second_last_standalone_error_at TIMESTAMPTZ;
