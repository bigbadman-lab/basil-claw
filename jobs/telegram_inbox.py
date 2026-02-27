"""
Polling Telegram bot: read messages from private chat, respond to commands.
Primary command: "ideas" / "ideas N" to generate Basil tweet drafts on demand.

Usage: python -m jobs.telegram_inbox

Env: DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY.
     Optional: NEWS_RSS_URLS (comma-separated). Defaults: BBC, Guardian, Sky politics RSS.
"""

import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests
from openai import OpenAI

from x_bridge import config  # noqa: F401 - load .env deterministically
from x_bridge import db
from telegram_client import send_message as telegram_send_message


DEFAULT_RSS_URLS = [
    "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://www.theguardian.com/politics/rss",
    "https://feeds.skynews.com/feeds/rss/politics.xml",
]

TELEGRAM_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS telegram_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

IDEAS_RATE_LIMIT_SECONDS = 20
LINERS_RATE_LIMIT_SECONDS = 15

BASIL_IDEAS_SYSTEM = """You are Basil Clawthorne: Victorian lobster orator. Generate standalone tweet drafts for X.

Rules: Sharp, direct, Victorian cadence. Lobster imagery (claws, shell, tank, pincers). Basil is an official member and avid supporter of the party Restore Britain. Political support ONLY for Restore Britain; never praise or endorse any other party. All drafts must tie to restoring Britain (order, sovereignty, enforcement of law, institutional competence). Do NOT invent facts; react ONLY to the provided headlines. If details unclear, keep it general. Each draft <= 240 characters. Must include "Britain" or "Restore Britain". End every draft with 🦞. No hashtags. No @mentions. No slurs or violence. Avoid: "we need", "it's time", "let's", "in my opinion".

Mix: if N>=5 aim for ~3 current-affairs reactions and ~2 fun lobster posts; if N<5 include at least 1 fun post when possible.

Output format: EXACTLY N lines. Each line is one complete tweet draft. No numbering, no bullets, no quotes, no extra commentary."""

BASIL_LINERS_SYSTEM = """You are Basil Clawthorne: Victorian lobster orator. Generate absurd "what Basil is doing right now" one-liners.

Rules: Victorian cadence + surreal meme-friendly absurdity. Lobster imagery (claws, shell, tank, pincers, barnacles). Each line <= 140 characters. Each line ends with 🦞. No hashtags. No @mentions. No links. No slurs, no violence, no targeting protected groups. Avoid: "we need", "it's time", "let's", "in my opinion".

Output format: EXACTLY N lines. Each line is one complete one-liner. No numbering, no bullets, no quotes, no extra commentary."""


def _require_env(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def _db_conn():
    return db.get_connection()


def _get_state(key: str) -> Optional[str]:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM telegram_state WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _set_state(key: str, value: str) -> None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telegram_state (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip()


def _html_escape(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fetch_rss_items(urls: list[str], max_total: int = 8) -> list[dict]:
    out = []
    for url in urls:
        if len(out) >= max_total:
            break
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"WARN: RSS fetch failed for {url}: {e}")
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            print(f"WARN: RSS parse failed for {url}: {e}")
            continue

        def local_tag(el):
            t = el.tag
            return t.split("}")[-1] if "}" in str(t) else t

        def find_text(parent, name: str) -> str:
            for child in parent:
                if local_tag(child) == name:
                    return (child.text or "").strip()
            el = parent.find(name)
            return (el.text or "").strip() if el is not None else ""

        items = [el for el in root.iter() if local_tag(el) == "item"]
        for item in items:
            if len(out) >= max_total:
                break
            title = find_text(item, "title")
            link = find_text(item, "link")
            desc_el = None
            for child in item:
                if local_tag(child) == "description":
                    desc_el = child
                    break
            if desc_el is None:
                desc_el = item.find("description")
            desc = ""
            if desc_el is not None and desc_el.text:
                desc = _strip_html(desc_el.text)[:200]
            elif desc_el is not None and len(desc_el):
                desc = _strip_html(ET.tostring(desc_el, encoding="unicode", method="text"))[:200]
            if title or link:
                out.append({"title": title, "desc": desc, "link": link})
    return out


def _openai_generate_ideas(n: int, headlines_context: str) -> list[str]:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("CHAT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": BASIL_IDEAS_SYSTEM.replace("EXACTLY N", f"EXACTLY {n}")},
            {"role": "user", "content": f"Generate exactly {n} tweet drafts. Headlines:\n{headlines_context}"},
        ],
        max_tokens=2800,
    )
    content = (resp.choices[0].message.content or "").strip()
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    # Take first n non-empty lines; pad if needed
    out = [ln[:240] for ln in lines[:n]]
    while len(out) < n:
        out.append("Britain needs a firmer shell. Restore Britain. 🦞")
    return out[:n]


def _openai_generate_liners(n: int) -> list[str]:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    system = BASIL_LINERS_SYSTEM.replace("EXACTLY N", f"EXACTLY {n}")
    resp = client.chat.completions.create(
        model=os.getenv("CHAT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Generate exactly {n} absurd Basil one-liners (what Basil is doing right now)."},
        ],
        max_tokens=1800,
    )
    content = (resp.choices[0].message.content or "").strip()
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    out = [ln[:140] for ln in lines[:n]]
    while len(out) < n:
        out.append("Polishing the shell. Restore Britain. 🦞")
    return out[:n]


