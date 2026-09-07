# #339 Topic 结论阅读与地址恢复

状态：Approved；L1 经用户在本会话回复「go」批准。
分支：`feat/339-topic-reading-state`。

## 1 背景

[#339](https://github.com/xforce-io/kairo/issues/339) 的 S1–S3 要求默认阅读已有结论，并在刷新、新窗口中恢复对象。

## 2 名词解释

N/A。沿用[名词表](../glossary.md)的 Topic、Ref、活 target。

## 3 目标与非目标

目标：零次额外选文、结论选择可由地址恢复、无结论与失效选择可区分。非目标：恢复滚动位置、自动加工、重定义 Ref 分享、窄屏改版。

## 4 能力

### 4.1 UI/UX

桌面保留三栏。未显式选择时优先选择可读 understanding.md，再取声明顺序中的首个可读活 target。正文、左侧选中态、右侧元信息在首次响应中一致。选择结论后更新浏览器地址；刷新和新窗口使用同一选择。显式 Ref 选择保留旧行为且优先于结论参数。无结论时中央说明尚无可读结论，引导选择参考或添加资料；失效结论链接中央说明不可用并提供返回 Topic 的链接。不把失效选择替换成其它正文。

## 5 思路与折衷

GET 地址作为选择的事实源，服务端首次渲染与局部切换共享元信息、正文生成。放弃 session/localStorage 持久选择，避免跨窗口不一致及新链接受历史浏览干扰。显式选择优先于默认；未知选择展示可恢复错误，不猜测意图。

## 6 架构

路由层解析选择并执行可读边界；视图层形成正文和元信息；模板层呈现选中态与链接。主路径为 GET Topic → 校验可读 target → 渲染正文；局部切换仍经 target 校验入口。失败路径为不存在/不可读 → 通用不可用说明，不回显文件内容。Ref 与 public-read 继续使用原有权限过滤。

## 7 模块

Web 路由负责解析和读取，workspace 与 target 模板负责完整页及局部切换。领域层和存数不变。

## 8 API/CLI

GET `/w/{slug}?target={声明路径}` 选择结论；`/topics/{slug}` 保留查询参数转向兼容路由。已有 `ref` 参数优先，包括失效的显式 Ref，不因失败自动选择结论。target 缺省才启用默认。局部 target 成功响应将地址更新为对应完整页地址。仅允许当前入口可读的已声明 target；路径读取仍必须位于 workspace 内。CLI N/A。

## 9 边界

不跟随越界 symlink，不将未知路径当文件读取；public-read 仅可读原本获准 target。journal 无活 target 时无默认结论。空 target 参数属于显式无效选择。旧 Ref URL 保留加载行为。

## 10 迁移/兼容/回滚

无数据迁移。旧无查询地址获得默认阅读，旧 Ref 地址不变。回滚代码恢复旧展示，不修改正文、分类或权限。

## 11 测试计划

E2E/S1：浏览器进入含结论 Topic，正文首屏出现且不用点击。E2E/S2：切换两个 target 后刷新与新窗口打开，正文和选中态保持一致。E2E/S3：无正文、失效选择均能返回或继续选择资料。Integration：TestClient 覆盖声明外路径、目录穿越、symlink 越界、旧 Ref 查询、/topics 转向和 public-read 拒绝。Unit：通过上述参数组合覆盖默认选择，不另建镜像测试。

## 12 开放问题

N/A。

## 13 关联

- [Issue](https://github.com/xforce-io/kairo/issues/339)
- [L1](https://github.com/xforce-io/kairo/issues/339#issuecomment-5564009016)
- #206、#249；PR 在提交时回链。
