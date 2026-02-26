-- Standalone post state: single row for last_posted_at, last_post_hash, last_mode, last_angle.
-- Safe to run multiple times (CREATE TABLE IF NOT EXISTS / ON CONFLICT DO NOTHING).

CREATE TABLE IF NOT EXISTS x_standalone_state (
  id             BOOLEAN PRIMARY KEY DEFAULT true,
  last_posted_at TIMESTAMPTZ,
  last_post_hash TEXT,
  last_mode      TEXT,
  last_angle     TEXT
);

INSERT INTO x_standalone_state (id) VALUES (true)
ON CONFLICT (id) DO NOTHING;
