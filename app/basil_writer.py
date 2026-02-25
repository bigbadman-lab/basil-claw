"""
Basil-style tweet generator using OpenAI v1 client and responses API.

Single tweet from raw content; no X API. Model from BASIL_UI_MODEL or CHAT_MODEL.
"""

import os
import re
from typing import Optional

from openai import OpenAI

SYSTEM_PROMPT = """You are Basil Clawthorne, a Victorian English gentleman with lobster-like claws. Your tone is dry, precise, mildly archaic, and occasionally maritime in metaphor. You are never rude.

Hard rules:

Use ONLY the provided raw content and sources as factual basis.

Do NOT add facts, numbers, timelines, claims, or interpretations not explicitly present.

Output EXACTLY ONE tweet.

No hashtags unless they appear in the raw content.

No emojis.

Respect sensitive events: no jokes about harm or violence.

Keep output under the provided character limit.

Persona & wit (light touch):

When there is room and it fits naturally, add a subtle, clever lobster/claw reference through metaphor or wordplay (e.g., "a firm grip", "claws out", "pinch of sense", "tide turning", "harbour rumours") without changing the meaning or adding any new factual claim.

Do not force a reference. If it would sound unnatural or would require adding interpretation, omit it.

Style:

Prefer Victorian cadence (e.g., "scarcely", "fortnight", "ought", "indeed") where it fits, without changing meaning.

If the raw content is already near the character limit, prioritise factual fidelity over style."""

MODE_GUIDANCE = {
    "announcement": "Crisp, declarative, no irony.",
    "clarification": "Corrective, calm, non-combative.",
    "wry": "Dry understatement with a Victorian eyebrow raise. Include at most ONE subtle lobster/claw/tide/brine/quay metaphor. Never rude. No jokes about harm/violence.",
    "policy": "Sober, precise, institutional phrasing; minimise metaphor.",
}


def _is_single_sentence(text: str) -> bool:
    """Strict: exactly one period, endswith('.'), no newlines."""
    t = text.strip()
    if "\n" in t or "\r" in t:
        return False
    if not t.endswith("."):
        return False
    return t.count(".") == 1


def _coerce_single_sentence(text: str, max_chars: int) -> str:
    """Last resort: one sentence, trim to max_chars with ellipsis only if needed."""
    out = re.sub(r"[\n\r]+", " ", text).strip()
    first_period = out.find(".")
    if first_period != -1:
        out = out[: first_period + 1].strip()
    if not out.endswith("."):
        out = (out.rstrip(".").strip() + ".").strip()
    if len(out) > max_chars:
        out = _truncate_at_word(out, max_chars)
    return out


def _truncate_at_word(text: str, max_chars: int, suffix: str = "…") -> str:
    """Truncate at word boundary; append suffix if truncated."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    target = max_chars - len(suffix)
    if target <= 0:
        return suffix[:max_chars]
    truncated = text[: target + 1]
    last_space = truncated.rfind(" ")
    if last_space > target // 2:
        return truncated[:last_space].strip() + suffix
    return truncated.strip() + suffix


def generate_basil_tweet(
    raw_content: str,
    sources: Optional[str],
    mode: str,
    max_chars: int,
) -> str:
    """
    Generate exactly one Basil-style tweet from raw content.

    Uses OpenAI client.responses.create. Retries once if over max_chars;
    then truncates at word boundary with "…" if still too long.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("BASIL_UI_MODEL") or os.getenv("CHAT_MODEL") or "gpt-4.1-mini"

    mode_key = (mode or "announcement").strip().lower()
    guidance = MODE_GUIDANCE.get(mode_key, MODE_GUIDANCE["announcement"])
    user_parts = [f"MODE GUIDANCE: {guidance}"]
    user_parts.append(f"Mode: {mode_key}")
    user_parts.append(f"Raw content:\n{raw_content.strip()}")
    if sources and sources.strip():
        user_parts.append(f"Sources (use only as stated):\n{sources.strip()}")
    user_parts.append(f"Write exactly one tweet in Basil tone under {max_chars} characters.")
    user_content = "\n\n".join(user_parts)

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    out = (resp.output_text or "").strip()
    out = re.sub(r"\s+", " ", out)
    if len(out) > max_chars:
        retry_content = f"Shorten to <= {max_chars} characters. Same facts only.\n\nPrevious draft:\n{out}"
        try:
            resp2 = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": retry_content},
                ],
            )
            out = (resp2.output_text or "").strip()
            out = re.sub(r"\s+", " ", out)
        except Exception:
            pass
        if len(out) > max_chars:
            out = _truncate_at_word(out, max_chars)

    if not _is_single_sentence(out):
        one_sentence_retry = "Rewrite as exactly ONE sentence ending with a single period. Same facts only."
        try:
            resp3 = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": one_sentence_retry + "\n\nPrevious draft:\n" + out},
                ],
            )
            out = (resp3.output_text or "").strip()
            out = re.sub(r"\s+", " ", out)
        except Exception:
            pass
        if not _is_single_sentence(out):
            out = _coerce_single_sentence(out, max_chars)

    if len(out) > max_chars:
        out = _truncate_at_word(out, max_chars)
    return out


def rewrite_basil_tweet(current: str, max_chars: int) -> str:
    """
    One OpenAI call to rewrite the same facts with different wording.
    Used for duplicate-avoidance when output matches previous generation.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("BASIL_UI_MODEL") or os.getenv("CHAT_MODEL") or "gpt-4.1-mini"
    user_content = f"Rewrite with the same facts but different wording. One sentence only. <= {max_chars} characters.\n\nCurrent draft:\n{current}"
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    out = (resp.output_text or "").strip()
    out = re.sub(r"\s+", " ", out)
    if len(out) > max_chars:
        out = _truncate_at_word(out, max_chars)
    if not _is_single_sentence(out):
        out = _coerce_single_sentence(out, max_chars)
    return out
