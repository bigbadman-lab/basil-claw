"""
Minimal password-protected web UI for Basil-style tweet drafts.

Loads .env at startup. GET / shows form; POST /generate validates password
and raw_content, then calls basil_writer.generate_basil_tweet. No X API.
"""

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BASIL_UI_PASSWORD = os.getenv("BASIL_UI_PASSWORD")
if not OPENAI_API_KEY and not BASIL_UI_PASSWORD:
    raise RuntimeError(
        "OPENAI_API_KEY and BASIL_UI_PASSWORD must be set in the environment."
    )
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY must be set in the environment.")
if not BASIL_UI_PASSWORD:
    raise RuntimeError("BASIL_UI_PASSWORD must be set in the environment.")

app = FastAPI(title="Basil Tweet UI")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

LAST_OUTPUT: str | None = None
LAST_REQUEST_TS: float = 0.0
COOLDOWN_SECONDS: float = 5.0


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    error: str = "",
    output: str = "",
    raw_content: str = "",
    sources: str = "",
    mode: str = "announcement",
    max_chars: str = "240",
):
    """Render the tweet draft form. Optional query params for redirects."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "error": error,
            "output": output,
            "raw_content": raw_content,
            "sources": sources,
            "mode": mode,
            "max_chars": max_chars,
        },
    )


@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    password: str = Form(""),
    raw_content: str = Form(""),
    sources: str = Form(""),
    mode: str = Form("announcement"),
    max_chars: str = Form("240"),
):
    """
    Validate password and raw_content; call OpenAI via basil_writer.
    Return same template with error or generated output.
    """
    from app.basil_writer import generate_basil_tweet, rewrite_basil_tweet

    if password != BASIL_UI_PASSWORD:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Invalid password.",
                "output": "",
                "raw_content": raw_content,
                "sources": sources,
                "mode": mode,
                "max_chars": max_chars,
            },
        )
    if not (raw_content and raw_content.strip()):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Raw content is required.",
                "output": "",
                "raw_content": raw_content,
                "sources": sources,
                "mode": mode,
                "max_chars": max_chars,
            },
        )
    try:
        max_n = int(max_chars) if max_chars.strip() else 240
        max_n = max(50, min(500, max_n))
    except ValueError:
        max_n = 240

    now = time.time()
    if now - LAST_REQUEST_TS < COOLDOWN_SECONDS:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Please wait a few seconds before generating again.",
                "output": "",
                "raw_content": raw_content,
                "sources": sources,
                "mode": mode,
                "max_chars": max_chars,
            },
        )

    try:
        global LAST_REQUEST_TS, LAST_OUTPUT
        LAST_REQUEST_TS = now
        out = generate_basil_tweet(
            raw_content=raw_content.strip(),
            sources=sources.strip() or None,
            mode=mode.strip() if mode else "announcement",
            max_chars=max_n,
        )
        if LAST_OUTPUT is not None and out == LAST_OUTPUT:
            try:
                out = rewrite_basil_tweet(out, max_n)
            except Exception:
                pass
        LAST_OUTPUT = out
    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": f"Generation failed: {e!s}",
                "output": "",
                "raw_content": raw_content,
                "sources": sources,
                "mode": mode,
                "max_chars": max_chars,
            },
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "error": "",
            "output": out,
            "raw_content": raw_content,
            "sources": sources,
            "mode": mode,
            "max_chars": max_chars,
        },
    )
