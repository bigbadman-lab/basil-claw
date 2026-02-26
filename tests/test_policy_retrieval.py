"""
Tests for x_bridge.standalone.policy_retrieval: angle choice, snippet retrieval, diversify.
Run: python -m tests.test_policy_retrieval
"""

import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")

# Avoid loading real x_bridge.db or ingest.reply_engine (psycopg2, etc.) in tests
_mock_db = MagicMock()
_mock_db.get_standalone_last_angle = MagicMock()
_mock_db.get_connection = MagicMock(return_value=MagicMock())
sys.modules["x_bridge.db"] = _mock_db

_mock_reply_engine = MagicMock()
sys.modules["ingest.reply_engine"] = _mock_reply_engine

from x_bridge.standalone.policy_retrieval import (
    POLICY_ANGLES,
    choose_policy_angle,
    diversify_snippets,
    retrieve_policy_snippets,
)


def test_choose_policy_angle_avoids_last():
    """When last_angle is set and list has > 1 angle, chosen angle should be different."""
    assert len(POLICY_ANGLES) > 1, "need multiple angles to test avoidance"
    rng = __import__("random").Random(42)
    mock_conn = MagicMock()
    _mock_db.get_standalone_last_angle.return_value = "immigration and borders"
    _mock_db.get_standalone_last_angle.reset_mock()
    chosen = choose_policy_angle(mock_conn, rng)
    assert chosen in POLICY_ANGLES
    assert chosen != "immigration and borders"
    _mock_db.get_standalone_last_angle.assert_called_once()
    print("OK: choose_policy_angle returns different angle when last_angle set (list size > 1)")


def test_choose_policy_angle_allows_any_when_last_none():
    """When last_angle is None, any angle can be chosen."""
    rng = __import__("random").Random(123)
    mock_conn = MagicMock()
    _mock_db.get_standalone_last_angle.return_value = None
    _mock_db.get_standalone_last_angle.reset_mock()
    chosen = choose_policy_angle(mock_conn, rng)
    assert chosen in POLICY_ANGLES


def test_choose_policy_angle_curated_list_size():
    """Curated list has 10–20 items."""
    assert 10 <= len(POLICY_ANGLES) <= 20
    assert all(isinstance(a, str) and len(a) > 0 for a in POLICY_ANGLES)
    print("OK: choose_policy_angle and curated list")


def test_retrieve_policy_snippets_returns_shape():
    """retrieve_policy_snippets returns list of dicts with chunk_id, source_doc, text."""
    mock_conn = MagicMock()
    mock_embedder = MagicMock(return_value=[0.1] * 1536)
    _mock_reply_engine.retrieve_chunks.return_value = (
        [
            (1, "Doc A", "chunk one about economy and growth"),
            (2, "Doc B", "chunk two on business and investment"),
        ],
        {"total_candidates": 2, "after_filters": 2},
    )
    _mock_reply_engine.retrieve_chunks.reset_mock()
    out = retrieve_policy_snippets(mock_conn, mock_embedder, "economy and growth", top_k=8)
    mock_embedder.assert_called_once_with("economy and growth")
    _mock_reply_engine.retrieve_chunks.assert_called_once()
    call_kw = _mock_reply_engine.retrieve_chunks.call_args[1]
    assert call_kw.get("k") == 8
    assert call_kw.get("exclude_basil_about") is True
    assert call_kw.get("return_counts") is True
    assert out == [
        {"chunk_id": 1, "source_doc": "Doc A", "text": "chunk one about economy and growth"},
        {"chunk_id": 2, "source_doc": "Doc B", "text": "chunk two on business and investment"},
    ]
    print("OK: retrieve_policy_snippets shape")


