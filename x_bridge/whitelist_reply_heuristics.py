"""
Lightweight heuristics for whitelist reply decision: score tweet_text, then apply rules.
Deterministic randomness via hash(tweet_id) so reruns don't flip-flop.
"""

import hashlib
import os
import re
from typing import Any, Tuple

from x_bridge import db


def _reply_prob_default() -> float:
    raw = os.getenv("WHITELIST_REPLY_PROB_DEFAULT")
    if raw is None or raw.strip() == "":
        return 0.35
    try:
        v = float(raw.strip())
        return max(0.0, min(1.0, v))
    except ValueError:
        return 0.35


# Strong claim: modal/absolute + political noun
_CLAIM_MARKERS = re.compile(
    r"\b(is|will|always|never|must|can't|cannot)\b",
    re.IGNORECASE,
)
_POLITICAL_NOUNS = re.compile(
    r"\b(government|party|minister|mp|mps|election|policy|brexit|eu|labour|tory|conservative|reform|pm|parliament|commons|vote|mps)\b",
    re.IGNORECASE,
)

# Numbers, percentages, currency
_NUMBERS_PERCENT = re.compile(r"\d|%|£|\$|percent|per cent", re.IGNORECASE)

# Controversy triggers
_CONTROVERSY = re.compile(
    r"\b(borders|tax|taxes|immigration|immigrant|crime|starmer|sunak|nhs|economy|inflation|refugee|asylum|nhs|health|education|defence|army)\b",
    re.IGNORECASE,
)

# Mobilization
_MOBILIZATION = re.compile(
    r"\b(vote|polling day|retweet|rt\b|share|sign|petition|turn out|turnout|register to vote)\b",
    re.IGNORECASE,
)

# Celebration / thanks / congrats
_CELEBRATION = re.compile(
    r"\b(congrats|congratulations|thanks|thank you|well done|celebrat|great result|brilliant|amazing|fantastic|proud of)\b",
    re.IGNORECASE,
)

# Link-like (URLs)
_LINK_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _deterministic_random(tweet_id: str) -> float:
    """Return value in [0, 1) deterministic from tweet_id (stable across runs)."""
    h = hashlib.sha256(tweet_id.encode("utf-8")).hexdigest()
    return int(h[:14], 16) / (16**14)


def whitelist_should_reply(tweet_id: str, tweet_text: str) -> Tuple[str, float, str]:
    """
    Heuristic score and decision for whether to reply to this whitelist target.
    Returns (decision, score, reason) with decision in ("reply", "skip").
    Deterministic: same tweet_id always yields same decision.
    """
    text = (tweet_text or "").strip()
    score = 0.0
    reasons = []

    # -3 if very short or mostly links (apply first so we don't add positives to junk)
    link_matches = _LINK_PATTERN.findall(text)
    link_len = sum(len(m) for m in link_matches)
    text_no_links = _LINK_PATTERN.sub(" ", text)
    content_len = len(text_no_links.strip())
    if len(text) < 40:
        score -= 3
        reasons.append("short")
    elif text and link_len >= 0.5 * len(text):
        score -= 3
        reasons.append("mostly_links")

    # +2 question
    if "?" in text:
        score += 2
        reasons.append("question")

    # +1 strong claim (marker + political context)
    if _CLAIM_MARKERS.search(text) and _POLITICAL_NOUNS.search(text):
        score += 1
        reasons.append("claim")

    # +1 numbers/percentages/currency
    if _NUMBERS_PERCENT.search(text):
        score += 1
        reasons.append("numbers")

    # +1 controversy
    if _CONTROVERSY.search(text):
        score += 1
        reasons.append("controversy")

    # -2 mobilization
    if _MOBILIZATION.search(text):
        score -= 2
        reasons.append("mobilization")

    # -2 celebration/thanks
    if _CELEBRATION.search(text):
        score -= 2
        reasons.append("celebration")

    # Decision rules
    if "?" in text:
        decision = "reply"
        reason = "question;" + ";".join(reasons)
    elif score >= 2:
        prob = _reply_prob_default()
        rnd = _deterministic_random(tweet_id)
        if rnd < prob:
            decision = "reply"
            reason = f"score={score:.1f},prob={prob},rnd={rnd:.3f};" + ";".join(reasons)
        else:
            decision = "skip"
            reason = f"score={score:.1f},prob={prob},rnd={rnd:.3f};" + ";".join(reasons)
    else:
        decision = "skip"
        reason = f"score={score:.1f};" + ";".join(reasons)

    return (decision, score, reason)


def whitelist_should_reply_and_persist(
    tweet_id: str,
    tweet_text: str,
    conn: Any = None,
) -> Tuple[str, float, str]:
    """
    Compute reply decision, persist to x_targets (reply_decision, reply_score, reply_reason), return (decision, score, reason).
    """
    decision, score, reason = whitelist_should_reply(tweet_id, tweet_text)
    db.update_target_reply_decision(tweet_id, decision, score, reason, conn=conn)
    return (decision, score, reason)
