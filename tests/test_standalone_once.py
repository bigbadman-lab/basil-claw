"""
Tests for x_bridge.run_standalone_once: empty snippets => no LLM call, dry-run prints skip.
Run: python -m tests.test_standalone_once
"""

import io
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

# Optional deps so tests run without full install
try:
    import dotenv
except ImportError:
    sys.modules["dotenv"] = MagicMock()

# Avoid loading real db/psycopg2
_mock_db = MagicMock()
_mock_conn = MagicMock()
_mock_conn.commit = MagicMock()
_mock_db.get_connection.return_value = _mock_conn
_mock_db.try_advisory_xact_lock.return_value = True
_mock_db.ensure_standalone_state_table = MagicMock()
_mock_db.get_standalone_state.return_value = {
    "last_posted_at": None,
    "last_post_hash": None,
    "last_mode": None,
    "last_angle": None,
    "next_allowed_at": None,
    "last_standalone_error_at": None,
    "second_last_standalone_error_at": None,
}
_mock_db.get_posting_state.return_value = (True, None, None)
sys.modules["x_bridge.db"] = _mock_db

_mock_reply_engine = MagicMock()
sys.modules["ingest.reply_engine"] = _mock_reply_engine

sys.modules["x_bridge.x_client"] = MagicMock()

# Optional: avoid needing openai package when testing skip path (OpenAI not called)
_mock_openai_class = MagicMock()
sys.modules["openai"] = MagicMock(OpenAI=_mock_openai_class)

# Import after mocks so run_standalone_once gets mock db
from x_bridge.run_standalone_once import run_once  # noqa: E402


def test_policy_empty_snippets_skips_no_llm_call():
    """When mode is policy and retrieve_policy_snippets returns [], we skip: no Responses API call, dry-run prints SKIP."""
    mock_rng = MagicMock()
    mock_rng.random.return_value = 0  # 0 < policy_weight => policy mode
    with patch.dict("os.environ", {"X_DRY_RUN": "1"}, clear=False):
        with patch("x_bridge.run_standalone_once.X_DRY_RUN", True):
            with patch("x_bridge.standalone.basil_moment.make_rng", return_value=mock_rng):
                with patch("x_bridge.standalone.policy_retrieval.choose_policy_angle", return_value="economy and growth"):
                        with patch("x_bridge.standalone.policy_retrieval.retrieve_policy_snippets", return_value=[]):
                            _mock_openai_class.reset_mock()
                            stdout_capture = io.StringIO()
                            with patch("sys.stdout", stdout_capture):
                                run_once()

                            out = stdout_capture.getvalue()
                            assert "standalone_skip" in out, "dry-run should print standalone_skip"
                            assert "no_snippets" in out, "dry-run should print reason=no_snippets"
                            assert "economy and growth" in out, "dry-run should print angle"

                            _mock_openai_class.assert_not_called()
    print("OK: empty snippets => no API call, dry-run prints SKIP")


if __name__ == "__main__":
    try:
        test_policy_empty_snippets_skips_no_llm_call()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
