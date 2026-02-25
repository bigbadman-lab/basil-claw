"""
Run the X mentions loop once: fetch mentions, reply with Basil engine, store in DB.

Usage: python3 -m x_bridge.run_mentions_once

Requires: DATABASE_URL, X_* env vars, OPENAI_API_KEY. Loads .env via reply_engine.
On X API failure exits non-zero. On per-mention failure logs and continues.
"""

import logging
import os
import re
import sys

from dotenv import load_dotenv
load_dotenv()

from x_bridge import db
from x_bridge import x_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

X_USER_ID = (os.getenv("X_USER_ID") or "").strip()

_DRY_RUN_RAW = (os.getenv("X_DRY_RUN") or "").strip().lower()
X_DRY_RUN = _DRY_RUN_RAW in ("1", "true", "yes")

X_REPLY_MAX_LEN = 280
X_REPLY_ELLIPSIS = "..."


def _numeric_id(tweet_id: str) -> int:
    """For sorting; treat non-numeric as 0."""
    try:
        return int(tweet_id)
    except (ValueError, TypeError):
        return 0


def _is_retweet(text: str) -> bool:
    """True if text is a retweet (starts with RT )."""
    return (text or "").strip().upper().startswith("RT ")


def _is_empty_mention(text: str) -> bool:
    """True if text after removing @handles has < 3 characters."""
    tokens = [t for t in (text or "").split() if not t.startswith("@")]
    rest = " ".join(tokens).strip()
    return len(rest) < 3


def _truncate_reply_to_limit(reply: str, max_len: int = X_REPLY_MAX_LEN, suffix: str = X_REPLY_ELLIPSIS) -> str:
    """Single line; if over max_len, truncate at word boundary and add suffix."""
    reply = re.sub(r"\s+", " ", (reply or "").strip())
    if len(reply) <= max_len:
        return reply
    take = max_len - len(suffix)
    if take <= 0:
        return suffix[:max_len]
    s = reply[: take + 1]
    s = s.rsplit(" ", 1)[0] if " " in s else s.rstrip()
    return (s or reply[:take]).strip() + suffix


def run_once() -> None:
    if X_DRY_RUN:
        logger.info("DRY RUN enabled: will not post to X")

    cursor = db.get_cursor("mentions_since_id")
    try:
        mentions = x_client.get_mentions(since_id=cursor, max_results=50)
    except Exception as e:
        logger.error("X API get_mentions failed: %s", e)
        sys.exit(1)

    if not mentions:
        logger.info("No new mentions.")
        return

    mentions_sorted = sorted(mentions, key=lambda m: _numeric_id(m.get("tweet_id") or "0"))
    newest_tweet_id = max((m.get("tweet_id") or "" for m in mentions_sorted), key=_numeric_id, default=cursor or "0")

    from ingest.reply_engine import generate_reply_for_tweet

    for m in mentions_sorted:
        tweet_id = m.get("tweet_id") or ""
        author_id = m.get("author_id") or ""
        author_username = m.get("author_username") or ""
        text = m.get("text") or ""
        created_at = m.get("created_at")
        raw_json = m.get("raw_json")

        if author_id == X_USER_ID:
            try:
                db.upsert_mention(
                    tweet_id=tweet_id,
                    author_id=author_id,
                    author_username=author_username,
                    text=text,
                    created_at=created_at,
                    raw_json=raw_json,
                    status="skipped",
                )
            except Exception as e:
                logger.warning("upsert_mention (skipped self) failed for %s: %s", tweet_id, e)
            continue

        if _is_retweet(text):
            try:
                db.upsert_mention(
                    tweet_id=tweet_id,
                    author_id=author_id,
                    author_username=author_username,
                    text=text,
                    created_at=created_at,
                    raw_json=raw_json,
                    status="skipped",
                )
            except Exception as e:
                logger.warning("upsert_mention (skipped RT) failed for %s: %s", tweet_id, e)
            continue

        if _is_empty_mention(text):
            try:
                db.upsert_mention(
                    tweet_id=tweet_id,
                    author_id=author_id,
                    author_username=author_username,
                    text=text,
                    created_at=created_at,
                    raw_json=raw_json,
                    status="skipped",
                )
            except Exception as e:
                logger.warning("upsert_mention (skipped empty) failed for %s: %s", tweet_id, e)
            continue

        try:
            db.upsert_mention(
                tweet_id=tweet_id,
                author_id=author_id,
                author_username=author_username,
                text=text,
                created_at=created_at,
                raw_json=raw_json,
            )
        except Exception as e:
            logger.warning("upsert_mention failed for %s: %s", tweet_id, e)
            continue

        if db.is_replied(tweet_id):
            continue

        try:
            reply_text = generate_reply_for_tweet(text)
        except Exception as e:
            logger.warning("generate_reply_for_tweet failed for %s: %s", tweet_id, e)
            continue

        reply_text = _truncate_reply_to_limit(reply_text)

        if X_DRY_RUN:
            try:
                db.insert_reply(
                    in_reply_to_tweet_id=tweet_id,
                    reply_tweet_id=None,
                    reply_text=reply_text,
                    decision="dry_run",
                )
                db.mark_mention_status(tweet_id, "drafted")
            except Exception as e:
                logger.warning("insert_reply/mark_mention_status (dry_run) failed for %s: %s", tweet_id, e)
            continue

        try:
            reply_tweet_id = x_client.post_reply(reply_text, tweet_id)
        except Exception as e:
            logger.warning("post_reply failed for %s: %s", tweet_id, e)
            try:
                db.insert_reply(
                    in_reply_to_tweet_id=tweet_id,
                    reply_tweet_id=None,
                    reply_text=reply_text,
                )
            except Exception as e2:
                logger.warning("insert_reply (error) failed: %s", e2)
            continue

        try:
            db.insert_reply(
                in_reply_to_tweet_id=tweet_id,
                reply_tweet_id=reply_tweet_id,
                reply_text=reply_text,
            )
            db.mark_replied(tweet_id)
        except Exception as e:
            logger.warning("insert_reply/mark_replied failed for %s: %s", tweet_id, e)

    if newest_tweet_id and newest_tweet_id != cursor:
        try:
            db.set_cursor("mentions_since_id", newest_tweet_id)
        except Exception as e:
            logger.warning("set_cursor failed: %s", e)


if __name__ == "__main__":
    run_once()
