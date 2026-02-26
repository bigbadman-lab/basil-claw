"""
DB helpers for X mentions loop: cursor, x_mentions, x_replies.

Uses DATABASE_URL (psycopg2). One transaction per logical operation where appropriate.
Optional conn= allows running all operations in a single transaction (e.g. under advisory lock).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2

logger = logging.getLogger(__name__)

_DATABASE_URL = os.environ.get("DATABASE_URL")

# Advisory lock key for run_mentions_once: only one run holds this lock per transaction.
ADVISORY_LOCK_KEY = 1234567890
# Advisory lock key for run_standalone_once (separate so mentions and standalone can run in parallel).
ADVISORY_LOCK_KEY_STANDALONE = 1234567891


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be set.")
    return psycopg2.connect(_DATABASE_URL)


def get_connection():
    """Return a new connection (caller must close). Used to hold one transaction for the whole run."""
    return _connect()


def try_advisory_xact_lock(conn, lock_key: Optional[int] = None) -> bool:
    """Try to acquire transaction-level advisory lock. Returns True if acquired. Lock held until commit/rollback."""
    key = lock_key if lock_key is not None else ADVISORY_LOCK_KEY
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (key,))
        row = cur.fetchone()
        return bool(row and row[0])


def get_cursor(key: str, conn=None) -> Optional[str]:
    """
    Get cursor value for key. Key "mentions_since_id" maps to x_cursor row id=1.
    Returns None if not set.
    """
    if key != "mentions_since_id":
        return None
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT since_id FROM x_cursor WHERE id = 1")
            row = cur.fetchone()
            if row and row[0] is not None:
                return str(row[0])
            return None
    finally:
        if own_conn:
            c.close()


def set_cursor(key: str, value: str, conn=None) -> None:
    """Set cursor value. Key "mentions_since_id" updates x_cursor row id=1."""
    if key != "mentions_since_id":
        return
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE x_cursor SET since_id = %s, updated_at = now() WHERE id = 1",
                (value,),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def upsert_mention(
    tweet_id: str,
    author_id: str,
    author_username: Optional[str],
    text: str,
    created_at: Any,
    raw_json: Any = None,
    status: str = "new",
    conn=None,
) -> None:
    """Insert or update x_mentions by tweet_id. Uses ON CONFLICT DO UPDATE. status: new|skipped|replied|failed."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            created_ts = None
            if created_at is not None:
                try:
                    created_ts = str(created_at)
                except Exception:
                    created_ts = None
            raw_js = None
            if raw_json is not None:
                raw_js = json.dumps(raw_json) if not isinstance(raw_json, str) else raw_json
            cur.execute(
                """
                INSERT INTO x_mentions (tweet_id, author_id, author_username, text, created_at, raw_json, status)
                VALUES (%s, %s, %s, %s, %s::timestamptz, %s::jsonb, %s)
                ON CONFLICT (tweet_id) DO UPDATE SET
                    author_id = EXCLUDED.author_id,
                    author_username = EXCLUDED.author_username,
                    text = EXCLUDED.text,
                    created_at = EXCLUDED.created_at,
                    raw_json = COALESCE(EXCLUDED.raw_json, x_mentions.raw_json),
                    status = EXCLUDED.status
                """,
                (tweet_id, author_id, author_username or None, text, created_ts, raw_js, status),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def mark_replied(tweet_id: str, conn=None) -> None:
    """Set x_mentions.status = 'replied' for the given tweet_id."""
    mark_mention_status(tweet_id, "replied", conn=conn)


def mark_mention_status(tweet_id: str, status: str, conn=None) -> None:
    """Set x_mentions.status (e.g. 'replied', 'drafted')."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute("UPDATE x_mentions SET status = %s WHERE tweet_id = %s", (status, tweet_id))
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def is_replied(tweet_id: str, conn=None) -> bool:
    """True if mention exists and status is 'replied' or a row exists in x_replies."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM x_mentions WHERE tweet_id = %s AND status = 'replied'",
                (tweet_id,),
            )
            if cur.fetchone():
                return True
            cur.execute("SELECT 1 FROM x_replies WHERE mention_tweet_id = %s", (tweet_id,))
            return cur.fetchone() is not None
    finally:
        if own_conn:
            c.close()


