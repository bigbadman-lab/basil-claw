import os
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import psycopg2
from psycopg2.extras import execute_values

from openai import OpenAI  # openai-python v1 style client


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

CANON_PATH = os.path.join("ingest", "sources", "basil_canon.md")
URLS_PATH = os.path.join("ingest", "sources", "restore_britain_urls.txt")


@dataclass
class SourceDoc:
    source_type: str          # e.g. "canon" or "url"
    locator: str              # file path or URL
    title: str
    text: str


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_url_text(url: str, timeout: int = 30) -> Tuple[str, str]:
    """Return (title, clean_text) from a URL."""
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "basil-claw-ingest/1.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # title
    title = soup.title.get_text(strip=True) if soup.title else url

    # remove scripts/styles/nav-ish
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)

    # de-noise: collapse many blank lines
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    clean = "\n".join(lines)

    return title, clean


def load_sources() -> List[SourceDoc]:
    docs: List[SourceDoc] = []

    # 1) Canon markdown
    with open(CANON_PATH, "r", encoding="utf-8") as f:
        canon_text = f.read().strip()
    if not canon_text:
        raise RuntimeError("basil_canon.md is empty")

    docs.append(SourceDoc(
        source_type="canon",
        locator=CANON_PATH,
        title="Basil Canon",
        text=canon_text
    ))

    # 2) URLs list
    with open(URLS_PATH, "r", encoding="utf-8") as f:
        urls = [ln.strip() for ln in f.read().splitlines() if ln.strip() and not ln.strip().startswith("#")]

    for url in urls:
        title, clean = fetch_url_text(url)
        docs.append(SourceDoc(
            source_type="url",
            locator=url,
            title=title,
            text=clean
        ))

    return docs


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> List[str]:
    """
    Simple chunker:
    - splits by paragraphs
    - packs into ~max_chars chunks
    - adds overlap between chunks
    """
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    buf: List[str] = []
    size = 0

    for p in paras:
        if size + len(p) + 1 > max_chars and buf:
            chunk = "\n".join(buf).strip()
            chunks.append(chunk)

            # overlap: keep last overlap chars
            tail = chunk[-overlap:] if overlap > 0 else ""
            buf = [tail] if tail else []
            size = len(tail)

        buf.append(p)
        size += len(p) + 1

    if buf:
        chunks.append("\n".join(buf).strip())

    # remove empty chunks
    return [c for c in chunks if c.strip()]


def get_table_columns(cur, table_name: str) -> List[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,)
    )
    return [r[0] for r in cur.fetchall()]


def insert_source(cur, cols: List[str], doc: SourceDoc) -> Any:
    """
    Insert into sources, returning the new/existing source id.
    This tries to be schema-agnostic by only using columns that exist.
    """
    # candidate values
    values: Dict[str, Any] = {}
    # common patterns
    if "source_type" in cols:
        values["source_type"] = doc.source_type
    if "type" in cols and "source_type" not in values:
        values["type"] = doc.source_type

    if "url" in cols and doc.source_type == "url":
        values["url"] = doc.locator
    if "path" in cols and doc.source_type == "canon":
        values["path"] = doc.locator
    if "locator" in cols and ("url" not in values and "path" not in values):
        values["locator"] = doc.locator

    if "title" in cols:
        values["title"] = doc.title

    # store raw text if there is a place for it
    for candidate in ["raw_text", "text", "content", "body"]:
        if candidate in cols:
            values[candidate] = doc.text
            break

    # always store a stable hash if the schema supports it
    doc_hash = sha256(doc.text)
    for candidate in ["content_sha256", "content_hash", "hash", "sha256"]:
        if candidate in cols:
            values[candidate] = doc_hash
            break

    if not values:
        raise RuntimeError("sources table has none of the expected columns to insert into.")

    colnames = list(values.keys())
    placeholders = ", ".join(["%s"] * len(colnames))

    # If you have a unique constraint, prefer ON CONFLICT.
    # We try common uniqueness columns.
    conflict_target = None
    if "url" in colnames:
        conflict_target = "url"
    elif "path" in colnames:
        conflict_target = "path"
    elif "locator" in colnames:
        conflict_target = "locator"

    if conflict_target:
        sql = f"""
            INSERT INTO sources ({", ".join(colnames)})
            VALUES ({placeholders})
            ON CONFLICT ({conflict_target}) DO UPDATE SET
              {", ".join([f"{c}=EXCLUDED.{c}" for c in colnames if c != conflict_target])}
            RETURNING id
        """
    else:
        sql = f"""
            INSERT INTO sources ({", ".join(colnames)})
            VALUES ({placeholders})
            RETURNING id
        """

    cur.execute(sql, [values[c] for c in colnames])
    return cur.fetchone()[0]


