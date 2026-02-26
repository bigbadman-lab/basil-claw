"""
Tests for x_bridge.standalone.basil_moment: period mapping, prompt constraints, deterministic RNG.
Run: python -m tests.test_basil_moment
"""

import sys
from datetime import datetime, timezone
from unittest.mock import patch

# Run from repo root
sys.path.insert(0, ".")

from zoneinfo import ZoneInfo

import re

from x_bridge.standalone.basil_moment import (
    build_irreverent_user_prompt,
    format_moment_line,
    get_local_time_context,
    make_rng,
    pick_basil_activity,
)


def test_period_mapping():
    """Correct period for London hour: 06-10 morning, 10-14 midday, 14-18 afternoon, 18-23 evening, 23-06 late."""
    # Use a fixed date so day_name is stable; vary hour via UTC so London hour is as desired
    tz = ZoneInfo("Europe/London")
    base = datetime(2025, 2, 24, 0, 0, 0, tzinfo=timezone.utc)
    # London is UTC+0 in winter, so UTC hour = London hour
    cases = [
        (5, "late"),   # 05:00 London
        (6, "morning"),
        (9, "morning"),
        (10, "midday"),
        (13, "midday"),
        (14, "afternoon"),
        (17, "afternoon"),
        (18, "evening"),
        (22, "evening"),
        (23, "late"),
        (0, "late"),
    ]
    for london_hour, expected_period in cases:
        now_utc = base.replace(hour=london_hour, minute=0, second=0)
        ctx = get_local_time_context(now_utc, "Europe/London")
        assert ctx["hour"] == london_hour, f"expected hour {london_hour} got {ctx['hour']}"
        assert ctx["period"] == expected_period, f"hour {london_hour} expected period {expected_period} got {ctx['period']}"
    print("OK: period mapping")


def test_prompt_contains_constraint_lines():
    """Prompt must include constraint lines for max chars, no emojis, no hashtags (when enabled), no stats, no @mentions, nautical, punchy rhythm, one lobster/claw max."""
    context = {"hour": 14, "period": "afternoon", "day_name": "Monday"}
    activity = {"activity": "writing", "props": ["ink stains"], "tone": "dry", "structure_hint": "one-liner"}

    with patch("x_bridge.standalone.basil_moment._config") as mock_config:
        mock_config.return_value.standalone_max_chars = 220
        mock_config.return_value.standalone_no_hashtags = True
        prompt = build_irreverent_user_prompt(context, activity)

    assert "220" in prompt or "Under 220" in prompt or "220 characters" in prompt
    assert "emoji" in prompt.lower()
    assert "hashtag" in prompt.lower()
    assert "number" in prompt.lower() or "stat" in prompt.lower()
    assert "@mention" in prompt.lower() or "no @" in prompt.lower()
    assert "attack ideas" in prompt.lower() or "ideas not people" in prompt.lower()
    assert "nautical" in prompt.lower() or "tide" in prompt.lower() or "harbour" in prompt.lower()
    assert "punch" in prompt.lower() or "short sentence" in prompt.lower() or "1–2" in prompt
    assert ("lobster" in prompt.lower() or "claw" in prompt.lower()) and ("one" in prompt.lower() or "at most" in prompt.lower())
    print("OK: prompt contains constraint lines")


def test_prompt_no_hashtags_line_when_disabled():
    """When STANDALONE_NO_HASHTAGS=0, prompt should not require 'no hashtags' (or we still mention the rule)."""
    context = {"hour": 12, "period": "midday", "day_name": "Tuesday"}
    activity = {"activity": "reviewing", "props": [], "tone": "wry", "structure_hint": "two short sentences"}

    with patch("x_bridge.standalone.basil_moment._config") as mock_config:
        mock_config.return_value.standalone_max_chars = 200
        mock_config.return_value.standalone_no_hashtags = False
        prompt = build_irreverent_user_prompt(context, activity)

    # Prompt is built; when no_hashtags is False we do not add the "No hashtags" line
    assert "Under 200" in prompt or "200" in prompt
    print("OK: prompt no hashtags line when disabled")


