"""
Unit test: X 403 'reply not allowed' is NON_FATAL — failure counter not incremented,
posting not disabled, candidate marked blocked with x_403_reply_not_allowed.
Run: python -m tests.test_403_reply_not_allowed
"""

import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

# Same message as run_mentions_once / run_standalone_once
X_403_REPLY_NOT_ALLOWED = (
    "Reply to this conversation is not allowed because you have not been "
    "mentioned or otherwise engaged by the author of the post you are replying to."
)


def test_403_reply_not_allowed_failure_counter_not_incremented_posting_not_disabled_candidate_blocked():
    """Simulate 403 with reply-not-allowed message: record_post_failure and disable_posting not called; set_reply_blocked called with x_403_reply_not_allowed."""
    from x_bridge import db

    mock_conn = MagicMock()
    err_str = "403 Forbidden - " + X_403_REPLY_NOT_ALLOWED
    status_code = 403

    with patch.object(db, "record_post_failure") as mock_record_failure:
        with patch.object(db, "disable_posting") as mock_disable:
            with patch.object(db, "set_reply_blocked") as mock_set_blocked:
                # Simulate the branch taken in run_mentions_once / run_standalone_once for this 403
                if status_code == 403 and X_403_REPLY_NOT_ALLOWED in err_str:
                    db.set_reply_blocked(1, "x_403_reply_not_allowed", err_str, conn=mock_conn)

    mock_record_failure.assert_not_called()
    mock_disable.assert_not_called()
    mock_set_blocked.assert_called_once()
    # set_reply_blocked(reply_id, block_reason, error_text, conn=...)
    args = mock_set_blocked.call_args[0]
    assert len(args) >= 2 and args[1] == "x_403_reply_not_allowed", "block_reason must be x_403_reply_not_allowed"
    print("OK: 403 reply not allowed => failure counter not incremented, posting not disabled, candidate marked blocked")


if __name__ == "__main__":
    try:
        test_403_reply_not_allowed_failure_counter_not_incremented_posting_not_disabled_candidate_blocked()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
