"""
Regression: Basil token contract address (CA) and no investment advice in replies.

- test_token_ca_in_prompt: reply_engine exposes the exact CA and crypto addendum (no DB/API).
- test_token_reply_contains_ca_no_investment_advice: when DATABASE_URL and OPENAI_API_KEY are set,
  generate_reply_for_tweet("what's the Basil token contract address?") returns text containing the
  exact CA and no investment-advice keywords. Otherwise skipped.

Run: python -m pytest tests/test_basil_token_reply.py -v
  or: python -m tests.test_basil_token_reply

Manual: With DATABASE_URL and OPENAI_API_KEY set, ask the reply generator
  "what's the Basil token contract address?" and verify the reply contains
  the exact CA (Hr1C1JB1C5U5NpjfA1MKmjmTmt4PT2SgmvP8rtmpump) and no investment-advice wording.
"""

import os
import sys
sys.path.insert(0, ".")

# Forbidden phrases (case-insensitive) in token replies
INVESTMENT_advice_FORBIDDEN = [
    "buy", "sell", "pump", "moon", "guaranteed", "profit", "returns", "financial advice",
]


def test_token_ca_in_prompt():
    """Reply engine must define the canonical CA and include it in the crypto addendum."""
    from ingest.reply_engine import BASIL_TOKEN_CA, CRYPTO_MODE_ADDENDUM
    expected_ca = "Hr1C1JB1C5U5NpjfA1MKmjmTmt4PT2SgmvP8rtmpump"
    assert BASIL_TOKEN_CA == expected_ca, "BASIL_TOKEN_CA must match canonical contract address"
    assert expected_ca in CRYPTO_MODE_ADDENDUM, "Crypto addendum must contain the exact CA"


def test_token_reply_contains_ca_no_investment_advice():
    """Smoke test: reply to 'what's the Basil token contract address?' contains exact CA and no investment advice."""
    if not os.environ.get("DATABASE_URL") or not os.environ.get("OPENAI_API_KEY"):
        print("SKIP: DATABASE_URL and OPENAI_API_KEY required for token reply smoke test")
        return
    from ingest.reply_engine import generate_reply_for_tweet, BASIL_TOKEN_CA
    query = "what's the Basil token contract address?"
    reply = generate_reply_for_tweet(query)
    assert BASIL_TOKEN_CA in reply, f"Reply must contain the exact contract address. Got: {reply!r}"
    reply_lower = reply.lower()
    for word in INVESTMENT_ADVICE_FORBIDDEN:
        assert word not in reply_lower, f"Reply must not contain investment-advice phrase {word!r}. Got: {reply!r}"


if __name__ == "__main__":
    test_token_ca_in_prompt()
    print("OK: token CA in prompt")
    test_token_reply_contains_ca_no_investment_advice()
    print("OK: token reply smoke test")
