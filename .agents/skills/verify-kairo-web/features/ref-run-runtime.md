# 单 Ref 一次推进墙钟（#378）

无新页面。与 Web 主按钮等价的入口是 CLI `kairo run`。本功能禁止对现网 `~/kairo` 计时，禁止真 LLM Run。

## 入口

- CLI：`KAIRO_STUB=1 kairo run`（cwd 或 `--topic` 指向 scratch Topic）
- 可复跑计时：`env -u KAIRO_SERVE_ROOT uv run --all-extras pytest tests/test_ref_run_runtime_378.py -q`

## Fixture

`tests/test_ref_run_runtime_378.py` 的 `_fresh_topic`：一条 Ref，走 digest + digest 提取 + compose + understanding 提取。计时用带固定休眠的 stub，不调 grok。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| A | `pytest tests/test_ref_run_runtime_378.py::test_single_ref_run_wall_clock_drops_at_least_40_percent` | PASS；墙钟 ≤ 旧串行 6 次调用基线的 60%；digest.md 与 understanding.md 存在 |
| B | 同文件 `test_same_digest_is_extracted_once` | PASS；`knowledge-candidates.yaml` 恰好 2 次 |
| C | 同文件 `test_digest_extract_overlaps_compose` | PASS；digest 提取与 compose 时间窗重叠 |
| D | 同文件 `test_extract_failure_does_not_block_main_products` | PASS；提取抛错后主产物仍在 |
