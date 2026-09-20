"""#378: one Ref run overlaps digest sidecars with compose and extracts a digest once."""

from __future__ import annotations

import time

from kairo.engine import run_workspace, step
from kairo.provider import AgentResult, StubProvider
from kairo.workspace import Workspace

# Old serial path was 6 provider calls (digest, brief, digest extract,
# compose, compose-digest extract, understanding extract). Equal sleeps
# on that path are the S1 baseline.
_OLD_SERIAL_CALLS = 6
_DELAY_S = 0.25


class _TimedStub:
    name = "stub"
    model = "stub"
    supports_read_dirs = True

    def __init__(self, delay: float = _DELAY_S):
        self.delay = delay
        self.events: list[tuple[str, float, float]] = []
        self._inner = StubProvider()

    def run(self, config, signal=None) -> AgentResult:
        art = config.artifact or ""
        started = time.monotonic()
        time.sleep(self.delay)
        result = self._inner.run(config, signal)
        self.events.append((art, started, time.monotonic()))
        return result


class _ExtractBoom(_TimedStub):
    def run(self, config, signal=None) -> AgentResult:
        art = config.artifact or ""
        if art == "knowledge-candidates.yaml":
            time.sleep(self.delay)
            raise RuntimeError("extract boom")
        return super().run(config, signal)


def _fresh_topic(tmp_path) -> Workspace:
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws", topic="ws")
    material = tmp_path / "a.txt"
    material.write_text("林值秋强调能源优先。")
    ws.add([material], ref_id="a")
    return ws


def test_same_digest_is_extracted_once(tmp_path):
    ws = _fresh_topic(tmp_path)
    provider = _TimedStub(delay=0.01)
    step(ws, provider)
    extract_arts = [art for art, _, _ in provider.events if art == "knowledge-candidates.yaml"]
    assert len(extract_arts) == 2
    assert (ws.root / "references/a/digest.md").is_file()
    assert (ws.root / "understanding.md").is_file()


def test_digest_extract_overlaps_compose(tmp_path):
    ws = _fresh_topic(tmp_path)
    provider = _TimedStub(delay=0.08)
    step(ws, provider)
    by_art = {}
    for art, start, end in provider.events:
        by_art.setdefault(art, []).append((start, end))
    digest_extract = by_art["knowledge-candidates.yaml"][0]
    compose = by_art["doc.md"][0]
    assert digest_extract[0] < compose[1]
    assert compose[0] < digest_extract[1]


def test_extract_failure_does_not_block_main_products(tmp_path):
    ws = _fresh_topic(tmp_path)
    step(ws, _ExtractBoom(delay=0.01))
    assert (ws.root / "references/a/digest.md").is_file()
    assert (ws.root / "understanding.md").is_file()


def test_single_ref_run_wall_clock_drops_at_least_40_percent(tmp_path):
    """S1: measured run ≤ 60% of the old 6-call serial baseline."""
    ws = _fresh_topic(tmp_path)
    provider = _TimedStub()
    started = time.monotonic()
    run_workspace(ws, provider)
    elapsed = time.monotonic() - started
    baseline = _OLD_SERIAL_CALLS * provider.delay
    assert elapsed <= baseline * 0.60, f"elapsed={elapsed:.3f}s baseline={baseline:.3f}s ratio={elapsed / baseline:.2%}"
    assert (ws.root / "references/a/digest.md").is_file()
    assert (ws.root / "understanding.md").is_file()
    assert len(provider.events) <= 5