def insert_chunks(cur, cols: List[str], source_id: Any, chunks: List[str]) -> List[Any]:
    """
    Insert chunks and return list of chunk ids in the same order.
    """
    # identify columns
    # required-ish: source_id, chunk_index, text/content, hash (optional)
    text_col = next((c for c in ["text", "content", "body", "chunk_text"] if c in cols), None)
    if not text_col:
        raise RuntimeError("chunks table missing a text/content column (expected one of text/content/body/chunk_text).")

    source_fk = "source_id" if "source_id" in cols else None
    if not source_fk:
        raise RuntimeError("chunks table missing source_id column.")

    idx_col = "chunk_index" if "chunk_index" in cols else ("index" if "index" in cols else None)

    hash_col = next((c for c in ["chunk_sha256", "content_sha256", "content_hash", "hash", "sha256"] if c in cols), None)

    insert_cols = [source_fk, text_col]
    if idx_col:
        insert_cols.append(idx_col)
    if hash_col:
        insert_cols.append(hash_col)

    rows = []
    for i, ch in enumerate(chunks):
        row = [source_id, ch]
        if idx_col:
            row.append(i)
        if hash_col:
            row.append(sha256(ch))
        rows.append(tuple(row))

    sql = f"INSERT INTO chunks ({', '.join(insert_cols)}) VALUES %s RETURNING id"
    execute_values(cur, sql, rows)
    ids = [r[0] for r in cur.fetchall()]
    return ids


def embed_texts(client: OpenAI, texts: List[str]) -> List[List[float]]:
    """
    Batch embeddings.
    Note: we explicitly request float output. :contentReference[oaicite:0]{index=0}
    """
    out: List[List[float]] = []
    batch_size = 96  # safe-ish batch size; tweak later
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        resp = client.embeddings.create(
            model=EMBED_MODEL,
            input=batch,
            encoding_format="float",
        )
        # Ensure ordered by index
        data_sorted = sorted(resp.data, key=lambda d: d.index)
        out.extend([d.embedding for d in data_sorted])
    return out


def insert_embeddings(cur, embeddings_cols: List[str], chunk_ids: List[Any], vectors: List[List[float]]):
    """
    Insert into embeddings table.

    Expected:
    - chunk_id
    - embedding/vector column
    - model column may be required
    """
    if "chunk_id" not in embeddings_cols:
        raise RuntimeError("embeddings table missing chunk_id column.")

    vec_col = next((c for c in ["embedding", "vector"] if c in embeddings_cols), None)
    if not vec_col:
        raise RuntimeError("embeddings table missing embedding/vector column.")

    insert_cols = ["chunk_id"]
    include_model = "model" in embeddings_cols
    if include_model:
        insert_cols.append("model")
    insert_cols.append(vec_col)

    rows = []
    for cid, vec in zip(chunk_ids, vectors):
        row = [cid]
        if include_model:
            row.append(EMBED_MODEL)
        row.append(vec)
        rows.append(tuple(row))

    sql = f"INSERT INTO embeddings ({', '.join(insert_cols)}) VALUES %s"
    execute_values(cur, sql, rows)


def main():
    print("Loading sources...")
    docs = load_sources()
    print(f"Found {len(docs)} sources")

    print("Connecting to Postgres...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    client = OpenAI()  # reads OPENAI_API_KEY from env

    try:
        with conn.cursor() as cur:
            sources_cols = get_table_columns(cur, "sources")
            chunks_cols = get_table_columns(cur, "chunks")
            embeddings_cols = get_table_columns(cur, "embeddings")

            for doc in docs:
                print(f"\n--- Ingesting source: {doc.title} ({doc.source_type})")
                source_id = insert_source(cur, sources_cols, doc)
                print(f"source_id = {source_id}")

                chs = chunk_text(doc.text)
                print(f"chunks = {len(chs)}")

                # Optional: clear old chunks/embeddings for this source if your schema supports it.
                # (We’re not doing deletes yet—keeping it simple & safe.)

                chunk_ids = insert_chunks(cur, chunks_cols, source_id, chs)
                print(f"inserted chunks = {len(chunk_ids)}")

                vecs = embed_texts(client, chs)
                print(f"embeddings generated = {len(vecs)}")

                insert_embeddings(cur, embeddings_cols, chunk_ids, vecs)
                print("embeddings inserted")

        conn.commit()
        print("\n✅ Ingestion complete.")
    except Exception as e:
        conn.rollback()
        print("\n❌ Ingestion failed. Rolled back.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()