import os
import re
from typing import List, Tuple, Optional, Dict, Any

from dotenv import load_dotenv
import psycopg2
from openai import OpenAI

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")  # change later if you want

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- 1) Baby intent classifier (simple + predictable) ---
def classify_intent(text: str) -> str:
    t = text.lower().strip()

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


# --- 2) Embed the user query ---
def embed_query(text: str) -> List[float]:
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input=[text],
        encoding_format="float",
    )
    return resp.data[0].embedding


# --- 3) Retrieve top-K relevant chunks via pgvector ---
def retrieve_chunks(conn, query_vec: List[float], query_text: str, k: int = 6) -> List[Tuple[int, str, str]]:
    """
    Returns list of tuples: (chunk_id, source_title, chunk_text)

    Assumes:
      embeddings(chunk_id, embedding OR vector, optional model)
      chunks(id, source_id, text OR content OR chunk_text)
      sources(id, title, optional source_type/type)
    Uses cosine distance, model filter, source weighting, and diversity (max 2 per source).
    """
    DEBUG_RETRIEVAL = True
    BEST_MATCH_MAX = 0.85
    KEEP_MATCH_MAX = 0.92
    ENTITY_BONUS = 0.18
    candidate_limit = 20
    with conn.cursor() as cur:
        # detect actual column names
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
        if "source_type" in src_cols:
            source_type_expr = "s.source_type"
        elif "type" in src_cols:
            source_type_expr = "s.type"
        else:
            source_type_expr = "NULL::text"

        qt = (query_text or "").lower()
        name_where = ""
        if ("musk" in qt) or ("elon" in qt):
            name_where = f" AND (COALESCE(s.title,'') ILIKE '%%musk%%' OR COALESCE(s.title,'') ILIKE '%%elon%%' OR c.{text_col} ILIKE '%%musk%%' OR c.{text_col} ILIKE '%%elon%%')"

        # Placeholder order: 1st SELECT distance (%s), 2nd WHERE e.<model_col> = %s, 3rd ORDER BY (%s), 4th LIMIT %s (when filter_by_model)
        where_model = f" AND e.{model_col} = %s" if filter_by_model else ""

        sql_cosine = f"""
            SELECT
                e.chunk_id,
                COALESCE(s.title, 'Unknown Source') as source_title,
                c.{text_col} as chunk_text,
                (e.{vec_col} <=> (%s)::vector) as distance,
                {source_type_expr} as source_type
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            LEFT JOIN sources s ON s.id = c.source_id
            WHERE 1=1{where_model}{name_where}
            ORDER BY e.{vec_col} <=> (%s)::vector
            LIMIT %s
        """
        sql_l2 = f"""
            SELECT
                e.chunk_id,
                COALESCE(s.title, 'Unknown Source') as source_title,
                c.{text_col} as chunk_text,
                (e.{vec_col} <-> (%s)::vector) as distance,
                {source_type_expr} as source_type
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            LEFT JOIN sources s ON s.id = c.source_id
            WHERE 1=1{where_model}{name_where}
            ORDER BY e.{vec_col} <-> (%s)::vector
            LIMIT %s
        """
        if filter_by_model:
            params_cosine = (query_vec, EMBED_MODEL, query_vec, candidate_limit)
            params_l2 = (query_vec, EMBED_MODEL, query_vec, candidate_limit)
        else:
            params_cosine = (query_vec, query_vec, candidate_limit)
            params_l2 = (query_vec, query_vec, candidate_limit)

        try:
            print("SQL (cosine):", sql_cosine)
            print("sql.count('%s'):", sql_cosine.count("%s"))
            print("len(params_cosine):", len(params_cosine))
            cur.execute(sql_cosine, params_cosine)
        except Exception:
            conn.rollback()
            cur.execute(sql_l2, params_l2)
        rows = cur.fetchall()

    if DEBUG_RETRIEVAL:
        print("Top candidates (raw):")
        for r in rows[:10]:
            chunk_id, source_title, chunk_text, raw_dist, source_type = r[0], r[1], r[2], float(r[3]), r[4]
            print(f"  {raw_dist:.4f}  {chunk_id}  {source_title}  {source_type}")

    # Weight by source: canon +0.05 penalty, else -0.02 bonus
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

    # Diversity: max 2 chunks per source_title
    chosen: List[Tuple[int, str, str]] = []
    chosen_debug: List[Tuple[float, float, int, str]] = []
    per_source_count: Dict[str, int] = {}
    for adjusted_distance, raw_distance, chunk_id, source_title, chunk_text in weighted:
        n = per_source_count.get(source_title, 0)
        if n >= 2:
            continue
        per_source_count[source_title] = n + 1
        chosen.append((chunk_id, source_title, chunk_text))
        chosen_debug.append((adjusted_distance, raw_distance, chunk_id, source_title))
        if len(chosen) >= k:
            break

    if DEBUG_RETRIEVAL:
        print("Selected (after weighting/diversity):")
        for adj, raw, cid, st in chosen_debug:
            print(f"  {adj:.4f}  {raw:.4f}  {cid}  {st}")

    if not chosen:
        return []

    best_adjusted_distance = chosen_debug[0][0]
    if best_adjusted_distance > BEST_MATCH_MAX:
        if DEBUG_RETRIEVAL:
            print(f"Retrieval rejected: best match too weak (best_adjusted={best_adjusted_distance:.4f})")
        return []

    chosen_filtered = [chosen[i] for i in range(len(chosen)) if chosen_debug[i][0] <= KEEP_MATCH_MAX]
    if DEBUG_RETRIEVAL:
        print(f"Filtered weak chunks: kept {len(chosen_filtered)} of {len(chosen)} (KEEP_MATCH_MAX={KEEP_MATCH_MAX})")

    return chosen_filtered


