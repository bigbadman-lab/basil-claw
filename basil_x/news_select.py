"""
News selection for Basil X daily posts.

Deduplicate by title similarity, then select top N items by recency with
diversity by source. Output: selected items for today.
"""

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Any, Dict, Optional

from difflib import SequenceMatcher

# Item = dict with source, title, link, published, summary (from rss_fetch)
FeedItem = Dict[str, Any]

# Default similarity threshold for title dedup (0 = exact norm match, 1 = any)
DEFAULT_DEDUPE_RATIO = 0.85
# Max items per source when selecting for diversity
MAX_PER_SOURCE = 3


def _normalize_title(title: Optional[str]) -> str:
    """Lowercase, strip, collapse whitespace."""
    if not title:
        return ""
    return re.sub(r"\s+", " ", str(title).lower().strip())


def _parse_published(published: Optional[str]) -> Optional[datetime]:
    """Best-effort parse of published string to datetime. Returns None if unparseable."""
    if not published:
        return None
    s = (published or "").strip()
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def dedupe_items(items: List[FeedItem], ratio: float = DEFAULT_DEDUPE_RATIO) -> List[FeedItem]:
    """
    Deduplicate items by title normalization and similarity.

    Titles are normalized (lower, strip, collapse whitespace). An item is
    dropped if its normalized title is at least `ratio` similar (difflib
    ratio, 0–1) to any already-kept item's normalized title. First
    occurrence wins. Returns list in original order with duplicates removed.
    """
    if not items:
        return []
    kept: List[FeedItem] = []
    kept_norm: List[str] = []
    for item in items:
        norm = _normalize_title(item.get("title"))
        if not norm:
            kept.append(item)
            kept_norm.append(norm)
            continue
        is_dup = False
        for k in kept_norm:
            if SequenceMatcher(None, norm, k).ratio() >= ratio:
                is_dup = True
                break
        if not is_dup:
            kept.append(item)
            kept_norm.append(norm)
    return kept


def select_top_items(
    items: List[FeedItem],
    n: int = 8,
    max_per_source: int = MAX_PER_SOURCE,
) -> List[FeedItem]:
    """
    Select up to n items, preferring most recent and ensuring diversity by source.

    Items are sorted by published date descending (most recent first; no date
    sorts last). Then we greedily take items until we have n, capping at
    max_per_source items from any single source. Returns selected items in
    selection order.
    """
    if not items:
        return []
    # Sort by published descending (None at end)
    def sort_key(it: FeedItem) -> tuple:
        dt = _parse_published(it.get("published"))
        return (dt is None, -(dt.timestamp() if dt else 0))

    sorted_items = sorted(items, key=sort_key)
    selected: List[FeedItem] = []
    per_source: Dict[str, int] = {}
    for it in sorted_items:
        if len(selected) >= n:
            break
        src = it.get("source") or ""
        if per_source.get(src, 0) >= max_per_source:
            continue
        selected.append(it)
        per_source[src] = per_source.get(src, 0) + 1
    return selected


def select_candidates(entries: List[Any], limit: int = 10) -> List[Any]:
    """
    From raw feed entries, dedupe and select up to `limit` candidates.
    Uses dedupe_items then select_top_items(..., n=limit).
    """
    deduped = dedupe_items(entries)
    return select_top_items(deduped, n=limit)


def rank_candidates(candidates: List[Any]) -> List[Any]:
    """
    Rank candidates by recency (most recent first). Diversity already
    applied in select_top_items; this preserves order.
    """
    return list(candidates)
