"""
Daily post orchestration for Basil X.

Takes selected news items and produces exactly 6 draft outputs (strings).
Each output uses one template slot and references a headline + link. No
pgvector retrieval yet; no X API calls.
"""

from typing import List, Any, Optional

from basil_x import config
from basil_x import llm_writer
from basil_x import news_select
from basil_x import post_templates
from basil_x import rss_fetch

# Fixed order: one slot per daily output (6 total)
DAILY_SLOT_ORDER = [
    post_templates.SLOT_MORNING_LEDGER,
    post_templates.SLOT_THE_RECORD,
    post_templates.SLOT_COMPARISON_STUB,
    post_templates.SLOT_MINI_EXPLAINER,
    post_templates.SLOT_FROM_ARCHIVE,
    post_templates.SLOT_ENGAGEMENT_PROMPT,
]

NUM_DAILY_OUTPUTS = 6


def _headline_and_link(item: Any) -> tuple:
    """Extract (headline, link) from a feed item dict or object."""
    if isinstance(item, dict):
        headline = (item.get("title") or "").strip() or "—"
        link = (item.get("link") or "").strip() or ""
        return headline, link
    headline = getattr(item, "title", "") or "—"
    link = getattr(item, "link", "") or ""
    return headline, link


def _format_one_output(slot_name: str, headline: str, link: str) -> str:
    """One output string: template text plus headline and link."""
    template_text = post_templates.get_slot(slot_name)
    if link:
        return f"{template_text} — {headline} {link}".strip()
    return f"{template_text} — {headline}".strip()


def build_daily_outputs(items: List[Any]) -> List[str]:
    """
    From a list of selected news items, produce exactly 6 output strings.

    Each output uses one of the six template slots (in fixed order) and
    references one item's headline and link. If there are fewer than 6
    items, items are cycled. If there are none, headline/link are
    placeholders. No retrieval into pgvector.
    """
    outputs: List[str] = []
    # Cycle through items so we always have something for each slot
    n = len(items)
    for i in range(NUM_DAILY_OUTPUTS):
        slot = DAILY_SLOT_ORDER[i]
        item = items[i % n] if n else None
        if item is not None:
            headline, link = _headline_and_link(item)
        else:
            headline, link = "—", ""
        outputs.append(_format_one_output(slot, headline, link))
    return outputs


def run_daily_one(limit: Optional[int] = None) -> str:
    """
    Run the single-output daily pipeline: fetch RSS, dedupe, select top 8,
    then call the LLM writer to produce one "9am Daily Brief" string.
    """
    config.load_env()
    feed_urls = config.get_rss_feeds()
    if not feed_urls:
        return llm_writer.write_daily_brief([])

    entries = rss_fetch.fetch_all_feeds(feed_urls)
    deduped = news_select.dedupe_items(entries)
    selected = news_select.select_top_items(deduped, n=8)
    return llm_writer.write_daily_brief(selected)


def run_daily(limit: Optional[int] = None) -> List[str]:
    """
    Run the daily pipeline: load config, fetch RSS, dedupe and select
    items, then produce exactly 6 draft outputs. Each output uses a
    template slot and references a headline + link. No pgvector; no X API.
    """
    config.load_env()
    feed_urls = config.get_rss_feeds()
    if not feed_urls:
        return build_daily_outputs([])

    entries = rss_fetch.fetch_all_feeds(feed_urls)
    deduped = news_select.dedupe_items(entries)
    selected = news_select.select_top_items(deduped, n=8)
    return build_daily_outputs(selected)
