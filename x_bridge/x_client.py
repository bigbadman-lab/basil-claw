"""
X (Twitter) API v2 client wrapper using Tweepy v4 and OAuth 1.0a user context.

Env: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_USER_ID.
"""

import os
from typing import Any, Optional

import tweepy


def get_v2_client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def _user_id() -> str:
    uid = os.getenv("X_USER_ID")
    if not uid:
        raise RuntimeError("X_USER_ID must be set (numeric id of Basil account).")
    return uid.strip()


def get_tweet(
    tweet_id: str,
    tweet_fields: Optional[list[str]] = None,
    expansions: Optional[list[str]] = None,
    user_fields: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """
    Fetch a single tweet by ID.
    Returns dict with tweet_id, author_id, author_username, author_name, created_at, conversation_id,
    text, reply_settings, referenced_tweets; is_reply, is_retweet if derivable. None if not found/error.
    """
    if not tweet_id or not tweet_id.strip():
        return None
    client = get_v2_client()
    fields = tweet_fields or [
        "created_at", "author_id", "text", "lang",
        "referenced_tweets", "conversation_id", "reply_settings",
    ]
    exp = expansions if expansions is not None else ["author_id"]
    uf = user_fields if user_fields is not None else ["username", "name"]
    try:
        resp = client.get_tweet(
            id=tweet_id.strip(),
            tweet_fields=fields,
            expansions=exp,
            user_fields=uf,
            user_auth=True,
        )
    except Exception:
        return None
    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
    if not data:
        return None
    tid = getattr(data, "id", None) or (data.get("id") if isinstance(data, dict) else None)
    author_id = getattr(data, "author_id", None) or (data.get("author_id") if isinstance(data, dict) else None)
    text = getattr(data, "text", None) or (data.get("text") if isinstance(data, dict) else "") or ""
    created_at = getattr(data, "created_at", None) or (data.get("created_at") if isinstance(data, dict) else None)
    conversation_id = getattr(data, "conversation_id", None) or (data.get("conversation_id") if isinstance(data, dict) else None)
    reply_settings = getattr(data, "reply_settings", None) or (data.get("reply_settings") if isinstance(data, dict) else None)
    refs = getattr(data, "referenced_tweets", None) or (data.get("referenced_tweets") if isinstance(data, dict) else None) or []

    author_username = ""
    author_name = ""
    includes = getattr(resp, "includes", None) or (resp.get("includes") if isinstance(resp, dict) else {})
    users_list = includes.get("users", []) if isinstance(includes, dict) else getattr(includes, "users", []) or []
    for u in users_list:
        uid = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
        if str(uid) == str(author_id):
            author_username = getattr(u, "username", None) or (u.get("username") if isinstance(u, dict) else "") or ""
            author_name = getattr(u, "name", None) or (u.get("name") if isinstance(u, dict) else "") or ""
            break

    is_reply = any(
        (getattr(r, "type", None) or (r.get("type") if isinstance(r, dict) else None)) == "replied_to"
        for r in refs
    )
    is_retweet = any(
        (getattr(r, "type", None) or (r.get("type") if isinstance(r, dict) else None)) == "retweeted"
        for r in refs
    )

    return {
        "tweet_id": str(tid) if tid else tweet_id,
        "author_id": str(author_id) if author_id else "",
        "author_username": author_username,
        "author_name": author_name,
        "created_at": created_at,
        "conversation_id": str(conversation_id) if conversation_id else "",
        "text": text,
        "reply_settings": str(reply_settings) if reply_settings is not None else None,
        "referenced_tweets": refs,
        "is_reply": is_reply,
        "is_retweet": is_retweet,
    }


def get_mentions(
    since_id: Optional[str] = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """
    Fetch mentions of the authenticated user (Basil).
    Returns list of dicts with: tweet_id, author_id, author_username, text, created_at, raw (API tweet obj).
    """
    client = get_v2_client()
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
    for attr in ("id", "text", "author_id", "created_at", "conversation_id", "in_reply_to_user_id", "referenced_tweets"):
        if hasattr(t, attr):
            v = getattr(t, attr)
            if v is not None:
                d[attr] = _json_safe(v)
    if hasattr(t, "data") and isinstance(getattr(t, "data"), dict):
        for k, v in t.data.items():
            d[k] = _json_safe(v)
    return d if d else None


def get_user_tweets(
    user_id: str,
    since_id: Optional[str] = None,
    max_results: int = 100,
    exclude: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Fetch tweets from a user's timeline (Tweepy v2 get_users_tweets).
    Returns list of dicts with: tweet_id, author_id, author_username, text, created_at, referenced_tweets, raw_json.
    """
    if exclude is None:
        exclude = ["replies", "retweets"]
    client = get_v2_client()
    kwargs = {
        "id": user_id.strip(),
        "max_results": min(100, max(5, max_results)),
        "tweet_fields": ["created_at", "author_id", "referenced_tweets", "reply_settings", "conversation_id"],
        "expansions": ["author_id"],
        "user_fields": ["username"],
        "user_auth": True,
    }
    if since_id:
        kwargs["since_id"] = since_id.strip()
    if exclude:
        kwargs["exclude"] = exclude
    resp = client.get_users_tweets(**kwargs)
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
        refs = getattr(t, "referenced_tweets", None) or (t.get("referenced_tweets") if isinstance(t, dict) else None) or []
        reply_settings = getattr(t, "reply_settings", None) or (t.get("reply_settings") if isinstance(t, dict) else None)
        conversation_id = getattr(t, "conversation_id", None) or (t.get("conversation_id") if isinstance(t, dict) else None)
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
            "referenced_tweets": refs,
            "reply_settings": str(reply_settings) if reply_settings is not None else None,
            "conversation_id": str(conversation_id) if conversation_id is not None else None,
            "raw_json": raw_json,
        })
    return out


def _json_safe(v: Any) -> Any:
    """Make value JSON-serializable (e.g. datetime -> str)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def post_reply(text: str, in_reply_to_tweet_id: str) -> str:
    """
    Posts a reply using X API v2.
    Returns the reply tweet ID.
    """
    client = get_v2_client()

    resp = client.create_tweet(
        text=text,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        user_auth=True,
    )

    if not resp or not getattr(resp, "data", None):
        raise RuntimeError(f"Unexpected X API response: {resp}")

    data = resp.data
    reply_id = getattr(data, "id", None) or (data.get("id") if isinstance(data, dict) else None)
    if reply_id is None:
        raise RuntimeError(f"Unexpected X API response: {resp}")

    return str(reply_id)


def post_tweet(text: str) -> str:
    """
    Post a new tweet (no reply) using X API v2.
    Returns the new tweet ID.
    """
    client = get_v2_client()
    resp = client.create_tweet(text=text, user_auth=True)
    if not resp or not getattr(resp, "data", None):
        raise RuntimeError(f"Unexpected X API response: {resp}")
    data = resp.data
    tweet_id = getattr(data, "id", None) or (data.get("id") if isinstance(data, dict) else None)
    if tweet_id is None:
        raise RuntimeError(f"Unexpected X API response: {resp}")
    return str(tweet_id)
