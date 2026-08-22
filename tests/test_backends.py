from kairo.backends import _normalize_srt


def test_normalize_srt_preserves_cue_text_and_real_start_times():
    text = (
        "\ufeff1\n00:00:01,500 --> 00:00:03,000\n第一行\n第二行\n\n"
        "2\n01:02:03,040 --> 01:02:05,000\n后续\n"
    )
    assert _normalize_srt(text) == ("[0:00:01.5] 第一行\n第二行\n[1:02:03.04] 后续")


def test_normalize_srt_returns_malformed_input_unchanged():
    text = "plain transcript without subtitle timing"
    assert _normalize_srt(text) == text
