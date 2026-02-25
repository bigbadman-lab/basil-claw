"""
DB helpers for X mentions loop: cursor, x_mentions, x_replies.

Uses DATABASE_URL (psycopg2). One transaction per logical operation where appropriate.
Optional conn= allows running all operations in a single transaction (e.g. under advisory lock).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2

_DATABASE_URL = os.environ.get("DATABASE_URL")

# Advisory lock key for run_mentions_once: only one run holds this lock per transaction.
ADVISORY_LOCK_KEY = 1234567890


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be set.")
    return psycopg2.connect(_DATABASE_URL)


def get_connection():
    """Return a new connection (caller must close). Used to hold one transaction for the whole run."""
    return _connect()


def try_advisory_xact_lock(conn) -> bool:
    """Try to acquire transaction-level advisory lock. Returns True if acquired. Lock held until commit/rollback."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
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
    """Return count of x_replies rows with reply_tweet_id set and posted_at in the last hour."""
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
    """Claim up to `limit` unposted reply rows (reply_tweet_id IS NULL and no error_text). Returns list of (id, mention_tweet_id, reply_text)."""
    if limit <= 0:
        return []
    own_conn = conn is None
    c = conn or _connect()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                WITH sel AS (
                    SELECT id, mention_tweet_id, reply_text
                    FROM x_replies
                    WHERE reply_tweet_id IS NULL
                      AND (error_text IS NULL OR error_text = '')
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE x_replies r
                SET post_claimed_at = now(), post_claimed_by = %s
                FROM sel
                WHERE r.id = sel.id
                RETURNING r.id, r.mention_tweet_id, r.reply_text
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