def test_retrieve_policy_snippets_angle_keyword_filtering_reduces_unrelated():
    """Angle keyword filtering keeps only rows with at least one keyword; unrelated matches are dropped; 0 matches returns []."""
    mock_conn = MagicMock()
    mock_embedder = MagicMock(return_value=[0.1] * 1536)
    angle = "crime and justice"
    # One row has keyword "police", one has "justice"; one is unrelated (no keyword).
    _mock_reply_engine.retrieve_chunks.return_value = (
        [
            (1, "Doc A", "This passage discusses the weather and gardening."),
            (2, "Doc B", "Police numbers and community safety matter."),
            (3, "Doc C", "Courts and sentencing reform are needed."),
        ],
        {"total_candidates": 3, "after_filters": 3},
    )
    _mock_reply_engine.retrieve_chunks.reset_mock()
    out = retrieve_policy_snippets(mock_conn, mock_embedder, angle, top_k=8)
    # Only rows 2 and 3 contain at least one keyword from ANGLE_KEYWORDS["crime and justice"].
    assert len(out) == 2
    texts = [s["text"] for s in out]
    assert "Police numbers and community safety matter." in texts
    assert "Courts and sentencing reform are needed." in texts
    assert "This passage discusses the weather and gardening." not in texts
    # When no row has any keyword, returns [].
    _mock_reply_engine.retrieve_chunks.return_value = (
        [
            (10, "Doc X", "Only tomatoes and fishing here."),
            (11, "Doc Y", "No policy terms in this chunk."),
        ],
        {"total_candidates": 2, "after_filters": 2},
    )
    out_empty = retrieve_policy_snippets(mock_conn, mock_embedder, angle, top_k=8)
    assert out_empty == []
    print("OK: angle keyword filtering reduces unrelated matches")


def test_diversify_snippets_empty():
    """diversify_snippets([]) returns []."""
    assert diversify_snippets([]) == []
    print("OK: diversify_snippets empty")


def test_diversify_snippets_default_one():
    """diversify_snippets returns exactly 1 snippet by default."""
    one = [{"chunk_id": 1, "source_doc": "A", "text": "hello world"}]
    assert diversify_snippets(one) == one
    two = [
        {"chunk_id": 1, "source_doc": "A", "text": "alpha beta"},
        {"chunk_id": 2, "source_doc": "B", "text": "gamma delta"},
    ]
    out = diversify_snippets(two)
    assert len(out) == 1
    assert out[0] == two[0]
    print("OK: diversify_snippets default one")


def test_diversify_snippets_two_same_source_only():
    """With max_snippets=2, returns 2 only when second has same source_doc; otherwise 1."""
    two_same_doc = [
        {"chunk_id": 1, "source_doc": "DocA", "text": "tax spending economy"},
        {"chunk_id": 2, "source_doc": "DocA", "text": "budget deficit"},
    ]
    out = diversify_snippets(two_same_doc, max_snippets=2)
    assert len(out) == 2
    assert out[0]["source_doc"] == out[1]["source_doc"] == "DocA"
    two_diff_doc = [
        {"chunk_id": 1, "source_doc": "A", "text": "alpha"},
        {"chunk_id": 2, "source_doc": "B", "text": "beta"},
    ]
    out2 = diversify_snippets(two_diff_doc, max_snippets=2)
    assert len(out2) == 1
    assert out2[0]["chunk_id"] == 1
    print("OK: diversify_snippets two only when same source_doc")


def test_diversify_snippets_picks_low_overlap_same_doc():
    """With max_snippets=2 and 3+ from same doc, second snippet has minimal overlap with first."""
    snippets = [
        {"chunk_id": 1, "source_doc": "A", "text": "tax spending economy"},
        {"chunk_id": 2, "source_doc": "A", "text": "tax spending budget"},
        {"chunk_id": 3, "source_doc": "A", "text": "moon stars cheese"},
    ]
    out = diversify_snippets(snippets, max_snippets=2)
    assert len(out) == 2
    assert out[0] == snippets[0]
    assert out[1]["chunk_id"] == 3  # least similar to first
    print("OK: diversify_snippets minimal overlap within same doc")


if __name__ == "__main__":
    try:
        test_choose_policy_angle_avoids_last()
        test_choose_policy_angle_allows_any_when_last_none()
        test_choose_policy_angle_curated_list_size()
        test_retrieve_policy_snippets_returns_shape()
        test_retrieve_policy_snippets_angle_keyword_filtering_reduces_unrelated()
        test_diversify_snippets_empty()
        test_diversify_snippets_default_one()
        test_diversify_snippets_two_same_source_only()
        test_diversify_snippets_picks_low_overlap_same_doc()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
