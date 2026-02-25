"""
Configuration and environment for Basil X pipeline.

Loads env vars (e.g. RSS feed URLs) and exposes settings for the daily
post flow. BASIL_RSS_FEEDS = comma-separated URLs; fallback to BBC
Politics and Sky Politics feeds.
"""

import os
from typing import List

from dotenv import load_dotenv

# Fallback feeds when BASIL_RSS_FEEDS is unset
DEFAULT_RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://feeds.skynews.com/feeds/rss/politics.xml",
    # "https://feeds.skynews.com/feeds/rss/uk.xml",  # Sky UK (optional)
]


def load_env() -> None:
    """Load environment variables from a .env file. No-op if .env is missing."""
    load_dotenv()


def get_rss_feeds() -> List[str]:
    """Return list of RSS feed URLs. Uses BASIL_RSS_FEEDS or default BBC/Sky Politics."""
    load_env()
    raw = os.getenv("BASIL_RSS_FEEDS", "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return list(DEFAULT_RSS_FEEDS)


def get_daily_post_limit() -> int:
    """Max number of posts to consider for daily run. Stub default."""
    try:
        return int(os.getenv("BASIL_X_DAILY_POST_LIMIT", "3"))
    except ValueError:
        return 3
