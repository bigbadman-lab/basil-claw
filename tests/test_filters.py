"""
Tests for x_bridge.standalone.filters: normalize, forbidden tokens, numbers rule, similarity, passes_all_filters.
Run: python -m tests.test_filters
"""

import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")

# Avoid loading real x_bridge.db (psycopg2) in tests
_mock_db = MagicMock()
_mock_db.get_connection = MagicMock(return_value=MagicMock())
# When db is used as conn (hasattr cursor), cursor returns empty recent replies by default
_mock_cursor = MagicMock()
_mock_cursor.fetchall.return_value = []
_mock_db.cursor.return_value.__enter__.return_value = _mock_cursor
_mock_db.cursor.return_value.__exit__.return_value = None
sys.modules["x_bridge.db"] = _mock_db

from x_bridge.standalone import filters


def test_normalize_text():
    assert filters.normalize_text("  Hello   World  ") == "hello world"
    assert filters.normalize_text("") == ""
    assert filters.normalize_text(None) == ""


def test_digits_rejected_in_irreverent():
    """Irreverent mode: any digit in text should fail numbers rule."""
    assert filters.violates_numbers_rule("No numbers here.", "irreverent", set()) is False
    assert filters.violates_numbers_rule("We have 1 idea.", "irreverent", set()) is True
    assert filters.violates_numbers_rule("42 ways.", "irreverent", set()) is True
    assert filters.violates_numbers_rule("Year 2025.", "irreverent", set()) is True
    # passes_all_filters should reject
    _mock_db.get_connection.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
    _mock_db.get_connection.return_value.cursor.return_value.__enter__.return_value.execute = MagicMock()
    passed, reason = filters.passes_all_filters(_mock_db, "The answer is 42.", "irreverent", window=5, conn=None)
    assert passed is False
    assert reason == "numbers_rule"
    print("OK: digits rejected in irreverent")


def test_hashtags_rejected_when_no_hashtags_enabled():
    """When no_hashtags is True, text with hashtags should fail forbidden_tokens."""
    assert filters.contains_forbidden_tokens("Hello #world", "irreverent", no_hashtags=True) is True
    assert filters.contains_forbidden_tokens("Hello #world", "policy", no_hashtags=True) is True
    assert filters.contains_forbidden_tokens("Hello world", "irreverent", no_hashtags=True) is False
    assert filters.contains_forbidden_tokens("Hello #world", "irreverent", no_hashtags=False) is False
    # passes_all_filters with no_hashtags=True
    _mock_db.get_connection.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
    passed, reason = filters.passes_all_filters(
        _mock_db, "A fine point. #restore", "irreverent", no_hashtags=True, conn=None
    )
    assert passed is False
    assert reason == "forbidden_tokens"
    print("OK: hashtags rejected when no_hashtags enabled")


def test_similarity_rejects_near_duplicate():
    """too_similar_to_recent returns True when recent posts contain very similar text."""
    def make_conn_mock(recent_texts):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [(t,) for t in recent_texts]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = None
        return mock_conn

    similar = "The tide turns and the claw holds firm."
    mock_conn = make_conn_mock(["the tide turns and the claw holds firm."])
    is_too_similar = filters.too_similar_to_recent(
        _mock_db, similar, window=10, threshold=0.75, conn=mock_conn
    )
    assert is_too_similar is True

    # Different text should not be too similar
    mock_conn2 = make_conn_mock(["the tide turns and the claw holds firm."])
    different = "Parliament sits and the ledger awaits."
    is_too_similar = filters.too_similar_to_recent(
        _mock_db, different, window=10, threshold=0.75, conn=mock_conn2
    )
    assert is_too_similar is False

    # passes_all_filters should reject when too_similar
    mock_conn3 = make_conn_mock(["the tide turns and the claw holds firm."])
    passed, reason = filters.passes_all_filters(
        _mock_db, similar, "irreverent", window=10, threshold=0.75, conn=mock_conn3
    )
    assert passed is False
    assert reason == "too_similar_to_recent"
    print("OK: similarity rejects near-duplicate")


def test_jaccard_similarity():
    assert filters.jaccard_similarity("a b c", "a b c") == 1.0
    assert filters.jaccard_similarity("a b", "c d") == 0.0
    assert 0 < filters.jaccard_similarity("the claw holds", "the claw holds firm") < 1.0
    assert filters.jaccard_similarity("", "") == 0.0


def test_policy_numbers_allowed_from_snippets():
    snippets = [{"chunk_id": 1, "source_doc": "Doc", "text": "Spending rose by 42 percent in 2025."}]
    allowed = filters.extract_allowed_numbers(snippets)
    assert "42" in allowed
    assert "2025" in allowed
    assert filters.violates_numbers_rule("Spending rose by 42 percent.", "policy", allowed) is False
    assert filters.violates_numbers_rule("Spending rose by 99 percent.", "policy", allowed) is True


def test_passes_all_filters_empty():
    passed, reason = filters.passes_all_filters(_mock_db, "", "irreverent", conn=None)
    assert passed is False
    assert reason == "empty"


if __name__ == "__main__":
    try:
        test_normalize_text()
        test_digits_rejected_in_irreverent()
        test_hashtags_rejected_when_no_hashtags_enabled()
        test_similarity_rejects_near_duplicate()
        test_jaccard_similarity()
        test_policy_numbers_allowed_from_snippets()
        test_passes_all_filters_empty()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
