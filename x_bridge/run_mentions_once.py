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
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from x_bridge import db
from x_bridge import x_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

X_USER_ID = (os.getenv("X_USER_ID") or "").strip()

_DRY_RUN_RAW = (os.getenv("X_DRY_RUN") or "").strip().lower()
X_DRY_RUN = _DRY_RUN_RAW in ("1", "true", "yes")


def _parse_positive_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw.strip())
        return v if v > 0 else default
    except ValueError:
        return default


def _parse_float(env_key: str, default: float) -> float:
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _parse_bool_default_false(env_key: str) -> bool:
    raw = (os.getenv(env_key) or "").strip().lower()
    return raw in ("1", "true", "yes")


max_posts_per_run = _parse_positive_int("MAX_POSTS_PER_RUN", 50)
hourly_post_cap = _parse_positive_int("HOURLY_POST_CAP", 300)

whitelist_reply_enabled = _parse_bool_default_false("WHITELIST_REPLY_ENABLED")
whitelist_engagement_mode = _parse_positive_int("WHITELIST_ENGAGEMENT_MODE", 1)
whitelist_max_replies_per_run = _parse_positive_int("WHITELIST_MAX_REPLIES_PER_RUN", 3)
whitelist_reply_prob_default = _parse_float("WHITELIST_REPLY_PROB_DEFAULT", 0.35)
whitelist_reply_max_age_minutes = _parse_positive_int("WHITELIST_REPLY_MAX_AGE_MINUTES", 30)

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
    conn = db.get_connection()
    try:
        conn.autocommit = False
        if not db.try_advisory_xact_lock(conn):
            conn.rollback()
            conn.close()
            logger.info("run_skipped_lock_held")
            sys.exit(0)
        logger.info("advisory_lock_acquired key=%s", db.ADVISORY_LOCK_KEY)

        posted_last_hour = db.count_posts_last_hour(conn=conn)
        remaining_hour_budget = max(0, hourly_post_cap - posted_last_hour)
        allowed_this_run = min(max_posts_per_run, remaining_hour_budget)
        _posting_enabled_env = (os.getenv("X_POSTING_ENABLED") or "").strip().lower() in ("1", "true", "yes")
        whitelist_targets_inserted = 0
        whitelist_drafts_created = 0
        whitelist_skipped = 0
        logger.info(
            "run_start X_DRY_RUN=%s X_POSTING_ENABLED=%s max_posts_per_run=%s hourly_post_cap=%s posted_last_hour=%s remaining_hour_budget=%s allowed_this_run=%s whitelist_targets_inserted=%s whitelist_drafts_created=%s whitelist_skipped=%s",
            X_DRY_RUN,
            _posting_enabled_env,
            max_posts_per_run,
            hourly_post_cap,
            posted_last_hour,
            remaining_hour_budget,
            allowed_this_run,
            whitelist_targets_inserted,
            whitelist_drafts_created,
            whitelist_skipped,
        )
        logger.info(
            "whitelist_config whitelist_reply_enabled=%s whitelist_engagement_mode=%s whitelist_max_replies_per_run=%s whitelist_reply_prob_default=%s whitelist_reply_max_age_minutes=%s",
            whitelist_reply_enabled,
            whitelist_engagement_mode,
            whitelist_max_replies_per_run,
            whitelist_reply_prob_default,
            whitelist_reply_max_age_minutes,
        )
        if X_DRY_RUN:
            logger.info("DRY RUN enabled: will not post to X")

        cursor = db.get_cursor("mentions_since_id", conn=conn)
        fetch_failed = False
        try:
            mentions = x_client.get_mentions(since_id=cursor, max_results=50)
        except Exception as e:
            fetch_failed = True
            mentions = []
            err_str = str(e)
            response = getattr(e, "response", None)
            status_code = getattr(response, "status_code", None) if response else None
            logger.info("mentions_fetch_failed status_code=%s error=%s", status_code, err_str)
            db.set_fetch_error(err_str, conn=conn)

        if not mentions and not fetch_failed:
            logger.info("No new mentions.")
            _posting_enabled, _posting_disabled_until, _posting_disabled_reason = db.get_posting_state(conn=conn)
            _posted_last_hour = db.count_posts_last_hour(conn=conn)
            _extra = ""
            if not _posting_enabled and _posting_disabled_reason:
                _extra = " posting_disabled_reason=%s posting_disabled_until=%s" % (
                    _posting_disabled_reason,
                    _posting_disabled_until,
                )
            logger.info(
                "run_end mentions_fetched=0 drafts_created=0 claimed=0 posted_this_run=0 allowed_this_run=%s posted_last_hour=%s hourly_post_cap=%s posting_enabled=%s whitelist_targets_inserted=%s whitelist_drafts_created=%s whitelist_skipped=%s%s",
                allowed_this_run,
                _posted_last_hour,
                hourly_post_cap,
                _posting_enabled,
                whitelist_targets_inserted,
                whitelist_drafts_created,
                whitelist_skipped,
                _extra,
            )
            conn.commit()
            return

        mentions_fetched = len(mentions) if mentions else 0
        drafts_created = 0
        if mentions:
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
                            conn=conn,
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
                            conn=conn,
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
                            conn=conn,
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
                        conn=conn,
                    )
                except Exception as e:
                    logger.warning("upsert_mention failed for %s: %s", tweet_id, e)
                    continue

                if db.is_replied(tweet_id, conn=conn):
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
                            conn=conn,
                        )
                        db.mark_mention_status(tweet_id, "drafted", conn=conn)
                        drafts_created += 1
                    except Exception as e:
                        logger.warning("insert_reply/mark_mention_status (dry_run) failed for %s: %s", tweet_id, e)
                    continue

                try:
                    db.insert_reply(
                        in_reply_to_tweet_id=tweet_id,
                        reply_tweet_id=None,
                        reply_text=reply_text,
                        decision="drafted",
                        conn=conn,
                    )
                    db.mark_mention_status(tweet_id, "drafted", conn=conn)
                    drafts_created += 1
                except Exception as e:
                    logger.warning("insert_reply (draft) failed for %s: %s", tweet_id, e)

            if newest_tweet_id and newest_tweet_id != cursor:
                try:
                    db.set_cursor("mentions_since_id", newest_tweet_id, conn=conn)
                except Exception as e:
                    logger.warning("set_cursor failed: %s", e)

        if whitelist_reply_enabled:
            try:
                from x_bridge import run_whitelist_once as whitelist

                whitelist_targets_inserted, whitelist_drafts_created, whitelist_skipped = whitelist.run_ingest_and_draft(conn)
            except Exception as e:
                logger.warning("whitelist_ingest_draft_failed error=%s", e)

        posted_this_run = 0
        claimed_count = 0
        can_post = True
        if not X_DRY_RUN:
            posting_enabled, posting_disabled_until, posting_disabled_reason = db.get_posting_state(conn=conn)
            if posting_enabled:
                can_post = True
            elif posting_disabled_until is not None and posting_disabled_until > datetime.now(timezone.utc):
                logger.info("posting_cooldown_active")
                can_post = False
            elif (
                posting_disabled_until is not None
                and posting_disabled_until <= datetime.now(timezone.utc)
                and posting_disabled_reason == "rate_limited_429"
            ):
                db.re_enable_posting(conn=conn)
                logger.info("posting_reenabled_after_cooldown")
                can_post = True
            else:
                can_post = False

        X_403_REPLY_NOT_ALLOWED = (
            "Reply to this conversation is not allowed because you have not been "
            "mentioned or otherwise engaged by the author of the post you are replying to."
        )
        claimed_count = 0
        if not X_DRY_RUN and can_post:
            if allowed_this_run == 0:
                logger.info("hourly_cap_reached: skipping claim+post phase")
            else:
                # Pass 1: claim mention replies up to full budget
                claimed_mentions = db.claim_replies_for_posting(
                    limit=allowed_this_run,
                    claimed_by="run_mentions_once",
                    conn=conn,
                )
                # Pass 2: claim whitelist drafts up to remaining budget and whitelist cap
                remaining_after_mentions = allowed_this_run - len(claimed_mentions)
                whitelist_cap = (
                    min(remaining_after_mentions, whitelist_max_replies_per_run)
                    if whitelist_reply_enabled
                    else 0
                )
                claimed_whitelist = (
                    db.claim_whitelist_replies_for_posting(
                        limit=whitelist_cap,
                        claimed_by="run_mentions_once",
                        conn=conn,
                    )
                    if whitelist_cap > 0
                    else []
                )
                claimed_count = len(claimed_mentions) + len(claimed_whitelist)

                def post_one(in_reply_to_tweet_id: str, reply_text: str, kind: str, id_for_log) -> bool:
                    """Post one reply; update DB on success; handle errors. Returns True if posted."""
                    nonlocal posted_this_run
                    try:
                        reply_tweet_id = x_client.post_reply(reply_text, in_reply_to_tweet_id)
                        if kind == "mention":
                            db.update_reply_posted(id_for_log, reply_tweet_id, conn=conn)
                            db.mark_replied(in_reply_to_tweet_id, conn=conn)
                        else:
                            db.update_target_reply_posted(in_reply_to_tweet_id, reply_tweet_id, conn=conn)
                        db.record_post_success(conn=conn)
                        posted_this_run += 1
                        return True
                    except Exception as e:
                        err_str = str(e)
                        response = getattr(e, "response", None)
                        status_code = getattr(response, "status_code", None) if response else None
                        if status_code == 429:
                            if kind == "mention":
                                db.set_reply_error(id_for_log, err_str, conn=conn)
                            else:
                                db.set_target_reply_error(in_reply_to_tweet_id, err_str, conn=conn)
                            db.disable_posting("rate_limited_429", timedelta(minutes=60), conn=conn)
                            logger.info("posting_disabled_429")
                        elif status_code == 403 and X_403_REPLY_NOT_ALLOWED in err_str:
                            if kind == "mention":
                                db.set_reply_blocked(id_for_log, "x_reply_not_allowed", err_str, conn=conn)
                            else:
                                db.set_target_reply_blocked(
                                    in_reply_to_tweet_id, "x_reply_not_allowed", err_str, conn=conn
                                )
                            logger.warning(
                                "post_reply blocked (reply not allowed) for %s %s: %s",
                                kind,
                                in_reply_to_tweet_id,
                                e,
                            )
                        elif status_code == 403:
                            if kind == "mention":
                                db.set_reply_error(id_for_log, err_str, conn=conn)
                            else:
                                db.set_target_reply_error(in_reply_to_tweet_id, err_str, conn=conn)
                            db.disable_posting("forbidden_403", None, conn=conn)
                            logger.info("posting_disabled_403")
                        else:
                            if kind == "mention":
                                db.set_reply_error(id_for_log, err_str, conn=conn)
                            else:
                                db.set_target_reply_error(in_reply_to_tweet_id, err_str, conn=conn)
                            db.record_post_failure(err_str, conn=conn)
                            if db.get_consecutive_post_failures(conn=conn) >= 3:
                                db.disable_posting("repeated_failures", timedelta(minutes=30), conn=conn)
                        if status_code != 403 or X_403_REPLY_NOT_ALLOWED not in err_str:
                            logger.warning("post_reply failed for %s %s: %s", kind, in_reply_to_tweet_id, e)
                        return False

                for reply_id, mention_tweet_id, reply_text in claimed_mentions:
                    if posted_this_run >= allowed_this_run:
                        break
                    post_one(mention_tweet_id, reply_text, "mention", reply_id)
                for target_tweet_id, reply_text in claimed_whitelist:
                    if posted_this_run >= allowed_this_run:
                        break
                    post_one(target_tweet_id, reply_text, "whitelist", target_tweet_id)

        posted_last_hour_end = db.count_posts_last_hour(conn=conn)
        posting_enabled_end, posting_disabled_until_end, posting_disabled_reason_end = db.get_posting_state(conn=conn)
        extra_disabled = ""
        if not posting_enabled_end and posting_disabled_reason_end:
            extra_disabled = " posting_disabled_reason=%s posting_disabled_until=%s" % (
                posting_disabled_reason_end,
                posting_disabled_until_end,
            )
        logger.info(
            "run_end mentions_fetched=%s drafts_created=%s claimed=%s posted_this_run=%s allowed_this_run=%s posted_last_hour=%s hourly_post_cap=%s posting_enabled=%s whitelist_targets_inserted=%s whitelist_drafts_created=%s whitelist_skipped=%s%s",
            mentions_fetched,
            drafts_created,
            claimed_count,
            posted_this_run,
            allowed_this_run,
            posted_last_hour_end,
            hourly_post_cap,
            posting_enabled_end,
            whitelist_targets_inserted,
            whitelist_drafts_created,
            whitelist_skipped,
            extra_disabled,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_once()
