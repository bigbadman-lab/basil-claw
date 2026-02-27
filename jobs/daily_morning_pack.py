"""
Cron job: fetch UK political news via RSS, summarize, draft 12 Basil posts (4 news / 4 policy / 4 fun), send to Telegram.
For manual posting only (no X posting).

Usage: python -m jobs.daily_morning_pack

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OPENAI_API_KEY, DATABASE_URL.
      Optional: NEWS_RSS_URLS (comma-separated). Defaults: BBC politics, Guardian politics, Sky politics.
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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

DAILY_PACK_TABLE = """
CREATE TABLE IF NOT EXISTS daily_pack_runs (
  run_date DATE PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

LONDON = ZoneInfo("Europe/London")


def _require_env(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def _today_london() -> date:
    return datetime.now(LONDON).date()


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DAILY_PACK_TABLE)
    conn.commit()


def _already_sent_today(conn, run_date: date) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM daily_pack_runs WHERE run_date = %s", (run_date,))
        return cur.fetchone() is not None


def _insert_run(conn, run_date: date) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO daily_pack_runs (run_date) VALUES (%s)", (run_date,))
    conn.commit()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def _get_rss_items(url: str, max_items: int = 5) -> list[dict]:
    out = []
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"WARN: RSS fetch failed for {url}: {e}")
        return out
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"WARN: RSS parse failed for {url}: {e}")
        return out

    def local_tag(el):
        t = el.tag
        return t.split("}")[-1] if "}" in str(t) else t

    def find_text(parent, name: str) -> str:
        for child in parent:
            if local_tag(child) == name:
                return (child.text or "").strip()
        el = parent.find(name)
        if el is not None:
            return (el.text or "").strip()
        return ""

    items = []
    for el in root.iter():
        if local_tag(el) == "item":
            items.append(el)
    for item in items[:max_items]:
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
            desc = _strip_html(desc_el.text)
        elif desc_el is not None and len(desc_el):
            desc = _strip_html(ET.tostring(desc_el, encoding="unicode", method="text"))
        if title or link:
            out.append({"title": title, "link": link, "description": desc})
    return out


def _parse_rss_urls(raw: str) -> list[str]:
    if not (raw or "").strip():
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def _fetch_news_corpus(urls: list[str]) -> str:
    parts = []
    for url in urls:
        for item in _get_rss_items(url, 5):
            line = item["title"]
            if item.get("description"):
                line += " " + item["description"][:500]
            if item.get("link"):
                line += " [" + item["link"] + "]"
            parts.append(line)
    return "\n\n".join(parts) if parts else "No items fetched."


def _summarize_news(corpus: str, client: OpenAI) -> str:
    if not corpus or "No items fetched" in corpus:
        return "• No news items retrieved."
    resp = client.chat.completions.create(
        model=os.getenv("CHAT_MODEL", "gpt-4.1-mini"),
        messages=[
            {
                "role": "system",
                "content": "You summarize UK political news. Output 6 to 10 bullet points. Use a single bullet per line starting with '• '. Do not invent facts; only summarize what appears in the provided text. No other commentary.",
            },
            {"role": "user", "content": corpus[:12000]},
        ],
        max_tokens=800,
    )
    content = (resp.choices[0].message.content or "").strip()
    return content if content else "• No summary produced."


def _draft_twelve_posts(summary: str, client: OpenAI) -> list[tuple[str, str]]:
    """Returns list of (category, text) where category is NEWS, POLICY, or FUN."""
    resp = client.chat.completions.create(
        model=os.getenv("CHAT_MODEL", "gpt-4.1-mini"),
        messages=[
            {
                "role": "system",
                "content": """You are Basil Clawthorne: Victorian lobster orator. Draft 12 standalone posts for X.

Rules: Sharp, direct, Victorian cadence. Use lobster imagery (claws, shell, tide, harbour, etc.). Every post must tie to restoring Britain (order, enforcement, competence, sovereignty). Each post MUST include "Britain" or "Restore Britain". End every post with 🦞. No hashtags. No @mentions. No slurs or violence. No praise for non–Restore Britain parties. Each post <= 240 characters. 1–2 sentences max. Varied, not repetitive.

Ideological: Basil is an official member and avid supporter of the political party Restore Britain. If expressing political support, it must only ever be for Restore Britain. Never endorse, praise, or promote any other political party. If other parties are mentioned, tone may be critical, comparative, or analytical — but never supportive. If a post calls for reform or action, frame Restore Britain as the serious vehicle for national restoration. Avoid generic multi-party balance language. Do not say "all parties are the same." Do not use neutral political pundit tone. If referencing policy direction, align it with Restore Britain's principles: sovereignty, enforcement of law, institutional competence, electoral integrity.

Output format: EXACTLY 12 lines. Each line must start with one of:
NEWS) 
POLICY) 
FUN) 
in that order: 4 NEWS, then 4 POLICY, then 4 FUN. After the prefix, the post text only. Nothing else.""",
            },
            {
                "role": "user",
                "content": f"Morning brief to react to:\n{summary}\n\nProduce 12 posts: 4 NEWS (react to headlines), 4 POLICY (Restore Britain policy framing), 4 FUN (lobster/Victorian humour). All must tie to restoring Britain.",
            },
        ],
        max_tokens=3200,
    )
    content = (resp.choices[0].message.content or "").strip()
    result = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        cat = "NEWS"
        if line.upper().startswith("POLICY)"):
            cat = "POLICY"
            line = line[7:].strip()
        elif line.upper().startswith("FUN)"):
            cat = "FUN"
            line = line[4:].strip()
        elif line.upper().startswith("NEWS)"):
            line = line[5:].strip()
        else:
            continue
        result.append((cat, line[:240]))
    # Ensure we have 12
    while len(result) < 12:
        result.append(("FUN", "Britain needs a firmer shell. Restore Britain. 🦞"))
    return result[:12]


def main() -> None:
    _require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENAI_API_KEY", "DATABASE_URL")

    run_date = _today_london()
    run_date_str = run_date.isoformat()

    conn = db.get_connection()
    try:
        _ensure_table(conn)
        if _already_sent_today(conn, run_date):
            print("INFO: daily pack already sent today")
            return
        _insert_run(conn, run_date)
    finally:
        conn.close()

    urls = _parse_rss_urls(os.environ.get("NEWS_RSS_URLS", ""))
    if not urls:
        urls = DEFAULT_RSS_URLS
    print("INFO: fetching RSS...")
    corpus = _fetch_news_corpus(urls)
    if not corpus or "No items" in corpus:
        print("WARN: no news corpus; continuing with placeholder.")
        corpus = "No headlines available."

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    print("INFO: summarizing news...")
    summary = _summarize_news(corpus, client)
    print("INFO: drafting 12 posts...")
    posts = _draft_twelve_posts(summary, client)

    bundle_lines = [
        f"🗞️ BASIL MORNING PACK — {run_date_str} — 12 drafts",
        "Morning brief:",
        summary,
        "",
        "Posts below — copy/paste to X",
    ]
    bundle = "\n".join(bundle_lines)
    telegram_send_message(bundle)
    print("INFO: sent bundle to Telegram.")

    labels = ["NEWS"] * 4 + ["POLICY"] * 4 + ["FUN"] * 4
    for i, (cat, text) in enumerate(posts[:12], 1):
        label = labels[i - 1] if i <= len(labels) else cat
        msg = f"{i}/12 ({label}) {text}"
        telegram_send_message(msg)
    print("INFO: sent 12 posts to Telegram.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
