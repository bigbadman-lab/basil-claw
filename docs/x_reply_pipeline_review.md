# X Reply Pipeline Review

## 1) High-level overview

- **Single entrypoint for reply pipeline:** `python3 -m x_bridge.run_mentions_once` (cron). It fetches mentions, drafts mention replies, optionally runs whitelist ingest+draft in the same run, then claims and posts both mention and whitelist replies in one loop.
- **Mentions:** Fetched via X API `get_users_mentions` (Basil’s user ID). Stored in `x_mentions`. Filtered (self, RT, empty, already replied). Reply generated with intent + RAG + canon; stored in `x_replies` (mention_tweet_id). Posted via `x_client.post_reply`; success written back to `x_replies` and `x_mentions.status = 'replied'`.
- **Whitelist:** Accounts from `x_whitelist_accounts` (enabled, user_id set). Tweets fetched per account via `get_user_tweets` (exclude replies/retweets). Targets stored in `x_targets`. Draft decision by `whitelist_reply_heuristics` (score + MIN_REPLY_SCORE + eligible + wildcard). Drafts from `run_whitelist_once` go to `x_replies` via `insert_whitelist_reply`; alternate path `whitelist_reply.py` uses `x_target_replies` via `upsert_target_reply`. Claim/post: mention rows from `x_replies`, whitelist rows from `x_target_replies` (separate claim), same `post_one` and `x_client.post_reply`.
- **Shared:** One posting budget (`allowed_this_run`, `hourly_post_cap`), one circuit breaker (`x_cursor` posting_enabled/disable), one X client `post_reply`, truncation to 280. Generation differs: mention uses `generate_reply_for_tweet` (intent + RAG); whitelist uses `generate_reply_whitelist_text` (whitelist prompt, numbers_safe option).
- **Risk:** Whitelist drafts from `run_whitelist_once` are written to `x_replies` only; claim flow has Pass 1 (x_replies) and Pass 2 (x_target_replies). So currently all claimable whitelist drafts from the main run are in `x_replies` and are claimed in Pass 1; Pass 2 is for drafts coming from `x_target_replies` (e.g. `whitelist_reply.py`). If only `run_whitelist_once` is used, everything is in Pass 1 and whitelist rows are posted with `kind="mention"` (DB updates still correct for `x_replies`; `mark_replied` is a no-op for target_tweet_id).

---

## 2) Flow diagram / call graph

