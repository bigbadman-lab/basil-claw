"""
Unit tests for ingest/voice.py: build_basil_voice, tighten_reply (caps, sentences, filler, one metaphor).
Run: python -m pytest tests/test_voice.py -v
"""

import sys
sys.path.insert(0, ".")

from ingest.voice import build_basil_voice, tighten_reply, _sentences


def test_build_basil_voice_mention():
    s = build_basil_voice("mention")
    assert "240" in s
    assert "1–2 sentences" in s or "1-2 sentences" in s
    assert "Direct answer" in s
    assert "Restore Britain" in s


def test_build_basil_voice_whitelist():
    s = build_basil_voice("whitelist")
    assert "180" in s
    assert "1 sentence" in s
    assert "Punchy" in s or "punchy" in s
    assert "Restore Britain" in s


def test_tighten_reply_mention_cap():
    long = "A" * 300
    out = tighten_reply(long, "mention")
    assert len(out) <= 240


def test_tighten_reply_whitelist_cap():
    long = "B" * 200
    out = tighten_reply(long, "whitelist")
    assert len(out) <= 180


def test_tighten_reply_mention_two_sentences():
    text = "First sentence here. Second sentence here. Third sentence here."
    out = tighten_reply(text, "mention")
    assert len(_sentences(out)) <= 2  # at most 2 sentences
    assert len(out) <= 240


def test_tighten_reply_whitelist_one_sentence_preferred():
    text = "First sentence here. Second sentence here."
    out = tighten_reply(text, "whitelist")
    # Should keep 1 (or 2 if first very short)
    sents = _sentences(out)
    assert len(sents) <= 2
    assert len(out) <= 180


def test_tighten_reply_strips_filler():
    text = "Indeed, the real answer is simple."
    out = tighten_reply(text, "mention")
    assert not out.startswith("Indeed")
    assert "real answer" in out or "answer" in out


def test_tighten_reply_one_metaphor():
    # Two metaphor sentences: second should be dropped for mention
    text = "The shell of the state needs mending. Our claws are sharp for the fight."
    out = tighten_reply(text, "mention")
    sents = _sentences(out)
    # Should have at most one sentence containing metaphor words
    metaphor_words = ["shell", "claws", "barnacles", "hull", "tide"]
    count = sum(1 for s in sents if any(w in s.lower() for w in metaphor_words))
    assert count <= 1, "at most one metaphor sentence"


def test_tighten_reply_no_newlines():
    text = "Line one.\n\nLine two."
    out = tighten_reply(text, "mention")
    assert "\n" not in out
