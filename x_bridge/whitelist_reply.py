"""
Whitelist reply orchestration: decide (skip vs reply), then draft or record blocked.
Uses whitelist_reply_heuristics for decision and ingest.reply_engine for retrieval + OpenAI draft.
Persists to x_targets (reply_decision/score/reason) and x_target_replies (draft or blocked).
"""

import re
from typing import Any, Optional

from ingest.reply_engine import generate_reply_whitelist_text

from x_bridge import db
from x_bridge.whitelist_reply_heuristics import whitelist_should_reply_and_persist


def _reply_contains_digit(text: str) -> bool:
    return bool(text and re.search(r"\d", text))


def generate_reply_for_whitelist_target(target: dict, conn: Any = None) -> None:
    """
    For a whitelist target: compute reply decision (or use existing), persist to x_targets;
    if skip → insert x_target_replies with decision='blocked', block_reason='whitelist_skip:<reason>';
    if reply → generate draft via retrieval + OpenAI (whitelist instructions), insert x_target_replies
    with decision='drafted', source='whitelist'. When target has digits, uses numbers_safe mode
    (no digits in reply); if reply still contains digits after one retry, blocks with numbers_reply_failed.
    """
    tweet_id = (target.get("tweet_id") or "").strip()
    tweet_text = (target.get("tweet_text") or "").strip()
    if not tweet_id or tweet_text is None:
        return

    own_conn = conn is None
    if own_conn:
        conn = db.get_connection()

    try:
        decision = target.get("reply_decision")
        score = target.get("reply_score")
        reason = target.get("reply_reason")
        constraints = target.get("constraints") or {}
        if decision is None:
            decision, score, reason, constraints = whitelist_should_reply_and_persist(tweet_id, tweet_text, conn=conn)

        if decision == "skip":
            db.upsert_target_reply(
                target_tweet_id=tweet_id,
                reply_text=None,
                decision="blocked",
                block_reason=f"whitelist_skip:{reason or 'score'}",
                source="whitelist",
                conn=conn,
            )
            if own_conn:
                conn.commit()
            return

        needs_numbers_safe = constraints.get("needs_numbers_safe_reply", False)
        reply_text = generate_reply_whitelist_text(tweet_text, conn, needs_numbers_safe_reply=needs_numbers_safe)
        if needs_numbers_safe and _reply_contains_digit(reply_text):
            reply_text = generate_reply_whitelist_text(tweet_text, conn, needs_numbers_safe_reply=True)
            if _reply_contains_digit(reply_text):
                db.upsert_target_reply(
                    target_tweet_id=tweet_id,
                    reply_text=None,
                    decision="blocked",
                    block_reason="whitelist_skip:numbers_reply_failed",
                    source="whitelist",
                    conn=conn,
                )
                if own_conn:
                    conn.commit()
                return
        db.upsert_target_reply(
            target_tweet_id=tweet_id,
            reply_text=reply_text,
            decision="drafted",
            block_reason=None,
            source="whitelist",
            conn=conn,
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn and conn:
            conn.close()