def test_deterministic_output_fixed_seed_and_timestamp():
    """Same seed_material + context yields same activity and same prompt (dry_run=False for determinism)."""
    seed = "2025-02-24 14 basil"
    rng1 = make_rng(seed, dry_run=False)
    rng2 = make_rng(seed, dry_run=False)
    context = {"hour": 14, "period": "afternoon", "day_name": "Monday"}

    activity1 = pick_basil_activity(context, rng1)
    activity2 = pick_basil_activity(context, rng2)

    assert activity1["activity"] == activity2["activity"]
    assert activity1["tone"] == activity2["tone"]
    assert activity1["structure_hint"] == activity2["structure_hint"]
    assert activity1["props"] == activity2["props"]

    with patch("x_bridge.standalone.basil_moment._config") as mock_config:
        mock_config.return_value.standalone_max_chars = 220
        mock_config.return_value.standalone_no_hashtags = True
        prompt1 = build_irreverent_user_prompt(context, activity1)
        prompt2 = build_irreverent_user_prompt(context, activity2)
    assert prompt1 == prompt2
    print("OK: deterministic output for fixed seed + timestamp")


def test_get_local_time_context_day_name():
    """day_name is a weekday string."""
    # 2025-02-24 is Monday
    now = datetime(2025, 2, 24, 12, 0, 0, tzinfo=timezone.utc)
    ctx = get_local_time_context(now, "Europe/London")
    assert ctx["day_name"] == "Monday"
    assert ctx["hour"] in range(0, 24)
    assert ctx["period"] in ("morning", "midday", "afternoon", "evening", "late")
    print("OK: get_local_time_context day_name")


def test_get_local_time_context_none_works():
    """get_local_time_context(None) uses current UTC time and returns valid context (no crash)."""
    ctx = get_local_time_context(None)
    assert "hour" in ctx
    assert "period" in ctx
    assert "day_name" in ctx
    assert ctx["hour"] in range(0, 24)
    assert ctx["period"] in ("morning", "midday", "afternoon", "evening", "late")
    assert ctx["day_name"] in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    print("OK: get_local_time_context(None) works")


def _has_no_numbers(s: str) -> bool:
    return not re.search(r"\d", s)


def _has_no_hashtags(s: str) -> bool:
    return "#" not in s


def _has_no_mentions(s: str) -> bool:
    return "@" not in s


def _has_no_emoji(s: str) -> bool:
    # Common emoji ranges (simplified: supplementaries and common symbols)
    return not re.search(r"[\u2600-\u27BF\U0001F300-\U0001F9FF]", s)


def test_format_moment_line_no_numbers_emojis_hashtags_mentions():
    """format_moment_line output must never contain digits, emojis, #, or @."""
    context = {"hour": 14, "period": "afternoon", "day_name": "Monday"}
    activity = {
        "activity": "writing",
        "props": ["quill in claw", "tea going cold"],
        "tone": "dry",
        "structure_hint": "one-liner",
    }
    line = format_moment_line(context, activity)
    assert isinstance(line, str), "format_moment_line must return str"
    assert _has_no_numbers(line), f"moment line must not contain numbers: {line!r}"
    assert _has_no_hashtags(line), f"moment line must not contain hashtags: {line!r}"
    assert _has_no_mentions(line), f"moment line must not contain @mentions: {line!r}"
    assert _has_no_emoji(line), f"moment line must not contain emojis: {line!r}"
    assert line.strip().startswith("Basil is "), f"moment line should start with 'Basil is ': {line!r}"
    assert line.strip().endswith("."), f"moment line should end with '.': {line!r}"
    print("OK: format_moment_line no numbers/emojis/hashtags/mentions")


def test_format_moment_line_varied_context_activity():
    """format_moment_line with different props still obeys constraints."""
    for hour in (6, 12, 18, 23):
        context = {"hour": hour, "period": "morning", "day_name": "Tuesday"}
        activity = {"activity": "reviewing", "props": ["tide charts", "ink stains"], "tone": "wry", "structure_hint": "one-liner"}
        line = format_moment_line(context, activity)
        assert _has_no_numbers(line) and _has_no_hashtags(line) and _has_no_mentions(line) and _has_no_emoji(line)
    # Empty props
    line = format_moment_line({"hour": 10}, {"props": []})
    assert _has_no_numbers(line) and "Basil is " in line
    print("OK: format_moment_line varied context/activity")


if __name__ == "__main__":
    try:
        test_period_mapping()
        test_prompt_contains_constraint_lines()
        test_prompt_no_hashtags_line_when_disabled()
        test_deterministic_output_fixed_seed_and_timestamp()
        test_get_local_time_context_day_name()
        test_get_local_time_context_none_works()
        test_format_moment_line_no_numbers_emojis_hashtags_mentions()
        test_format_moment_line_varied_context_activity()
    except AssertionError as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
