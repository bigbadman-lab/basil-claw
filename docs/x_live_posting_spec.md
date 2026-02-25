# X Live Posting: Cursor-Correct, Idempotent, Safe, Auditable

This spec describes **exact** changes to move Basil’s X automation from dry-run to controlled live posting: cursor-correct, idempotent, safe to post, and auditable.

---

## A) Unique keys

### Mention (tweet id)

- **Unique identifier:** X’s tweet id (string), e.g. `"1234567890123456789"`.
- **Table:** `x_mentions`
- **Column(s):** `tweet_id` — must be **UNIQUE** so the same mention is never stored twice and upserts are deterministic.
- **Current state:** `schema.sql` already has `tweet_id TEXT NOT NULL UNIQUE`. Migration `002_x_live_posting_safety.sql` adds an explicit unique index: `x_mentions_tweet_id_key`.

### Reply (one per mention)

- **Unique identifier for “one reply per mention”:** `mention_tweet_id` (the tweet we replied to).
- **Table:** `x_replies`
- **Column(s):** `mention_tweet_id` — must be **UNIQUE** so we never have two reply rows for the same mention (no duplicate posts, no double “drafted” rows).
- **Current state:** No unique constraint. Migration adds `CREATE UNIQUE INDEX x_replies_mention_tweet_id_key ON x_replies (mention_tweet_id)`.

---

## B) Postgres migrations

**File:** `migrations/002_x_live_posting_safety.sql`

- **Unique indexes**
  - `CREATE UNIQUE INDEX IF NOT EXISTS x_mentions_tweet_id_key ON x_mentions (tweet_id);`
  - `CREATE UNIQUE INDEX IF NOT EXISTS x_replies_mention_tweet_id_key ON x_replies (mention_tweet_id);`

- **New columns**
  - **x_replies:** `post_claimed_at TIMESTAMPTZ`, `post_claimed_by TEXT`, `error_text TEXT` (for claim-then-post and audit).
  - **x_cursor:** `posting_enabled BOOLEAN NOT NULL DEFAULT false` (DB kill switch; posting only when env + this are both on).

- **Advisory lock (application-level, no DDL)**
  - At start of `run_once()`: in a transaction (or first thing), run:
    - `SELECT pg_try_advisory_xact_lock(1234567890) AS acquired;`
  - Use a single global key (e.g. `1234567890`) for “mentions run”. If `acquired` is false, log and exit so another process doesn’t run the loop.
  - Alternative: `pg_try_advisory_lock(1234567890)` (session-level); then release at end of run. Prefer `pg_try_advisory_xact_lock` so the lock is held for the whole transaction if you do cursor read + process + cursor write in one transaction; otherwise use session lock for the full run.

---

## C) Exact code changes

### 1) `x_bridge/run_mentions_once.py`

- **Cursor (cursor-correct, monotonic)**
  - **Read cursor:** Keep at start: `cursor = db.get_cursor("mentions_since_id")` (e.g. line ~69). Log `cursor_before = cursor`.
  - **Fetch mentions:** Keep `x_client.get_mentions(since_id=cursor, max_results=50)`. API uses `since_id` as exclusive, so we don’t refetch the same tweet.
  - **Insert mentions:** Keep `db.upsert_mention(...)` for every mention (self, RT, empty, or “to reply”). Rely on `ON CONFLICT (tweet_id)` so re-runs don’t create duplicate mention rows.
  - **Update cursor:** Only after processing the batch, set cursor to the **maximum tweet id** in this batch: `newest_tweet_id = max(..., key=_numeric_id)` then `db.set_cursor("mentions_since_id", newest_tweet_id)`. Do this only when `newest_tweet_id` is set and `newest_tweet_id != cursor`. Log `cursor_after = newest_tweet_id`. This keeps the cursor monotonic and avoids refetching the same mention due to cursor bugs.

- **Idempotency (no duplicate replies / double-post)**
  - **Before generating a reply:** Keep `if db.is_replied(tweet_id): continue`. That checks `x_mentions.status = 'replied'` or existence in `x_replies`, so we never generate a second reply for the same mention.
  - **Insert reply (draft):** For every mention you intend to reply to (after skips), **first** insert a single “draft” row: `db.insert_reply(mention_tweet_id=tweet_id, reply_tweet_id=None, reply_text=reply_text, decision='drafted')` using **INSERT ... ON CONFLICT (mention_tweet_id) DO NOTHING**. So re-runs or cron overlap only add one row per mention; second insert is a no-op.
  - **Posting:** Do **not** “generate then post then insert”. Do “insert draft (idempotent), then (if live) claim and post”. So: in the same run, after inserting drafts, call a new function that **claims** up to `MAX_POSTS_PER_RUN` rows where `reply_tweet_id IS NULL`, then for each claimed row call `x_client.post_reply`, then update that row with `reply_tweet_id` and `decision='posted'`. That way two processes cannot post the same reply (only the one that wins the claim updates the row).

