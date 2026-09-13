---
name: verify-kairo-web
description: >
  Drive the Kairo Web Console on a scratch serve root to prove user-visible
  Stories (S1…Sn) before independent review. Never point it at the live root
  (~/kairo) and never trigger LLM Runs from here.
---

# verify-kairo-web

真用户路径证明手册。所有步骤在 **scratch serve root** 上做；现网 `~/kairo`、现网 8787 端口一律不碰。不在这里触发任何会调 provider 的 Run。

## Launch

```bash
ROOT=$(mktemp -d /tmp/kairo-verify-root.XXXX)
PORT=8798
# 用本仓当前工作树代码（被验证的 SHA）起服务；scratch root 里的 Topic 由 features/ 各文件的 Fixture 段生成。
cd <repo>
env -u KAIRO_SERVE_ROOT uv run --all-extras kairo serve "$ROOT" -p $PORT > /tmp/kairo-verify-serve.log 2>&1 &
echo $! > /tmp/kairo-verify-serve.pid
```

Fixture 一律用进程内 Python（`uv run python <script>`）调 `kairo.workspace.Workspace.init`、`kairo.knowledge_review.ingest_candidates` 等公开函数生成，不手写 yaml。各功能文件给出自己的 Fixture 段；脚本放 `/tmp`，不进仓库。

## Doctor

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://127.0.0.1:$PORT/     # 期望 200
curl -s "http://127.0.0.1:$PORT/knowledge?workspace=<slug>" | grep -c "queue-nav"   # 期望 ≥1
```

任一失败：看 `/tmp/kairo-verify-serve.log`，不要继续 Drive。

## Drive

用宿主浏览器工具（Cursor：`cursor-ide-browser`，先 `browser_navigate` 再 `browser_lock`）。对每个要证的 Story：

1. 打开 `features/<feature>.md` 里列出的**全部**入口（页面 URL / 队列切换 / 表单按钮），不能只走一条。
2. 按文件中「路径 → 可判定结果」逐条做，每步 `browser_snapshot` 确认文案与计数，不凭截图猜。
3. 状态变化类操作（采纳 / 忽略 / 合并）做完后 **重新 GET** 页面再读计数，不信任 POST 返回页。

## Evidence

目录：`/tmp/kairo-verify/<issue-no>/`。每个 Story 至少：

- `S<n>-before.png`、`S<n>-after.png`（`browser_take_screenshot`）
- `S<n>.txt`：所走入口、关键 snapshot 片段（计数行、按钮文案）、对应 review yaml 中的状态行（`grep status`）

PR 验收表里引用这些路径。证据目录不进仓库。

## Cleanup

```bash
kill "$(cat /tmp/kairo-verify-serve.pid)"; rm -f /tmp/kairo-verify-serve.pid
rm -rf "$ROOT"            # 只删 scratch root
# /tmp/kairo-verify/<issue-no>/ 保留
```

`browser_lock unlock` 收尾。