```
Entrypoint: python3 -m x_bridge.run_mentions_once
  └─ run_mentions_once.run_once()
       ├─ db.get_connection(), db.try_advisory_xact_lock(conn)
       ├─ db.count_posts_last_hour(), allowed_this_run = min(max_posts_per_run, hourly_post_cap - posted_last_hour)
       ├─ db.apply_expired_disable_clear(conn), posting_gate log
       ├─ db.get_cursor("mentions_since_id", conn)
       ├─ x_client.get_mentions(since_id=cursor, max_results=50)   # FETCH MENTIONS
       ├─ for each mention (sorted by tweet_id):
       │     ├─ filters: author_id == X_USER_ID → skip + upsert_mention(status=skipped)
       │     ├─ filters: _is_retweet(text) → skip + upsert_mention(status=skipped)
       │     ├─ filters: _is_empty_mention(text) → skip + upsert_mention(status=skipped)
       │     ├─ db.upsert_mention(..., no status)  # stored
       │     ├─ db.is_replied(tweet_id) → continue
       │     ├─ ingest.reply_engine.generate_reply_for_tweet(text)
       │     ├─ _truncate_reply_to_limit(reply_text)
       │     ├─ db.insert_reply(mention_tweet_id=tweet_id, reply_text=..., decision=drafted/dry_run)
       │     └─ db.mark_mention_status(tweet_id, "drafted")
       ├─ db.set_cursor("mentions_since_id", newest_tweet_id, conn)
       ├─ if whitelist_reply_enabled: run_whitelist_once.run_ingest_and_draft(conn)
       │     └─ run_whitelist_once.run_ingest_and_draft(conn)
       │           ├─ db.list_enabled_whitelist_accounts(conn)
       │           ├─ for each (handle, user_id): x_client.get_user_tweets(user_id, since_id=cursor, exclude=["replies","retweets"])
       │           │     ├─ filter: _is_reply_or_retweet(refs), created_at < cutoff, reply_settings != "everyone", tweet_id != conversation_id
       │           │     ├─ db.upsert_target(...)  # x_targets
       │           │     └─ db.set_whitelist_cursor(user_id, advanced_since_id)
       │           ├─ db.list_unreplied_targets(limit=50, conn)
       │           ├─ for each target: whitelist_reply_heuristics.whitelist_should_reply_and_persist(tweet_id, tweet_text, conn)
       │           │     ├─ db.update_target_reply_decision(tweet_id, decision, score, reason)
       │           │     ├─ if decision=="skip": skipped_eligible.append(...); continue
       │           │     ├─ generate_reply_whitelist_text(tweet_text, conn, needs_numbers_safe_reply=...)
       │           │     ├─ (optional retry if numbers_safe and reply contains digit)
       │           │     ├─ db.insert_whitelist_reply(target_tweet_id, reply_text, decision=drafted)  # x_replies
       │           │     └─ or db.upsert_target_reply(..., decision=blocked) on numbers_reply_failed
       │           └─ wildcard: if drafts_created==0 and skipped_eligible: pick best, update_target_reply_decision(..., ";wildcard"), _do_draft again
       ├─ claim + post phase (if not X_DRY_RUN and can_post and allowed_this_run > 0):
       │     ├─ db.claim_replies_for_posting(limit=allowed_this_run, ...)   # x_replies: mention_tweet_id OR target_tweet_id
       │     ├─ db.claim_whitelist_replies_for_posting(limit=whitelist_cap, ...)   # x_target_replies
       │     ├─ for (reply_id, mention_tweet_id, reply_text) in claimed_mentions: post_one(mention_tweet_id, reply_text, "mention", reply_id)
       │     ├─ for (target_tweet_id, reply_text) in claimed_whitelist: post_one(target_tweet_id, reply_text, "whitelist", target_tweet_id)
       │     └─ post_one: x_client.post_reply(reply_text, in_reply_to_tweet_id) → db.update_reply_posted / db.mark_replied OR db.update_target_reply_posted, db.record_post_success; on error: set_reply_error/set_target_reply_error, disable_posting/record_post_failure
       ├─ db.apply_expired_disable_clear(conn), run_end log
       └─ conn.commit(), conn.close()
```

---

## 3) Mention replies: step-by-step (file → function refs)