- **Safe to post**
  - **Conditions:** Post only when **all** of: `X_DRY_RUN=0`, env kill switch (e.g. `X_POSTING_ENABLED=1`), and DB `x_cursor.posting_enabled = true` for the row used by the run (e.g. `id=1`). Add a small helper e.g. `db.get_posting_enabled()` that reads `posting_enabled` from `x_cursor WHERE id = 1`.
  - **Caps:** `MAX_POSTS_PER_RUN` (e.g. env, default 1 for Phase 2A). Optionally `MAX_POSTS_PER_HOUR`: before claiming, `SELECT count(*) FROM x_replies WHERE decision = 'posted' AND created_at > now() - interval '1 hour'`; if count >= cap, don’t claim any. Enforce in the “claim then post” path.

- **Audit logging**
  - Log at start: `cursor_before`, run id or timestamp.
  - Log after fetch: number of mentions, optionally tweet ids.
  - Log after processing: inserted mention count (or ids), generated reply count (or mention_tweet_ids), attempted posts (mention_tweet_id, reply_text length), posted (mention_tweet_id, post_id), skip reasons (e.g. “already replied”, “dry run”, “posting disabled”, “hour cap reached”).

### 2) `x_bridge/db.py`

- **insert_reply**
  - Change to **idempotent insert:**  
    `INSERT INTO x_replies (mention_tweet_id, reply_tweet_id, reply_text, decision, raw_json, created_at) VALUES (%s, %s, %s, %s, %s::jsonb, COALESCE(%s::timestamptz, now())) ON CONFLICT (mention_tweet_id) DO NOTHING`  
  - Use column name `mention_tweet_id` (already used in schema). Keep existing params; for “draft” pass `reply_tweet_id=None`, `decision='drafted'`. Optional: `ON CONFLICT (mention_tweet_id) DO UPDATE SET reply_text = EXCLUDED.reply_text, ...` if you want to refresh draft text on re-run; for strict idempotency, `DO NOTHING` is enough.

- **New: get_posting_enabled()**
  - `SELECT posting_enabled FROM x_cursor WHERE id = 1`; return `True` only if row exists and `posting_enabled = true`.

- **New: claim_replies_for_posting(limit, claimed_by)**
  - Single transaction:
    - `WITH sel AS (SELECT id, mention_tweet_id, reply_text FROM x_replies WHERE reply_tweet_id IS NULL ORDER BY created_at ASC LIMIT %s FOR UPDATE SKIP LOCKED) UPDATE x_replies r SET post_claimed_at = now(), post_claimed_by = %s FROM sel WHERE r.id = sel.id RETURNING r.id, r.mention_tweet_id, r.reply_text`.
  - Returns list of `(id, mention_tweet_id, reply_text)`. Caller then posts and calls **update_reply_posted**.

- **New: update_reply_posted(reply_row_id, reply_tweet_id)**
  - `UPDATE x_replies SET reply_tweet_id = %s, decision = 'posted' WHERE id = %s`.

- **New (optional): set_reply_error(reply_row_id, error_text)**
  - If post fails after claim: `UPDATE x_replies SET error_text = %s WHERE id = %s` so the row can be retried or inspected.

### 3) `ingest/reply_engine.py`

- **280-char enforcement**
  - After generating the reply text (in `generate_reply_for_tweet` or in the caller that uses it), enforce 280 characters for the **reply body** (no @handle prefix in the tweet text; X handles threading). So truncate the string to 280 (e.g. at word boundary with “…”). Current `run_mentions_once` already has `_truncate_reply_to_limit(reply_text)`; keep that. Optionally add the same truncation inside `generate_reply_for_tweet` before returning so all callers get a safe length.

### 4) Posting flow (atomic “claim then post”)

