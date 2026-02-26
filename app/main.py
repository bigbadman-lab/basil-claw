"""
Minimal password-protected web UI for Basil-style tweet drafts.

Loads .env at startup. Session-based unlock (signed cookie); GET / shows
unlock form or generator form; POST /login, POST /logout, POST /generate.
No X API.
"""

import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BASIL_UI_PASSWORD = os.getenv("BASIL_UI_PASSWORD")
BASIL_UI_SECRET_KEY = os.getenv("BASIL_UI_SECRET_KEY")
if not OPENAI_API_KEY and not BASIL_UI_PASSWORD and not BASIL_UI_SECRET_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY, BASIL_UI_PASSWORD, and BASIL_UI_SECRET_KEY must be set in the environment."
    )
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY must be set in the environment.")
if not BASIL_UI_PASSWORD:
    raise RuntimeError("BASIL_UI_PASSWORD must be set in the environment.")
if not BASIL_UI_SECRET_KEY:
    raise RuntimeError("BASIL_UI_SECRET_KEY must be set in the environment.")

app = FastAPI(title="Basil Tweet UI")
app.add_middleware(
    SessionMiddleware,
    secret_key=BASIL_UI_SECRET_KEY,
    same_site="lax",
    https_only=True,
)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

LAST_OUTPUT: Optional[str] = None
LAST_REQUEST_TS: float = 0.0
COOLDOWN_SECONDS: float = 5.0


def _template_context(
    request: Request,
    authed: bool,
    error: str = "",
    output: str = "",
    raw_content: str = "",
    sources: str = "",
    mode: str = "announcement",
    max_chars: str = "240",
) -> dict:
    return {
        "request": request,
        "authed": authed,
        "error": error,
        "output": output,
        "raw_content": raw_content,
        "sources": sources,
        "mode": mode,
        "max_chars": max_chars,
    }


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
    """Render unlock form (if not authed) or generator form + Logout (if authed)."""
    authed = bool(request.session.get("authed"))
    return templates.TemplateResponse(
        "index.html",
        _template_context(
            request, authed, error, output, raw_content, sources, mode, max_chars
        ),
    )


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form("")):
    """If password valid, set session and redirect to /. Else render index with error."""
    if password != BASIL_UI_PASSWORD:
        return templates.TemplateResponse(
            "index.html",
            _template_context(request, False, error="Invalid password."),
        )
    request.session["authed"] = True
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout", response_class=RedirectResponse)
async def logout(request: Request):
    """Clear session and redirect to /."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    raw_content: str = Form(""),
    sources: str = Form(""),
    mode: str = Form("announcement"),
    max_chars: str = Form("240"),
):
    """
    Require session authed; then validate raw_content and call basil_writer.
    Preserve cooldown and LAST_OUTPUT duplicate logic.
    """
    global LAST_REQUEST_TS, LAST_OUTPUT
    authed = bool(request.session.get("authed"))
    if not authed:
        return templates.TemplateResponse(
            "index.html",
            _template_context(request, False, error="Please unlock first."),
        )

    from app.basil_writer import generate_basil_tweet, rewrite_basil_tweet

    if not (raw_content and raw_content.strip()):
        return templates.TemplateResponse(
            "index.html",
            _template_context(
                request, True, error="Raw content is required.",
                raw_content=raw_content, sources=sources, mode=mode, max_chars=max_chars,
            ),
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
            _template_context(
                request, True,
                error="Please wait a few seconds before generating again.",
                raw_content=raw_content, sources=sources, mode=mode, max_chars=max_chars,
            ),
        )

    try:
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
            _template_context(
                request, True, error=f"Generation failed: {e!s}",
                raw_content=raw_content, sources=sources, mode=mode, max_chars=max_chars,
            ),
        )

    return templates.TemplateResponse(
        "index.html",
        _template_context(
            request, True, output=out,
            raw_content=raw_content, sources=sources, mode=mode, max_chars=max_chars,
        ),
    )
