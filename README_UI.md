# Basil Tweet UI

Minimal password-protected web UI to generate a single "Basil style" tweet from user-provided raw content. Drafts only; no X API integration.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
export OPENAI_API_KEY=...
export BASIL_UI_PASSWORD=...
uvicorn app.main:app --reload
```

Visit: **http://127.0.0.1:8000**

## Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for tweet generation |
| `BASIL_UI_PASSWORD` | Yes | Password required to submit the form and generate |
| `BASIL_UI_MODEL` | No | Model override (default: `CHAT_MODEL` or `gpt-4.1-mini`) |
| `CHAT_MODEL` | No | Fallback model if `BASIL_UI_MODEL` not set |

## Security note

This is basic password protection intended for internal use. The password is checked on each POST; there are no sessions or cookies. **Do not deploy publicly without HTTPS and stronger authentication.**
