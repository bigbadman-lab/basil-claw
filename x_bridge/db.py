"""
DB helpers for X mentions loop: cursor, x_mentions, x_replies.

Uses DATABASE_URL (psycopg2). One transaction per logical operation where appropriate.
"""

import json
import os
from typing import Any, Optional

import psycopg2

_DATABASE_URL = os.environ.get("DATABASE_URL")


def _connect():
    if not _DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be set.")
    return psycopg2.connect(_DATABASE_URL)


def get_cursor(key: str) -> Optional[str]:
    """
    Get cursor value for key. Key "mentions_since_id" maps to x_cursor row id=1.
    Returns None if not set.
    """
    if key != "mentions_since_id":
        return None
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT since_id FROM x_cursor WHERE id = 1")
            row = cur.fetchone()
            if row and row[0] is not None:
                return str(row[0])
            return None
    finally:
        conn.close()


def set_cursor(key: str, value: str) -> None:
    """Set cursor value. Key "mentions_since_id" updates x_cursor row id=1."""
    if key != "mentions_since_id":
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE x_cursor SET since_id = %s, updated_at = now() WHERE id = 1",
                (value,),
            )
        conn.commit()
    finally:
        conn.close()


def upsert_mention(
    tweet_id: str,
    author_id: str,
    author_username: Optional[str],
    text: str,
    created_at: Any,
    raw_json: Any = None,
    status: str = "new",
) -> None:
    """Insert or update x_mentions by tweet_id. Uses ON CONFLICT DO UPDATE. status: new|skipped|replied|failed."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
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
        conn.commit()
    finally:
        conn.close()


def mark_replied(tweet_id: str) -> None:
    """Set x_mentions.status = 'replied' for the given tweet_id."""
    mark_mention_status(tweet_id, "replied")


def mark_mention_status(tweet_id: str, status: str) -> None:
    """Set x_mentions.status (e.g. 'replied', 'drafted')."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE x_mentions SET status = %s WHERE tweet_id = %s", (status, tweet_id))
        conn.commit()
    finally:
        conn.close()


def is_replied(tweet_id: str) -> bool:
    """True if mention exists and status is 'replied' or a row exists in x_replies."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM x_mentions WHERE tweet_id = %s AND status = 'replied'",
                (tweet_id,),
            )
            if cur.fetchone():
                return True
            cur.execute("SELECT 1 FROM x_replies WHERE mention_tweet_id = %s", (tweet_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def insert_reply(
    in_reply_to_tweet_id: str,
    reply_tweet_id: Optional[str],
    reply_text: str,
    created_at: Any = None,
    raw_json: Any = None,
    decision: Optional[str] = None,
) -> None:
    """Insert one row into x_replies. decision defaults to 'posted' when reply_tweet_id set else 'error'."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if decision is None:
                decision = "posted" if reply_tweet_id else "error"
            raw_js = None
            if raw_json is not None:
                raw_js = json.dumps(raw_json) if not isinstance(raw_json, str) else raw_json
            cur.execute(
                """
                INSERT INTO x_replies (mention_tweet_id, reply_tweet_id, reply_text, decision, raw_json, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, COALESCE(%s::timestamptz, now()))
                """,
                (in_reply_to_tweet_id, reply_tweet_id, reply_text, decision, raw_js, created_at),
            )
        conn.commit()
    finally:
        conn.close()
