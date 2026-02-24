import os
import re
from typing import List, Tuple, Optional

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
def retrieve_chunks(conn, query_vec: List[float], k: int = 6) -> List[Tuple[int, str, str]]:
    """
    Returns list of tuples: (chunk_id, source_title, chunk_text)

    Assumes:
      embeddings(chunk_id, embedding OR vector)
      chunks(id, source_id, text OR content OR chunk_text)
      sources(id, title)
    """
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

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='chunks'
        """)
        chunk_cols = {r[0] for r in cur.fetchall()}
        text_col = "text" if "text" in chunk_cols else ("content" if "content" in chunk_cols else ("chunk_text" if "chunk_text" in chunk_cols else None))
        if not text_col:
            raise RuntimeError("chunks table missing text column (expected 'text' or 'content' or 'chunk_text').")

        sql = f"""
            SELECT
                e.chunk_id,
                COALESCE(s.title, 'Unknown Source') as source_title,
                c.{text_col} as chunk_text
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            LEFT JOIN sources s ON s.id = c.source_id
            ORDER BY e.{vec_col} <-> %s
            LIMIT %s
        """
        cur.execute(sql, (query_vec, k))
        return cur.fetchall()


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
    test_tweet = "gm basil"

    intent = classify_intent(test_tweet)
    print(f"\nIntent = {intent}")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        canon = load_basil_canon(conn)

        retrieved = []
        if intent in ("policy_question", "other"):
            qvec = embed_query(test_tweet)
            retrieved = retrieve_chunks(conn, qvec, k=6)

        print(f"Retrieved chunks = {len(retrieved)}")
        for cid, st, _ in retrieved[:3]:
            print(f"  - chunk_id={cid} source={st}")

        reply = generate_reply(test_tweet, intent, retrieved, canon)
        print("\nBasil reply:\n", reply)
    finally:
        conn.close()


if __name__ == "__main__":
    main()