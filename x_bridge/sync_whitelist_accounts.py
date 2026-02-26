"""
Sync whitelist account handles to user_id via X API v2 (get_user by username).

Usage: python3 -m x_bridge.sync_whitelist_accounts

Loads enabled rows from x_whitelist_accounts where user_id IS NULL; for each handle
calls Tweepy v2 client.get_user(username=handle), updates user_id, logs handle -> user_id.
Exits 0 even if a handle is not found (log and continue).
Requires: DATABASE_URL, X_* env vars.
"""

import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from x_bridge import db
from x_bridge import x_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    conn = db.get_connection()
    try:
        handles = db.list_whitelist_accounts_missing_user_id(conn=conn)
        if not handles:
            logger.info("sync_whitelist_accounts: no rows with user_id IS NULL")
            return

        client = x_client.get_v2_client()
        for handle in handles:
            clean = (handle or "").strip().lstrip("@")
            if not clean:
                logger.warning("sync_whitelist_accounts: empty handle, skipping")
                continue
            try:
                resp = client.get_user(username=clean, user_auth=True)
                data = getattr(resp, "data", None)
                if not data:
                    logger.warning("sync_whitelist_accounts: handle=%s not found (no data)", handle)
                    continue
                user_id = getattr(data, "id", None) or (data.get("id") if isinstance(data, dict) else None)
                if not user_id:
                    logger.warning("sync_whitelist_accounts: handle=%s no id in response", handle)
                    continue
                user_id_str = str(user_id)
                db.update_whitelist_account_user_id(handle, user_id_str, conn=conn)
                logger.info("sync_whitelist_accounts: handle=%s -> user_id=%s", handle, user_id_str)
            except Exception as e:
                logger.warning("sync_whitelist_accounts: handle=%s error=%s", handle, e)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
    sys.exit(0)
