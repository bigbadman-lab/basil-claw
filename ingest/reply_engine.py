"""
Reply engine: retrieval-grounded Basil reply from user tweet text.

Importable as generate_reply_for_tweet(user_text) -> str.
Uses DATABASE_URL, OPENAI_API_KEY, EMBEDDING_MODEL, CHAT_MODEL.
"""

import os
import re
import random
from typing import Any, Dict, List, Tuple, Union

import psycopg2
from openai import OpenAI

from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MISSION_HOOKS = [
    "Still plotting how to restore Britain.",
    "Quietly working on the restore-Britain problem.",
    "Anyway—what's the first thing you'd fix to restore Britain?",
    "I'm fine. Britain's systems? Needs work.",
    "All good. Now: what bit of Britain do we repair first?",
]


def classify_intent(text: str) -> str:
    t = text.lower().strip()
    about_basil_phrases = [
        "who are you", "what are you", "where are you from", "bexleyheath",
        "who made you", "who created you", "who built you",
        "are you conservative", "conservative",
        "rupert lowe", "do you support rupert lowe", "who do you support",
    ]
    if any(p in t for p in about_basil_phrases):
        return "about_basil"
    casual_patterns = [
        r"\bgm\b", r"\bgn\b", r"\blol\b", r"\blmao\b", r"\bhiya\b", r"\bhey\b",
        r"how are you", r"how r u", r"u ok", r"what's up", r"whats up",
    ]
    if any(re.search(p, t) for p in casual_patterns):
        return "casual"
    policy_keywords = [
        "policy", "tax", "immigration", "nhs", "crime", "housing", "energy",
        "schools", "education", "borders", "welfare", "benefits", "jobs",
        "inflation", "economy", "net zero", "transport", "prisons",
    ]
    if any(k in t for k in policy_keywords) or "how do we" in t or "what would you" in t:
        return "policy_question"
    bait_keywords = ["idiot", "stupid", "moron", "kill", "die", "traitor"]
    if any(k in t for k in bait_keywords):
        return "abuse_bait"
    return "other"


def embed_query(text: str) -> List[float]:
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input=[text],
        encoding_format="float",
    )
    return resp.data[0].embedding


