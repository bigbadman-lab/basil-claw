"""
Shared Basil voice spec and reply tightening for mention and whitelist pipelines.
Used by ingest/reply_engine.py only; no DB or posting logic.
"""

import re
from typing import Literal

Mode = Literal["mention", "whitelist"]

# Leading filler to strip (case-insensitive, with optional trailing comma/space)
FILLER_PATTERNS = re.compile(
    r"^\s*(Indeed,?|One suspects,?|Let us |To be clear,?|In truth,?|Well,? |Ah,? |So,? )\s*",
    re.IGNORECASE,
)

# Metaphor trigger words/phrases — at most one per reply
METAPHOR_WORDS = [
    "barnacles", "hull", "nets", "claws", "spines", "realm", "shell", "tide",
    "harbour", "pincers", "boiling pot", "crustacean", "lobster", "claw",
    "maritime", "vessel", "anchor", "currents", "waters",
]

# Sentence split: . ! ? followed by space or end
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def build_basil_voice(mode: Mode) -> str:
    """
    Shared Basil voice rules for system prompts.
    mode "mention" = direct answer, 1-2 sentences, 240 chars.
    mode "whitelist" = punchy, 1 sentence preferred (hard cap 2), meme-friendly, 180 chars.
    """
    common = """
- Restore Britain aligned: order, sovereignty, enforcement, institutional repair.
- Dry wit. Confident. No slurs, no incitement, democratic framing.
- No Victorian filler: avoid "One suspects…", "Indeed…", "Let us…", "To be clear", "In truth".
- No multi-clause build-ups. No lists. No bullet points. No newlines in reply.
- At most ONE lobster/claw metaphor per reply (shell, claws, tide, harbour, etc.).
""".strip()
    if mode == "mention":
        return common + """

- 1–2 sentences max. Direct answer first. No lecturing.
- Max 240 characters. No hashtags. No links unless asked.
""".strip()
    else:
        return common + """

- 1 sentence preferred; hard cap 2 sentences. Punchy, meme-friendly rhythm. Minimal exposition.
- Max 180 characters. No hashtags. No links. No lists.
""".strip()


def _sentences(text: str) -> list[str]:
    """Split into sentences (by . ! ?)."""
    text = (text or "").strip()
    if not text:
        return []
    parts = SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def _contains_metaphor(s: str) -> bool:
    low = s.lower()
    return any(w in low for w in METAPHOR_WORDS)


def _keep_one_metaphor_sentence(sentences: list[str]) -> list[str]:
    """Keep sentences so that at most one sentence contains a metaphor (first occurrence kept)."""
    out = []
    metaphor_seen = False
    for s in sentences:
        if _contains_metaphor(s):
            if metaphor_seen:
                break
            metaphor_seen = True
        out.append(s)
    return out


def _strip_filler(text: str) -> str:
    t = text.strip()
    while True:
        m = FILLER_PATTERNS.match(t)
        if not m:
            break
        t = t[m.end() :].strip()
    return t


def _enforce_no_lists(text: str) -> str:
    """Remove newlines and collapse repeated spaces."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t


def tighten_reply(text: str, mode: Mode) -> str:
    """
    Post-pass: enforce char cap, sentence count, strip filler, one metaphor max, no lists.
    mention: <=240 chars, first 2 sentences.
    whitelist: <=180 chars, 1 sentence preferred (2 if first is very short).
    """
    if not (text or "").strip():
        return ""
    t = _enforce_no_lists(text)
    t = _strip_filler(t)
    sentences = _sentences(t)
    if not sentences:
        return t[: 240 if mode == "mention" else 180]

    # One metaphor max: keep only first sentence that contains metaphor + prior + one following non-metaphor
    sentences = _keep_one_metaphor_sentence(sentences)

    cap = 240 if mode == "mention" else 180
    if mode == "mention":
        # Keep first 2 sentences
        kept = sentences[:2]
    else:
        # Whitelist: 1 sentence preferred; 2 if first is very short (< 40 chars)
        if len(sentences) >= 1 and len(sentences[0]) < 40 and len(sentences) >= 2:
            kept = sentences[:2]
        else:
            kept = sentences[:1]

    out = " ".join(kept).strip()
    if len(out) > cap:
        out = out[: cap - 3].rsplit(" ", 1)[0] + "..." if " " in out[: cap - 3] else out[: cap - 3] + "..."
    return out
