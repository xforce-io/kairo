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


@pytest.fixture(autouse=True)
def _isolate_serve_root(monkeypatch):
    """守卫:测试默认不继承本机 KAIRO_SERVE_ROOT,避免无 --root 的 CLI 写进生产 serve 根。

    只 delenv,不 setenv 到其它临时根:CLI 优先读环境变量,设成与用例 serve 不同的根
    会让 chdir(serve) 的断言找不到文件。需要环境变量的测试自行 setenv 覆盖即可。
    清任意继承值,不是按路径黑名单拒绝某一个生产根。
    """
    monkeypatch.delenv("KAIRO_SERVE_ROOT", raising=False)

