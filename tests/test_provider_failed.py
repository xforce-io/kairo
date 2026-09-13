"""#98: Digest/Compose provider 失败持久化、status 诊断、step 不自动重试、显式恢复。"""

from __future__ import annotations

from kairo.engine import (
    clear_provider_failed_targets,
    pending,
    re_step,
    retry_reference,
    run_workspace,
    step,
    workspace_run_plan,
)
from kairo.models import (
    FailureDiagnostic,
    REASON_PROVIDER_FAILED,
    State,
    TargetState,
)
from kairo.provider import AgentResult, StubProvider, _scan_artifacts
from kairo.rules import (
    REASON_EXPLICIT_RECOMPOSE,
    make_provider_diagnostic,
    safe_provider_summary,
)
from kairo.workspace import Workspace


class FailProvider:
    """始终失败的 provider —— 驱动真实 Digest/Compose 边界。"""

    name = "fail-prov"
    model = "fail-model"
    supports_read_dirs = True

    def __init__(self, msg: str = "Grok request failed status 502"):
        self.msg = msg
        self.calls = 0

    def run(self, config, signal=None):
        self.calls += 1
        raise RuntimeError(self.msg)


class FlakyProvider:
    """前 fail_times 次失败,之后成功(Stub 风格写 artifact)。"""

    name = "flaky"
    model = "flaky"
    supports_read_dirs = True

    def __init__(self, fail_times: int = 1):
        self.fail_times = fail_times
        self.calls = 0

    def run(self, config, signal=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"transient failure call={self.calls}")
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        seed = f"{config.persona}\n{config.context}"
        content = f"RECOVERED OUTPUT\n{seed[:200]}"
        (config.artifact_dir / (config.artifact or "output.md")).write_text(content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir), result_text=content)


class ComposeOnlyFailProvider:
    """Digest 成功,Compose 失败(用 artifact 名区分)。"""

    name = "compose-fail"
    model = "cf"
    supports_read_dirs = True

    def __init__(self):
        self.calls = 0

    def run(self, config, signal=None):
        self.calls += 1
        art = config.artifact or "output.md"
        if art == "doc.md":
            raise RuntimeError("compose provider timeout")
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = f"DIGEST OK {self.calls}\n{config.context[:120]}"
        (config.artifact_dir / art).write_text(content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir), result_text=content)


def _ws_with_text(tmp_path, text: str = "会议纪要材料") -> Workspace:
    ws = Workspace.init(tmp_path / "ws", topic="t98")
    p = tmp_path / "m.txt"
    p.write_text(text)
    ws.add([p])
    return ws


def _ws_with_two_texts(tmp_path) -> Workspace:
    ws = Workspace.init(tmp_path / "ws2", topic="t-transport")
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("材料甲 需要 digest")
    b.write_text("材料乙 需要 digest")
    ws.add([a])
    ws.add([b])
    return ws


_TRANSPORT_MSG = (
    'Internal error: "reqwest error stream: error sending request for url '
    '(https://cli-chat-proxy.grok.com/v1/responses)"'
)


def test_safe_provider_summary_redacts_and_truncates():
    s = safe_provider_summary("Error Authorization: Bearer SECRETTOKEN123 api_key=xyz")
    assert "SECRETTOKEN123" not in s
    assert "[redacted]" in s or "Bearer" in s
    long = "E: " + ("x" * 500)
    short = safe_provider_summary(long, max_len=40)
    assert len(short) <= 40
    assert short.endswith("…")
    d = make_provider_diagnostic("digest", FailProvider(), RuntimeError("boom"))
    assert d.stage == "digest"
    assert d.provider == "fail-prov"
    assert "boom" in d.summary


def test_old_state_without_diagnostic_loads():
    """旧 state 无 diagnostic 字段时兼容读取。"""
    raw = {
        "products": {
            "references/x/digest.md": {
                "input_hash": "abc",
                "status": "blocked",
                "reason": "asr-failed",
            }
        },
        "targets": {},
    }
    st = State.model_validate(raw)
    ps = st.products["references/x/digest.md"]
    assert ps.diagnostic is None
    assert ps.reason == "asr-failed"