| Step | Where | What |
|------|--------|------|
| Entrypoint | `run_mentions_once.py` | `run_once()` (called via `__main__`). |
| Fetch | `x_client.get_mentions(since_id=cursor, max_results=50)` | `x_client.py`: `get_mentions()` → Tweepy `client.get_users_mentions(id=uid, since_id=..., max_results=100, tweet_fields=..., expansions=["author_id"])`. Returns list of dicts: `tweet_id`, `author_id`, `author_username`, `text`, `created_at`, `raw_json`. |
| Store mentions | `db.upsert_mention(tweet_id, author_id, author_username, text, created_at, raw_json, status=optional)` | `db.py`: `upsert_mention()` → INSERT into `x_mentions` ON CONFLICT (tweet_id) DO UPDATE. Key fields: tweet_id (PK), author_id, author_username, text, created_at, raw_json, status (new/skipped/replied/failed/drafted). |
| Filters (skip) | `run_mentions_once.py` in loop over mentions | (1) **Self:** `author_id == X_USER_ID` → upsert with status=skipped, continue. (2) **Retweet:** `_is_retweet(text)` (text.upper().startswith("RT ")) → skipped, continue. (3) **Empty:** `_is_empty_mention(text)` (non-@ tokens < 3 chars) → skipped, continue. (4) **Already replied:** `db.is_replied(tweet_id)` (x_mentions.status='replied' or row in x_replies for mention_tweet_id) → continue. No explicit “age” filter for mentions; cursor-based so only new since last run. |
| Generation | `ingest.reply_engine.generate_reply_for_tweet(text)` | `reply_engine.py`: Opens own DB connection; `load_basil_canon(conn)`; `classify_intent(user_text)` (casual / policy_question / abuse_bait / other / about_basil); for policy_question/other/about_basil: `embed_query(user_text)`, `retrieve_chunks(conn, qvec, ...)` or `get_basil_about_chunks`; `_generate_reply(user_text, intent, retrieved, canon)` → OpenAI `client.responses.create` with canon + style (1–2 sentences, max 240 chars) + context block. Returns reply text. |
| Constraints | `run_mentions_once.py` | Reply truncated with `_truncate_reply_to_limit(reply_text, max_len=280)` (word boundary). Safety: “no invented facts” and style in reply_engine prompt; no separate safety filter. Rate: `allowed_this_run = min(max_posts_per_run, hourly_post_cap - posted_last_hour)`; posting also gated by `x_cursor` (posting_enabled, posting_disabled_until). |
| Store draft | `db.insert_reply(in_reply_to_tweet_id=tweet_id, reply_tweet_id=None, reply_text=..., decision="drafted")` | `db.py`: `insert_reply()` → INSERT into `x_replies` (mention_tweet_id, reply_tweet_id, reply_text, decision, ...) ON CONFLICT (mention_tweet_id) DO NOTHING. Then `db.mark_mention_status(tweet_id, "drafted")`. |
| Claim | `db.claim_replies_for_posting(limit=allowed_this_run, claimed_by="run_mentions_once", conn)` | `db.py`: SELECT from `x_replies` WHERE reply_tweet_id IS NULL AND (error_text NULL or '') AND (mention_tweet_id OR target_tweet_id) NOT NULL, ORDER BY created_at ASC, LIMIT; UPDATE post_claimed_at, post_claimed_by; RETURNING (id, in_reply_to, reply_text). |
| Post | `x_client.post_reply(reply_text, in_reply_to_tweet_id)` | `x_client.py`: `post_reply(text, in_reply_to_tweet_id)` → Tweepy `client.create_tweet(text=..., in_reply_to_tweet_id=..., user_auth=True)`. No conversation_id or other reply params. |
| Record success | `db.update_reply_posted(reply_id, reply_tweet_id)`, `db.mark_replied(in_reply_to_tweet_id)` | `db.py`: UPDATE `x_replies` SET reply_tweet_id, decision='posted', posted_at=now() WHERE id=reply_id; UPDATE `x_mentions` SET status='replied' WHERE tweet_id=in_reply_to_tweet_id. |

---

## 4) Whitelist replies: step-by-step (file → function refs)

