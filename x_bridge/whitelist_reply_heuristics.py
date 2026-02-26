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


def _min_reply_score() -> float:
    raw = os.getenv("MIN_REPLY_SCORE")
    if raw is None or raw.strip() == "":
        return 1.0
    try:
        return float(raw.strip())
    except ValueError:
        return 1.0


# Strong claim: modal/absolute + political noun
_CLAIM_MARKERS = re.compile(
    r"\b(is|will|always|never|must|can't|cannot)\b",
    re.IGNORECASE,
)
_POLITICAL_NOUNS = re.compile(
    r"\b(government|party|minister|mp|mps|election|policy|brexit|eu|labour|tory|conservative|reform|pm|parliament|commons|vote|mps)\b",
    re.IGNORECASE,
)

# Tight numbers_safe trigger: digits, %, or currency (£ $ €) in content. Check text with URLs removed so links don't trigger.
# Do NOT trigger on links, emojis, uppercase, or words like "million" without digits.
_DIGIT_OR_PERCENT_OR_CURRENCY = re.compile(r"\d|%|£|\$|€")

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


def whitelist_should_reply(tweet_id: str, tweet_text: str) -> Tuple[str, float, str, dict]:
    """
    Heuristic score and decision for whether to reply to this whitelist target.
    Returns (decision, score, reason, constraints) with decision in ("reply", "skip").
    constraints may contain needs_numbers_safe_reply=True when the target tweet contains digits
    (we do not auto-skip for digits; we tag for numbers-safe reply instead).
    Deterministic: same tweet_id always yields same decision.
    """
    text = (tweet_text or "").strip()
    score = 0.0
    reasons = []
    constraints: dict = {}

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

    # digits/percent/currency (in content, not in URLs): neutral for score; tag for numbers-safe reply and reason
    text_for_numbers = _LINK_PATTERN.sub(" ", text)
    if _DIGIT_OR_PERCENT_OR_CURRENCY.search(text_for_numbers):
        reasons.append("digits_present")
        constraints["needs_numbers_safe_reply"] = True

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

    # Basic eligibility: not just a link, has some text (same as not short / not mostly_links)
    eligible = len(text) >= 40 and not (link_len >= 0.5 * len(text) if text else True)
    constraints["eligible"] = eligible

    min_reply_score = _min_reply_score()
    # Decision: reply if question OR (score >= MIN_REPLY_SCORE and eligible)
    if "?" in text:
        decision = "reply"
        reason = "question;" + ";".join(reasons)
    elif score >= min_reply_score and eligible:
        decision = "reply"
        reason = f"score={score:.1f},min={min_reply_score};" + ";".join(reasons)
    else:
        decision = "skip"
        reason = f"score={score:.1f},min={min_reply_score};" + ";".join(reasons)

    return (decision, score, reason, constraints)


def whitelist_should_reply_and_persist(
    tweet_id: str,
    tweet_text: str,
    conn: Any = None,
) -> Tuple[str, float, str, dict]:
    """
    Compute reply decision, persist to x_targets (reply_decision, reply_score, reply_reason), return (decision, score, reason, constraints).
    When decision is reply and constraints has needs_numbers_safe_reply, reply_reason is suffixed with ";numbers_safe".
    """
    decision, score, reason, constraints = whitelist_should_reply(tweet_id, tweet_text)
    if decision == "reply" and constraints.get("needs_numbers_safe_reply"):
        reason = reason + ";numbers_safe"
    db.update_target_reply_decision(tweet_id, decision, score, reason, conn=conn)
    return (decision, score, reason, constraints)