def retrieve_chunks(
    conn,
    query_vec: List[float],
    query_text: str,
    k: int = 6,
    only_canon_or_basil_about: bool = False,
    exclude_basil_about: bool = False,
    return_counts: bool = False,
) -> Union[List[Tuple[int, str, str]], Tuple[List[Tuple[int, str, str]], Dict[str, Any]]]:
    """Returns list of (chunk_id, source_title, chunk_text). If return_counts=True, returns (list, {total_candidates, after_filters, retrieved_rows_count, first_row_*})."""
    DEBUG_RETRIEVAL = False
    BEST_MATCH_MAX = 0.85
    KEEP_MATCH_MAX = 0.92
    ENTITY_BONUS = 0.18
    candidate_limit = 20
    counts: Dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='embeddings'
        """)
        emb_cols = {r[0] for r in cur.fetchall()}
        vec_col = "embedding" if "embedding" in emb_cols else ("vector" if "vector" in emb_cols else None)
        if not vec_col:
            raise RuntimeError("embeddings table missing vector column (expected 'embedding' or 'vector').")
        model_col = next((c for c in ("model", "embedding_model", "embed_model") if c in emb_cols), None)
        filter_by_model = model_col is not None

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='chunks'
        """)
        chunk_cols = {r[0] for r in cur.fetchall()}
        text_col = "text" if "text" in chunk_cols else ("content" if "content" in chunk_cols else ("chunk_text" if "chunk_text" in chunk_cols else None))
        if not text_col:
            raise RuntimeError("chunks table missing text column (expected 'text' or 'content' or 'chunk_text').")

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='sources'
        """)
        src_cols = {r[0] for r in cur.fetchall()}
        source_type_expr = "s.source_type" if "source_type" in src_cols else ("s.type" if "type" in src_cols else "NULL::text")

        qt = (query_text or "").lower()
        name_where = ""
        if ("musk" in qt) or ("elon" in qt):
            name_where = f" AND (COALESCE(s.title,'') ILIKE '%%musk%%' OR COALESCE(s.title,'') ILIKE '%%elon%%' OR c.{text_col} ILIKE '%%musk%%' OR c.{text_col} ILIKE '%%elon%%')"
        if only_canon_or_basil_about:
            name_where += f" AND (COALESCE(s.title,'') ILIKE '%%basil_about%%' OR COALESCE(s.title,'') ILIKE '%%Basil Canon%%' OR ({source_type_expr})::text ILIKE 'canon')"
        if exclude_basil_about:
            name_where += " AND COALESCE(s.title,'') NOT ILIKE '%basil_about%'"
        esc_model = EMBED_MODEL.replace("'", "''") if filter_by_model else ""
        where_model = f" AND e.{model_col} = '{esc_model}'" if filter_by_model else ""

        if return_counts:
            base_from = f"FROM embeddings e JOIN chunks c ON c.id = e.chunk_id LEFT JOIN sources s ON s.id = c.source_id"
            cur.execute(f"SELECT COUNT(*) {base_from} WHERE 1=1{where_model}")
            counts["total_candidates"] = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) {base_from} WHERE 1=1{where_model}{name_where}")
            counts["after_filters"] = int(cur.fetchone()[0])

        qvec_str = "[" + ",".join(str(float(x)) for x in query_vec) + "]"
        qvec_literal = "'" + qvec_str + "'::vector"
        sql_cosine = f"""
            SELECT e.chunk_id, COALESCE(s.title, 'Unknown Source') as source_title, c.{text_col} as chunk_text,
                   (e.{vec_col} <=> {qvec_literal}) as distance, {source_type_expr} as source_type
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            LEFT JOIN sources s ON s.id = c.source_id
            WHERE 1=1{where_model}{name_where}
            ORDER BY e.{vec_col} <=> {qvec_literal}
            LIMIT {candidate_limit}
        """
        sql_l2 = f"""
            SELECT e.chunk_id, COALESCE(s.title, 'Unknown Source') as source_title, c.{text_col} as chunk_text,
                   (e.{vec_col} <-> {qvec_literal}) as distance, {source_type_expr} as source_type
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            LEFT JOIN sources s ON s.id = c.source_id
            WHERE 1=1{where_model}{name_where}
            ORDER BY e.{vec_col} <-> {qvec_literal}
            LIMIT {candidate_limit}
        """
        try:
            cur.execute(sql_cosine)
        except Exception:
            conn.rollback()
            cur.execute(sql_l2)
        rows = cur.fetchall()

        if return_counts:
            counts["retrieved_rows_count"] = len(rows)
            counts["accepted_candidates_pre_filter"] = len(rows)
            if rows:
                r0 = rows[0]
                chunk_text = r0[2] if len(r0) > 2 else None
                counts["first_row_distance"] = float(r0[3]) if len(r0) > 3 else None
                counts["first_row_text_empty"] = not (chunk_text or "").strip()
                counts["first_row_text_len"] = len(chunk_text or "")

    # Rejection counts for debug (only meaningful when return_counts=True)
    rejected_empty_text = sum(1 for r in rows if not ((r[2] if len(r) > 2 else None) or "").strip()) if return_counts else 0
    rejected_missing_fields = sum(1 for r in rows if (r[0] is None or r[1] is None)) if return_counts else 0

    query_lower = (query_text or "").lower()
    entity_terms = ("elon", "musk")
    query_has_entity = any(t in query_lower for t in entity_terms)
    weighted: List[Tuple[float, float, int, str, str]] = []
    for r in rows:
        chunk_id, source_title, chunk_text, raw_distance, source_type = r[0], r[1], r[2], float(r[3]), r[4]
        adjusted_distance = raw_distance
        if source_type == "canon":
            adjusted_distance += 0.05
        else:
            adjusted_distance -= 0.02
        if query_has_entity:
            st_lower = (source_title or "").lower()
            ct_lower = (chunk_text or "").lower()
            if any(t in st_lower or t in ct_lower for t in entity_terms):
                adjusted_distance -= ENTITY_BONUS
        weighted.append((adjusted_distance, raw_distance, chunk_id, source_title, chunk_text))
    weighted.sort(key=lambda x: x[0])

    chosen: List[Tuple[int, str, str]] = []
    chosen_debug: List[Tuple[float, float, int, str]] = []
    per_source_count: Dict[str, int] = {}
    rejected_doc_constraint = 0
    for adjusted_distance, raw_distance, chunk_id, source_title, chunk_text in weighted:
        n = per_source_count.get(source_title, 0)
        if n >= 2:
            if return_counts:
                rejected_doc_constraint += 1
            continue
        per_source_count[source_title] = n + 1
        chosen.append((chunk_id, source_title, chunk_text))
        chosen_debug.append((adjusted_distance, raw_distance, chunk_id, source_title))
        if len(chosen) >= k:
            break
    if not chosen:
        if return_counts:
            counts["accepted_candidates_post_filter"] = 0
            counts["rejected_empty_text"] = rejected_empty_text
            counts["rejected_missing_fields"] = rejected_missing_fields
            counts["rejected_distance_threshold"] = 0
            counts["rejected_doc_constraint"] = rejected_doc_constraint
        return ([], counts) if return_counts else []
    if chosen_debug[0][0] > BEST_MATCH_MAX:
        if return_counts:
            counts["accepted_candidates_post_filter"] = 0
            counts["rejected_empty_text"] = rejected_empty_text
            counts["rejected_missing_fields"] = rejected_missing_fields
            counts["rejected_distance_threshold"] = 1
            counts["rejected_doc_constraint"] = rejected_doc_constraint
        return ([], counts) if return_counts else []
    chosen_filtered = [chosen[i] for i in range(len(chosen)) if chosen_debug[i][0] <= KEEP_MATCH_MAX]
    if return_counts:
        counts["accepted_candidates_post_filter"] = len(chosen_filtered)
        counts["rejected_empty_text"] = rejected_empty_text
        counts["rejected_missing_fields"] = rejected_missing_fields
        counts["rejected_distance_threshold"] = len(chosen) - len(chosen_filtered)
        counts["rejected_doc_constraint"] = rejected_doc_constraint
    return (chosen_filtered, counts) if return_counts else chosen_filtered


def get_basil_about_chunks(conn, limit: int = 8) -> List[Tuple[int, str, str]]:
    """
    Fetch chunks for the basil_about.md source only, by source title/locator.
    No vector similarity; order by c.id, return first limit chunks.
    Returns list of (chunk_id, source_title, chunk_text).
    """
    n = min(10, max(6, limit))
    with conn.cursor() as cur:
        sql = f"""
            SELECT c.id, s.title, c.content
            FROM chunks c
            JOIN sources s ON s.id = c.source_id
            WHERE s.title ILIKE '%basil_about%'
            ORDER BY c.id
            LIMIT {n}
        """
        cur.execute(sql)
        return list(cur.fetchall())


def load_basil_canon(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='sources'
        """)
        src_cols = {r[0] for r in cur.fetchall()}
        text_col = next((c for c in ["raw_text", "text", "content", "body"] if c in src_cols), None)
        if text_col:
            cur.execute(f"SELECT {text_col} FROM sources WHERE title ILIKE %s ORDER BY id ASC LIMIT 1", ("%Basil Canon%",))
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0])
    canon_path = os.path.join(os.path.dirname(__file__), "sources", "basil_canon.md")
    with open(canon_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _generate_reply(user_text: str, intent: str, retrieved: List[Tuple[int, str, str]], canon: str) -> str:
    context_block = ""
    if retrieved and intent in ("policy_question", "other", "about_basil"):
        lines = [f"[{cid}] {st}\n{ct}" for (cid, st, ct) in retrieved]
        context_block = "\n\n".join(lines)
    hook_hint = ""
    if intent == "casual":
        hook_hint = f"Mission-hook suggestion (use or paraphrase): {random.choice(MISSION_HOOKS)}"
    system = f"""
You are Basil Clawthorne 🦞. Follow the canon below strictly.

CANON:
{canon}

STYLE:
- 1–2 sentences (max 240 characters).
- Dry wit. Confident. Slightly mischievous.
- Mission hook must feel conversational, not like a slogan.

RULES:
- Do not invent facts.
- No hashtags.
- No bullet points.
- No links unless asked.
""".strip()
    user = f"""
Tweet: {user_text}
Intent: {intent}

{hook_hint}

Context:
{context_block if context_block else "[no retrieved context]"}
""".strip()
    resp = client.responses.create(
        model=CHAT_MODEL,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.output_text.strip()


_NUMBERS_SAFE_WHITELIST_RULES = """
NUMBERS-SAFE MODE (target tweet contains digits/stats; your reply must not):
- Your reply MUST NOT contain any digits (0-9).
- Your reply MUST NOT introduce any statistics or quantitative claims.
- You MAY challenge sourcing, definitions, framing, or enforcement.
- Keep under 220 characters.
- Basil voice: sharp, witty, combative; no personal abuse.
"""


def _generate_reply_whitelist(
    user_text: str,
    retrieved: List[Tuple[int, str, str]],
    canon: str,
    needs_numbers_safe_reply: bool = False,
) -> str:
    """Whitelist style: max 2 sentences, 280 chars, witty/sharp/confident, no hashtags, at most one emoji (use sparingly), no ungrounded factual assertions. If needs_numbers_safe_reply, reply must have no digits or stats."""
    context_block = ""
    if retrieved:
        lines = [f"[{cid}] {st}\n{ct}" for (cid, st, ct) in retrieved]
        context_block = "\n\n".join(lines)
    style_block = """
STYLE (whitelist engagement):
- Maximum 2 sentences. Maximum 280 characters total.
- Witty, sharp, confident. Dry wit. Slightly mischievous.
- No hashtags. No bullet points. No links.
- At most one emoji; use only when it really fits (roughly 10% of the time).
- Do not start with "One must acknowledge" or similar formal openers.
""".strip()
    if needs_numbers_safe_reply:
        style_block += "\n\n" + _NUMBERS_SAFE_WHITELIST_RULES.strip()
    system = """
You are Basil Clawthorne. Follow the canon below strictly.

CANON:
{canon}

{style_block}

RULES:
- Do not invent or assert factual claims unless they are clearly grounded in the retrieved context below.
- If you cannot ground a fact from retrieval, phrase your point as opinion or a question instead.
""".strip().format(canon=canon, style_block=style_block)
    user = """
Tweet: {user_text}

Context (use only to ground facts; otherwise be concise and sharp):
{context_block}
""".strip().format(
        user_text=user_text,
        context_block=context_block if context_block else "[no retrieved context]",
    )
    resp = client.responses.create(
        model=CHAT_MODEL,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    out = (resp.output_text or "").strip()
    if len(out) > 280:
        out = out[:277].rsplit(" ", 1)[0] + "..." if " " in out[:277] else out[:277] + "..."
    return out


def generate_reply_whitelist_text(
    user_text: str,
    conn,
    needs_numbers_safe_reply: bool = False,
) -> str:
    """
    Generate a single Basil reply for whitelist engagement: same retrieval + canon as mentions,
    but with whitelist instruction set (max 2 sentences, 280 chars, witty/sharp, no ungrounded facts).
    If needs_numbers_safe_reply is True, the reply must contain no digits and no statistics.
    Caller must pass an open DB connection (e.g. from psycopg2).
    """
    canon = load_basil_canon(conn)
    qvec = embed_query(user_text)
    retrieved = retrieve_chunks(conn, qvec, user_text, k=6)
    return _generate_reply_whitelist(user_text, retrieved, canon, needs_numbers_safe_reply)


def generate_reply_for_tweet(user_text: str) -> str:
    """
    Generate a single Basil reply for the given mention text.
    Uses DB for canon and retrieval, OpenAI for embed + reply. Opens and closes its own connection.
    """
    conn = psycopg2.connect(DATABASE_URL)
    try:
        canon = load_basil_canon(conn)
        intent = classify_intent(user_text)
        retrieved: List[Tuple[int, str, str]] = []
        if intent in ("policy_question", "other", "about_basil"):
            if intent == "about_basil":
                retrieved = get_basil_about_chunks(conn, limit=8)
            elif intent == "policy_question":
                qvec = embed_query(user_text)
                retrieved = retrieve_chunks(conn, qvec, user_text, k=6, exclude_basil_about=True)
            else:
                qvec = embed_query(user_text)
                retrieved = retrieve_chunks(conn, qvec, user_text, k=6)
        return _generate_reply(user_text, intent, retrieved, canon)
    finally:
        conn.close()