| Step | Where | What |
|------|--------|------|
| Config | Env + DB | **Env:** `WHITELIST_REPLY_ENABLED`, `WHITELIST_MAX_REPLIES_PER_RUN` (default 3), `WHITELIST_REPLY_MAX_AGE_MINUTES` (30), `MIN_REPLY_SCORE` (1.0), `WHITELIST_REPLY_PROB_DEFAULT` (0.35). **DB:** `x_whitelist_accounts` (handle, user_id, enabled); `x_whitelist_cursor` (user_id, since_id); `x_targets` (tweet_id, source, author_user_id, author_handle, tweet_text, …); `x_target_replies` (target_tweet_id, reply_text, decision, block_reason, source, post_claimed_at, …). |
| Fetch targets | `run_whitelist_once.run_ingest_and_draft(conn)` | `db.list_enabled_whitelist_accounts(conn)` → (handle, user_id). Per account: `db.get_whitelist_cursor(user_id)`, then `x_client.get_user_tweets(user_id, since_id=cursor, max_results=100, exclude=["replies","retweets"])` (tweet_fields include reply_settings, conversation_id). |
| Store targets | `db.upsert_target(tweet_id, source="whitelist", author_user_id, author_handle, tweet_text, tweet_created_at, raw_json)` | `db.py`: `upsert_target()` → INSERT into `x_targets` ON CONFLICT (tweet_id) DO UPDATE. |
| Filters (skip) | `run_whitelist_once.py` in loop over tweets | (1) **Reply/retweet:** `_is_reply_or_retweet(referenced_tweets)`. (2) **Age:** `created_at < cutoff` (now - WHITELIST_REPLY_MAX_AGE_MINUTES). (3) **Reply settings:** `reply_settings != "everyone"` → log whitelist_skip_reply_restricted, skip. (4) **Not root:** `tweet_id != conversation_id` → log whitelist_skip_not_root, skip. |
| Unreplied list | `db.list_unreplied_targets(limit=50, conn)` | `db.py`: SELECT from `x_targets` t LEFT JOIN `x_replies` r ON r.target_tweet_id = t.tweet_id WHERE r.target_tweet_id IS NULL, ORDER BY tweet_created_at ASC, LIMIT. Returns (tweet_id, source, author_user_id, author_handle, tweet_text, tweet_created_at, inserted_at, raw_json). |
| Reply decision | `whitelist_reply_heuristics.whitelist_should_reply_and_persist(tweet_id, tweet_text, conn)` | `whitelist_reply_heuristics.py`: `whitelist_should_reply(tweet_id, tweet_text)` → score (short/mostly_links -3, question +2, claim +1, controversy +1, mobilization -2, celebration -2); eligible = len≥40 and not mostly_links; decision = reply if "?" in text or (score >= MIN_REPLY_SCORE and eligible); constraints (e.g. needs_numbers_safe_reply). Then `db.update_target_reply_decision(tweet_id, decision, score, reason)`. |
| Wildcard | `run_whitelist_once.run_ingest_and_draft` | If drafts_created==0 and skipped_eligible non-empty: pick max by score, `db.update_target_reply_decision(..., "reply", ..., reason+";wildcard")`, then _do_draft for that target. |
| Generation | `ingest.reply_engine.generate_reply_whitelist_text(tweet_text, conn, needs_numbers_safe_reply=...)` | `reply_engine.py`: `load_basil_canon(conn)`, `embed_query(user_text)`, `retrieve_chunks(conn, qvec, user_text, k=6)`, `_generate_reply_whitelist(...)` with whitelist style (max 2 sentences, 280 chars, witty/sharp, no hashtags; optional numbers_safe rules). If numbers_safe and reply contains digit: retry once; if still contains digit, `db.upsert_target_reply(..., decision=blocked, block_reason=numbers_reply_failed)`. |
| Store draft | `db.insert_whitelist_reply(target_tweet_id, reply_text, decision)` | `db.py`: `insert_whitelist_reply()` → INSERT into `x_replies` (target_tweet_id, mention_tweet_id=NULL, reply_tweet_id=NULL, reply_text, decision, source='whitelist') ON CONFLICT (target_tweet_id) DO UPDATE. (Draft storage is x_replies in this path; `whitelist_reply.py` uses `upsert_target_reply` → `x_target_replies`.) |
| Limits | `run_mentions_once.py` | `whitelist_cap = min(remaining_after_mentions, whitelist_max_replies_per_run)`. Same hourly cap and circuit breaker as mentions. |
| Claim | Pass 1: `db.claim_replies_for_posting(...)` (x_replies; includes rows with target_tweet_id from insert_whitelist_reply). Pass 2: `db.claim_whitelist_replies_for_posting(limit=whitelist_cap, ...)` | Pass 2 in `db.py`: `claim_whitelist_replies_for_posting()` → SELECT from `x_target_replies` WHERE decision='drafted', post_claimed_at IS NULL, reply_text NOT NULL; UPDATE post_claimed_at, post_claimed_by; RETURNING (target_tweet_id, reply_text). |
| Post | Same `post_one(..., kind="whitelist")` | `x_client.post_reply(reply_text, in_reply_to_tweet_id)` (same API). On success: `db.update_target_reply_posted(in_reply_to_tweet_id, reply_tweet_id)` (updates `x_target_replies`). |

---

## 5) Differences table (short)

