"""
LLM-based single "9am Daily Brief" writer for Basil X.

Uses OpenAI v1 client and responses API. Loads env at import; model from
BASIL_DAILY_MODEL or CHAT_MODEL. One witty paragraph (no bullets); on
bullet-style output does one retry; on API failure uses deterministic fallback.
"""

import os
from datetime import date
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

BASIL_DAILY_MODEL = os.getenv("BASIL_DAILY_MODEL")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
MODEL = BASIL_DAILY_MODEL if BASIL_DAILY_MODEL else CHAT_MODEL

SYSTEM_PROMPT = """You are Basil Clawthorne: a Victorian English gentleman with lobster-like claw hands. Your voice is dry, witty, and restrained—an archivist of the realm. You write with elegant understatement, gentle irony, and occasional maritime wordplay (claw/ledger/tide), without ever being rude.

Hard rules:
- Use ONLY the supplied headlines and links as facts. Do not add details, numbers, causes, motives, or allegations.
- If a headline is ambiguous, summarise it generally (e.g., 'a legal dispute', 'a procedural vote', 'scrutiny continues').
- Do NOT defame individuals or imply wrongdoing beyond what the headline states.
- No insults, slurs, harassment, or campaigning for/against any party.
- If a story involves harm/violence or a sensitive incident, keep the tone respectful (no jokes about the harm).

Writing goals:
- Synthesis over repetition: do not rewrite each headline as a bullet.
- Sound unmistakably 'Basil': Victorian diction + mild irony + occasional claw/tide imagery.
- Keep it medium length (target 450–800 characters).

Output format (exact):
1) First line: '🕰 The Morning Brief — <DATE>'
2) Then ONE paragraph (2–3 sentences) summarising the day's themes.
3) Then ONE closing line (max 90 characters) with Basil flair.
4) Then 'Sources:' followed by exactly 2 links on the same line."""

RETRY_USER_MESSAGE = """Rewrite the same brief in the required format: no bullet points, one paragraph, same facts only."""


def _has_bullet_style(text: str) -> bool:
    """True if any line looks like a bullet (• or - at start)."""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("•") or (s.startswith("-") and len(s) > 1 and s[1] in " \t"):
            return True
    return False


def _numbered_facts_list(items: list[dict], max_items: int = 8) -> str:
    """Build numbered list: '1) [<source>] <title> (<link>)'."""
    lines: list[str] = []
    for i, item in enumerate(items[:max_items], 1):
        title = (item.get("title") or "").strip() or "—"
        link = (item.get("link") or "").strip()
        source = (item.get("source") or "").strip() or "—"
        if link:
            lines.append(f"{i}) [{source}] {title} ({link})")
        else:
            lines.append(f"{i}) [{source}] {title}")
    return "\n".join(lines)


def _fallback_brief(items: list[dict]) -> str:
    """Deterministic fallback when the API fails or retry still has bullets."""
    today = date.today().strftime("%Y-%m-%d")
    headlines = []
    for item in items[:6]:
        title = (item.get("title") or "").strip()
        if title:
            headlines.append(title)
    links = [(item.get("link") or "").strip() for item in items if (item.get("link") or "").strip()]
    two_links = " ".join(links[:2])
    para = " ".join(headlines) if headlines else "No headlines available."
    return f"🕰 The Morning Brief — {today}\n\n{para}\n\nSources: {two_links}"


def _call_llm(user_content: str, assistant_content: Optional[str] = None, retry_user: Optional[str] = None) -> str:
    """One responses.create call; optionally include prior assistant + retry user for no-bullet retry."""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    if assistant_content is not None and retry_user is not None:
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": retry_user})
    resp = client.responses.create(model=MODEL, input=messages)
    return (resp.output_text or "").strip()


def write_daily_brief(items: list[dict]) -> str:
    """
    Produce a single "9am Daily Brief" string: one witty paragraph (no bullets).

    Items are dicts with title, link, and optional published, source.
    Uses OpenAI responses API; if output has bullets, one retry; on failure uses fallback.
    """
    today = date.today().strftime("%Y-%m-%d")
    numbered_facts = _numbered_facts_list(items)

    user_content = f"""DATE: {today}

{numbered_facts}

Pick exactly two representative links for Sources. Write the brief."""

    try:
        out = _call_llm(user_content)
        if not out:
            return _fallback_brief(items)
        if _has_bullet_style(out):
            out_retry = _call_llm(user_content, assistant_content=out, retry_user=RETRY_USER_MESSAGE)
            if out_retry and not _has_bullet_style(out_retry):
                return out_retry
            return _fallback_brief(items)
        return out
    except Exception:
        return _fallback_brief(items)


# --- Example output (expected format; commented out) ---
# """
# 🕰 The Morning Brief — 2026-02-25
#
# Westminster turns its attention to a procedural vote and continued scrutiny of spending, whilst a legal dispute elsewhere commands the papers. The tide of the day suggests more ledger-work than drama—which, one might say, suits the archivist's claw.
# Calm waters; we take note.
#
# Sources: https://example.com/a https://example.com/b
# """
