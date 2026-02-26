"""
Preview Basil's reply for one or more tweets (by ID or URL). No DB write, no post.

Usage:
  python -m x_bridge.preview_reply --tweet-id 1234567890
  python -m x_bridge.preview_reply --tweet-url "https://twitter.com/user/status/1234567890"
  python -m x_bridge.preview_reply --tweet-url <url1> --tweet-id <id2>

Uses existing Tweepy Client and ingest.reply_engine.generate_reply_for_tweet.
Exits 0 even if fetch or generation fails for a tweet.
"""

import argparse
import re
import sys
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from x_bridge import x_client


def extract_tweet_id_from_url(url: str) -> Optional[str]:
    """Extract numeric tweet ID from twitter.com or x.com status URL. Returns None if not found."""
    if not url or not url.strip():
        return None
    url = url.strip()
    # Match /status/1234567890 (with optional query string)
    m = re.search(r"/status/(\d+)", url, re.IGNORECASE)
    return m.group(1) if m else None


def collect_tweet_ids(urls: list[str], ids: list[str]) -> list[str]:
    """Deduplicate and return list of tweet IDs from URLs and raw IDs."""
    seen = set()
    out = []
    for u in urls:
        tid = extract_tweet_id_from_url(u)
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    for i in ids:
        tid = (i or "").strip()
        if tid and tid.isdigit() and tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def preview_one(tweet_id: str) -> None:
    """Fetch tweet, generate reply, print structured preview. On error print message and continue."""
    tweet = x_client.get_tweet(
        tweet_id,
        tweet_fields=["author_id", "created_at", "conversation_id", "text"],
    )
    if not tweet:
        print("==============================")
        print("Tweet ID:", tweet_id)
        print("(fetch failed or tweet not found)")
        print("--- Generated Reply ---")
        print("(skipped)")
        print("==============================")
        return

    text = tweet.get("text") or ""
    created = tweet.get("created_at")
    created_str = str(created) if created else ""

    try:
        from ingest.reply_engine import generate_reply_for_tweet
        reply_text = generate_reply_for_tweet(text)
    except Exception as e:
        reply_text = f"(generation failed: {e})"

    print("==============================")
    print("Tweet ID:", tweet.get("tweet_id", tweet_id))
    print("Author ID:", tweet.get("author_id", ""))
    print("Created:", created_str)
    print("Text:")
    print(text or "(empty)")
    print("")
    print("--- Generated Reply ---")
    print(reply_text)
    print("==============================")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview Basil reply for one or more tweets (by ID or URL). No DB write, no post."
    )
    parser.add_argument(
        "--tweet-url",
        action="append",
        default=[],
        dest="tweet_urls",
        metavar="URL",
        help="Twitter/X status URL (e.g. https://twitter.com/user/status/1234567890). May be repeated.",
    )
    parser.add_argument(
        "--tweet-id",
        action="append",
        default=[],
        dest="tweet_ids",
        metavar="ID",
        help="Tweet ID. May be repeated.",
    )
    args = parser.parse_args()

    ids = collect_tweet_ids(args.tweet_urls or [], args.tweet_ids or [])
    if not ids:
        print("No tweet IDs provided. Use --tweet-url <url> or --tweet-id <id>.", file=sys.stderr)
        sys.exit(0)

    for tweet_id in ids:
        preview_one(tweet_id)

    sys.exit(0)


if __name__ == "__main__":
    main()