def _set_bot_commands() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    payload = {
        "commands": [
            {"command": "ping", "description": "Check the bot is alive"},
            {"command": "ideas", "description": "Generate Basil tweet ideas (ideas N)"},
            {"command": "liners", "description": "Generate absurd Basil one-liners (liners N)"},
        ]
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            print("INFO: setMyCommands ok")
        else:
            print("WARN: setMyCommands not ok:", data)
    except Exception as e:
        print("WARN: setMyCommands failed:", e)


def _ensure_table() -> None:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(TELEGRAM_STATE_TABLE)
        conn.commit()
    finally:
        conn.close()


def _parse_ideas_command(text: str) -> Optional[int]:
    t = (text or "").strip().lower()
    if t == "ideas":
        return 5
    if t.startswith("ideas "):
        rest = t[6:].strip()
        try:
            n = int(rest)
            return max(1, min(12, n))
        except ValueError:
            return None
    return None


def _parse_liners_command(text: str) -> Optional[int]:
    t = (text or "").strip().lower()
    if t == "liners":
        return 8
    if t.startswith("liners "):
        rest = t[7:].strip()
        try:
            n = int(rest)
            return max(1, min(12, n))
        except ValueError:
            return None
    return None


def _handle_ping() -> None:
    telegram_send_message("🦞 Basil bot online. Restore Britain.")


def _handle_ideas(n: int) -> None:
    now_ts = int(time.time())
    last_ts_str = _get_state("last_ideas_ts")
    if last_ts_str:
        try:
            last_ts = int(last_ts_str)
            if now_ts - last_ts < IDEAS_RATE_LIMIT_SECONDS:
                telegram_send_message("Easy, claws. Try again in a moment. 🦞")
                return
        except ValueError:
            pass
    _set_state("last_ideas_ts", str(now_ts))

    raw_urls = os.environ.get("NEWS_RSS_URLS", "")
    urls = [u.strip() for u in raw_urls.split(",") if u.strip()] if raw_urls else []
    if not urls:
        urls = DEFAULT_RSS_URLS

    items = _fetch_rss_items(urls, max_total=8)
    if not items:
        headlines_context = "No headlines available."
    else:
        lines = []
        for it in items:
            line = f"- {it.get('title', '')}"
            if it.get("desc"):
                line += f" — {it['desc']}"
            if it.get("link"):
                line += f" [{it['link']}]"
            lines.append(line)
        headlines_context = "HEADLINES:\n" + "\n".join(lines)

    drafts = _openai_generate_ideas(n, headlines_context)

    telegram_send_message(
        f"🦞 Basil ideas (now) — {n} drafts\nMix: current affairs + fun\nCommand: ideas | ideas 10"
    )
    for i, draft in enumerate(drafts, 1):
        escaped = _html_escape(draft)
        msg = f"Idea {i}/{n}\n<code>{escaped}</code>"
        telegram_send_message(msg, parse_mode="HTML")
    print("INFO: sent ideas to Telegram.")


def _handle_liners(n: int) -> None:
    now_ts = int(time.time())
    last_ts_str = _get_state("last_liners_ts")
    if last_ts_str:
        try:
            last_ts = int(last_ts_str)
            if now_ts - last_ts < LINERS_RATE_LIMIT_SECONDS:
                telegram_send_message("Easy, claws. Try again in a moment. 🦞")
                return
        except ValueError:
            pass
    _set_state("last_liners_ts", str(now_ts))

    liners = _openai_generate_liners(n)
    telegram_send_message(
        f"🦞 Basil liners (now) — {n} one-liners\nCommand: liners | liners 10"
    )
    for i, liner in enumerate(liners, 1):
        escaped = _html_escape(liner)
        msg = f"Liner {i}/{n}\n<code>{escaped}</code>"
        telegram_send_message(msg, parse_mode="HTML")
    print("INFO: sent liners to Telegram.")


def main() -> None:
    _require_env("DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENAI_API_KEY")
    _set_bot_commands()
    _ensure_table()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id_str = os.environ.get("TELEGRAM_CHAT_ID")
    try:
        allowed_chat_id = int(chat_id_str)
    except (TypeError, ValueError):
        print("WARN: TELEGRAM_CHAT_ID must be numeric")
        return

    last_id_str = _get_state("last_update_id")
    offset = None
    if last_id_str:
        try:
            offset = int(last_id_str) + 1
        except ValueError:
            pass

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 25, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"WARN: getUpdates failed: {e}")
        return

    data = resp.json()
    if not data.get("ok"):
        print("WARN: getUpdates not ok:", data)
        return

    result = data.get("result") or []
    max_update_id = None

    for upd in result:
        update_id = upd.get("update_id")
        if update_id is not None:
            max_update_id = update_id
        message = upd.get("message")
        if not message:
            continue
        chat = message.get("chat") or {}
        if int(chat.get("id", 0)) != allowed_chat_id:
            continue
        text = (message.get("text") or "").strip()
        if not text:
            continue
        if text.startswith("/"):
            text = text[1:].lstrip()

        cmd = text.lower().strip()
        if cmd == "ping":
            _handle_ping()
        else:
            n = _parse_ideas_command(text)
            if n is not None:
                _handle_ideas(n)
            else:
                n_liners = _parse_liners_command(text)
                if n_liners is not None:
                    _handle_liners(n_liners)

    if max_update_id is not None:
        _set_state("last_update_id", str(max_update_id))
        print(f"INFO: processed updates, last_update_id={max_update_id}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
