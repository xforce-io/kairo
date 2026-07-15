# 73 — 参考 blocked 可见 + 重新处理

- Issue: [#73](https://github.com/xforce-io/kairo/issues/73)
- 分支: `feat/73-ref-blocked-retry`
- 状态: 已实现

## 行为
- 参考元信息展示 `state.products` 中该 ref 的 blocked 项
- 「重新处理」→ `kairo retry-ref <id>`：清派生产物/forms(非 added)/blocked → step
- `re-step <ref_id>` 同样走完整重试（不再只删 digest）
