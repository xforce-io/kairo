import pytest


@pytest.fixture(autouse=True)
def _isolate_machine_asr(monkeypatch, tmp_path_factory):
    """守卫:测试默认不读本机 ASR 配置(env / ~/.config/kairo),避免误触发真实 whisper。

    需要本机配置的测试自行 setenv 覆盖即可。
    """
    monkeypatch.delenv("KAIRO_ASR_CMD", raising=False)
    monkeypatch.delenv("KAIRO_ASR_ORIGIN", raising=False)
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-isolated"))
    )