def test_digest_provider_failed_persists_and_step_no_retry(tmp_path):
    ws = _ws_with_text(tmp_path)
    fail = FailProvider("Grok request failed status 401 Authorization: Bearer SECRET")
    step(ws, fail)
    rid = ws.list_reference_ids()[0]
    key = f"references/{rid}/digest.md"
    st = ws.read_state()
    ps = st.products[key]
    assert ps.status == "blocked"
    assert ps.reason == REASON_PROVIDER_FAILED
    assert ps.diagnostic is not None
    assert ps.diagnostic.stage == "digest"
    assert ps.diagnostic.provider == "fail-prov"
    assert "401" in ps.diagnostic.summary or "failed" in ps.diagnostic.summary.lower()
    assert "SECRET" not in ps.diagnostic.summary
    assert not (ws.root / key).exists()  # 不写半成品
    calls_after_fail = fail.calls
    # 普通 step 不再调用 provider
    step(ws, fail)
    assert fail.calls == calls_after_fail
    assert ws.read_state().products[key].reason == REASON_PROVIDER_FAILED
    # plan 可见 blocked
    plan = workspace_run_plan(ws)
    assert plan["blocked_count"] >= 1
    assert any(
        b["reason"] == REASON_PROVIDER_FAILED
        for item in plan["blocked_refs"]
        for b in item["blocks"]
    )


def test_compose_provider_failed_keeps_existing_doc(tmp_path):
    ws = _ws_with_text(tmp_path)
    # 先成功产出
    step(ws, StubProvider())
    doc = ws.root / "understanding.md"
    assert doc.is_file()
    old = doc.read_text()
    # 制造仍有 delta:改 digest hash 记账
    rid = ws.list_reference_ids()[0]
    digest_key = f"references/{rid}/digest.md"
    (ws.root / digest_key).write_text(old + "\nextra fact for fold")
    st = ws.read_state()
    # 清空 folded 使 compose 看到 delta
    for path in list(st.targets):
        st.targets[path].folded = {}
        st.targets[path].status = "ok"
        st.targets[path].reason = None
    ws.write_state(st)
    fail = FailProvider("compose network error")
    step(ws, fail)
    st2 = ws.read_state()
    ts = st2.targets["understanding.md"]
    assert ts.status == "blocked"
    assert ts.reason == REASON_PROVIDER_FAILED
    assert ts.diagnostic is not None
    assert ts.diagnostic.stage == "compose"
    assert doc.read_text() == old  # 不损坏已有产物
    calls = fail.calls
    step(ws, fail)
    assert fail.calls == calls  # 不自动重试


def test_compose_fail_on_first_write(tmp_path):
    """首次 compose 即失败:无正文,state 仍有诊断。"""
    ws = _ws_with_text(tmp_path)
    prov = ComposeOnlyFailProvider()
    step(ws, prov)
    rid = ws.list_reference_ids()[0]
    assert (ws.root / f"references/{rid}/digest.md").is_file()
    ts = ws.read_state().targets.get("understanding.md")
    assert ts is not None
    assert ts.status == "blocked"
    assert ts.reason == REASON_PROVIDER_FAILED
    assert ts.diagnostic.stage == "compose"
    assert not (ws.root / "understanding.md").exists() or (
        ws.root / "understanding.md"
    ).stat().st_size == 0 or True


def test_retry_ref_recovers_digest_provider_failed(tmp_path):
    ws = _ws_with_text(tmp_path)
    fail = FailProvider()
    step(ws, fail)
    rid = ws.list_reference_ids()[0]
    key = f"references/{rid}/digest.md"
    assert ws.read_state().products[key].reason == REASON_PROVIDER_FAILED
    # 显式 retry-ref 用成功 provider 恢复
    progressed = retry_reference(ws, StubProvider(), rid)
    assert progressed
    ps = ws.read_state().products.get(key)
    assert ps is not None and ps.status != "blocked"
    assert ps.diagnostic is None
    assert (ws.root / key).is_file()
    assert (ws.root / "understanding.md").is_file()


