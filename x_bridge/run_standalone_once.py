"""
Run the standalone post flow once: policy or irreverent, generate, filter, post at most one.

Usage: python3 -m x_bridge.run_standalone_once

Requires: DATABASE_URL, OPENAI_API_KEY, X_* for posting. STANDALONE_POST_ENABLED=1 to run.
Uses same advisory lock pattern as run_mentions_once (separate lock key); enforces interval
and HOURLY_POST_CAP; idempotency via last_post_hash. Max 1 post per run.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from x_bridge import db
from x_bridge import x_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def _is_dry_run() -> bool:
    raw = (os.getenv("X_DRY_RUN") or os.getenv("DRY_RUN") or "").strip().lower()
    return raw in ("1", "true", "yes")


X_DRY_RUN = _is_dry_run()

REGENERATION_NUDGE = "Different structure; more original; avoid repeating phrases."


def _trim_to_max(text: str, max_chars: int, suffix: str = "…") -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_chars:
        return text
    take = max_chars - len(suffix)
    if take <= 0:
        return suffix[:max_chars]
    part = text[: take + 1].rsplit(" ", 1)[0] if " " in text[: take + 1] else text[:take]
    return (part or text[:take]).strip() + suffix


def run_once() -> None:
    from x_bridge import config
    from x_bridge.standalone import filters
    from x_bridge.standalone import prompt_builders
    from x_bridge.standalone.basil_moment import (
        build_irreverent_user_prompt,
        get_local_time_context,
        get_seed_material,
        make_rng,
        pick_basil_activity,
    )
    from x_bridge.standalone.policy_retrieval import (
        choose_policy_angle,
        diversify_snippets,
        retrieve_policy_snippets,
    )
    from ingest.reply_engine import embed_query
    from openai import OpenAI

    conn = db.get_connection()
    try:
        conn.autocommit = False
        if not db.try_advisory_xact_lock(conn, db.ADVISORY_LOCK_KEY_STANDALONE):
            conn.rollback()
            conn.close()
            logger.info("run_skipped_lock_held")
            sys.exit(0)
        logger.info("advisory_lock_acquired key=%s", db.ADVISORY_LOCK_KEY_STANDALONE)
        db.ensure_standalone_state_table(conn=conn)

        if not X_DRY_RUN and not config.standalone_post_enabled:
            logger.info("run_skipped standalone_post_enabled=false")
            conn.commit()
            return

        # Same global bot enabled / auto-disabled state as run_mentions_once (skip in dry-run so we always generate)
        if not X_DRY_RUN:
            now_utc = datetime.now(timezone.utc)
            posting_disabled_until, posting_disabled_reason = db.apply_expired_disable_clear(conn=conn)
            _posting_enabled_env = (os.getenv("X_POSTING_ENABLED") or "").strip().lower() in ("1", "true", "yes")
            disable_active = posting_disabled_until is not None and now_utc < posting_disabled_until
            posting_enabled = _posting_enabled_env and not disable_active
            if not posting_enabled:
                if disable_active:
                    logger.info("run_skipped posting_disabled cooldown until=%s", posting_disabled_until)
                else:
                    logger.info("run_skipped posting_disabled reason=%s", posting_disabled_reason or "unknown")
                conn.commit()
                return

        state = db.get_standalone_state(conn=conn)
        now_utc = datetime.now(timezone.utc)
        # Standalone-specific backoff: skip until next_allowed_at if set (skip in dry-run so we always generate)
        if not X_DRY_RUN:
            next_allowed = state.get("next_allowed_at")
            if next_allowed is not None and now_utc < next_allowed:
                logger.info("run_skipped standalone_backoff next_allowed_at=%s", next_allowed)
                conn.commit()
                return

        interval_minutes = config.standalone_post_interval_minutes
        last_posted = state.get("last_posted_at")
        if not X_DRY_RUN and last_posted is not None and interval_minutes > 0:
            next_ok = last_posted + timedelta(minutes=interval_minutes)
            if now_utc < next_ok:
                logger.info(
                    "run_skipped interval not elapsed last_posted_at=%s next_ok=%s",
                    last_posted,
                    next_ok,
                )
                conn.commit()
                return

        if not X_DRY_RUN:
            hourly_cap = int(os.getenv("HOURLY_POST_CAP") or "300")
            posted_last_hour = db.count_posts_last_hour(conn=conn)
            if posted_last_hour >= hourly_cap:
                logger.info("run_skipped hourly_cap_reached posted_last_hour=%s hourly_cap=%s", posted_last_hour, hourly_cap)
                conn.commit()
                return

        rng = make_rng(get_seed_material(), dry_run=X_DRY_RUN)
        mode = "policy" if rng.random() < config.standalone_policy_weight else "irreverent"
        logger.info("standalone_mode mode=%s", mode)

        angle = None
        snippets = None
        snippet_ids = []
        if mode == "policy":
            angle = choose_policy_angle(conn, rng)
            logger.info("standalone_angle angle=%s", angle)
            snippets = retrieve_policy_snippets(conn, embed_query, angle, top_k=8)
            snippets = diversify_snippets(snippets)
            if not (snippets or []):
                logger.info("standalone_skip reason=no_snippets angle=%s", angle)
                if X_DRY_RUN:
                    print("--- DRY RUN ---", flush=True)
                    print("standalone_skip reason=no_snippets angle=%s" % angle, flush=True)
                conn.commit()
                return
            min_snippet_chars = int(os.getenv("STANDALONE_MIN_SNIPPET_CHARS") or "200")
            total_text_len = sum(len((s.get("text") or "").strip()) for s in snippets)
            if total_text_len < min_snippet_chars:
                logger.info("standalone_skip reason=snippets_too_thin angle=%s total_text_len=%s min=%s", angle, total_text_len, min_snippet_chars)
                if X_DRY_RUN:
                    print("--- DRY RUN ---", flush=True)
                    print("standalone_skip reason=snippets_too_thin angle=%s total_text_len=%s min=%s" % (angle, total_text_len, min_snippet_chars), flush=True)
                conn.commit()
                return
            snippet_ids = [s.get("chunk_id") for s in (snippets or []) if s.get("chunk_id") is not None]
            logger.info("standalone_snippet_ids snippet_ids=%s", snippet_ids)
            payload = prompt_builders.build_policy_prompt(
                angle, snippets or [], config.standalone_max_chars
            )
        else:
            context = get_local_time_context(datetime.now(timezone.utc))
            activity = pick_basil_activity(context, rng)
            moment_prompt = build_irreverent_user_prompt(context, activity)
            payload = prompt_builders.build_irreverent_prompt(moment_prompt)

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        max_attempts = 1 + config.standalone_max_regenerations
        no_hashtags = config.standalone_no_hashtags
        window = config.standalone_similarity_window
        threshold = config.standalone_similarity_threshold
        final_text = None
        filter_reason = None

        for attempt in range(max_attempts):
            if attempt > 0:
                payload["input"] = list(payload["input"]) + [
                    {"role": "user", "content": REGENERATION_NUDGE},
                ]
            resp = client.responses.create(
                model=payload["model"],
                input=payload["input"],
            )
            raw = (resp.output_text or "").strip()
            text = _trim_to_max(raw, config.standalone_max_chars)
            passed, reason = filters.passes_all_filters(
                conn,
                text,
                mode,
                snippets=snippets,
                window=window,
                threshold=threshold,
                no_hashtags=no_hashtags,
                source="standalone",
                conn=conn,
            )
            if passed:
                final_text = text
                filter_reason = None
                break
            filter_reason = reason
            logger.info("standalone_filter_rejection attempt=%s reason=%s", attempt + 1, reason)

        if final_text is None:
            if X_DRY_RUN:
                print("--- DRY RUN ---", flush=True)
                print("mode=%s" % mode, flush=True)
                print("filter_result=rejected reason=%s" % (filter_reason or "unknown"), flush=True)
                print("(no post generated)", flush=True)
            else:
                logger.info("standalone_run_aborted filter_rejection reason=%s", filter_reason)
            conn.commit()
            return

        post_hash = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
        if not X_DRY_RUN and state.get("last_post_hash") == post_hash:
            logger.info("run_skipped idempotent last_post_hash match")
            conn.commit()
            return

        if X_DRY_RUN:
            print("--- DRY RUN ---", flush=True)
            print("mode=%s" % mode, flush=True)
            if angle is not None:
                print("angle=%s" % angle, flush=True)
            print("filter_result=passed", flush=True)
            print("--- generated post (%d chars) ---" % len(final_text), flush=True)
            print(final_text, flush=True)
            print("--- end ---", flush=True)
            conn.commit()
            return

        reply_id = db.insert_standalone_reply(final_text, conn=conn)
        if reply_id is None:
            logger.warning("standalone_insert_reply_failed")
            conn.rollback()
            return

        try:
            tweet_id = x_client.post_tweet(final_text)
            db.update_reply_posted(reply_id, tweet_id, conn=conn)
            db.record_post_success(conn=conn)
            db.clear_standalone_backoff(conn=conn)
            db.update_standalone_state(
                conn=conn,
                last_posted_at=datetime.now(timezone.utc),
                last_post_hash=post_hash,
                last_mode=mode,
                last_angle=angle,
            )
            conn.commit()
            logger.info(
                "standalone_posted mode=%s angle=%s snippet_ids=%s tweet_id=%s",
                mode, angle, snippet_ids, tweet_id,
            )
        except Exception as e:
            err_str = str(e)
            response = getattr(e, "response", None)
            status_code = getattr(response, "status_code", None) if response else None
            X_403_REPLY_NOT_ALLOWED = (
                "Reply to this conversation is not allowed because you have not been "
                "mentioned or otherwise engaged by the author of the post you are replying to."
            )
            if status_code == 403 and X_403_REPLY_NOT_ALLOWED in err_str:
                # NON_FATAL: do not increment repeated_failures or disable posting; mark reply blocked.
                db.set_reply_blocked(reply_id, "x_403_reply_not_allowed", err_str, conn=conn)
                conn.commit()
                logger.warning("standalone_post_blocked_403_reply_not_allowed reply_id=%s", reply_id)
                return
            db.record_post_failure(err_str, conn=conn)
            db.record_standalone_posting_error(conn=conn)
            if status_code == 429:
                db.disable_posting("rate_limited_429", timedelta(minutes=60), conn=conn)
                logger.info("posting_disabled_429")
            elif status_code == 403:
                db.disable_posting("forbidden_403", None, conn=conn)
                logger.info("posting_disabled_403")
            else:
                failures_in_window = db.get_consecutive_post_failures(conn=conn)
                if failures_in_window >= 3:
                    _msg_trunc = (err_str[:200] + "...") if len(err_str or "") > 200 else (err_str or "")
                    logger.info(
                        "posting_disabled_repeated_failures action_type=standalone last_exception_class=%s last_http_status=%s last_error_message=%s failures_in_window=%s window_minutes=%s",
                        type(e).__name__,
                        status_code,
                        _msg_trunc,
                        failures_in_window,
                        None,
                    )
                    db.disable_posting("repeated_failures", timedelta(minutes=30), conn=conn)
            conn.commit()
            logger.warning("standalone_post_failed reply_id=%s error=%s", reply_id, e)
            raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_once()