# --- 4) Load Basil canon from DB if present, else fallback to file ---
def load_basil_canon(conn) -> str:
    # Try to get canonical source text from sources table if it has a raw text column.
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='sources'
        """)
        src_cols = {r[0] for r in cur.fetchall()}

        text_col = None
        for candidate in ["raw_text", "text", "content", "body"]:
            if candidate in src_cols:
                text_col = candidate
                break

        if text_col:
            cur.execute(f"""
                SELECT {text_col}
                FROM sources
                WHERE title ILIKE %s
                ORDER BY id ASC
                LIMIT 1
            """, ("%Basil Canon%",))
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0])

    # fallback file
    canon_path = os.path.join("ingest", "sources", "basil_canon.md")
    with open(canon_path, "r", encoding="utf-8") as f:
        return f.read().strip()


# --- 5) Generate reply ---
def generate_reply(user_text: str, intent: str, retrieved, canon: str) -> str:
    context_block = ""
    if retrieved and intent in ("policy_question", "other"):
        lines = []
        for (chunk_id, source_title, chunk_text) in retrieved:
            lines.append(f"[{chunk_id}] {source_title}\n{chunk_text}")
        context_block = "\n\n".join(lines)

    import random
    MISSION_HOOKS = [
        "Still plotting how to restore Britain.",
        "Quietly working on the restore-Britain problem.",
        "Anyway—what's the first thing you'd fix to restore Britain?",
        "I'm fine. Britain's systems? Needs work.",
        "All good. Now: what bit of Britain do we repair first?",
    ]

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


def main():
    # Change this line to test different messages
    test_tweet = "what do you think about elon musk?"

    intent = classify_intent(test_tweet)
    print(f"\nIntent = {intent}")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        canon = load_basil_canon(conn)

        retrieved = []
        if intent in ("policy_question", "other"):
            qvec = embed_query(test_tweet)
            retrieved = retrieve_chunks(conn, qvec, test_tweet, k=6)

        print(f"Retrieved chunks = {len(retrieved)}")
        for cid, st, _ in retrieved[:3]:
            print(f"  - chunk_id={cid} source={st}")

        reply = generate_reply(test_tweet, intent, retrieved, canon)
        print("\nBasil reply:\n", reply)
    finally:
        conn.close()


if __name__ == "__main__":
    main()