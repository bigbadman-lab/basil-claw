# X (Twitter) mentions loop for Basil

Poll Basil’s X mentions, generate replies with the retrieval-grounded reply engine, and post them. State is stored in Postgres (`x_mentions`, `x_replies`, `x_cursor`).

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Postgres connection string (same as ingest). |
| `OPENAI_API_KEY` | Yes | For embeddings and reply generation. |
| `X_API_KEY` | Yes | X OAuth 1.0a Consumer Key (API Key). |
| `X_API_SECRET` | Yes | X OAuth 1.0a Consumer Secret (API Secret). |
| `X_ACCESS_TOKEN` | Yes | X OAuth 1.0a Access Token. |
| `X_ACCESS_TOKEN_SECRET` | Yes | X OAuth 1.0a Access Token Secret. |
| `X_USER_ID` | Yes | Numeric X user ID of the Basil account (used for mentions endpoint). |
| `EMBEDDING_MODEL` | No | Default `text-embedding-3-small`. |
| `CHAT_MODEL` | No | Default `gpt-4.1-mini`. |
| `X_DRY_RUN` | No | Set to `1`, `true`, or `yes` to fetch and draft only (no posts to X). |
| `X_POSTING_ENABLED` | No | Env kill switch; posting only when set (e.g. `1`) and DB `posting_enabled` is true. |
| `MAX_POSTS_PER_RUN` | No | Max replies to post per run. Default `50`. |
| `HOURLY_POST_CAP` | No | Max replies in any rolling hour. Default `300`. |

Create and use a project/app in the [X Developer Portal](https://developer.x.com/) with OAuth 1.0a and “Read and write” (so you can read mentions and post replies). Use the same app’s keys and generate a user Access Token and Secret for the Basil account.

## Database

Apply the project schema so that `x_mentions`, `x_replies`, and `x_cursor` exist (see `schema.sql`). Optional columns `raw_json` on `x_mentions` and `x_replies` are added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `schema.sql`.

## Run once

From the repo root:

```bash
python3 -m x_bridge.run_mentions_once
```

- Loads `.env` (via `dotenv`).
- Reads cursor `mentions_since_id` from `x_cursor`.
- Fetches mentions from X (Tweepy v4, API v2).
- Sorts mentions oldest → newest and for each: upserts into `x_mentions`, skips if already replied, generates reply via `ingest.reply_engine.generate_reply_for_tweet`, posts reply with `x_client.post_reply`, inserts into `x_replies`, marks mention replied, then advances cursor.
- Saves the new `mentions_since_id` after processing the batch.

If the X API call to get mentions fails, the script exits non-zero. If a single mention fails (generate or post), it logs and continues. The cursor is only updated after processing the fetched list so it is not lost on partial failure.

## Scheduling

This file does not implement scheduling. Run `python3 -m x_bridge.run_mentions_once` from cron, a job runner, or your own scheduler at the desired interval (e.g. every 5–15 minutes, respecting X rate limits).

Example cron (live posting; caps shown in env; each run logs `max_posts_per_run`, `hourly_post_cap`, `allowed_this_run`, and at end `posted_this_run`, `posted_last_hour`, etc.):

```bash
X_DRY_RUN=0 X_POSTING_ENABLED=1 MAX_POSTS_PER_RUN=50 HOURLY_POST_CAP=300 python3 -m x_bridge.run_mentions_once
```

For dry-run (no posts), use `X_DRY_RUN=1` and omit or leave `X_POSTING_ENABLED` unset.
