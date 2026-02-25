-- x_cursor: posting disable state and post failure tracking.
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS posting_disabled_reason   TEXT;
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS posting_disabled_at      TIMESTAMPTZ;
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS posting_disabled_until   TIMESTAMPTZ;
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS consecutive_post_failures INT NOT NULL DEFAULT 0;
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS last_post_error_at        TIMESTAMPTZ;
