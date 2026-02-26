"""
Tests for whitelist reply: heuristics (no auto-skip for digits, numbers_safe constraint), numbers_safe reply content.
Run: python -m tests.test_whitelist_reply
"""

import re
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

# Avoid loading real DB in heuristics tests
_mock_db = MagicMock()
sys.modules["x_bridge.db"] = _mock_db

from x_bridge.whitelist_reply_heuristics import whitelist_should_reply


def test_tweet_with_2025_not_auto_skipped_for_digits():
    """A tweet containing '2025' should NOT be auto-skipped solely for digits; we reply with numbers_safe constraint."""
    tweet_id = "12345678"
    tweet_text = "Election 2025 will be decisive. What do you think?"
    decision, score, reason, constraints = whitelist_should_reply(tweet_id, tweet_text)
    assert decision == "reply", "tweet with question and 2025 should get reply (question rule), not skip"
    assert constraints.get("needs_numbers_safe_reply") is True, "tweet contains digits so must have numbers_safe constraint"
    assert "digits_present" in reason
    print("OK: tweet with 2025 not auto-skipped for digits")


def test_tweet_with_only_url_no_digits_does_not_set_needs_numbers_safe_reply():
    """Tweet with no digits in content and only a URL should NOT set needs_numbers_safe_reply (URLs ignored)."""
    tweet_id = "999"
    tweet_text = "Check this out https://example.com/path/2024/news and share your thoughts?"
    decision, score, reason, constraints = whitelist_should_reply(tweet_id, tweet_text)
    assert constraints.get("needs_numbers_safe_reply") is not True, "URL may contain digits but content has no digits/percent/currency"
    print("OK: tweet with only URL (no digits in content) does not set needs_numbers_safe_reply")


def test_tweet_10_times_harder_sets_needs_numbers_safe_reply():
    """Tweet with '10 times harder' (digit in content) should set needs_numbers_safe_reply."""
    tweet_id = "888"
    tweet_text = "This is 10 times harder than we thought. What do you think?"
    decision, score, reason, constraints = whitelist_should_reply(tweet_id, tweet_text)
    assert constraints.get("needs_numbers_safe_reply") is True
    assert "digits_present" in reason
    print("OK: tweet with '10 times harder' sets needs_numbers_safe_reply")


def test_numbers_safe_prompt_instructions():
    """When needs_numbers_safe_reply is True, the whitelist reply prompt must require no digits and no statistics."""
    from ingest.reply_engine import _NUMBERS_SAFE_WHITELIST_RULES

    assert "MUST NOT contain any digits" in _NUMBERS_SAFE_WHITELIST_RULES or "0-9" in _NUMBERS_SAFE_WHITELIST_RULES
    assert "statistics" in _NUMBERS_SAFE_WHITELIST_RULES.lower() or "quantitative" in _NUMBERS_SAFE_WHITELIST_RULES.lower()
    print("OK: numbers_safe prompt instructions")


def test_run_ingest_and_draft_returns_three_counts():
    """run_ingest_and_draft returns (targets_inserted, drafts_created, skipped) for run_mentions_once unpacking."""
    from unittest.mock import MagicMock, patch

    _mock_db = MagicMock()
    _mock_db.list_enabled_whitelist_accounts.return_value = []
    _mock_db.list_unreplied_targets.return_value = []
    with patch("x_bridge.run_whitelist_once.db", _mock_db):
        from x_bridge import run_whitelist_once as whitelist

        conn = MagicMock()
        result = whitelist.run_ingest_and_draft(conn)
    assert isinstance(result, tuple) and len(result) == 3
    a, b, c = result
    assert isinstance(a, int) and isinstance(b, int) and isinstance(c, int)
    print("OK: run_ingest_and_draft returns 3-tuple (for run_mentions_once)")


def test_numbers_safe_reply_contains_no_digits():
    """Generated reply in numbers_safe mode must contain no digits (contract: we validate after generation)."""
    from ingest.reply_engine import _generate_reply_whitelist

    # Mock OpenAI so we get a controlled reply with no digits
    mock_resp = MagicMock()
    mock_resp.output_text = "A sharp reply with no digits. Challenge the framing, not the number."
    with patch("ingest.reply_engine.client") as mock_client:
        mock_client.responses.create.return_value = mock_resp
        out = _generate_reply_whitelist(
            "Tax will rise by 42% in 2025.",
            retrieved=[(1, "Doc", "Some context.")],
            canon="Basil is a lobster.",
            needs_numbers_safe_reply=True,
        )
    assert not re.search(r"\d", out), f"numbers_safe reply must contain no digits: {out!r}"
    print("OK: numbers_safe reply contains no digits")


if __name__ == "__main__":
    try:
        test_tweet_with_2025_not_auto_skipped_for_digits()
        test_tweet_with_only_url_no_digits_does_not_set_needs_numbers_safe_reply()
        test_tweet_10_times_harder_sets_needs_numbers_safe_reply()
        test_numbers_safe_prompt_instructions()
        test_run_ingest_and_draft_returns_three_counts()
        test_numbers_safe_reply_contains_no_digits()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
