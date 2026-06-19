from kairo.provider import StubProvider


def test_stub_complete_is_deterministic_and_marked():
    p = StubProvider()
    out1 = p.complete("分析这段文字:你好")
    out2 = p.complete("分析这段文字:你好")
    assert out1 == out2  # 同输入 → 同输出(确定性)
    assert "STUB" in out1  # 显式标记,不被当真


def test_stub_complete_varies_with_prompt():
    p = StubProvider()
    assert p.complete("AAA") != p.complete("BBB")  # 不同输入 → 不同输出


def test_stub_provider_identity_for_provenance():
    p = StubProvider()
    assert p.name == "stub"
    assert p.model == "stub"
