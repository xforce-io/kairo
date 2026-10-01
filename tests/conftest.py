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


# #436：隔离新增自动 note 的模型调用；供应商专测可以再次替换 runner。
import json
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolate_automatic_note_model(monkeypatch):
    import kairo.provider as provider
    original = provider._default_cli_runner
    def runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        prompt = Path(cwd) / "_prompt.md"
        if cmd == "grok" and prompt.is_file() and prompt.read_text().startswith("根据下面这一份详备纪要写一条短 note"):
            Path(stdout_file).write_text(json.dumps({"type": "result", "result": "测试自动 note：已有纪要的事实。"}) + "\n")
            return None
        return original(cmd, args, cwd=cwd, input=input, stdout_file=stdout_file, timeout=timeout)
    monkeypatch.setattr(provider, "_default_cli_runner", runner)
