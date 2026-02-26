"""
Policy angle choice and snippet retrieval for standalone posts. Uses pgvector and x_standalone_state.
No web calls. Embedder is a callable(text) -> list[float] (e.g. ingest.reply_engine.embed_query).
"""

from __future__ import annotations

import logging
import os
from typing import Callable, List

logger = logging.getLogger(__name__)

# Curated policy angles (10–20 items) relevant to Restore Britain / policy docs
POLICY_ANGLES = [
    "immigration and borders",
    "economy and growth",
    "tax and spending",
    "NHS and healthcare",
    "education and skills",
    "crime and justice",
    "housing and planning",
    "energy and net zero",
    "transport and infrastructure",
    "welfare and benefits",
    "defence and security",
    "constitution and democracy",
    "local government and devolution",
    "business and regulation",
    "culture and heritage",
    "environment and conservation",
    "foreign policy and trade",
    "parliament and accountability",
]

# Keywords/phrases per angle: row text must contain at least one (case-insensitive) to be kept.
ANGLE_KEYWORDS: dict[str, list[str]] = {
    "immigration and borders": ["immigration", "borders", "asylum", "deportation", "visa", "refugee", "illegal migration", "border control"],
    "economy and growth": ["economy", "growth", "GDP", "inflation", "employment", "business", "investment", "productivity"],
    "tax and spending": ["tax", "spending", "budget", "fiscal", "revenue", "public spending", "taxation", "deficit"],
    "NHS and healthcare": ["NHS", "healthcare", "health", "hospital", "GP", "patient", "medical", "treatment", "care"],
    "education and skills": ["education", "school", "university", "skills", "training", "curriculum", "teachers", "students"],
    "crime and justice": ["crime", "justice", "police", "prison", "courts", "sentencing", "criminal", "law enforcement"],
    "housing and planning": ["housing", "planning", "homes", "planning permission", "planning reform", "affordable housing", "development"],
    "energy and net zero": ["energy", "net zero", "renewable", "carbon", "electricity", "nuclear", "solar", "wind", "emissions"],
    "transport and infrastructure": ["transport", "infrastructure", "roads", "rail", "trains", "DLR", "driverless", "highway"],
    "welfare and benefits": ["welfare", "benefits", "universal credit", "pension", "disability", "claimants", "social security"],
    "defence and security": ["defence", "defense", "military", "armed forces", "NATO", "security", "troops", "veterans"],
    "constitution and democracy": ["constitution", "democracy", "voting", "electoral", "referendum", "parliamentary", "sovereignty"],
    "local government and devolution": ["local government", "devolution", "councils", "local authority", "mayor", "devolved", "council tax"],
    "business and regulation": ["business", "regulation", "regulatory", "enterprise", "small business", "red tape", "competition"],
    "culture and heritage": ["culture", "heritage", "arts", "museum", "historic", "tourism", "broadcasting", "creative"],
    "environment and conservation": ["environment", "conservation", "wildlife", "biodiversity", "nature", "pollution", "green"],
    "foreign policy and trade": ["foreign policy", "trade", "international", "exports", "EU", "trade deal", "diplomacy", "tariffs"],
    "parliament and accountability": ["parliament", "accountability", "MP", "Westminster", "bill", "legislation", "scrutiny", "select committee"],
}

# Max retries to pick an angle different from last_angle; then allow repeat.
CHOOSE_ANGLE_RETRIES = 10


def choose_policy_angle(db, rng) -> str:
    """Choose from a curated list of policy angles. Prefer a different angle from x_standalone_state.last_angle (retry up to N times); allow repeat if list has 1 angle or N retries fail.
    db: database connection (or x_bridge.db module; a connection will be obtained and closed).
    """
    from x_bridge import db as db_module

    if hasattr(db, "cursor"):
        conn = db
        own_conn = False
    else:
        conn = db_module.get_connection()
        own_conn = True
    try:
        last = db_module.get_standalone_last_angle(conn=conn)
    finally:
        if own_conn and conn:
            conn.close()
    if len(POLICY_ANGLES) <= 1:
        return rng.choice(POLICY_ANGLES)
    for _ in range(CHOOSE_ANGLE_RETRIES):
        candidate = rng.choice(POLICY_ANGLES)
        if candidate != last:
            return candidate
    return rng.choice(POLICY_ANGLES)


def _is_dry_run() -> bool:
    raw = (os.getenv("X_DRY_RUN") or os.getenv("DRY_RUN") or "").strip().lower()
    return raw in ("1", "true", "yes")