def insert_reply(
    in_reply_to_tweet_id: str,
    reply_tweet_id: Optional[str],
    reply_text: str,
    created_at: Any = None,
    raw_json: Any = None,
    decision: Optional[str] = None,
    conn=None,
) -> None:
    """Insert one row into x_replies. decision defaults to 'posted' when reply_tweet_id set else 'error'.
    When reply_tweet_id is set, posted_at is set to now()."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            if decision is None:
                decision = "posted" if reply_tweet_id else "error"
            raw_js = None
            if raw_json is not None:
                raw_js = json.dumps(raw_json) if not isinstance(raw_json, str) else raw_json
            cur.execute(
                """
                INSERT INTO x_replies (mention_tweet_id, reply_tweet_id, reply_text, decision, raw_json, created_at, posted_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, COALESCE(%s::timestamptz, now()), CASE WHEN %s IS NOT NULL THEN now() ELSE NULL END)
                ON CONFLICT (mention_tweet_id) DO NOTHING
                """,
                (in_reply_to_tweet_id, reply_tweet_id, reply_text, decision, raw_js, created_at, reply_tweet_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def get_reply_posted_at(mention_tweet_id: str, conn=None) -> Any:
    """Return posted_at for the x_replies row for this mention, or None if no row or column missing."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT posted_at FROM x_replies WHERE mention_tweet_id = %s ORDER BY created_at DESC LIMIT 1",
                (mention_tweet_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        if own_conn:
            c.close()


def count_posts_last_hour(conn=None) -> int:
    """Return count of x_replies rows with reply_tweet_id set and posted_at in the last hour.
    Whitelist replies are stored in x_replies with source='whitelist', so a separate table is unnecessary."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM x_replies
                WHERE reply_tweet_id IS NOT NULL AND posted_at >= now() - interval '1 hour'
                """
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    finally:
        if own_conn:
            c.close()


def claim_replies_for_posting(limit: int, claimed_by: str, conn=None) -> list[tuple[int, str, str]]:
    """Claim up to `limit` unposted reply rows (mention or whitelist). Returns list of (id, in_reply_to_tweet_id, reply_text)."""
    if limit <= 0:
        return []
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                WITH sel AS (
                    SELECT id, COALESCE(mention_tweet_id, target_tweet_id) AS in_reply_to, reply_text
                    FROM x_replies
                    WHERE reply_tweet_id IS NULL
                      AND (error_text IS NULL OR error_text = '')
                      AND (mention_tweet_id IS NOT NULL OR target_tweet_id IS NOT NULL)
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE x_replies r
                SET post_claimed_at = now(), post_claimed_by = %s
                FROM sel
                WHERE r.id = sel.id
                RETURNING r.id, COALESCE(r.mention_tweet_id, r.target_tweet_id), r.reply_text
                """,
                (limit, claimed_by),
            )
            rows = [(int(r[0]), str(r[1]), str(r[2])) for r in cur.fetchall()]
        if own_conn:
            c.commit()
        return rows
    finally:
        if own_conn:
            c.close()


def update_reply_posted(reply_id: int, reply_tweet_id: str, conn=None) -> None:
    """Set reply_tweet_id and decision='posted', posted_at=now() for a claimed reply row."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_replies
                SET reply_tweet_id = %s, decision = 'posted', posted_at = now()
                WHERE id = %s
                """,
                (reply_tweet_id, reply_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def disable_posting(reason: str, until: Optional[timedelta] = None, conn=None) -> None:
    """
    Set posting disabled state on x_cursor (row id=1).
    Sets posting_enabled = false, posting_disabled_reason, posting_disabled_at, and optionally posting_disabled_until.
    until: optional timedelta; posting_disabled_until = now() + until. If None, until is NULL (no auto-expiry).
    """
    disabled_at = datetime.now(timezone.utc)
    disabled_until = (disabled_at + until) if until is not None else None
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_cursor
                SET posting_enabled = false,
                    posting_disabled_reason = %s,
                    posting_disabled_at = %s,
                    posting_disabled_until = %s
                WHERE id = 1
                """,
                (reason, disabled_at, disabled_until),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def enable_posting(conn=None) -> None:
    """
    Clears any posting-disabled state in persistent DB, re-enabling posting.
    Sets posting_enabled = true and clears posting_disabled_reason, posting_disabled_at, posting_disabled_until.
    """
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_cursor
                SET posting_enabled = true,
                    posting_disabled_reason = NULL,
                    posting_disabled_at = NULL,
                    posting_disabled_until = NULL
                WHERE id = 1
                """
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def record_post_success(conn=None) -> None:
    """Reset consecutive_post_failures to 0 on x_cursor (row id=1)."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE x_cursor SET consecutive_post_failures = 0 WHERE id = 1"
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def record_post_failure(error_text: str, conn=None) -> None:
    """Increment consecutive_post_failures and set last_post_error_at = now() on x_cursor (row id=1)."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_cursor
                SET consecutive_post_failures = COALESCE(consecutive_post_failures, 0) + 1,
                    last_post_error_at = now()
                WHERE id = 1
                """
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def get_consecutive_post_failures(conn=None) -> int:
    """Return consecutive_post_failures from x_cursor (row id=1). Returns 0 if column missing or NULL."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(consecutive_post_failures, 0) FROM x_cursor WHERE id = 1"
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    finally:
        if own_conn:
            c.close()


def get_posting_state(conn=None) -> tuple[bool, Optional[Any], Optional[str]]:
    """
    Return (posting_enabled, posting_disabled_until, posting_disabled_reason) for x_cursor row id=1.
    posting_disabled_until is datetime or None; posting_disabled_reason is str or None.
    """
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT posting_enabled, posting_disabled_until, posting_disabled_reason
                FROM x_cursor WHERE id = 1
                """
            )
            row = cur.fetchone()
            if not row:
                return (True, None, None)
            enabled = bool(row[0]) if row[0] is not None else True
            until = row[1]
            reason = str(row[2]) if row[2] is not None else None
            return (enabled, until, reason)
    finally:
        if own_conn:
            c.close()


def re_enable_posting(conn=None) -> None:
    """Set posting_enabled=true and clear disabled fields and consecutive_post_failures (for rate_limited_429 cooldown expiry)."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_cursor
                SET posting_enabled = true,
                    posting_disabled_reason = NULL,
                    posting_disabled_at = NULL,
                    posting_disabled_until = NULL,
                    consecutive_post_failures = 0
                WHERE id = 1
                """
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def apply_expired_disable_clear(conn=None) -> tuple[Optional[Any], Optional[str]]:
    """
    Disable is active ONLY if posting_disabled_until is not null AND now_utc < posting_disabled_until.
    If posting_disabled_until is null OR now_utc >= posting_disabled_until: allow posting, clear disable
    state in DB (reason=NULL, until=NULL), log, and return (None, None).
    Callers: disable_active = until is not None and now_utc < until;
    posting_enabled = X_POSTING_ENABLED and not disable_active.
    """
    _enabled, posting_disabled_until, posting_disabled_reason = get_posting_state(conn=conn)
    now_utc = datetime.now(timezone.utc)
    # Not active when until is null or now >= until
    if posting_disabled_until is None or now_utc >= posting_disabled_until:
        if posting_disabled_reason is not None or posting_disabled_until is not None:
            prev_reason, prev_until = posting_disabled_reason, posting_disabled_until
            re_enable_posting(conn=conn)
            logger.info(
                "posting_reenabled reason=disable_expired prev_reason=%s prev_until=%s",
                prev_reason,
                prev_until,
            )
        return (None, None)
    return (posting_disabled_until, posting_disabled_reason)


def set_fetch_error(error_text: str, conn=None) -> None:
    """Set last_fetch_error_at = now() and last_fetch_error_text on x_cursor (row id=1)."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_cursor
                SET last_fetch_error_at = now(), last_fetch_error_text = %s
                WHERE id = 1
                """,
                (error_text,),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def set_reply_blocked(reply_id: int, block_reason: str, error_text: str, conn=None) -> None:
    """Set x_replies row to decision='blocked', block_reason, error_text, and clear claim so it is not retried."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_replies
                SET decision = 'blocked', block_reason = %s, error_text = %s,
                    post_claimed_at = NULL, post_claimed_by = NULL
                WHERE id = %s
                """,
                (block_reason, error_text, reply_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def set_reply_error(reply_id: int, error_text: str, conn=None) -> None:
    """Set error_text on x_replies row so the reply is not retried until manually cleared."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE x_replies SET error_text = %s WHERE id = %s",
                (error_text, reply_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


# ---------- Whitelist and targets ----------


def list_enabled_whitelist_accounts(conn=None) -> list[tuple[str, str]]:
    """Return list of (handle, user_id) for x_whitelist_accounts where enabled = true and user_id IS NOT NULL (synced accounts only)."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT handle, user_id FROM x_whitelist_accounts WHERE enabled = true AND user_id IS NOT NULL ORDER BY handle"
            )
            return [(str(r[0]), str(r[1])) for r in cur.fetchall()]
    finally:
        if own_conn:
            c.close()


def list_whitelist_accounts_missing_user_id(conn=None) -> list[str]:
    """Return list of handle for x_whitelist_accounts where enabled = true and user_id IS NULL."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT handle FROM x_whitelist_accounts WHERE enabled = true AND user_id IS NULL ORDER BY handle"
            )
            return [str(r[0]) for r in cur.fetchall()]
    finally:
        if own_conn:
            c.close()


def update_whitelist_account_user_id(handle: str, user_id: str, conn=None) -> None:
    """Set user_id for the x_whitelist_accounts row with the given handle."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE x_whitelist_accounts SET user_id = %s WHERE handle = %s",
                (user_id, handle),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def get_whitelist_cursor(user_id: str, conn=None) -> Optional[str]:
    """Get since_id for user from x_whitelist_cursor. Returns None if no row or since_id is null."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT since_id FROM x_whitelist_cursor WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return str(row[0]) if row and row[0] is not None else None
    finally:
        if own_conn:
            c.close()


def set_whitelist_cursor(user_id: str, since_id: Optional[str], conn=None) -> None:
    """Insert or update x_whitelist_cursor for user_id. since_id can be None."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO x_whitelist_cursor (user_id, since_id, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET
                    since_id = EXCLUDED.since_id,
                    updated_at = now()
                """,
                (user_id, since_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def upsert_target(
    tweet_id: str,
    source: str,
    author_user_id: str,
    author_handle: Optional[str],
    tweet_text: str,
    tweet_created_at: Any,
    raw_json: Any = None,
    conn=None,
) -> None:
    """Insert or update x_targets by tweet_id. Uses ON CONFLICT DO UPDATE."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        created_ts = None
        if tweet_created_at is not None:
            try:
                created_ts = str(tweet_created_at)
            except Exception:
                created_ts = None
        raw_js = None
        if raw_json is not None:
            raw_js = json.dumps(raw_json) if not isinstance(raw_json, str) else raw_json
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO x_targets (tweet_id, source, author_user_id, author_handle, tweet_text, tweet_created_at, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s::jsonb)
                ON CONFLICT (tweet_id) DO UPDATE SET
                    source = EXCLUDED.source,
                    author_user_id = EXCLUDED.author_user_id,
                    author_handle = EXCLUDED.author_handle,
                    tweet_text = EXCLUDED.tweet_text,
                    tweet_created_at = EXCLUDED.tweet_created_at,
                    raw_json = COALESCE(EXCLUDED.raw_json, x_targets.raw_json)
                """,
                (tweet_id, source, author_user_id, author_handle or None, tweet_text, created_ts, raw_js),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def list_unreplied_targets(
    limit: int = 50,
    conn=None,
) -> list[tuple[str, str, str, Optional[str], str, Any, Any, Optional[Any]]]:
    """
    Return x_targets rows that have no x_replies row with target_tweet_id = tweet_id.
    Each row: (tweet_id, source, author_user_id, author_handle, tweet_text, tweet_created_at, inserted_at, raw_json).
    Order by tweet_created_at asc so oldest first.
    """
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            sql = """
                SELECT t.tweet_id, t.source, t.author_user_id, t.author_handle, t.tweet_text,
                       t.tweet_created_at, t.inserted_at, t.raw_json
                FROM x_targets t
                LEFT JOIN x_replies r ON r.target_tweet_id = t.tweet_id
                WHERE r.target_tweet_id IS NULL
                ORDER BY t.tweet_created_at ASC
                LIMIT %s
                """
            cur.execute(sql, (max(1, limit),))
            return [
                (
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    str(r[3]) if r[3] is not None else None,
                    str(r[4]),
                    r[5],
                    r[6],
                    r[7],
                )
                for r in cur.fetchall()
            ]
    finally:
        if own_conn:
            c.close()


def insert_whitelist_reply(
    target_tweet_id: str,
    reply_text: str,
    decision: str,
    conn=None,
) -> None:
    """Insert or update x_replies for a whitelist target. mention_tweet_id is NULL (no x_mentions row). Idempotent via ON CONFLICT (target_tweet_id)."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO x_replies (target_tweet_id, mention_tweet_id, reply_tweet_id, reply_text, decision, source)
                VALUES (%s, NULL, NULL, %s, %s, 'whitelist')
                ON CONFLICT (target_tweet_id) DO UPDATE SET
                    reply_text = EXCLUDED.reply_text,
                    decision = EXCLUDED.decision,
                    source = EXCLUDED.source
                """,
                (target_tweet_id, reply_text, decision),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def select_unreplied_targets(
    limit: Optional[int] = None,
    conn=None,
) -> list[tuple[str, str, str, Optional[str], str, Any, Any, Optional[Any]]]:
    """
    Return x_targets rows that have no corresponding x_replies row (mention_tweet_id = tweet_id).
    Each row: (tweet_id, source, author_user_id, author_handle, tweet_text, tweet_created_at, inserted_at, raw_json).
    Optional limit; order by tweet_created_at asc so oldest first.
    """
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            sql = """
                SELECT t.tweet_id, t.source, t.author_user_id, t.author_handle, t.tweet_text,
                       t.tweet_created_at, t.inserted_at, t.raw_json
                FROM x_targets t
                LEFT JOIN x_replies r ON r.mention_tweet_id = t.tweet_id
                WHERE r.mention_tweet_id IS NULL
                ORDER BY t.tweet_created_at ASC
                """
            if limit is not None and limit > 0:
                sql += " LIMIT %s"
                cur.execute(sql, (limit,))
            else:
                cur.execute(sql)
            return [
                (
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    str(r[3]) if r[3] is not None else None,
                    str(r[4]),
                    r[5],
                    r[6],
                    r[7],
                )
                for r in cur.fetchall()
            ]
    finally:
        if own_conn:
            c.close()


def update_target_reply_decision(
    tweet_id: str,
    decision: str,
    score: float,
    reason: str,
    conn=None,
) -> None:
    """Set reply_decision, reply_score, reply_reason on x_targets for audit."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_targets
                SET reply_decision = %s, reply_score = %s, reply_reason = %s
                WHERE tweet_id = %s
                """,
                (decision, score, reason, tweet_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def upsert_target_reply(
    target_tweet_id: str,
    reply_text: Optional[str],
    decision: str,
    block_reason: Optional[str] = None,
    source: str = "whitelist",
    conn=None,
) -> None:
    """Insert or update x_target_replies by target_tweet_id. Use decision='blocked' for skip, reply_text null; decision='drafted' for draft with reply_text."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO x_target_replies (target_tweet_id, reply_text, decision, block_reason, source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (target_tweet_id) DO UPDATE SET
                    reply_text = EXCLUDED.reply_text,
                    decision = EXCLUDED.decision,
                    block_reason = EXCLUDED.block_reason,
                    source = EXCLUDED.source
                """,
                (target_tweet_id, reply_text, decision, block_reason, source),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def claim_whitelist_replies_for_posting(
    limit: int,
    claimed_by: str,
    conn=None,
) -> list[tuple[str, str]]:
    """Claim up to `limit` whitelist drafts (decision='drafted', post_claimed_at IS NULL). Returns list of (target_tweet_id, reply_text)."""
    if limit <= 0:
        return []
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                WITH sel AS (
                    SELECT target_tweet_id, reply_text
                    FROM x_target_replies
                    WHERE decision = 'drafted'
                      AND post_claimed_at IS NULL
                      AND (error_text IS NULL OR error_text = '')
                      AND reply_text IS NOT NULL
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE x_target_replies r
                SET post_claimed_at = now(), post_claimed_by = %s
                FROM sel
                WHERE r.target_tweet_id = sel.target_tweet_id
                RETURNING r.target_tweet_id, r.reply_text
                """,
                (limit, claimed_by),
            )
            rows = [(str(r[0]), str(r[1])) for r in cur.fetchall()]
        if own_conn:
            c.commit()
        return rows
    finally:
        if own_conn:
            c.close()