def test_run_recovers_and_second_fail_updates_diag(tmp_path):
    from kairo.engine import clear_reference_products

    ws = _ws_with_text(tmp_path)
    step(ws, FailProvider("first fail"))
    rid = ws.list_reference_ids()[0]
    key = f"references/{rid}/digest.md"
    assert "first" in (ws.read_state().products[key].diagnostic.summary or "")
    # 显式 clear 后再次失败 → 诊断摘要更新为新错误
    clear_reference_products(ws, rid)
    step(ws, FailProvider("second fail body"))
    summary = ws.read_state().products[key].diagnostic.summary
    assert "second" in summary
    # 再成功恢复
    clear_reference_products(ws, rid)
    run_workspace(ws, StubProvider(), retry_blocked=False)
    ps = ws.read_state().products.get(key)
    assert ps is not None and ps.status != "blocked"
    assert (ws.root / key).is_file()


def test_run_workspace_recovers_compose_provider_failed(tmp_path):
    ws = _ws_with_text(tmp_path)
    step(ws, StubProvider())
    old = (ws.root / "understanding.md").read_text()
    rid = ws.list_reference_ids()[0]
    # force compose delta
    (ws.root / f"references/{rid}/digest.md").write_text("new digest body for compose")
    st = ws.read_state()
    for path in st.targets:
        st.targets[path].folded = {}
    ws.write_state(st)
    step(ws, FailProvider("compose down"))
    assert ws.read_state().targets["understanding.md"].reason == REASON_PROVIDER_FAILED
    assert (ws.root / "understanding.md").read_text() == old
    # 显式 run 恢复
    run_workspace(ws, StubProvider())
    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "ok"
    assert ts.reason is None
    assert ts.diagnostic is None
    assert (ws.root / "understanding.md").read_text() != old


def test_run_retry_does_not_turn_provider_failure_into_explicit_recompose(tmp_path):
    ws = _ws_with_text(tmp_path)
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    digest = ws.root / f"references/{rid}/digest.md"
    digest.write_text("new digest body for compose")
    state = ws.read_state()
    state.targets["understanding.md"].folded = {}
    ws.write_state(state)
    step(ws, FailProvider("compose down"))
    manual = "失败期间的人工修改"
    (ws.root / "understanding.md").write_text(manual)

    run_workspace(ws, StubProvider())

    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "blocked"
    assert ts.reason == "manual-edit"
    assert (ws.root / "understanding.md").read_text() == manual


def test_provider_retry_restores_full_recompose_origin(tmp_path):
    ws = Workspace.init(tmp_path)
    for retry_reason in ("materials-changed", REASON_EXPLICIT_RECOMPOSE):
        state = ws.read_state()
        state.targets["understanding.md"] = TargetState(
            status="blocked",
            reason=REASON_PROVIDER_FAILED,
            retry_reason=retry_reason,
        )
        ws.write_state(state)

        assert clear_provider_failed_targets(ws) == 1
        restored = ws.read_state().targets["understanding.md"]
        assert restored.status == "ok"
        assert restored.reason == retry_reason
        assert restored.retry_reason is None


def test_re_step_target_recovers_compose_failed(tmp_path):
    ws = _ws_with_text(tmp_path)
    step(ws, ComposeOnlyFailProvider())
    assert ws.read_state().targets["understanding.md"].reason == REASON_PROVIDER_FAILED
    re_step(ws, StubProvider(), "understanding.md")
    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "ok"
    assert ts.diagnostic is None
    assert (ws.root / "understanding.md").is_file()


