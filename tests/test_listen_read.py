"""#122 听读时间契约与 audio↔transcript 配对。"""

from kairo.listen_read import pair_audio_transcripts, parse_units, search_hits, unit_at
from kairo.models import Form


def _starts(units):
    return [u.start for u in units]


def test_parse_valid_two_and_three_part_and_fraction():
    text = "\n".join(
        [
            "untimed head",
            "[1:02] sixty two",
            "[01:02.5] frac",
            "[14:53.0] long",
            "[1:02:03] hourish",
        ]
    )
    units = parse_units(text, duration=4000)
    by_text = {u.text: (u.start, u.end) for u in units}
    assert by_text["sixty two"] == (62, 62.5)
    assert by_text["frac"] == (62.5, 893.0)
    assert by_text["long"][0] == 893.0
    assert by_text["hourish"] == (3723, 4000)
    assert by_text["sixty two"][0] == 62


def test_parse_inline_timestamps_as_separate_units():
    units = parse_units("[0:00:00] first [0:00:04.92] second", duration=10)
    assert [(u.start, u.text) for u in units] == [(0, "first"), (4.92, "second")]


def test_parse_rejects_invalid_prefixes():
    text = "\n".join(
        [
            "[01:00] keep",
            "[01:2] bad digits",
            "[00:60] bad sec",
            "[01:02.] dangling",
            "[01:02.0.3] extra",
            "[01: 02] space",
            "[01:10] next",
        ]
    )
    units = parse_units(text, duration=120)
    assert [u.text for u in units] == [
        "keep\n[01:2] bad digits\n[00:60] bad sec\n[01:02.] dangling\n[01:02.0.3] extra\n[01: 02] space",
        "next",
    ]
    assert units[0].end == 70


def test_parse_out_of_order_and_past_duration_ignored():
    text = "\n".join(
        [
            "[01:00] a",
            "[00:30] rewind",
            "[02:00] b",
            "[10:00] past",
        ]
    )
    units = parse_units(text, duration=150)
    assert [(u.text, u.start, u.end) for u in units] == [
        ("a\nrewind", 60, 120),
        ("b\npast", 120, 150),
    ]


def test_same_second_zero_duration_highlight_goes_to_next():
    text = "\n".join(["[01:00] zero", "[01:00] live", "[01:10] later"])
    units = parse_units(text, duration=80)
    assert units[0].start == units[0].end == 60
    assert units[1].start == 60
    assert units[1].end == 70
    assert unit_at(units, 60) is units[1]
    assert unit_at(units, 59.9) is None
    assert unit_at(units, 70) is None or unit_at(units, 70) is not units[1]


def test_gap_before_first_prefix_has_no_highlight():
    units = parse_units("hello\n[00:10] body", duration=20)
    timed = next(u for u in units if u.start is not None)
    assert unit_at(units, 0) is None
    assert unit_at(units, 9.9) is None
    assert unit_at(units, 10) is timed


def test_search_hits_map_to_own_unit_starts():
    text = "\n".join(["[00:10] foo alpha", "[00:20] bar alpha", "[00:30] other"])
    units = parse_units(text, duration=40)
    hits = search_hits(units, "alpha")
    assert [(h.text, h.start) for h in hits] == [
        ("foo alpha", 10),
        ("bar alpha", 20),
    ]


def test_pair_origin_then_unique_fallback():
    a1 = Form(role="audio", location="a1.wav", hash="h1", origin="added")
    a2 = Form(role="audio", location="a2.wav", hash="h2", origin="added")
    t1 = Form(
        role="transcript",
        location="t1.md",
        hash="t1",
        origin="asr-from:h1",
    )
    t2 = Form(role="transcript", location="t2.md", hash="t2", origin="whisper")
    pairs = pair_audio_transcripts([a1, a2, t1, t2])
    linked = {(p.audio.hash, p.transcript.hash if p.transcript else None) for p in pairs if p.linked}
    assert ("h1", "t1") in linked
    assert ("h2", "t2") in linked


def test_pair_two_audios_one_transcript_not_guessed():
    a1 = Form(role="audio", location="a1.wav", hash="h1")
    a2 = Form(role="audio", location="a2.wav", hash="h2")
    t1 = Form(role="transcript", location="t.md", hash="t")
    pairs = pair_audio_transcripts([a1, a2, t1])
    assert all(not p.linked for p in pairs)
    assert {p.audio.hash for p in pairs} == {"h1", "h2"}
    assert all(p.transcript is None for p in pairs)


def test_untimed_transcript_is_readable():
    units = parse_units("plain line\nsecond line", duration=60)
    assert len(units) == 1
    assert units[0].start is None
    assert units[0].end is None
    assert units[0].text == "plain line\nsecond line"
    assert "-->" not in units[0].text
    assert unit_at(units, 0) is None
    assert unit_at(units, 30) is None


def test_parse_units_times_body_cues_in_raw_srt_with_empty_cue():
    """听读读盘路径：已存原始 SRT（含空 cue）仍打出有效 cue 的起点。"""
    text = (
        "1\n00:00:01,500 --> 00:00:03,000\n第一句\n\n"
        "2\n00:00:04,000 --> 00:00:04,000\n\n"
        "3\n00:00:05,200 --> 00:00:07,000\n\n"
        "4\n00:00:08,000 --> 00:00:10,000\n第二句\n"
    )
    units = parse_units(text, duration=None)
    timed = [(u.start, u.text) for u in units if u.start is not None]
    assert timed == [(1.5, "第一句"), (8.0, "第二句")]
    assert all("-->" not in u.text and not u.text.strip().isdigit() for u in units)


def test_leading_untimed_visible_not_highlighted():
    units = parse_units("hello\n[00:10] body", duration=20)
    assert units[0].start is None and "hello" in units[0].text
    timed = [u for u in units if u.start is not None]
    assert timed[0].text == "body"
    assert unit_at(units, 0) is None
    assert unit_at(units, 10) is timed[0]


def test_duplicate_origin_is_not_linked():
    audio = Form(role="audio", location="a.wav", hash="a")
    t1 = Form(role="transcript", location="t1.md", hash="t1", origin="asr-from:a")
    t2 = Form(role="transcript", location="t2.md", hash="t2", origin="asr-from:a")
    pairs = pair_audio_transcripts([audio, t1, t2])
    assert len(pairs) == 1
    assert pairs[0].linked is False
    assert pairs[0].transcript is None


def test_apply_duration_merges_past_prefix():
    from kairo.listen_read import apply_duration

    units = parse_units("[00:10] valid\n[10:00] past", duration=None)
    assert any(u.start == 600 for u in units)
    trimmed = apply_duration(units, 30)
    assert [u.text for u in trimmed if u.start is not None] == ["valid\npast"]
    assert all(u.start is None or u.start < 30 for u in trimmed)