def update_target_reply_posted(
    target_tweet_id: str,
    reply_tweet_id: str,
    conn=None,
) -> None:
    """Set reply_tweet_id and posted_at=now() for a claimed whitelist reply row."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_target_replies
                SET reply_tweet_id = %s, posted_at = now()
                WHERE target_tweet_id = %s
                """,
                (reply_tweet_id, target_tweet_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def set_target_reply_blocked(
    target_tweet_id: str,
    block_reason: str,
    error_text: str,
    conn=None,
) -> None:
    """Set x_target_replies to decision='blocked', block_reason, error_text; clear claim so it is not retried."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_target_replies
                SET decision = 'blocked', block_reason = %s, error_text = %s,
                    post_claimed_at = NULL, post_claimed_by = NULL
                WHERE target_tweet_id = %s
                """,
                (block_reason, error_text, target_tweet_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def set_target_reply_error(target_tweet_id: str, error_text: str, conn=None) -> None:
    """Set error_text on x_target_replies and clear claim so it is not retried until manually cleared."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_target_replies
                SET error_text = %s, post_claimed_at = NULL, post_claimed_by = NULL
                WHERE target_tweet_id = %s
                """,
                (error_text, target_tweet_id),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def ensure_standalone_state_table(conn=None) -> None:
    """Idempotent: create x_standalone_state if missing, insert seed row (id=true), add backoff columns if missing."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS x_standalone_state (
                  id             BOOLEAN PRIMARY KEY DEFAULT true,
                  last_posted_at TIMESTAMPTZ,
                  last_post_hash TEXT,
                  last_mode      TEXT,
                  last_angle     TEXT
                )
                """
            )
            cur.execute(
                """
                INSERT INTO x_standalone_state (id) VALUES (true)
                ON CONFLICT (id) DO NOTHING
                """
            )
            for col in ("next_allowed_at", "last_standalone_error_at", "second_last_standalone_error_at"):
                cur.execute(
                    f"ALTER TABLE x_standalone_state ADD COLUMN IF NOT EXISTS {col} TIMESTAMPTZ"
                )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def get_standalone_last_angle(conn=None) -> Optional[str]:
    """Return last_angle from the single row in x_standalone_state, or None."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT last_angle FROM x_standalone_state WHERE id = true")
            row = cur.fetchone()
            return str(row[0]).strip() if row and row[0] and str(row[0]).strip() else None
    finally:
        if own_conn:
            c.close()


def get_standalone_state(conn=None) -> dict:
    """Return the single row of x_standalone_state as dict: last_posted_at, last_post_hash, last_mode, last_angle, next_allowed_at, last_standalone_error_at, second_last_standalone_error_at."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT last_posted_at, last_post_hash, last_mode, last_angle,
                       next_allowed_at, last_standalone_error_at, second_last_standalone_error_at
                FROM x_standalone_state WHERE id = true
                """
            )
            row = cur.fetchone()
            if not row:
                return {
                    "last_posted_at": None,
                    "last_post_hash": None,
                    "last_mode": None,
                    "last_angle": None,
                    "next_allowed_at": None,
                    "last_standalone_error_at": None,
                    "second_last_standalone_error_at": None,
                }
            return {
                "last_posted_at": row[0],
                "last_post_hash": str(row[1]).strip() if row[1] and str(row[1]).strip() else None,
                "last_mode": str(row[2]).strip() if row[2] and str(row[2]).strip() else None,
                "last_angle": str(row[3]).strip() if row[3] and str(row[3]).strip() else None,
                "next_allowed_at": row[4],
                "last_standalone_error_at": row[5],
                "second_last_standalone_error_at": row[6],
            }
    finally:
        if own_conn:
            c.close()


def update_standalone_state(
    conn=None,
    *,
    last_posted_at: Optional[Any] = None,
    last_post_hash: Optional[str] = None,
    last_mode: Optional[str] = None,
    last_angle: Optional[str] = None,
    next_allowed_at: Optional[Any] = None,
    last_standalone_error_at: Optional[Any] = None,
    second_last_standalone_error_at: Optional[Any] = None,
) -> None:
    """Update the single row in x_standalone_state. Pass only fields to set."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            updates = []
            args = []
            if last_posted_at is not None:
                updates.append("last_posted_at = %s")
                args.append(last_posted_at)
            if last_post_hash is not None:
                updates.append("last_post_hash = %s")
                args.append(last_post_hash)
            if last_mode is not None:
                updates.append("last_mode = %s")
                args.append(last_mode)
            if last_angle is not None:
                updates.append("last_angle = %s")
                args.append(last_angle)
            if next_allowed_at is not None:
                updates.append("next_allowed_at = %s")
                args.append(next_allowed_at)
            if last_standalone_error_at is not None:
                updates.append("last_standalone_error_at = %s")
                args.append(last_standalone_error_at)
            if second_last_standalone_error_at is not None:
                updates.append("second_last_standalone_error_at = %s")
                args.append(second_last_standalone_error_at)
            if not updates:
                return
            args.append(True)
            cur.execute(
                "UPDATE x_standalone_state SET " + ", ".join(updates) + " WHERE id = %s",
                tuple(args),
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def record_standalone_posting_error(conn=None) -> None:
    """
    Record a standalone posting error and optionally set next_allowed_at.
    If the last 2 standalone runs ended in posting errors within 30 minutes, set next_allowed_at = now() + 2 hours.
    """
    now = datetime.now(timezone.utc)
    state = get_standalone_state(conn=conn)
    last_err = state.get("last_standalone_error_at")
    second_err = state.get("second_last_standalone_error_at")
    # Shift: second = last, last = now
    if last_err is not None and (now - last_err).total_seconds() <= 30 * 60:
        # Two errors within 30 min -> backoff 2 hours
        next_ok = now + timedelta(hours=2)
        update_standalone_state(
            conn=conn,
            last_standalone_error_at=now,
            second_last_standalone_error_at=last_err,
            next_allowed_at=next_ok,
        )
    else:
        update_standalone_state(
            conn=conn,
            last_standalone_error_at=now,
            second_last_standalone_error_at=last_err,
        )


def clear_standalone_backoff(conn=None) -> None:
    """Clear standalone error backoff (next_allowed_at and error timestamps) after a successful post."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                UPDATE x_standalone_state
                SET next_allowed_at = NULL, last_standalone_error_at = NULL, second_last_standalone_error_at = NULL
                WHERE id = true
                """
            )
        if own_conn:
            c.commit()
    finally:
        if own_conn:
            c.close()


def insert_standalone_reply(reply_text: str, conn=None) -> Optional[int]:
    """Insert one x_replies row for a standalone post (mention_tweet_id and target_tweet_id NULL, source='standalone'). Returns id."""
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO x_replies (mention_tweet_id, target_tweet_id, reply_tweet_id, reply_text, decision, source)
                VALUES (NULL, NULL, NULL, %s, 'drafted', 'standalone')
                RETURNING id
                """,
                (reply_text,),
            )
            row = cur.fetchone()
            reply_id = int(row[0]) if row and row[0] is not None else None
        if own_conn:
            c.commit()
        return reply_id
    finally:
        if own_conn:
            c.close()
