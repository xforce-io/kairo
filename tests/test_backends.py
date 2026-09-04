from kairo.backends import _normalize_srt

# 夹杂零时长空 cue 与无正文 cue 的标准 SRT（#147）。
EMPTY_CUE_SRT = (
    "1\n00:00:01,500 --> 00:00:03,000\n第一句\n\n"
    "2\n00:00:04,000 --> 00:00:04,000\n\n"
    "3\n00:00:05,200 --> 00:00:07,000\n\n"
    "4\n00:00:08,000 --> 00:00:10,000\n第二句\n"
)


def test_normalize_srt_preserves_cue_text_and_real_start_times():
    text = (
        "\ufeff1\n00:00:01,500 --> 00:00:03,000\n第一行\n第二行\n\n"
        "2\n01:02:03,040 --> 01:02:05,000\n后续\n"
    )
    assert _normalize_srt(text) == ("[0:00:01.5] 第一行\n第二行\n[1:02:03.04] 后续")


def test_normalize_srt_skips_empty_zero_and_nobody_cues():
    out = _normalize_srt(EMPTY_CUE_SRT)
    assert out == "[0:00:01.5] 第一句\n[0:00:08] 第二句"
    assert "-->" not in out
    assert "[0:00:04]" not in out
    assert "[0:00:05.2]" not in out


def test_normalize_srt_returns_malformed_input_unchanged():
    text = "plain transcript without subtitle timing"
    assert _normalize_srt(text) == text
