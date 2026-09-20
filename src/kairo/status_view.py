"""#401 status 人读/JSON 口径。不改 fold 算法。"""

from __future__ import annotations

from pathlib import Path

from kairo.engine import workspace_run_plan
from kairo.refs import is_global_home, load_catalog, topic_members
from kairo.rules import ComposeRule, _hash, effective_compose_block_reason
from kairo.workspace import Workspace

STATE_FOLDED_CURRENT = "folded_current"
STATE_NOT_FOLDED = "not_folded"
STATE_FOLDED_STALE = "folded_stale"
STATE_DIGEST_MISSING = "digest_missing"

STATE_LABEL = {
    STATE_FOLDED_CURRENT: "当前 digest 已融入",
    STATE_NOT_FOLDED: "尚未融入",
    STATE_FOLDED_STALE: "更新后未再融入",
    STATE_DIGEST_MISSING: "digest 未生成",
}


class StatusError(ValueError):
    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


def incremental_after_full_compose(folded: dict, last_major: dict) -> int:
    return max(0, len(folded) - len(last_major))


def _blocked_reasons(plan: dict) -> list[dict]:
    out: list[dict] = []
    for item in plan.get("blocked_refs") or []:
        for blk in item.get("blocks") or []:
            out.append(
                {
                    "scope": "ref",
                    "id": item.get("ref_id"),
                    "path": None,
                    "reason": blk.get("reason"),
                }
            )
    for item in plan.get("blocked_targets") or []:
        out.append(
            {
                "scope": "target",
                "id": None,
                "path": item.get("path"),
                "reason": item.get("reason"),
            }
        )
    return out


def topic_status_payload(ws: Workspace) -> dict:
    state = ws.read_state()
    plan = workspace_run_plan(ws)
    compose = ComposeRule(ws, None)
    targets = []
    for target in ws.constitution.live_targets():
        ts = state.targets.get(target.path)
        if ts is None:
            targets.append(
                {
                    "path": target.path,
                    "folded": 0,
                    "incremental_after_full_compose": 0,
                    "status": "missing",
                    "blocked_reason": None,
                }
            )
            continue
        reason = effective_compose_block_reason(ws, target.path, ts)
        blocked_reason = reason if ts.status == "blocked" else None
        targets.append(
            {
                "path": target.path,
                "folded": len(ts.folded),
                "incremental_after_full_compose": incremental_after_full_compose(
                    ts.folded, ts.last_major_folded
                ),
                "status": ts.status,
                "blocked_reason": blocked_reason,
            }
        )
    return {
        "ok": True,
        "topic": ws.root.name,
        "name": ws.constitution.topic,
        "pending": plan["pending_count"],
        "blocked": plan["blocked_count"],
        "plan": plan["mode"],
        "blocked_reasons": _blocked_reasons(plan),
        "targets": targets,
        "_corpus_flags": {
            t.path: compose.corpus_drifted(t.path, state)
            for t in ws.constitution.live_targets()
            if state.targets.get(t.path) is not None
        },
        "_target_flags": {
            t.path: effective_compose_block_reason(ws, t.path, state.targets[t.path])
            if state.targets.get(t.path) is not None
            else None
            for t in ws.constitution.live_targets()
        },
    }


def format_topic_status(ws: Workspace, payload: dict, state) -> list[str]:
    from kairo.refs import RefError, resolve_open, run_members, serve_root_of
    from kairo.rules import REASON_COMPOSE_MIGRATION_REQUIRED, REASON_COMPOSE_OVER_BUDGET

    lines = [
        f"topic {payload['topic']}  name={payload['name']}  "
        f"plan={payload['plan']}  待处理 {payload['pending']}；blocked {payload['blocked']}"
    ]
    rows: list[tuple[str, str, list]] = []
    seen: set[str] = set()
    for rec in run_members(ws):
        seen.add(rec.id)
        roles: list[str] = []
        try:
            src_ws, rid = resolve_open(serve_root_of(ws), rec.home, rec.id)
            man = src_ws.read_manifest(rid)
            roles = [f.role for f in man.forms]
        except (RefError, OSError):
            roles = []
        rows.append((rec.id, rec.title, roles))
    for ref_id in ws.list_reference_ids():
        if ref_id in seen:
            continue
        man = ws.read_manifest(ref_id)
        rows.append((ref_id, man.title or "", [f.role for f in man.forms]))
    for ref_id, title, roles in rows:
        title_s = f" «{title}»" if title and title != ref_id else ""
        blocked = [
            f"{k.rsplit('/', 1)[-1]}:{_format_block(v.reason, v.diagnostic)}"
            for k, v in state.products.items()
            if (
                k.startswith(f"references/{ref_id}/")
                or k.endswith(f"/{ref_id}")
                or k == ref_id
            )
            and v.status == "blocked"
        ]
        flag = f"  ⚠ {','.join(blocked)}" if blocked else ""
        lines.append(f"reference {ref_id}{title_s}: [{','.join(roles)}]{flag}")
    corpus_flags = payload.get("_corpus_flags") or {}
    for item in payload["targets"]:
        path = item["path"]
        if item["status"] == "missing":
            lines.append(f"target {path}: (未生成)")
            continue
        ts = state.targets[path]
        reason = item.get("blocked_reason")
        flag = f"  ⚠ blocked:{_format_block(reason, ts.diagnostic)}" if item["status"] == "blocked" else ""
        if reason in (REASON_COMPOSE_MIGRATION_REQUIRED, REASON_COMPOSE_OVER_BUDGET):
            flag += "；确认压缩历史正文后运行 kairo re-step understanding.md（失败保留旧版）"
        if corpus_flags.get(path):
            flag += "  ⚠ corpus 已变,可 re-step 重算"
        lines.append(
            f"target {path}: 已融入 {item['folded']}；"
            f"全量综合后已增量融入 {item['incremental_after_full_compose']}{flag}"
        )
    return lines