- In `run_mentions_once.run_once()` (after processing all mentions and advancing the cursor):
  1. If `X_DRY_RUN` or not `db.get_posting_enabled()` or env `X_POSTING_ENABLED` not set → do not call claim/post.
  2. Optional: check hourly cap; if at cap, skip claiming.
  3. `claimed = db.claim_replies_for_posting(limit=MAX_POSTS_PER_RUN, claimed_by=run_id_or_hostname)`.
  4. For each `(row_id, mention_tweet_id, reply_text)` in `claimed`:
     - Call `reply_tweet_id = x_client.post_reply(reply_text, mention_tweet_id)`.
     - On success: `db.update_reply_posted(row_id, reply_tweet_id)` and `db.mark_mention_status(mention_tweet_id, 'replied')`.
     - On failure: `db.set_reply_error(row_id, str(e))` (and optionally leave `reply_tweet_id` NULL so it can be retried later).
  5. Log each attempted post and result (post_id or error).

This way two processes cannot post the same reply: only one will get the row via `FOR UPDATE SKIP LOCKED`.

---

## D) Phase 2A verification checklist

### 1) Run with `X_DRY_RUN=1`

- **Before run:**  
  - `SELECT since_id, posting_enabled FROM x_cursor WHERE id = 1;`  
  - `SELECT count(*) FROM x_mentions;`  
  - `SELECT count(*) FROM x_replies;`

- **Run:**  
  `X_DRY_RUN=1 python3 -m x_bridge.run_mentions_once`

- **After run – confirm no duplicates, cursor moves:**
  - `SELECT since_id, updated_at FROM x_cursor WHERE id = 1;` — `since_id` should be **greater** than before (or unchanged if no new mentions).
  - `SELECT tweet_id, status FROM x_mentions ORDER BY tweet_id DESC LIMIT 20;` — no duplicate `tweet_id`s.
  - `SELECT mention_tweet_id, decision, reply_tweet_id FROM x_replies ORDER BY created_at DESC LIMIT 20;` — at most one row per `mention_tweet_id`; new drafts have `decision = 'drafted'` and `reply_tweet_id IS NULL`.
  - Run **again** immediately; then:
    - `SELECT mention_tweet_id, count(*) FROM x_replies GROUP BY mention_tweet_id HAVING count(*) > 1;` — must return 0 rows (idempotent inserts).

### 2) First live post: `X_DRY_RUN=0` + kill switch + `MAX_POSTS_PER_RUN=1`

- **Enable posting:**  
  - Set env: `X_POSTING_ENABLED=1`, `X_DRY_RUN=0`, `MAX_POSTS_PER_RUN=1`.  
  - DB: `UPDATE x_cursor SET posting_enabled = true WHERE id = 1;`

- **Run:**  
  `python3 -m x_bridge.run_mentions_once`

- **After run – confirm one post, audit trail:**
  - `SELECT mention_tweet_id, reply_tweet_id, decision, post_claimed_at, post_claimed_by FROM x_replies WHERE reply_tweet_id IS NOT NULL ORDER BY created_at DESC LIMIT 5;` — exactly one new row with `decision = 'posted'` and non-null `reply_tweet_id` (post_id).
  - `SELECT tweet_id, status FROM x_mentions WHERE tweet_id = '<that mention_tweet_id>';` — `status = 'replied'`.
  - Logs should show: cursor_before, cursor_after, one “posted” with post_id, and skip reasons for any other mentions.

### 3) Rerun immediately: confirm 0 additional posts

- **Run again (no new mentions):**  
  `python3 -m x_bridge.run_mentions_once`

- **Checks:**
  - No new rows in `x_replies` with `decision = 'posted'` (same count as after step 2).
  - `SELECT count(*) FROM x_replies WHERE decision = 'posted' AND created_at > now() - interval '1 minute';` — should match the single post from step 2, not 2.
  - Logs: “0 posts” or “claim returned 0 rows” (because all reply rows for fetched mentions already have `reply_tweet_id` set).

---

## Summary

| Goal | Mechanism |
|------|-----------|
| **Cursor-correct** | Single cursor read at start; cursor write only after processing batch; cursor = max(tweet_id) in batch; advisory lock to avoid concurrent runs. |
| **Idempotent** | `x_mentions.tweet_id` UNIQUE; `x_replies.mention_tweet_id` UNIQUE; insert draft with ON CONFLICT DO NOTHING; post only via claim (UPDATE ... RETURNING). |
| **Safe to post** | Post only when `X_DRY_RUN=0` and env kill switch and `x_cursor.posting_enabled`; enforce `MAX_POSTS_PER_RUN` (and optional per-hour cap) in claim. |
| **Auditable** | Log cursor_before/after, inserted mentions, generated replies, claim/post attempts, post_id, skip reasons; store in DB (post_claimed_at, post_claimed_by, error_text). |

Apply migration `migrations/002_x_live_posting_safety.sql`, then implement the code changes in C) and run the checklist in D).
