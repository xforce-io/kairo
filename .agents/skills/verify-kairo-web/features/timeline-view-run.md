# #396 Timeline 当前视图一次确认推进

不碰现网 `~/kairo`、不用 8787。执行路径必须 `KAIRO_STUB=1`，禁止为了本手册触发真 ASR / LLM。

## 入口（全部要走）

1. Console 日历单日：`/timeline?day=2026-08-24`
2. Console 闭区间：`/timeline?from=2026-08-24&to=2026-08-24`（有「推进」与「写这段回顾」并排）
3. 预览：`GET /timeline/run-preview?day=2026-08-24`
4. 确认执行：点「推进」→ 对话框「确认推进」（或 `POST /timeline/run`）；人留在 Timeline
5. CLI：`KAIRO_STUB=1 kairo run-view --day 2026-08-24` 与 `--yes`

不把 `kairo run --all` 当入口。不进 Topic 页 / Ref 详情做本票判定。

## Fixture

写到 `/tmp/kairo-verify-fixture-396.py` 后 `env -u KAIRO_SERVE_ROOT uv run python /tmp/kairo-verify-fixture-396.py "$ROOT"`：

```python
import sys
from pathlib import Path
from kairo.workspace import Workspace

root = Path(sys.argv[1])
tmp = root / "_src"
tmp.mkdir()

def write(name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p

wa = Workspace.init(root / "alpha", topic="能源梳理")
wb = Workspace.init(root / "beta", topic="招聘")
wa.add([write("a.txt", "A 日")], ref_id="in-a", title="A 日", occurred_at="2026-08-24")
wb.add([write("b.txt", "B 日")], ref_id="in-b", title="B 日", occurred_at="2026-08-24")
wa.add([write("o.txt", "外日")], ref_id="out-a", title="外日", occurred_at="2026-08-10")
print("ok")
```

期望：`2026-08-24` 可见 2 条未加工；入集 Topic=2（alpha、beta）；确认 Ref 数=3（含 alpha 的 08-10）。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| S1-a | GET `/timeline?day=2026-08-24`，不进 Topic / Ref | 未加工 stream 行有「未加工」类标记；已 digest 行无标记无空位 |
| S1-b | 同上闭区间 `/timeline?from=2026-08-24&to=2026-08-24` | 同上可判定；有「推进」；与「写这段回顾」并排、互不提交 |
| S2-a | GET `/timeline/run-preview?day=2026-08-24` | JSON/面板 Topic 数=2、Ref 数=3、Ref>可见未加工行；列出 alpha、beta |
| S2-b | `KAIRO_STUB=1` 点「确认推进」（或 POST `/timeline/run`） | 人仍在 Timeline；`#tl-run-area` 有进度后换摘要；切到 `?day=2026-08-10`（不进 Topic）可见 out-a 已有 digest；beta 被跑到 |
| S3-a | 打开预览后**不**确认 | ASR/LLM=0；digest / 活 target 文件不变 |
| S3-b | `KAIRO_STUB=1 kairo run-view --day 2026-08-24`（非 TTY、无 `--yes`） | 打印与预览同名数字后退出 2；零消耗 |
| S4 | 缺 brief 行、Artifact 行 | 无未加工标记；确认数字不含它们 |

## 已知不做

- 不 Drive `kairo run --all`
- 不为本票触发真语音转写 / 模型
- 不把日历点色当成未加工判定
