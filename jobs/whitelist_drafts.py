"""
Cron job: fetch latest tweets from WHITELIST_HANDLES, dedupe in Postgres,
generate 3 Basil-style reply drafts per new tweet, send to Telegram.

Usage: python -m jobs.whitelist_drafts

Env: WHITELIST_HANDLES, DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
     OPENAI_API_KEY, X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET.
"""

import os
import sys
from typing import Optional

from openai import OpenAI

from x_bridge import config  # noqa: F401 - load .env deterministically
from x_bridge import db
from x_bridge import x_client
from telegram_client import send_message as telegram_send_message


WHITELIST_SEEN_TABLE = """
CREATE TABLE IF NOT EXISTS whitelist_seen (
  tweet_id TEXT PRIMARY KEY,
  author_handle TEXT,
  tweet_url TEXT,
  tweet_text TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

BASIL_SYSTEM = """You are Basil Clawthorne: a Victorian-style lobster orator. Voice: dry wit, sharp, punchy parliamentary cadence. Use lobster imagery (claws, shell, tank, boiling pot, pincers, barnacles, tide, harbour, net). Short sentences. Few qualifiers. Every reply must tie back to restoring Britain — order, enforcement, competence, sovereignty, institutional repair. Each draft MUST include either the word "Britain" or the phrase "Restore Britain". End each draft with 🦞.

Hard constraints: each draft <= 240 characters; no @mentions; no hashtags; no slurs or calls for violence; 1–2 sentences max. Do not sound like a generic political pundit. No corporate tone. Avoid: "we need", "it's time", "let's", "in my opinion".

Output format: EXACTLY three lines, prefixed "A) ", "B) ", "C) " and nothing else."""


def _require_env(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(WHITELIST_SEEN_TABLE)
    conn.commit()


def _parse_handles(raw: str) -> list[str]:
    out = []
    for part in (raw or "").split(","):
        h = part.strip().lstrip("@").strip()
        if h:
            out.append(h)
    return out


def _resolve_user_id(handle: str) -> Optional[str]:
    client = x_client.get_v2_client()
    try:
        resp = client.get_user(username=handle, user_auth=True)
        data = getattr(resp, "data", None)
        if not data:
            return None
        uid = getattr(data, "id", None) or (data.get("id") if isinstance(data, dict) else None)
        return str(uid) if uid else None
    except Exception:
        return None


def _is_original_tweet(t: dict) -> bool:
    text = (t.get("text") or "").strip()
    if text.upper().startswith("RT "):
        return False
    refs = t.get("referenced_tweets") or []
    for r in refs:
        typ = getattr(r, "type", None) or (r.get("type") if isinstance(r, dict) else None)
        if typ in ("replied_to", "retweeted"):
            return False
    return True


def _seen(conn, tweet_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM whitelist_seen WHERE tweet_id = %s", (tweet_id,))
        return cur.fetchone() is not None


def _insert_seen(conn, tweet_id: str, author_handle: str, tweet_url: str, tweet_text: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO whitelist_seen (tweet_id, author_handle, tweet_url, tweet_text) VALUES (%s, %s, %s, %s)",
            (tweet_id, author_handle, tweet_url, tweet_text),
        )
    conn.commit()


def _generate_three_drafts(tweet_text: str) -> tuple[str, str, str]:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("CHAT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": BASIL_SYSTEM},
            {"role": "user", "content": f"Write exactly three reply drafts to this tweet (A), B), C)), each <= 240 chars, each ending with 🦞:\n\n{tweet_text}"},
        ],
        max_tokens=600,
    )
    content = (resp.choices[0].message.content or "").strip()
    a = b = c = ""
    for line in content.splitlines():
        line = line.strip()
        if line.upper().startswith("A)"):
            a = line[2:].strip()
        elif line.upper().startswith("B)"):
            b = line[2:].strip()
        elif line.upper().startswith("C)"):
            c = line[2:].strip()
    if not a:
        a = content[:240]
    if not b:
        b = content[240:480].strip()[:240] if len(content) > 240 else content[:240]
    if not c:
        c = content[480:720].strip()[:240] if len(content) > 480 else (content[:240] if not b else b)
    return (a[:240], b[:240], c[:240])


def main() -> None:
    _require_env(
        "WHITELIST_HANDLES",
        "DATABASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "OPENAI_API_KEY",
        "X_API_KEY",
        "X_API_SECRET",
        "X_ACCESS_TOKEN",
        "X_ACCESS_TOKEN_SECRET",
    )

    raw_handles = os.environ.get("WHITELIST_HANDLES", "")
    handles = _parse_handles(raw_handles)
    if not handles:
        print("No handles in WHITELIST_HANDLES")
        return

    conn = db.get_connection()
    try:
        _ensure_table(conn)

        for handle in handles:
            user_id = _resolve_user_id(handle)
            if not user_id:
                print(f"Could not resolve user_id for handle: {handle}")
                continue

            try:
                tweets = x_client.get_user_tweets(
                    user_id,
                    max_results=10,
                    exclude=["replies", "retweets"],
                )
            except Exception as e:
                print(f"Fetch error for {handle}: {e}")
                continue

            display_handle = f"@{handle}" if not handle.startswith("@") else handle
            for t in tweets:
                tweet_id = t.get("tweet_id") or ""
                text = (t.get("text") or "").strip()
                if not tweet_id:
                    continue
                if not _is_original_tweet(t):
                    continue
                if _seen(conn, tweet_id):
                    continue

                tweet_url = f"https://x.com/{handle}/status/{tweet_id}"
                tweet_preview = (text[:220] + "…") if len(text) > 220 else text

                _insert_seen(conn, tweet_id, display_handle, tweet_url, text)

                try:
                    draft_a, draft_b, draft_c = _generate_three_drafts(text)
                except Exception as e:
                    print(f"Draft error for {tweet_id}: {e}")
                    continue

                body = f"""🦞 WHITELIST PING — {display_handle}
Tweet: {tweet_preview}
Link: {tweet_url}

A) {draft_a}
B) {draft_b}
C) {draft_c}"""
                try:
                    telegram_send_message(body)
                    print(f"Sent Telegram for {tweet_id} ({display_handle})")
                except Exception as e:
                    print(f"Telegram error for {tweet_id}: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
