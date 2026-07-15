"""轻量 i18n:集中字符串表 + 语言解析 + translator。无框架。"""

from __future__ import annotations

from typing import Callable

DEFAULT_LANG = "en"
SUPPORTED: tuple[str, ...] = ("en", "zh")

CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "nav.targets": "Targets",
        "nav.references": "References",
        "nav.corpus": "Corpus",
        "nav.empty_streams": "No observations yet",
        "header.back_label": "← Overview",
        "header.back_title": "Back to overview",
        "dash.new_ws_btn": "New workspace",
        "dash.topic_placeholder": "Topic of the new workspace (e.g. Product planning)",
        "dash.workspaces_suffix": "workspaces",
        "dash.badge_stream": "obs",
        "dash.badge_corpus": "baseline",
        "dash.badge_stale": "to step",
        "dash.empty": "No workspace here (a subdirectory must contain constitution.yaml).",
        "dash.create_failed": "Create failed",
        "panel.actions": "Actions",
        "panel.metadata": "Metadata",
        "panel.hint": "Select an item on the left to view its metadata and previewable forms.",
        "reader.empty": "← Select an item to preview",
        "forms.label": "Forms",
        "forms.preview": "Preview",
        "forms.copy_path": "Copy path",
        "doc.export_pdf": "Export PDF",
        "ref.kicker": "Reference",
        "ref.empty_hint": "This reference has no inline-previewable text form (e.g. corpus tree, audio).",
        "ref.add_btn": "+ Add reference",
        "ref.add_title": "Add reference",
        "ref.path_label": "By path",
        "ref.add_path_btn": "Add",
        "ref.upload_label": "Upload file",
        "ref.upload_btn": "Upload",
        "ref.or": "or",
        "ref.add_failed": "Add failed — check the path or file.",
        "ref.path_placeholder": "Local file / dir path",
        "ref.copy_label": "Copy into workspace (survives if the original moves/deletes)",
        "ref.copy_forced_hint": "Browser files are always copied into the workspace.",
        "corpus.add_btn": "+ Add corpus",
        "corpus.add_title": "Add corpus",
        "corpus.path_label": "Corpus path",
        "corpus.path_placeholder": "Local file / directory path",
        "corpus.add_path_btn": "Add",
        "corpus.add_failed": "Add corpus failed — check the path.",
        "target.kicker_prefix": "Target",
        "target.reason_prefix": "Reason",
        "target.body": "Body",
        "target.not_generated": "Not generated yet",
        "target.empty_hint": "This target isn't generated yet. Click ▶ Step (top-right) to run, then view.",
        "target.regen": "↻ Regenerate",
        "target.regen_confirm": "Regenerate this target? The current document (including manual edits) will be discarded and fully re-composed.",
        "prose.gen_btn": "Generate readable prose",
        "step.btn": "▶ Step",
        "step.running": "Step running… (auto-refreshes when done)",
        "step.cancel": "Cancel",
        "step.busy": "⏳ Running — wait for the current step to finish.",
        "step.canceled": "Canceled.",
        "step.cannot_cancel": "Cannot cancel (already finished).",
        "role.transcript": "Transcript",
        "role.digest": "Digest",
        "role.audio": "Audio",
        "role.corpus_tree": "Corpus tree",
        "role.source_text": "Body",
        "role.note": "Note",
        "role.prose": "Prose",
        "role.attachment": "Attachment",
        "ref.attach_btn": "+ Attach material",
        "ref.attach_path_placeholder": "Local file path (image / audio / doc)",
        "ref.rename_label": "Display name (does not change paths or id)",
        "ref.rename_save": "Save",
        "err.topic_empty": "Topic cannot be empty",
        "err.topic_too_long": "Topic too long (max 64 characters)",
        "err.topic_control": "Topic cannot contain control characters",
        "err.topic_illegal": "Topic contains illegal characters (no /, \\, or leading .)",
        "err.topic_invalid": "Invalid topic",
        "err.topic_exists": "Workspace already exists: {topic}",
    },
    "zh": {
        "nav.targets": "产物",
        "nav.references": "参考",
        "nav.corpus": "基线",
        "nav.empty_streams": "暂无观测",
        "header.back_label": "← 总览",
        "header.back_title": "返回总览",
        "dash.new_ws_btn": "新建 workspace",
        "dash.topic_placeholder": "新 workspace 的 topic(如 产品规划)",
        "dash.workspaces_suffix": "workspaces",
        "dash.badge_stream": "观测",
        "dash.badge_corpus": "基线",
        "dash.badge_stale": "待 step",
        "dash.empty": "该目录下没有 workspace(子目录需含 constitution.yaml)。",
        "dash.create_failed": "创建失败",
        "panel.actions": "操作",
        "panel.metadata": "元信息",
        "panel.hint": "选左侧条目查看元信息与可预览形态。",
        "reader.empty": "← 选左侧条目预览",
        "forms.label": "形态",
        "forms.preview": "预览",
        "forms.copy_path": "复制路径",
        "doc.export_pdf": "导出 PDF",
        "ref.kicker": "参考",
        "ref.empty_hint": "此参考无可内联预览的文本形态（如 资料目录、音频）。",
        "ref.add_btn": "+ 添加参考",
        "ref.add_title": "添加参考",
        "ref.path_label": "按路径",
        "ref.add_path_btn": "添加",
        "ref.upload_label": "上传文件",
        "ref.upload_btn": "上传",
        "ref.or": "或",
        "ref.add_failed": "添加失败 —— 请检查路径或文件。",
        "ref.path_placeholder": "本地文件 / 目录路径",
        "ref.copy_label": "复制到工作区(源移动/删除后仍可用)",
        "ref.copy_forced_hint": "浏览器选择的文件会始终复制进工作区。",
        "corpus.add_btn": "+ 添加基线",
        "corpus.add_title": "添加基线",
        "corpus.path_label": "基线路径",
        "corpus.path_placeholder": "本地文件 / 目录路径",
        "corpus.add_path_btn": "添加",
        "corpus.add_failed": "添加基线失败 —— 请检查路径。",
        "target.kicker_prefix": "产物",
        "target.reason_prefix": "原因",
        "target.body": "正文",
        "target.not_generated": "尚未生成",
        "target.empty_hint": "该产物尚未生成,点右上 ▶ Step 运行后查看。",
        "target.regen": "↻ 重新生成",
        "target.regen_confirm": "重新生成该产物?当前文档(含手改)将被丢弃并整篇重综合。",
        "prose.gen_btn": "生成可读文稿",
        "step.btn": "▶ Step",
        "step.running": "step 进行中…(完成后自动刷新状态)",
        "step.cancel": "取消",
        "step.busy": "⏳ 正在运行,请等待当前 step 结束。",
        "step.canceled": "已取消。",
        "step.cannot_cancel": "无法取消(已结束)。",
        "role.transcript": "转写",
        "role.digest": "摘要",
        "role.audio": "音频",
        "role.corpus_tree": "资料目录",
        "role.source_text": "正文",
        "role.note": "笔记",
        "role.prose": "文稿",
        "role.attachment": "附件",
        "ref.attach_btn": "+ 附加素材",
        "ref.attach_path_placeholder": "本地文件路径(图片 / 音频 / 文档)",
        "ref.rename_label": "显示名(不改路径与 id)",
        "ref.rename_save": "保存",
        "err.topic_empty": "topic 不能为空",
        "err.topic_too_long": "topic 过长(最多 64 字符)",
        "err.topic_control": "topic 不能含控制字符",
        "err.topic_illegal": "topic 含非法字符(不能含 / \\ 或以 . 开头)",
        "err.topic_invalid": "非法 topic",
        "err.topic_exists": "已存在同名 workspace:{topic}",
    },
}


def resolve_lang(request) -> str:
    """cookie 'lang'(若 ∈ SUPPORTED) → Accept-Language(zh*→zh, en*→en) → DEFAULT_LANG。"""
    cookie = request.cookies.get("lang")
    if cookie in SUPPORTED:
        return cookie
    accept = request.headers.get("accept-language", "") or ""
    for part in accept.split(","):
        code = part.split(";")[0].strip().lower()
        if code.startswith("zh"):
            return "zh"
        if code.startswith("en"):
            return "en"
    return DEFAULT_LANG


def translator(lang: str) -> Callable[[str], str]:
    """返回 t(key):当前语言 → en 回退 → key 本身。永不抛。"""
    table = CATALOG.get(lang, CATALOG[DEFAULT_LANG])
    fallback = CATALOG[DEFAULT_LANG]

    def t(key: str) -> str:
        return table.get(key) or fallback.get(key) or key

    return t