def _format_block(reason, diagnostic) -> str:
    base = reason or "blocked"
    if diagnostic is None:
        return base
    bits = [base]
    if diagnostic.stage:
        bits.append(f"stage={diagnostic.stage}")
    if diagnostic.provider:
        bits.append(f"provider={diagnostic.provider}")
    if diagnostic.summary:
        bits.append(diagnostic.summary)
    return " ".join(bits)


def _normalize_home(home: str | None) -> str | None:
    if home is None:
        return None
    text = home.strip()
    if text in ("", "global"):
        return ""
    return text


def resolve_status_ref(ws: Workspace, ref_id: str, home: str | None):
    from kairo.refs import serve_root_of

    serve = serve_root_of(ws)
    wanted = _normalize_home(home)
    try:
        members = topic_members(serve, ws.root.name)
    except Exception:
        members = []
    matches = [m for m in members if m.id == ref_id]
    if not matches:
        local = ws.list_reference_ids()
        if ref_id in local and wanted in (None, ws.root.name):
            from kairo.refs import RefRecord

            digest = ws.references_dir() / ref_id / "digest.md"
            man = ws.read_manifest(ref_id)
            rec = RefRecord(
                home=ws.root.name,
                id=ref_id,
                title=man.title or ref_id,
                source_class=man.source_class,
                digest_path=digest if digest.is_file() else None,
            )
            return rec
        raise StatusError("reference 不存在", code="not_found")
    if wanted is not None:
        matches = [m for m in matches if _normalize_home(m.home) == wanted]
        if not matches:
            raise StatusError("reference 不存在", code="not_found")
    if len(matches) > 1:
        raise StatusError("reference 不唯一:请加 --home", code="invalid_request")
    return matches[0]


def resolve_live_target(ws: Workspace, target: str | None) -> str:
    lives = [t.path for t in ws.constitution.live_targets()]
    if target:
        if target not in lives:
            raise StatusError("target 不是该 Topic 活 target", code="not_found")
        return target
    if len(lives) == 1:
        return lives[0]
    raise StatusError("请指定 --target", code="invalid_request")


def digest_fold_key(ws: Workspace, rec) -> str:
    if rec.home != ws.root.name or load_catalog(ws.root.parent).get("strict_membership", False):
        return rec.key
    return f"references/{rec.id}/digest.md"


def ref_fold_payload(ws: Workspace, ref_id: str, home: str | None, target: str | None) -> dict:
    rec = resolve_status_ref(ws, ref_id, home)
    path = resolve_live_target(ws, target)
    state = ws.read_state()
    ts = state.targets.get(path)
    folded = dict(ts.folded) if ts else {}
    digest = rec.digest_path
    if digest is None or not Path(digest).is_file():
        fold_state = STATE_DIGEST_MISSING
    else:
        key = digest_fold_key(ws, rec)
        current = _hash(Path(digest).read_text(encoding="utf-8"))
        recorded = folded.get(key)
        if recorded is None:
            fold_state = STATE_NOT_FOLDED
        elif recorded == current:
            fold_state = STATE_FOLDED_CURRENT
        else:
            fold_state = STATE_FOLDED_STALE
    json_home = "" if is_global_home(rec.home) else rec.home
    return {
        "ok": True,
        "home": json_home,
        "id": rec.id,
        "target": path,
        "state": fold_state,
    }


def format_ref_status(payload: dict) -> str:
    home = payload["home"] or "global"
    label = STATE_LABEL[payload["state"]]
    return f"ref {home}/{payload['id']} target {payload['target']}: {label}"


def public_topic_payload(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if not k.startswith("_")}
