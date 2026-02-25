-- x_cursor: record last fetch failure for monitoring.
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS last_fetch_error_at   TIMESTAMPTZ;
ALTER TABLE x_cursor ADD COLUMN IF NOT EXISTS last_fetch_error_text TEXT;
