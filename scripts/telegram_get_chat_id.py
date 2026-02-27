"""
One-off script: GET getUpdates and print raw JSON to find chat_id after messaging the bot.
Env: TELEGRAM_BOT_TOKEN.
"""

import os

import requests


def main() -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN must be set")

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    print(resp.text)


if __name__ == "__main__":
    main()
