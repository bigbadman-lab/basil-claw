"""
Env-based config for x_bridge. Same pattern as run_mentions_once: parse at import, module-level vars.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _parse_positive_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw.strip())
        return v if v > 0 else default
    except ValueError:
        return default


def _parse_int_nonnegative(env_key: str, default: int) -> int:
    """Parse int >= 0 (allows 0)."""
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw.strip())
        return v if v >= 0 else default
    except ValueError:
        return default


def _parse_float(env_key: str, default: float) -> float:
    raw = os.getenv(env_key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _parse_bool_default_false(env_key: str) -> bool:
    raw = (os.getenv(env_key) or "").strip().lower()
    return raw in ("1", "true", "yes")


def _parse_bool_default_true(env_key: str) -> bool:
    raw = (os.getenv(env_key) or "1").strip().lower()
    return raw in ("1", "true", "yes")


# Standalone post flow
standalone_post_enabled = _parse_bool_default_false("STANDALONE_POST_ENABLED")
standalone_post_interval_minutes = _parse_positive_int("STANDALONE_POST_INTERVAL_MINUTES", 60)
standalone_policy_weight = _parse_float("STANDALONE_POLICY_WEIGHT", 0.65)
standalone_max_chars = _parse_positive_int("STANDALONE_MAX_CHARS", 220)
standalone_max_regenerations = _parse_int_nonnegative("STANDALONE_MAX_REGENERATIONS", 1)
standalone_similarity_window = _parse_positive_int("STANDALONE_SIMILARITY_WINDOW", 20)
standalone_similarity_threshold = _parse_float("STANDALONE_SIMILARITY_THRESHOLD", 0.75)
standalone_no_hashtags = _parse_bool_default_true("STANDALONE_NO_HASHTAGS")
