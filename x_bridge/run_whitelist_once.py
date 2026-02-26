"""
Run whitelist fetch once: load enabled accounts, fetch their tweets since cursor,
filter out replies/retweets and old tweets, upsert into x_targets, advance cursor.

Usage: python3 -m x_bridge.run_whitelist_once

Requires: DATABASE_URL, X_* env vars. Uses WHITELIST_REPLY_MAX_AGE_MINUTES (default 30).
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from x_bridge import config  # noqa: F401 - load .env deterministically
from x_bridge import db
from x_bridge import x_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _parse_positive_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw.strip())
        return v if v > 0 else default
    except ValueError:
        return default


def _parse_bool_default_false(env_key: str) -> bool:
    raw = (os.getenv(env_key) or "").strip().lower()
    return raw in ("1", "true", "yes")


whitelist_reply_max_age_minutes = _parse_positive_int("WHITELIST_REPLY_MAX_AGE_MINUTES", 30)
whitelist_reply_enabled = _parse_bool_default_false("WHITELIST_REPLY_ENABLED")
x_dry_run = _parse_bool_default_false("X_DRY_RUN")


def _is_reply_or_retweet(referenced_tweets: list) -> bool:
    """True if tweet has referenced_tweets with type replied_to or retweeted."""
    if not referenced_tweets:
        return False
    for ref in referenced_tweets:
        t = getattr(ref, "type", None) or (ref.get("type") if isinstance(ref, dict) else None)
        if t in ("replied_to", "retweeted"):
            return True
    return False


def _parse_created_at(created_at) -> Optional[datetime]:
    """Return datetime (naive UTC or timezone-aware) for comparison, or None."""
    if created_at is None:
        return None
    if isinstance(created_at, datetime):
        return created_at.astimezone(timezone.utc) if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def run_ingest_and_draft(conn) -> tuple[int, int, int]:
    """
    Run whitelist ingest (fetch tweets, upsert x_targets, advance cursors) and draft phase
    (unreplied targets -> heuristics -> generate -> insert_whitelist_reply). Uses existing conn;
    does not acquire a lock or commit. Does not post. Returns (targets_inserted, drafts_created, skipped).
    """
    targets_inserted = 0
    drafts_created = 0
    targets_skipped = 0

    accounts = db.list_enabled_whitelist_accounts(conn=conn)
    if not accounts:
        logger.info("whitelist_run accounts=0")
        return (0, 0, 0)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=whitelist_reply_max_age_minutes)

    for handle, user_id in accounts:
        fetched = 0
        inserted = 0
        skipped = 0
        advanced_since_id = None

        cursor = db.get_whitelist_cursor(user_id, conn=conn)
        try:
            tweets = x_client.get_user_tweets(
                user_id,
                since_id=cursor,
                max_results=100,
                exclude=["replies", "retweets"],
            )
        except Exception as e:
            logger.warning("whitelist_fetch_error handle=%s user_id=%s error=%s", handle, user_id, e)
            continue

        fetched = len(tweets)
        all_tweet_ids = []

        for t in tweets:
            tweet_id = t.get("tweet_id") or ""
            if not tweet_id:
                continue
            all_tweet_ids.append(tweet_id)

            refs = t.get("referenced_tweets") or []
            if _is_reply_or_retweet(refs):
                skipped += 1
                continue

            created_at = _parse_created_at(t.get("created_at"))
            if created_at is not None and created_at < cutoff:
                skipped += 1
                continue

            reply_settings = t.get("reply_settings")
            if reply_settings != "everyone":
                logger.info(
                    "whitelist_skip_reply_restricted tweet_id=%s reply_settings=%s",
                    tweet_id,
                    reply_settings,
                )
                skipped += 1
                continue

            db.upsert_target(
                tweet_id=tweet_id,
                source="whitelist",
                author_user_id=t.get("author_id") or user_id,
                author_handle=t.get("author_username") or handle,
                tweet_text=t.get("text") or "",
                tweet_created_at=created_at or datetime.now(timezone.utc),
                raw_json=t.get("raw_json"),
                conn=conn,
            )
            inserted += 1
        targets_inserted += inserted

        if all_tweet_ids:
            try:
                numeric_ids = [int(tid) for tid in all_tweet_ids if str(tid).isdigit()]
                advanced_since_id = str(max(numeric_ids)) if numeric_ids else str(all_tweet_ids[-1])
            except (ValueError, TypeError):
                advanced_since_id = str(all_tweet_ids[-1])
        if advanced_since_id:
            db.set_whitelist_cursor(user_id, advanced_since_id, conn=conn)

        logger.info(
            "whitelist_account handle=%s user_id=%s fetched=%s inserted=%s skipped=%s advanced_since_id=%s",
            handle,
            user_id,
            fetched,
            inserted,
            skipped,
            advanced_since_id,
        )

    targets_processed = 0
    numbers_safe_drafts_created = 0
    skipped_eligible: list = []  # (tweet_id, tweet_text, score, reason, constraints) for wildcard
    if whitelist_reply_enabled:
        from ingest.reply_engine import generate_reply_whitelist_text

        from x_bridge.whitelist_reply_heuristics import whitelist_should_reply_and_persist

        def _reply_contains_digit(text: str) -> bool:
            return bool(text and re.search(r"\d", text))

        def _do_draft(tweet_id: str, tweet_text: str, constraints: dict) -> bool:
            """Generate and insert one draft. Returns True if draft created."""
            needs_numbers_safe = constraints.get("needs_numbers_safe_reply", False)
            try:
                reply_text = generate_reply_whitelist_text(tweet_text, conn, needs_numbers_safe_reply=needs_numbers_safe)
            except Exception as e:
                logger.warning("whitelist_draft_error tweet_id=%s error=%s", tweet_id, e)
                return False
            if needs_numbers_safe and _reply_contains_digit(reply_text):
                try:
                    reply_text = generate_reply_whitelist_text(tweet_text, conn, needs_numbers_safe_reply=True)
                except Exception as e:
                    logger.warning("whitelist_draft_retry_error tweet_id=%s error=%s", tweet_id, e)
                if _reply_contains_digit(reply_text):
                    db.upsert_target_reply(
                        target_tweet_id=tweet_id,
                        reply_text=None,
                        decision="blocked",
                        block_reason="whitelist_skip:numbers_reply_failed",
                        source="whitelist",
                        conn=conn,
                    )
                    return False
            decision_str = "dry_run" if x_dry_run else "drafted"
            db.insert_whitelist_reply(
                target_tweet_id=tweet_id,
                reply_text=reply_text,
                decision=decision_str,
                conn=conn,
            )
            return True

        unreplied = db.list_unreplied_targets(limit=50, conn=conn)
        for row in unreplied:
            tweet_id, _source, _author_user_id, _author_handle, tweet_text, _tweet_created_at, _inserted_at, _raw_json = row
            targets_processed += 1
            decision, score, reason, constraints = whitelist_should_reply_and_persist(tweet_id, tweet_text, conn=conn)
            if decision == "skip":
                targets_skipped += 1
                if constraints.get("eligible"):
                    skipped_eligible.append((tweet_id, tweet_text, score, reason, constraints))
                continue
            if _do_draft(tweet_id, tweet_text, constraints):
                drafts_created += 1
                if constraints.get("needs_numbers_safe_reply"):
                    numbers_safe_drafts_created += 1

        # One wildcard per run: if no candidates met MIN_REPLY_SCORE, pick best-scoring eligible and reply anyway
        if drafts_created == 0 and skipped_eligible:
            best = max(skipped_eligible, key=lambda x: x[2])
            tweet_id, tweet_text, score, reason, constraints = best
            db.update_target_reply_decision(tweet_id, "reply", score, reason + ";wildcard", conn=conn)
            logger.info("whitelist_wildcard_used tweet_id=%s score=%s", tweet_id, score)
            if _do_draft(tweet_id, tweet_text, constraints):
                drafts_created += 1
                if constraints.get("needs_numbers_safe_reply"):
                    numbers_safe_drafts_created += 1

        logger.info(
            "whitelist_unreplied targets_processed=%s drafts_created=%s targets_skipped=%s numbers_safe_drafts=%s",
            targets_processed,
            drafts_created,
            targets_skipped,
            numbers_safe_drafts_created,
        )

    return (targets_inserted, drafts_created, targets_skipped)


def run_once() -> None:
    """Standalone entrypoint: get connection, acquire advisory lock, run_ingest_and_draft, commit."""
    conn = db.get_connection()
    try:
        conn.autocommit = False
        if not db.try_advisory_xact_lock(conn):
            conn.rollback()
            conn.close()
            logger.info("whitelist_run_skipped_lock_held")
            return
        logger.info("whitelist_advisory_lock_acquired key=%s", db.ADVISORY_LOCK_KEY)
        run_ingest_and_draft(conn)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    run_once()
