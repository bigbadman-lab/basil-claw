"""
CLI test harness for the Basil reply engine.
Run: python -m ingest.test_reply
"""

from dotenv import load_dotenv
load_dotenv()

from ingest.reply_engine import classify_intent, generate_reply_for_tweet


def main():
    test_tweet = "What is Restore Britain’s immigration policy?"
    intent = classify_intent(test_tweet)
    print(f"\nIntent = {intent}")

    reply = generate_reply_for_tweet(test_tweet)
    print("\nBasil reply:\n", reply)


if __name__ == "__main__":
    main()
