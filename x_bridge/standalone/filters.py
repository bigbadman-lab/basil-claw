"""
Standalone post filters: normalization, forbidden tokens, numbers rule, similarity to recent.
Uses x_replies (posted, source='standalone') for recent-post similarity. No web calls.
"""

from __future__ import annotations

import re
from typing import Optional


def normalize_text(s: str) -> str:
    """Lowercase, strip, collapse whitespace to single spaces."""
    if s is None:
        return ""
    out = " ".join((s or "").split()).lower().strip()
    return out


# Common emoji ranges (simplified): supplement, symbols & pictographs, misc symbols
_EMOJI_PATTERN = re.compile(
    "[\u2600-\u26FF\u2700-\u27BF\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]"
)


def contains_forbidden_tokens(text: str, mode: str, no_hashtags: bool = True) -> bool:
    """
    True if text contains any of: @mentions, emojis, or (when no_hashtags) hashtags.
    mode is currently unused but reserved for policy vs irreverent differences.
    """
    if not (text or "").strip():
        return False
    t = text.strip()
    # @mention: word starting with @
    if re.search(r"\B@\w+", t) or t.startswith("@"):
        return True
    # hashtags when disallowed
    if no_hashtags and (re.search(r"#\w+", t) or t.strip().startswith("#")):
        return True
    # emojis
    if _EMOJI_PATTERN.search(t):
        return True
    return False


# Number-like sequences: integers and decimals (e.g. 42, 1.5, 1,000)
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)*|\d+")


def _numbers_in_text(text: str) -> set[str]:
    """Extract number-like strings from text (normalize to no commas for set)."""
    if not text:
        return set()
    found = set()
    for m in _NUMBER_PATTERN.finditer(text):
        raw = m.group(0)
        # normalize 1,000 -> 1000 for consistent set membership
        normalized = raw.replace(",", "")
        found.add(normalized)
        found.add(raw)  # allow both forms
    return found


def extract_allowed_numbers(snippets: list[dict]) -> set[str]:
    """Extract all number-like strings from snippet texts for policy mode. Returns set of strings."""
    allowed = set()
    for s in snippets or []:
        text = (s.get("text") or "").strip()
        allowed |= _numbers_in_text(text)
    return allowed


def violates_numbers_rule(text: str, mode: str, allowed_numbers: set[str]) -> bool:
    """
    irreverent: True if text contains any digit.
    policy: True if text contains any number not in allowed_numbers (from snippets).
    """
    if not (text or "").strip():
        return False
    numbers_in_text = _numbers_in_text(text)
    if mode == "irreverent":
        return len(numbers_in_text) > 0
    if mode == "policy":
        return bool(numbers_in_text and not numbers_in_text.issubset(allowed_numbers))
    # unknown mode: treat as irreverent (no numbers allowed)
    return len(numbers_in_text) > 0


def _word_set(text: str) -> set[str]:
    return set(normalize_text(text).split()) if text else set()


def jaccard_similarity(a: str, b: str) -> float:
    """Word-set Jaccard similarity in [0, 1]. Empty sets -> 0."""
    wa, wb = _word_set(a), _word_set(b)
    if not wa and not wb:
        return 0.0
    inter = len(wa & wb)
    union = len(wa | wb)
    return inter / union if union else 0.0


def get_recent_standalone_reply_texts(db, window: int, source: str = "standalone", conn=None):
    """
    Return list of reply_text from x_replies: posted only (reply_tweet_id IS NOT NULL),
    source = source, ordered by posted_at DESC (or created_at), limit window.
    db: connection or x_bridge.db module. If conn is provided, use it and do not close.
    """
    if conn is not None:
        c = conn
        own_conn = False
    elif hasattr(db, "cursor"):
        c = db
        own_conn = False
    else:
        c = db.get_connection()
        own_conn = True
    try:
        with c.cursor() as cur:
            # Schema may have source (migration 011); posted_at (003)
            cur.execute(
                """
                SELECT reply_text FROM x_replies
                WHERE source = %s AND reply_tweet_id IS NOT NULL AND reply_text IS NOT NULL
                ORDER BY COALESCE(posted_at, created_at) DESC
                LIMIT %s
                """,
                (source, max(1, window)),
            )
            return [str(r[0]) for r in cur.fetchall() if r and r[0]]
    except Exception:
        return []
    finally:
        if own_conn and c:
            c.close()


def too_similar_to_recent(
    db,
    text: str,
    window: int,
    threshold: float,
    source: str = "standalone",
    conn=None,
) -> bool:
    """
    True if normalized text has Jaccard similarity >= threshold to any of the last
    `window` posted replies with the given source (e.g. 'standalone').
    """
    recent = get_recent_standalone_reply_texts(db, window, source=source, conn=conn)
    if not recent:
        return False
    norm = normalize_text(text)
    for past in recent:
        if jaccard_similarity(norm, past) >= threshold:
            return True
    return False


def passes_all_filters(
    db,
    text: str,
    mode: str,
    snippets: Optional[list[dict]] = None,
    *,
    window: int = 20,
    threshold: float = 0.75,
    no_hashtags: bool = True,
    source: str = "standalone",
    conn=None,
) -> tuple[bool, Optional[str]]:
    """
    Run all filters. Returns (True, None) if the text passes, else (False, reason).
    mode: 'policy' | 'irreverent'.
    snippets: required for policy mode (to derive allowed_numbers); ignored for irreverent.
    """
    if not (text or "").strip():
        return False, "empty"

    if contains_forbidden_tokens(text, mode, no_hashtags=no_hashtags):
        return False, "forbidden_tokens"

    if mode == "policy":
        allowed = extract_allowed_numbers(snippets or [])
    else:
        allowed = set()
    if violates_numbers_rule(text, mode, allowed):
        return False, "numbers_rule"

    if too_similar_to_recent(db, text, window=window, threshold=threshold, source=source, conn=conn):
        return False, "too_similar_to_recent"

    return True, None
