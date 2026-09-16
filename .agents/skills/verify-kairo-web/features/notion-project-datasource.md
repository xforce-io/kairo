# #388 Project 数据源只认一页 Notion（Console）

Atlas Drive 只证 Console 面。MCP Notion 读页 ≠ 验收。不碰现网 `~/kairo`，不用 8787，不在本手册触发 provider / LLM Run。

## 入口（全部要走）

1. Settings 连接目录：`/settings`（中英各一次）
2. Project 数据源添加表单：`/projects/<project_id>` 粘贴框 + 添加
3. 已落盘 Notion 行：列表 Reader 标签、打开正文 `/projects/<project_id>/datasources/<ds_id>`
4. 添加失败回页：错误条 `role="alert"` + `data-error-code`

**S3 不走：** agent Task Run 需要 CLI + provider。本手册 **skip**，禁止为了 S3 点 Run。

## Fixture

写到 `/tmp/kairo-verify-fixture-388.py` 后 `env -u KAIRO_SERVE_ROOT uv run python /tmp/kairo-verify-fixture-388.py "$ROOT"`：

```python
import sys
from pathlib import Path
from kairo.project_materials import write_cache
from kairo.projects import DataSource, create_project, save_project

root = Path(sys.argv[1])
project = create_project(root, "能源项目")
PAGE_HEX = "a" * 32
ds = DataSource(
    id="ds-notion-1",
    connection_id="notion",
    url=f"https://app.notion.com/p/{PAGE_HEX}",
    kind="page",
    purpose="项目材料",
    name="材料清单",
    reader="notion",
)
project.datasources.append(ds)
save_project(root, project)
write_cache(root, project.id, ds, "# 材料清单\n\n现场装机 80 MW。\n")
print(project.id)
```

记下打印的 `project_id`。不写 `NOTION_TOKEN`，不授权 Notion，不调用 Notion API。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1-a | GET `/settings`（en / zh） | 连接目录有 Notion 卡片；文案含 `NOTION_TOKEN`；可授权/撤销（live）；不出现 token 值 | Console |
| S1-b | GET `/projects/<id>`（en / zh） | 添加框 placeholder 是 Notion 页面 URL，含 `app.notion.com` 与 `/p/{id}`；**没有** `mail:// / imap://` | Console |
| S1-c | 同上，看已落盘「材料清单」行 | Reader 标签是 `Notion`（不是内部 id 当主文案） | Console + fixture |
| S1-d | GET `/projects/<id>/datasources/ds-notion-1` | 缓存正文可见「材料清单」「现场装机 80 MW」；不是假成功空页 | Console + fixture |
| S1-e | 粘贴合法 Notion URL（含 `app.notion.com/p/{32hex}`）添加 | 成功一行；`reader` 标签 Notion；`kind=page`；无假成功 | Console + 现网 infer（读正文仍需授权 + `NOTION_TOKEN`，本手册不 Drive live read） |
| S2-a | POST 添加 `https://example.com/not-docs`、`file:///tmp/a.md`、裸 `notes.md` 后再 GET 项目页 | `role="alert"` 且 `data-error-code="invalid_link"`；列表无对应新行；无成功提示 | Console |
| S2-b | POST 添加企微文档 / 腾讯表格 / `mail://` / `imap://` 后再 GET | 添加失败；`data-error-code="unsupported_reader"`；列表无新行 | Console（白名单已在引擎） |
| S3 | 仅绑定该 Notion 源的 agent Task → Run → 打开 Artifact | succeeded Artifact 来源含该 Notion 源 | **skip**：需 CLI/provider；verify-kairo-web 不触发 LLM Run |

## 已知不做

- 不把 MCP 读 Notion 页写成 S1/S2 证据
- 不为 S3 在 scratch 上点 Console Run
- 不改 `readers.infer_source` / `READER_CATALOG` live（Forge）
