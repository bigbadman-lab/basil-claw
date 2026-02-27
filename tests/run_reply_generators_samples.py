"""
Run both reply generators (mention + whitelist) on 3 sample inputs; print outputs and verify caps.
Requires: DATABASE_URL, OPENAI_API_KEY. Run: python -m tests.run_reply_generators_samples
"""

import os
import sys
sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

# Re-use sentence split from voice for verification
from ingest.voice import _sentences

SAMPLE_TWEETS = [
    "What would you do about immigration?",
    "Who are you and what's Restore Britain?",
    "Net zero is killing jobs. Your take?",
]

MENTION_CHAR_CAP = 240
WHITELIST_CHAR_CAP = 180
MENTION_SENTENCE_CAP = 2
WHITELIST_SENTENCE_CAP = 2


def main():
    if not os.environ.get("DATABASE_URL") or not os.environ.get("OPENAI_API_KEY"):
        print("Skip: set DATABASE_URL and OPENAI_API_KEY to run generator samples.")
        return

    from ingest.reply_engine import generate_reply_for_tweet, generate_reply_whitelist_text
    import psycopg2

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    ok = True
    try:
        print("--- Mention pipeline (generate_reply_for_tweet) ---")
        for i, tweet in enumerate(SAMPLE_TWEETS, 1):
            reply = generate_reply_for_tweet(tweet)
            sents = _sentences(reply)
            print(f"\n[{i}] Tweet: {tweet}")
            print(f"    Reply: {reply}")
            print(f"    len={len(reply)} (cap {MENTION_CHAR_CAP}) sentences={len(sents)} (cap {MENTION_SENTENCE_CAP})")
            if len(reply) > MENTION_CHAR_CAP or len(sents) > MENTION_SENTENCE_CAP:
                ok = False
                print("    FAIL: over cap")

        print("\n--- Whitelist pipeline (generate_reply_whitelist_text) ---")
        for i, tweet in enumerate(SAMPLE_TWEETS, 1):
            reply = generate_reply_whitelist_text(tweet, conn)
            sents = _sentences(reply)
            print(f"\n[{i}] Tweet: {tweet}")
            print(f"    Reply: {reply}")
            print(f"    len={len(reply)} (cap {WHITELIST_CHAR_CAP}) sentences={len(sents)} (cap {WHITELIST_SENTENCE_CAP})")
            if len(reply) > WHITELIST_CHAR_CAP or len(sents) > WHITELIST_SENTENCE_CAP:
                ok = False
                print("    FAIL: over cap")
    finally:
        conn.close()

    if ok:
        print("\nOK: all outputs within caps.")
    else:
        print("\nSome outputs exceeded caps.")
        sys.exit(1)


if __name__ == "__main__":
    main()
