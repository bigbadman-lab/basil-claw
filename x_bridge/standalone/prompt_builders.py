"""
Build prompt payloads for standalone posts: policy (snippet-grounded) and irreverent.
Returns dicts compatible with OpenAI Responses API: client.responses.create(**payload).
Mirrors ingest.reply_engine and app.basil_writer message shape (system + user).
"""

from __future__ import annotations

import os
from typing import Any


def _standalone_no_hashtags() -> bool:
    raw = (os.getenv("STANDALONE_NO_HASHTAGS") or "1").strip().lower()
    return raw in ("1", "true", "yes")


def _default_model() -> str:
    return os.getenv("CHAT_MODEL") or os.getenv("BASIL_UI_MODEL") or "gpt-4.1-mini"


# Basil voice: Victorian gent + lobster/claw (shared)
BASIL_VOICE_SYSTEM = """You are Basil Clawthorne: a Victorian English gentleman with lobster-like claw hands. Your voice is dry, witty, restrained—an archivist of the realm. Use elegant understatement, gentle irony, and occasional maritime wordplay (claw, ledger, tide, harbour) without being rude. Attack ideas, not people."""


def build_policy_prompt(angle: str, snippets: list[dict], max_chars: int) -> dict[str, Any]:
    """
    Build a structured payload for the OpenAI Responses API: policy post grounded only in snippets.

    Rules: grounded ONLY in provided snippets; no numbers unless present in snippets;
    no "studies show" / "data proves"; under max_chars; Basil voice; one punchline max;
    no emojis, no @mentions; no hashtags unless STANDALONE_NO_HASHTAGS=0.

    Returns dict with keys: model, input (list of {role, content} messages).
    """
    no_hashtags = _standalone_no_hashtags()
    rules = [
        "Use ONLY the specific claims stated in the snippet(s). Do not combine unrelated policies from different snippets.",
        "Use ONLY the provided snippets below as factual basis. Do not add facts, numbers, or claims not present in them.",
        "If the snippet(s) are too thin to support a single grounded post, output exactly: SKIP",
        "Do not use phrases like 'studies show', 'data proves', or similar. If you cannot ground a claim in the snippets, omit it.",
        "No numbers or statistics unless they appear verbatim in the snippets.",
        f"Output must be under {max_chars} characters.",
        "At most one punchline or wry twist.",
        "No emojis. No @mentions.",
    ]
    if no_hashtags:
        rules.append("No hashtags.")
    system = BASIL_VOICE_SYSTEM + "\n\nRULES:\n" + "\n".join("- " + r for r in rules)

    context_block = ""
    if snippets:
        lines = []
        for s in snippets:
            doc = (s.get("source_doc") or "Unknown").strip()
            text = (s.get("text") or "").strip()
            cid = s.get("chunk_id", "")
            lines.append(f"[{cid}] {doc}\n{text}")
        context_block = "\n\n".join(lines)
    else:
        context_block = "[No snippets provided—write a very short, general Basil remark on the theme without inventing policy details.]"

    user = f"""Policy angle: {angle}

Snippets (your only source of facts):

{context_block}

Write exactly one standalone post in Basil's voice, under {max_chars} characters. One punchline max. Ground every claim in the snippets above."""

    return {
        "model": _default_model(),
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def build_irreverent_prompt(moment_user_prompt: str) -> dict[str, Any]:
    """
    Build a structured payload for the OpenAI Responses API: irreverent/standalone post
    from a pre-built user prompt (e.g. from basil_moment.build_irreverent_user_prompt).

    Same Basil voice; no emojis, no @mentions; no hashtags unless STANDALONE_NO_HASHTAGS=0.
    Returns dict with keys: model, input (list of {role, content} messages).
    """
    no_hashtags = _standalone_no_hashtags()
    rules = [
        "Output exactly one post. No emojis. No @mentions.",
        "Avoid repeating nautical metaphors (tide, harbour, helm, storm, sea) unless the moment line or atmosphere explicitly includes one.",
        "Use a punchier rhythm: prefer 1–2 short sentences.",
        "Include at most one lobster or claw reference.",
    ]
    if no_hashtags:
        rules.append("No hashtags.")
    system = BASIL_VOICE_SYSTEM + "\n\nRULES:\n" + "\n".join("- " + r for r in rules)

    user = (moment_user_prompt or "").strip() or "Write one short Basil post: wry, Victorian, with a touch of lobster flair."

    return {
        "model": _default_model(),
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
