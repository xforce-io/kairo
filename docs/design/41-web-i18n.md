# 41 — Web Console 与 README 双语化（轻量 i18n，默认英文）

- Issue: [#41](https://github.com/xforce-io/kairo/issues/41)
- 分支: `feat/41-web-i18n`
- 状态: 设计已确认,待实现计划
- 日期: 2026-06-26

## 1. 背景与目标

Web Console（[#35](https://github.com/xforce-io/kairo/issues/35)）与 README 当前全中文。目标:**UI 与文档提供中英双语,默认英文**。

约束与现状:

- Web Console 是**服务端渲染**(FastAPI + Jinja2 + htmx),非前端 SPA——i18n 落在服务端。
- 可见文案总量小(约 40~50 条),分散在 9 个模板 **和** `views.py`(role 标签表、建 workspace 报错、空态提示、step 状态)两处。
- README 约 110 行,prose 中文,**领域词已是英文**(step / reference / corpus / stream / digest / fold / constitution / glossary / blocked)。

**非目标**:CLI 命令输出保持现状(中文),本次不动;不引 i18n 框架(Babel / gettext / `.po`);无运行时语言协商以外的本地化(日期/数字格式不处理)。

## 2. 核心设计决策

| 维度 | 决策 |
|---|---|
| 形态 | 轻量集中字符串表 + `t(key)` 助手,**不引框架** |
| 真问题 | 顺手把散落模板/Python 两处的文案收口到唯一字符串源(结构大于逻辑) |
| 默认语言 | `en` |
| 语言解析 | cookie `lang` → `Accept-Language`(`zh*`→`zh`) → 默认 `en` |
| 切换 | `GET /set-lang/{code}` 写 cookie + 重定向回 Referer;顶栏 `EN \| 中` 链接 |
| 缺失回退 | `t(key)`:当前语言缺 key → 回退 `en` → 再回退 key 本身(永不崩) |
| 文档 | `README.md`=英文(默认);`README.zh-CN.md`=中文;顶部互链 |
| 领域词 | UI 与英文 README 保留英文领域词,只译「外壳」prose |

## 3. 组件

### 3.1 `src/kairo/web/i18n.py`(新)— 唯一文案源

```python
DEFAULT_LANG = "en"
SUPPORTED = ("en", "zh")
CATALOG: dict[str, dict[str, str]] = {"en": {...}, "zh": {...}}  # 点分 key

def resolve_lang(request) -> str:
    """cookie 'lang' (若 ∈ SUPPORTED) → Accept-Language(zh* → zh) → DEFAULT_LANG。"""

def translator(lang: str):
    """返回 t(key):CATALOG[lang].get(key) → CATALOG['en'].get(key) → key。"""
```

含参文案返回 format 串,调用方 `.format(...)`(如 `err.topic_exists`)。

### 3.2 `views.py` 渲染接缝

```python
def _render(request, name, ctx):
    lang = resolve_lang(request)
    return templates.TemplateResponse(request, name, {**ctx, "lang": lang, "t": translator(lang)})
```

- 所有 `templates.TemplateResponse(...)` 改走 `_render`。
- `HTTPException(detail=…)`(建 workspace 报错)按 `resolve_lang(request)` 取 `t(key)`。
- 内联 `HTMLResponse`(step 忙 / 取消提示)同上。
- 构造 forms 列表的函数接收 `t`(原 `_ROLE_LABEL` 字典并入 catalog 的 `role.*`)。
- `/healthz`、`workspace/doc not found` 等技术性 API 错误**不译**(已英文)。

### 3.3 语言切换 `GET /set-lang/{code}`

校验 `code ∈ SUPPORTED` → `set_cookie("lang", code)` → 302 重定向到 `Referer`(缺省 `/`);非法 code 忽略并回 `/`。`base.html` 顶栏加两个小链接 `EN | 中`,高亮当前 `lang`。

### 3.4 模板

9 个模板的中文字面量换 `{{ t("…") }}`;`<html lang="zh">` → `<html lang="{{ lang }}">`;`base.html` 顶栏加语言切换。`{% block title %}kairo console{% endblock %}` 作品牌名保留。

## 4. 字符串目录(完整)

> EN 为默认。`{x}` 为模板/调用方填充的占位。dashboard 徽标沿用「观测/基线」措辞(obs/baseline),workspace 侧栏沿用「参考/基线」(References/Corpus)——保留现有区分。

| key | en | zh |
|---|---|---|
| `nav.targets` | Targets | 产物 |
| `nav.references` | References | 参考 |
| `nav.corpus` | Corpus | 基线 |
| `nav.empty_streams` | No observations yet | 暂无观测 |
| `header.back_label` | ← Overview | ← 总览 |
| `header.back_title` | Back to overview | 返回总览 |
| `dash.new_ws_btn` | New workspace | 新建 workspace |
| `dash.topic_placeholder` | Topic of the new workspace (e.g. Product planning) | 新 workspace 的 topic(如 产品规划) |
| `dash.workspaces_suffix` | workspaces | workspaces |
| `dash.badge_stream` | obs | 观测 |
| `dash.badge_corpus` | baseline | 基线 |
| `dash.badge_stale` | to step | 待 step |
| `dash.empty` | No workspace here (a subdirectory must contain constitution.yaml). | 该目录下没有 workspace(子目录需含 constitution.yaml)。 |
| `dash.create_failed` | Create failed | 创建失败 |
| `panel.actions` | Actions | 操作 |
| `panel.metadata` | Metadata | 元信息 |
| `panel.hint` | Select an item on the left to view its metadata and previewable forms. | 选左侧条目查看元信息与可预览形态。 |
| `reader.empty` | ← Select an item to preview | ← 选左侧条目预览 |
| `forms.label` | Forms | 形态 |
| `forms.preview` | Preview | 预览 |
| `forms.copy_path` | Copy path | 复制路径 |
| `ref.kicker` | Reference | 参考 |
| `ref.empty_hint` | This reference has no inline-previewable text form (e.g. corpus tree, audio). | 此参考无可内联预览的文本形态（如 资料目录、音频）。 |
| `target.kicker_prefix` | Target | 产物 |
| `target.reason_prefix` | Reason | 原因 |
| `target.body` | Body | 正文 |
| `target.not_generated` | Not generated yet | 尚未生成 |
| `target.empty_hint` | This target isn't generated yet. Click ▶ Step (top-right) to run, then view. | 该产物尚未生成,点右上 ▶ Step 运行后查看。 |
| `step.btn` | ▶ Step | ▶ Step |
| `step.running` | Step running… (auto-refreshes when done) | step 进行中…(完成后自动刷新状态) |
| `step.cancel` | Cancel | 取消 |
| `step.busy` | ⏳ Running — wait for the current step to finish. | ⏳ 正在运行,请等待当前 step 结束。 |
| `step.canceled` | Canceled. | 已取消。 |
| `step.cannot_cancel` | Cannot cancel (already finished). | 无法取消(已结束)。 |
| `role.transcript` | Transcript | 转写 |
| `role.digest` | Digest | 摘要 |
| `role.audio` | Audio | 音频 |
| `role.corpus_tree` | Corpus tree | 资料目录 |
| `role.source_text` | Body | 正文 |
| `role.note` | Note | 笔记 |
| `role.prose` | Prose | 文稿 |
| `err.topic_empty` | Topic cannot be empty | topic 不能为空 |
| `err.topic_too_long` | Topic too long (max 64 characters) | topic 过长(最多 64 字符) |
| `err.topic_control` | Topic cannot contain control characters | topic 不能含控制字符 |
| `err.topic_illegal` | Topic contains illegal characters (no /, \\, or leading .) | topic 含非法字符(不能含 / \\ 或以 . 开头) |
| `err.topic_invalid` | Invalid topic | 非法 topic |
| `err.topic_exists` | Workspace already exists: {topic} | 已存在同名 workspace:{topic} |

## 5. README 拆分

- `README.md` → **英文**(默认):译现有 prose;**保留**英文领域词、所有命令/代码块/issue 链接原样。顶部首行:`English | [简体中文](README.zh-CN.md)`。
- `README.zh-CN.md` → 现有中文内容原样搬入,顶部首行:`[English](README.md) | 简体中文`。

## 6. 测试

- **改现有 web 测试**:默认语言现为 EN,`tests/test_web_api.py` / `test_web_write.py` 中针对 **UI 外壳串** 的中文断言改英文默认值(`新建 workspace`→`New workspace`、`返回总览`→`Overview`、`>参考<`→`>References<`、`>基线<`→`>Corpus<`、`转写`→`Transcript`、`音频`→`Audio` …)。
- **不动数据内容断言**:topic「阿尔法/产品规划」、笔记「一条笔记」、正文「落地优先级讨论」等是**用户数据**而非 UI 外壳,保持中文。
- **新增 `tests/test_web_i18n.py`**:① 默认无 cookie/header → EN;② `Accept-Language: zh` → 中文;③ cookie `lang=zh` 覆盖 header;④ `GET /set-lang/zh` 写 cookie 并重定向;⑤ 未知 code 忽略回退 EN;⑥ catalog 缺 key 回退不崩。

## 7. 影响面与风险

- 仅触及 `src/kairo/web/`(新增 `i18n.py`、改 `views.py` + 模板 + `base.html`)、根目录 README、`tests/`。core 引擎零侵入。
- 新增对外接口 `GET /set-lang/{code}` + cookie `lang`(本地单用户,无鉴权影响)。
- 唯一行为变更风险:**默认语言从中文变英文**——现有中文用户首次访问看到英文,需点切换或浏览器 `Accept-Language: zh` 自动命中中文。属预期(需求即「默认英文」)。
