"""
Re-enable posting after DB circuit breaker has disabled it.

Usage: python3 -m x_bridge.reset_posting

Clears posting_disabled_reason, posting_disabled_until (and posting_enabled = true) in x_cursor.
Requires: DATABASE_URL (e.g. via .env).
"""

import logging

from x_bridge import config  # noqa: F401 - load .env deterministically
from x_bridge import db

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    conn = db.get_connection()
    try:
        db.enable_posting(conn=conn)
        conn.commit()
        logger.info("posting re-enabled")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
