# #457 Ref 的 HTML 形态预览

## Fixture

使用 scratch serve root。通过 Workspace / Ref 公开 API 创建一个 Topic 和一条全局 Ref，给 Ref 复制登记一份可点击节点的自包含 HTML、一份 Markdown 和一张 PNG，并用 Tag 把 Ref 加入 Topic。另在 Topic home 创建相同形态并发布该 Ref，用独立 public-read 进程在另一空闲端口验证。允许在 scratch 复制用户报告的图谱做原样复验。无需模型服务。

## 路径与判定

| 验收 | 入口 | 判定 |
|---|---|---|
| S1.A1 | Topic 点击全局 Ref，再点元信息中的 HTML 形态 | 中间阅读区显示 HTML，点击节点可见详情，切回 Markdown 后正文可读。 |
| S2.A1 | 从 Timeline 打开独立 Ref 页，在「来源形态」打开同一 HTML | 抽屉包含同一可交互文档，点击节点可见详情，关闭后仍在 Ref 详情。 |
| S3.A1 | Console 切换 HTML、Markdown、图片 | HTML 在独立 sandbox 框架内，文件响应施加 CSP。原 Markdown 和图片可预览。 |
| S3.A2 | 公开只读打开发布 Ref，同时核对同一 Ref 的 Console 视图 | 公开 HTML 无交互预览入口，直接预览和文件请求均被拒绝，Console 可用。 |

保留每条路径典型截图与成功、拒绝响应。使用独立 scratch root 与测试端口，验证完成后关停服务。
