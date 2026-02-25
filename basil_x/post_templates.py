"""
Post templates for Basil X daily posts.

Six reusable slots in Basil's Victorian parliamentary voice. Short,
controlled, no hashtags. Placeholders: {title}, {topic}, {date} as needed.
"""

from typing import Any, Optional, Dict

# Slot names for daily post variety
SLOT_MORNING_LEDGER = "morning_ledger"
SLOT_THE_RECORD = "the_record"
SLOT_COMPARISON_STUB = "comparison_stub"
SLOT_MINI_EXPLAINER = "mini_explainer"
SLOT_FROM_ARCHIVE = "from_archive"
SLOT_ENGAGEMENT_PROMPT = "engagement_prompt"

# Template text (no placeholders = use as-is; or {title}/{topic}/{date})
POST_SLOTS: Dict[str, str] = {
    SLOT_MORNING_LEDGER: (
        "Morning ledger: the papers are in. One notes the usual currents—"
        "we shall see what the day brings for the realm."
    ),
    SLOT_THE_RECORD: (
        "For the record: a brief clarification. One does not speak for any party; "
        "one speaks only to the question of restoring Britain."
    ),
    SLOT_COMPARISON_STUB: (
        "A comparison of positions may be drawn in due course. "
        "No party claims are advanced here—merely the lay of the land."
    ),
    SLOT_MINI_EXPLAINER: (
        "A short thread, if the House will permit: the matter in question "
        "admits of a concise summary. One will set it down presently."
    ),
    SLOT_FROM_ARCHIVE: (
        "From the archive: a matter that has lost none of its relevance. "
        "The file remains open."
    ),
    SLOT_ENGAGEMENT_PROMPT: (
        "The floor is open. What aspect of Britain's restoration "
        "ought we to take up next?"
    ),
}


def get_slot(slot_name: str) -> str:
    """Return template text for a slot. KeyError if unknown."""
    return POST_SLOTS[slot_name]


def get_available_templates() -> list:
    """Return names of all slot templates."""
    return list(POST_SLOTS.keys())


def render_draft(entry: Any, template_name: Optional[str] = None) -> str:
    """
    Render a draft post from a news entry using Basil's voice.

    If template_name is given and is a known slot, returns that slot text
    (optionally with {title} or {topic} filled from entry). Otherwise
    uses morning_ledger and substitutes entry title if present.
    """
    title = entry.get("title", "") if isinstance(entry, dict) else getattr(entry, "title", "")
    topic = title or "the day's business"
    slot = template_name if template_name and template_name in POST_SLOTS else SLOT_MORNING_LEDGER
    text = POST_SLOTS[slot]
    # Optional substitution for slots that benefit from it
    text = text.replace("{title}", title[:80] if title else topic)
    text = text.replace("{topic}", topic[:80] if topic else "the matter")
    text = text.replace("{date}", entry.get("published", "")[:10] if isinstance(entry, dict) and entry.get("published") else "")
    return text.strip()
