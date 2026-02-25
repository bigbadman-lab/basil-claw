"""
X (Twitter) API v2 client wrapper using Tweepy v4 and OAuth 1.0a user context.

Env: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_USER_ID.
"""

import os
from typing import Any, Optional

import tweepy

_X_CLIENT: Optional[tweepy.Client] = None


def _get_client() -> tweepy.Client:
    global _X_CLIENT
    if _X_CLIENT is not None:
        return _X_CLIENT
    key = os.getenv("X_API_KEY")
    secret = os.getenv("X_API_SECRET")
    token = os.getenv("X_ACCESS_TOKEN")
    token_secret = os.getenv("X_ACCESS_TOKEN_SECRET")
    if not all((key, secret, token, token_secret)):
        raise RuntimeError(
            "X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET must be set."
        )
    _X_CLIENT = tweepy.Client(
        consumer_key=key,
        consumer_secret=secret,
        access_token=token,
        access_token_secret=token_secret,
    )
    return _X_CLIENT


def _user_id() -> str:
    uid = os.getenv("X_USER_ID")
    if not uid:
        raise RuntimeError("X_USER_ID must be set (numeric id of Basil account).")
    return uid.strip()


def get_mentions(
    since_id: Optional[str] = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """
    Fetch mentions of the authenticated user (Basil).
    Returns list of dicts with: tweet_id, author_id, author_username, text, created_at, raw (API tweet obj).
    """
    client = _get_client()
    uid = _user_id()
    resp = client.get_users_mentions(
        id=uid,
        since_id=since_id,
        max_results=100,
        tweet_fields=["created_at", "author_id", "conversation_id", "referenced_tweets"],
        expansions=["author_id"],
        user_fields=["username"],
        user_auth=True,
    )
    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
    if not data:
        return []

    includes = getattr(resp, "includes", None) or (resp.get("includes") if isinstance(resp, dict) else {})
    users_list = includes.get("users", []) if isinstance(includes, dict) else getattr(includes, "users", []) or []
    users_by_id = {}
    for u in users_list:
        uid_key = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
        if uid_key:
            users_by_id[str(uid_key)] = u

    out = []
    for t in data:
        tid = getattr(t, "id", None) or (t.get("id") if isinstance(t, dict) else None)
        author_id = getattr(t, "author_id", None) or (t.get("author_id") if isinstance(t, dict) else None)
        text = getattr(t, "text", None) or (t.get("text") if isinstance(t, dict) else "") or ""
        created_at = getattr(t, "created_at", None) or (t.get("created_at") if isinstance(t, dict) else None)
        author_username = None
        if author_id:
            u = users_by_id.get(str(author_id))
            if u:
                author_username = getattr(u, "username", None) or (u.get("username") if isinstance(u, dict) else None)
        raw_json = _tweet_to_dict(t) if t else None
        out.append({
            "tweet_id": str(tid) if tid else "",
            "author_id": str(author_id) if author_id else "",
            "author_username": author_username or "",
            "text": text,
            "created_at": created_at,
            "raw_json": raw_json,
        })
    return out


def _tweet_to_dict(t: Any) -> Optional[dict]:
    """Convert Tweepy tweet-like object to a JSON-serializable dict for storage."""
    if t is None:
        return None
    if isinstance(t, dict):
        return {k: _json_safe(v) for k, v in t.items()}
    d = {}
    for attr in ("id", "text", "author_id", "created_at", "conversation_id", "in_reply_to_user_id"):
        if hasattr(t, attr):
            v = getattr(t, attr)
            if v is not None:
                d[attr] = _json_safe(v)
    if hasattr(t, "data") and isinstance(getattr(t, "data"), dict):
        for k, v in t.data.items():
            d[k] = _json_safe(v)
    return d if d else None


def _json_safe(v: Any) -> Any:
    """Make value JSON-serializable (e.g. datetime -> str)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def post_reply(text: str, in_reply_to_tweet_id: str) -> str:
    """
    Post a reply tweet. Returns the new tweet id (string).
    """
    client = _get_client()
    resp = client.create_tweet(
        text=text,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        user_auth=True,
    )
    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
    if not data:
        raise RuntimeError("create_tweet returned no data")
    tid = getattr(data, "id", None) or (data.get("id") if isinstance(data, dict) else None)
    if not tid:
        raise RuntimeError("create_tweet response had no tweet id")
    return str(tid)