def retrieve_policy_snippets(
    db,
    embedder: Callable[[str], List[float]],
    angle: str,
    top_k: int = 8,
) -> List[dict]:
    """Use pgvector to retrieve chunks for the given angle. Returns list of {chunk_id, source_doc, text}.
    db: database connection (or x_bridge.db module). embedder: callable(text) -> list[float].
    """
    from ingest.reply_engine import retrieve_chunks

    from x_bridge import db as db_module

    if hasattr(db, "cursor"):
        conn = db
        own_conn = False
    else:
        conn = db_module.get_connection()
        own_conn = True
    try:
        vec = embedder(angle)
        raw, counts = retrieve_chunks(
            conn,
            vec,
            angle,
            k=top_k,
            exclude_basil_about=True,
            return_counts=True,
        )
        total = counts.get("total_candidates", 0)
        after_filters = counts.get("after_filters", 0)
        retrieved_rows_count = counts.get("retrieved_rows_count", 0)
        logger.debug(
            "retrieve_policy_snippets total_candidates=%s after_filters=%s top_k=%s",
            total,
            after_filters,
            top_k,
        )
        if _is_dry_run():
            pre_filter = counts.get("accepted_candidates_pre_filter", len(raw))
            post_filter = counts.get("accepted_candidates_post_filter", len(raw))
            logger.info(
                "retrieve_policy_snippets accepted_candidates_pre_filter=%s accepted_candidates_post_filter=%s",
                pre_filter,
                post_filter,
            )
            if post_filter == 0:
                logger.info(
                    "retrieve_policy_snippets rejection_reasons rejected_empty_text=%s rejected_distance_threshold=%s rejected_missing_fields=%s rejected_doc_constraint=%s",
                    counts.get("rejected_empty_text", 0),
                    counts.get("rejected_distance_threshold", 0),
                    counts.get("rejected_missing_fields", 0),
                    counts.get("rejected_doc_constraint", 0),
                )
            logger.info(
                "retrieve_policy_snippets retrieved_rows_count=%s total_candidates=%s after_filters=%s top_k=%s",
                retrieved_rows_count,
                total,
                after_filters,
                top_k,
            )
            if retrieved_rows_count > 0:
                first_distance = counts.get("first_row_distance")
                first_text_empty = counts.get("first_row_text_empty")
                first_text_len = counts.get("first_row_text_len")
                logger.info(
                    "retrieve_policy_snippets first_row distance=%s text_empty=%s text_len=%s",
                    first_distance,
                    first_text_empty,
                    first_text_len,
                )
        snippets = [
            {"chunk_id": cid, "source_doc": source_title, "text": chunk_text}
            for (cid, source_title, chunk_text) in raw
        ]
        # Filter to rows whose text contains at least one angle keyword (case-insensitive).
        keywords = ANGLE_KEYWORDS.get(angle, [])
        if keywords:
            kw_lower = [k.lower() for k in keywords]
            filtered = [
                s for s in snippets
                if any(k in (s.get("text") or "").lower() for k in kw_lower)
            ]
            if len(filtered) == 0:
                logger.info("retrieve_policy_snippets reason=no_keyword_match angle=%s", angle)
                if _is_dry_run():
                    print("retrieve_policy_snippets reason=no_keyword_match angle=%s" % angle, flush=True)
                return []
            snippets = filtered
        if _is_dry_run():
            pre_filter = counts.get("accepted_candidates_pre_filter", len(raw))
            post_filter = counts.get("accepted_candidates_post_filter", len(raw))
            print(
                "retrieve_policy_snippets accepted_candidates_pre_filter=%s accepted_candidates_post_filter=%s"
                % (pre_filter, post_filter),
                flush=True,
            )
            if post_filter == 0:
                print(
                    "retrieve_policy_snippets rejection_reasons rejected_empty_text=%s rejected_distance_threshold=%s rejected_missing_fields=%s rejected_doc_constraint=%s"
                    % (
                        counts.get("rejected_empty_text", 0),
                        counts.get("rejected_distance_threshold", 0),
                        counts.get("rejected_missing_fields", 0),
                        counts.get("rejected_doc_constraint", 0),
                    ),
                    flush=True,
                )
            if not snippets:
                print(
                    "retrieve_policy_snippets total_candidates=%s after_filters=%s top_k=%s retrieved_rows_count=%s"
                    % (total, after_filters, top_k, retrieved_rows_count),
                    flush=True,
                )
                if retrieved_rows_count > 0:
                    print(
                        "retrieve_policy_snippets first_row distance=%s text_empty=%s text_len=%s"
                        % (
                            counts.get("first_row_distance"),
                            counts.get("first_row_text_empty"),
                            counts.get("first_row_text_len"),
                        ),
                        flush=True,
                    )
        return snippets
    finally:
        if own_conn and conn:
            conn.close()


def _word_set(text: str) -> set:
    return set((text or "").lower().split())


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def diversify_snippets(snippets: List[dict], max_snippets: int = 1) -> List[dict]:
    """Pick 1 or (if max_snippets=2) 2 snippets. Default returns exactly 1. If 2, the second must share source_doc with the first to avoid cross-doc blending."""
    input_count = len(snippets) if snippets else 0
    if not snippets:
        if _is_dry_run():
            logger.info("diversify_snippets input_count=0 output_count=0 reason=input_empty")
            print("diversify_snippets input_count=0 output_count=0 reason=input_empty", flush=True)
        return []
    if max_snippets <= 1:
        out = [snippets[0]]
        if _is_dry_run():
            logger.info("diversify_snippets input_count=%s output_count=1", input_count)
            print("diversify_snippets input_count=%s output_count=1" % input_count, flush=True)
        return out
    first = snippets[0]
    first_doc = (first.get("source_doc") or "").strip()
    # Second snippet only from same document
    same_doc = [s for i, s in enumerate(snippets) if i > 0 and (s.get("source_doc") or "").strip() == first_doc]
    if not same_doc:
        out = [first]
        if _is_dry_run():
            logger.info("diversify_snippets input_count=%s output_count=1 reason=no_same_doc_match", input_count)
            print("diversify_snippets input_count=%s output_count=1 reason=no_same_doc_match" % input_count, flush=True)
        return out
    w0 = _word_set(first.get("text") or "")
    best = same_doc[0]
    best_sim = _jaccard(w0, _word_set(best.get("text") or ""))
    for s in same_doc[1:]:
        sim = _jaccard(w0, _word_set(s.get("text") or ""))
        if sim < best_sim:
            best_sim = sim
            best = s
    out = [first, best]
    if _is_dry_run():
        logger.info("diversify_snippets input_count=%s output_count=2", input_count)
        print("diversify_snippets input_count=%s output_count=2" % input_count, flush=True)
    return out