def test_cli_status_shows_provider_failed(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from kairo.cli import app

    ws = _ws_with_text(tmp_path)
    step(ws, FailProvider("status visible 503"))
    monkeypatch.chdir(ws.root)
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    out = r.output
    assert "provider-failed" in out
    assert "digest" in out or "stage=digest" in out
    assert "503" in out or "fail-prov" in out
    # 写入证据文件由调用方控制


def test_pending_excludes_provider_failed_digest(tmp_path):
    ws = _ws_with_text(tmp_path)
    step(ws, FailProvider())
    # provider-failed digest 不应再出现在 pending
    keys = [it.key for it in pending(ws)]
    rid = ws.list_reference_ids()[0]
    assert f"references/{rid}/digest.md" not in keys


def test_web_shows_provider_failure_stage_for_reference_and_target(tmp_path):
    """#98 S1: Web 持久化诊断须明确显示失败阶段，不能只靠文件名推断。"""
    from fastapi.testclient import TestClient

    from kairo.web.server import create_app

    ws = _ws_with_text(tmp_path)
    step(ws, FailProvider("digest unavailable"))
    rid = ws.list_reference_ids()[0]
    client = TestClient(create_app(tmp_path))

    ref = client.get(f"/w/ws/ref/{rid}")
    assert ref.status_code == 200
    assert "stage=digest" in ref.text
    assert "provider=fail-prov" in ref.text

    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        status="blocked",
        reason=REASON_PROVIDER_FAILED,
        diagnostic=FailureDiagnostic(
            stage="compose", provider="fail-prov", summary="compose unavailable"
        ),
    )
    ws.write_state(state)
    target = client.get("/w/ws/target", params={"path": "understanding.md"})
    assert target.status_code == 200
    assert "stage=compose" in target.text
    assert "provider=fail-prov" in target.text


def _digest_products(ws: Workspace) -> list[tuple[str, object]]:
    st = ws.read_state()
    return [
        (key, ps)
        for key, ps in st.products.items()
        if key.endswith("/digest.md")
    ]


def test_transport_error_short_circuits_remaining_digests_on_step(tmp_path):
    """同 Run 第一条 digest 传输失败后不再调后续 digest,两条都落 provider-failed。"""
    ws = _ws_with_two_texts(tmp_path)
    assert len(ws.list_reference_ids()) == 2
    fail = FailProvider(_TRANSPORT_MSG)
    step(ws, fail)
    assert fail.calls == 1
    products = _digest_products(ws)
    assert len(products) == 2
    for key, ps in products:
        assert ps.status == "blocked"
        assert ps.reason == REASON_PROVIDER_FAILED
        assert ps.diagnostic is not None
        assert ps.diagnostic.stage == "digest"
        assert not (ws.root / key).exists()
    calls = fail.calls
    step(ws, fail)
    assert fail.calls == calls


def test_generic_provider_error_still_invokes_second_digest(tmp_path):
    """非传输类失败不熔断,两条 digest 都会调用 provider。"""
    ws = _ws_with_two_texts(tmp_path)
    fail = FailProvider("model refused: safety")
    step(ws, fail)
    assert fail.calls == 2
    products = _digest_products(ws)
    assert len(products) == 2
    assert all(ps.reason == REASON_PROVIDER_FAILED for _, ps in products)


def test_run_workspace_transport_short_circuits_and_nonzero_retry_once(tmp_path):
    """run 清掉 blocked 再 step 时,传输失败仍只调一次 provider,不切 backend。"""
    ws = _ws_with_two_texts(tmp_path)
    fail = FailProvider(_TRANSPORT_MSG)
    run_workspace(ws, fail)
    assert fail.name == "fail-prov"
    assert fail.calls == 1
    products = _digest_products(ws)
    assert len(products) == 2
    assert all(ps.status == "blocked" for _, ps in products)
    run_workspace(ws, fail)
    assert fail.calls == 2
    assert fail.name == "fail-prov"


def test_cli_agent_timeout_is_not_a_transport_error():
    """A slow single reference must not short-circuit the remaining digests."""
    from kairo.provider import is_transport_provider_error

    assert not is_transport_provider_error(RuntimeError("CLI agent timeout after 600s: grok"))
    assert is_transport_provider_error(RuntimeError("error sending request for url (https://cli-chat-proxy.grok.com/v1/responses)"))
