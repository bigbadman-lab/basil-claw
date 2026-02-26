"""
Basil moment: time-of-day context, activity picking, and irreverent user prompt for standalone posts.
Uses x_bridge.config for STANDALONE_MAX_CHARS, STANDALONE_NO_HASHTAGS.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from datetime import datetime, timezone
from typing import Optional

from zoneinfo import ZoneInfo

# Import after module load so tests can patch config
def _config():
    from x_bridge import config
    return config


# Period boundaries (London hour, inclusive start, exclusive end): 6-10 morning, 10-14 midday, 14-18 afternoon, 18-23 evening, 23-6 late
PERIODS = [
    (6, 10, "morning"),
    (10, 14, "midday"),
    (14, 18, "afternoon"),
    (18, 23, "evening"),
    (23, 6, "late"),  # 23, 0, 1, 2, 3, 4, 5
]

# Vocabulary rotation: lobster / claw / Victorian motifs
LOBSTER_MOTIFS = ["lobster", "claw", "pincer", "carapace", "antennae", "chelae"]
VICTORIAN_MOTIFS = ["waistcoat", "inkwell", "dispatches", "salon", "candlelight", "letter", "newspaper", "tide chart"]
STRUCTURE_HINTS = [
    "one-liner",
    "two short sentences",
    "understated complaint",
    "statement + rhetorical question",
]

# Period-specific activity hints (tone + props)
PERIOD_ACTIVITIES = {
    "morning": {
        "tone": "brisk, self-assured",
        "props": ["tea", "newspapers", "sharpening claws", "first light", "desk"],
    },
    "midday": {
        "tone": "busier, officious",
        "props": ["letters", "dispatches", "debating", "correspondence", "meetings"],
    },
    "afternoon": {
        "tone": "dry sarcasm",
        "props": ["ink stains", "tide charts", "pinching weak arguments", "ledger", "rebuttals"],
    },
    "evening": {
        "tone": "wry",
        "props": ["waistcoat loosened", "candlelight", "salon gossip", "sherry", "fire"],
    },
    "late": {
        "tone": "mischievous but safe",
        "props": ["moonlit shoreline", "clandestine memos", "night desk", "whisper"],
    },
}


def get_local_time_context(now_utc: Optional[datetime], tz_name: str = "Europe/London") -> dict:
    """Return { hour: int, period: str, day_name: str } for the given UTC time in the given timezone. If now_utc is None, uses now(UTC)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    tz = ZoneInfo(tz_name)
    local = now_utc.astimezone(tz)
    hour = local.hour
    day_name = local.strftime("%A")  # Monday, Tuesday, ...

    period = "late"
    for start, end, name in PERIODS:
        if name == "late":
            if hour >= 23 or hour < 6:
                period = "late"
            break
        if start <= hour < end:
            period = name
            break

    return {"hour": hour, "period": period, "day_name": day_name}


def _pick_period_activity(period: str, rng: random.Random) -> tuple[str, list[str], str]:
    entry = PERIOD_ACTIVITIES.get(period, PERIOD_ACTIVITIES["late"])
    tone = entry["tone"]
    props = list(entry["props"])
    rng.shuffle(props)
    n_props = rng.randint(1, min(3, len(props)))
    chosen_props = props[:n_props]
    activity = "writing" if rng.random() > 0.3 else "reviewing"
    return activity, chosen_props, tone


def pick_basil_activity(context: dict, rng: random.Random) -> dict:
    """Return { activity: str, props: [str], tone: str, structure_hint: str } using vocabulary rotation."""
    period = context.get("period", "afternoon")
    activity, props, tone = _pick_period_activity(period, rng)
    structure_hint = rng.choice(STRUCTURE_HINTS)
    lobster = rng.choice(LOBSTER_MOTIFS)
    victorian = rng.choice(VICTORIAN_MOTIFS)
    return {
        "activity": activity,
        "props": props,
        "tone": tone,
        "structure_hint": structure_hint,
        "motif_lobster": lobster,
        "motif_victorian": victorian,
    }


# Stage-direction templates: "Basil is [location], [prop], [prop]."
MOMENT_LOCATIONS = [
    "at his desk",
    "pacing the study",
    "by the window",
    "in the library",
    "at the writing table",
]


def format_moment_line(context: dict, activity: dict) -> str:
    """Produce a short stage direction (e.g. 'Basil is at his desk, quill in claw, tea going cold.').
    Uses only activity props and fixed vocabulary; never includes numbers, emojis, hashtags, or @mentions."""
    props = list(activity.get("props") or [])[:3]
    hour = context.get("hour", 12)
    location = MOMENT_LOCATIONS[hour % len(MOMENT_LOCATIONS)]
    if not props:
        return f"Basil is {location}."
    if len(props) == 1:
        return f"Basil is {location}, {props[0]}."
    return f"Basil is {location}, {props[0]}, {props[1]}."


def build_irreverent_user_prompt(context: dict, activity: dict) -> str:
    """Produce a concise user prompt for the LLM to write a single irreverent post. Embeds config constraints."""
    cfg = _config()
    max_chars = cfg.standalone_max_chars
    no_hashtags = cfg.standalone_no_hashtags

    lines = [
        f"Write one Basil post for {context.get('period', 'afternoon')} ({context.get('day_name', 'today')}).",
        f"Tone: {activity.get('tone', 'wry')}. Structure: {activity.get('structure_hint', 'one-liner')}.",
        "Include a touch of lobster / Victorian flair; attack ideas not people; no slurs or threats.",
    ]
    if activity.get("props"):
        lines.append(f"Atmosphere: {', '.join(activity['props'][:3])}.")
    lines.append("")
    lines.append("Constraints:")
    lines.append(f"- Under {max_chars} characters.")
    lines.append("- No emojis.")
    if no_hashtags:
        lines.append("- No hashtags.")
    lines.append("- No stats or numbers.")
    lines.append("- No @mentions.")
    lines.append("- Avoid nautical metaphors (tide, harbour, helm, storm, sea) unless the moment/atmosphere already includes one.")
    lines.append("- Use a punchier rhythm: prefer 1–2 short sentences.")
    lines.append("- At most one lobster or claw reference.")
    return "\n".join(lines)


def make_rng(seed_material: str, *, dry_run: bool = False) -> random.Random:
    """Return a random.Random instance seeded from seed_material. Use date (London), hour (London), and BOT_ACCOUNT_ID or X_USER_ID when building seed_material so output is deterministic per run but varies by day/hour/account. If dry_run=True, append a salt so repeated dry-run invocations vary."""
    if dry_run:
        seed_material = seed_material + " " + secrets.token_hex(4)
    h = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    seed = int(h[:16], 16)
    return random.Random(seed)


def get_seed_material(now_utc: Optional[datetime] = None) -> str:
    """Build seed_material for make_rng: London date, London hour, and server-unique id (BOT_ACCOUNT_ID or X_USER_ID)."""
    import os
    now = now_utc or datetime.now(timezone.utc)
    ctx = get_local_time_context(now, "Europe/London")
    date_part = now.astimezone(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")
    hour = ctx["hour"]
    server_id = os.getenv("BOT_ACCOUNT_ID") or os.getenv("X_USER_ID") or "basil"
    return f"{date_part} {hour} {server_id}"
