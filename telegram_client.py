"""
Send messages to a Telegram chat via Bot API.
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

import os
from typing import Optional

import requests


def send_message(text: str, parse_mode: Optional[str] = None) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
