"""
Integration test: insert_reply sets posted_at when reply_tweet_id is set; advisory lock can be acquired.
Run: python -m x_bridge.test_db
Requires: DATABASE_URL, and migrations applied (x_replies.posted_at exists).
"""

import os
import sys

from dotenv import load_dotenv
load_dotenv()

from x_bridge import db


def test_advisory_lock_acquired():
    """Prove that the run_mentions_once advisory lock can be acquired."""
    if not os.getenv("DATABASE_URL"):
        print("SKIP: DATABASE_URL not set")
        return
    conn = db.get_connection()
    try:
        conn.autocommit = False
        acquired = db.try_advisory_xact_lock(conn)
        assert acquired is True, "pg_try_advisory_xact_lock should return True when no other holder"
        print("OK: advisory lock acquired key=%s" % db.ADVISORY_LOCK_KEY)
    finally:
        conn.rollback()
        conn.close()


def test_posted_at_set_when_reply_tweet_id_set():
    if not os.getenv("DATABASE_URL"):
        print("SKIP: DATABASE_URL not set")
        return
    mention_tweet_id = "9999999999999999999"
    try:
        db.upsert_mention(
            tweet_id=mention_tweet_id,
            author_id="other",
            author_username="other",
            text="test mention",
            created_at=None,
            status="new",
        )
        db.insert_reply(
            in_reply_to_tweet_id=mention_tweet_id,
            reply_tweet_id="9999999999999999998",
            reply_text="test reply",
        )
        posted_at = db.get_reply_posted_at(mention_tweet_id)
        assert posted_at is not None, "posted_at must be set when reply_tweet_id is set"
        print("OK: posted_at is set when reply_tweet_id is set")
    except Exception as e:
        if "posted_at" in str(e) and ("does not exist" in str(e) or "column" in str(e).lower()):
            print("SKIP: posted_at column not present (run migrations/003_x_replies_posted_at.sql)")
            return
        raise
    finally:
        try:
            import psycopg2
            conn = psycopg2.connect(os.environ["DATABASE_URL"])
            with conn.cursor() as cur:
                cur.execute("DELETE FROM x_replies WHERE mention_tweet_id = %s", (mention_tweet_id,))
                cur.execute("DELETE FROM x_mentions WHERE tweet_id = %s", (mention_tweet_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        test_advisory_lock_acquired()
        test_posted_at_set_when_reply_tweet_id_set()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
