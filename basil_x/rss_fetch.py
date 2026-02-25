"""
RSS fetch utilities for Basil X.

Fetch and parse RSS/Atom feeds via feedparser. Returns normalized entry
dicts. Does not fetch article bodies; only feed metadata and entry fields.
"""

import logging
from typing import List, Dict, Any

import feedparser
import requests

logger = logging.getLogger(__name__)

# Type for a normalized feed entry (source, title, link, published, summary)
FeedEntry = Dict[str, Any]


def _normalize_entry(entry: Any, source_url: str) -> FeedEntry:
    """Build a single dict from a feedparser entry and the feed URL."""
    title = (entry.get("title") or "").strip() or None
    link = entry.get("link") or None
    # Best-effort published: prefer published, else updated (string or None)
    published = entry.get("published") or entry.get("updated")
    if published is None and entry.get("published_parsed"):
        try:
            import time
            published = time.strftime("%Y-%m-%dT%H:%M:%SZ", entry.published_parsed)
        except Exception:
            published = None
    summary = entry.get("summary") or entry.get("description")
    if summary and hasattr(summary, "strip"):
        summary = summary.strip() or None
    elif summary:
        summary = str(summary).strip() or None
    else:
        summary = None

    return {
        "source": source_url,
        "title": title,
        "link": link,
        "published": published,
        "summary": summary,
    }


def fetch_feed(url: str, timeout: int = 30) -> List[FeedEntry]:
    """
    Fetch and parse an RSS/Atom feed from url.

    Returns a list of dicts with keys: source, title, link, published (best-effort),
    summary (optional). Does not fetch article bodies. On timeout or parse error,
    returns an empty list and logs.
    """
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "basil-x-rss/1.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("RSS fetch failed for %s: %s", url, e)
        return []

    try:
        parsed = feedparser.parse(resp.content)
    except Exception as e:
        logger.warning("RSS parse failed for %s: %s", url, e)
        return []

    entries: List[FeedEntry] = []
    for entry in getattr(parsed, "entries", []):
        try:
            entries.append(_normalize_entry(entry, url))
        except Exception as e:
            logger.debug("Skip entry in %s: %s", url, e)
            continue
    return entries


def fetch_all_feeds(urls: List[str], timeout: int = 30) -> List[FeedEntry]:
    """
    Fetch all given feed URLs and merge entries into one list.

    Order is by feed order; entries within a feed keep feed order. Failed feeds
    are skipped (logged); their entries are omitted.
    """
    result: List[FeedEntry] = []
    for url in urls:
        result.extend(fetch_feed(url, timeout=timeout))
    return result
