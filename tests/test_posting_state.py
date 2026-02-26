"""
Lightweight tests for posting state: expired disable_until => posting_enabled True.
Run: python -m tests.test_posting_state
"""

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from x_bridge import db


def test_disabled_until_in_past_posting_enabled_true_and_state_cleared():
    """disable_until in the past => posting_enabled True and disable state cleared (re_enable_posting called)."""
    past_until = datetime(2020, 1, 1, tzinfo=timezone.utc)
    mock_conn = MagicMock()

    with patch.object(db, "get_posting_state") as mock_get:
        with patch.object(db, "re_enable_posting") as mock_re_enable:
            mock_get.return_value = (False, past_until, "rate_limited_429")
            until, reason = db.apply_expired_disable_clear(conn=mock_conn)
    assert until is None and reason is None
    mock_re_enable.assert_called_once_with(conn=mock_conn)

    posting_enabled_env = True
    disable_active = until is not None and datetime.now(timezone.utc) < until
    effective_posting_enabled = posting_enabled_env and not disable_active
    assert effective_posting_enabled is True
    print("OK: disabled_until in past => posting_enabled True and disable state cleared")


if __name__ == "__main__":
    try:
        test_disabled_until_in_past_posting_enabled_true_and_state_cleared()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