| Aspect | Mention replies | Whitelist replies |
|--------|------------------|-------------------|
| **Fetch** | `get_users_mentions` (Basil) | `get_user_tweets` per whitelist account |
| **Storage (inputs)** | `x_mentions` | `x_targets` (+ `x_whitelist_accounts`, `x_whitelist_cursor`) |
| **Decision** | All non-filtered mentions get a draft | Heuristics: score, MIN_REPLY_SCORE, eligible, question; wildcard if 0 drafts |
| **Generation** | `generate_reply_for_tweet` (intent, RAG, canon, 240 chars) | `generate_reply_whitelist_text` (RAG, canon, whitelist style, 280 chars, numbers_safe) |
| **Draft storage** | `x_replies` (mention_tweet_id) | `x_replies` (target_tweet_id) in run_whitelist_once; `x_target_replies` in whitelist_reply.py |
| **Claim** | `claim_replies_for_posting` (x_replies) | Same + `claim_whitelist_replies_for_posting` (x_target_replies) |
| **Post** | `post_reply`; success → update_reply_posted + mark_replied | Same post_reply; success → update_target_reply_posted |
| **Caps** | max_posts_per_run (50), hourly_post_cap (300) | Plus whitelist_max_replies_per_run (3), whitelist_max_age (30 min) |
| **Filters** | Self, RT, empty, is_replied | Reply/RT, age, reply_settings, conversation root |

---

## 6) Refactor suggestions

1. **Unify whitelist draft storage:** `run_whitelist_once` currently writes drafts to `x_replies` via `insert_whitelist_reply`, while `claim_whitelist_replies_for_posting` reads from `x_target_replies`. Either (a) have `run_whitelist_once` write drafts to `x_target_replies` via `upsert_target_reply(..., decision='drafted')` so Pass 2 consistently claims whitelist drafts, or (b) have Pass 1 claim only mention rows (`WHERE mention_tweet_id IS NOT NULL`) and keep whitelist drafts in `x_replies` and add a dedicated claim step for “x_replies with target_tweet_id” so posting and DB updates (e.g. no mark_replied for targets) are consistent.

2. **Distinguish kind when claiming from x_replies:** `claim_replies_for_posting` returns (id, in_reply_to, reply_text) and the loop always uses `post_one(..., "mention", reply_id)`. For rows that are whitelist (target_tweet_id set), use kind `"whitelist"` so `update_target_reply_posted` is called instead of `mark_replied` (and error paths use set_target_reply_*). E.g. return (id, in_reply_to, reply_text, kind) with kind from a column or from mention_tweet_id IS NOT NULL.

3. **Extract post loop and error handling:** The `post_one` closure in `run_mentions_once` (rate limit, 403 reply-not-allowed, 403 other, 429, repeated_failures, update_reply_posted vs update_target_reply_posted) is long and duplicated in spirit with `run_standalone_once`. Consider a small module (e.g. `x_bridge/post_reply.py`) with a function `post_reply_with_handling(reply_text, in_reply_to_tweet_id, kind, id_for_log, conn)` that calls `x_client.post_reply`, updates DB, and handles errors/circuit breaker so both run_mentions_once and run_standalone_once call it.

4. **Single whitelist “draft” entry point:** Both `run_whitelist_once` (inline _do_draft + insert_whitelist_reply) and `whitelist_reply.py` (generate_reply_for_whitelist_target + upsert_target_reply) implement “decide → generate → persist”. Unify on one (e.g. always use `upsert_target_reply` for drafts and have run_whitelist_once call into a shared “draft one target” function that uses the same storage and numbers_safe logic.

5. **Document x_replies vs x_target_replies:** In `db.py` or a short schema doc, state when a reply row is created in `x_replies` (mention_tweet_id vs target_tweet_id) vs `x_target_replies` (drafted/blocked whitelist from whitelist_reply path), and how claim/post and update_reply_posted vs update_target_reply_posted interact so future changes don’t break one path.

6. **Mention “age” / cursor semantics:** Mentions are effectively “new since last cursor” only; there’s no explicit “ignore mentions older than X minutes”. If desired, add an optional age filter (e.g. skip if mention created_at older than Y) in the same way whitelist uses WHITELIST_REPLY_MAX_AGE_MINUTES, and document the difference.
