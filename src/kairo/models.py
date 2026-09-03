"""kairo 数据模型(pydantic)。"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_DIGEST_PROMPT = (
    "为这条 reference 写一份忠实、高密度的记忆纪要(不是一页纸周报,也不是转写原文)。\n\n"
    "目标:让未听录音/未读原文的人仅凭本纪要,能恢复讨论的关键事实、决策、分歧与待办。\n\n"
    "必须保留:\n"
    "- 结论与决策(含未定论/待确认)\n"
    "- 关键数字、指标、比例、时间点、范围\n"
    "- 专名(人/组织/系统/项目);不确定标 ⚠️ 待核\n"
    "- 方案对比、约束条件、风险、失败模式、例外情况\n"
    "- 明确的待办、负责人线索、下一步\n"
    "- 有信息量的举例与场景(不要只留抽象口号)\n\n"
    "可以删:\n"
    "- ASR 广告串扰、无意义语气词、纯重复口头禅\n"
    "- 与业务无关的闲聊\n\n"
    "写法:\n"
    "- 按议题结构化;条目下写清因果与上下文,不要只剩高层 bullet\n"
    "- 宁详勿略:信息密度优先于篇幅短\n"
    "- 可溯源;不要编造正文没有的事实\n"
    "- 待办单独成节,节名固定为「待办」。正文议题里不要再散落行动句。\n"
    "- 待办每一条一行:P0 或 P1 或 P2 或 P? · 时间或未定时 · 动词开头的事项。负责人原文没有则省略。\n"
    "- 优先级按说话信号判定,不要等 P0 字样:先做/最重要/卡住别人 → P0;已定要做且有时间窗 → P1;提到了但无窗口 → P2;连上述信号都没有才用 P?。不得把所有待办都标成 P?。\n"
    "- 待办示例:- P0 · 下周 · 完成分流上线。宜兴\n"
    "- P1 · 下周二 · 开 FDE 小组第一次会\n"
    "- 待办排序:P0 在前,同级按时间从近到远,未定时在后。\n"
    "- 禁止表格、编号清单、把背景写进待办、虚构截止日期。不要输出花括号。时间原文没有则写未定时。"
)

# prose 是可选的人读档案(默认关),只服务可读性、不进 digest 路径,故按可读优化、不必无损。
DEFAULT_NORMALIZE_PROMPT = (
    "把这份机器转写的誊录整理成忠实、流畅、易读的全文:补标点、合理分段、"
    "纠正明显的同音/识别错误、合并重复的口水与寒暄。\n"
    "忠实于原意,不增删事实、不加评论;这是供人通读的全文,不是纪要,不要概括成摘要。"
)

DEFAULT_UNDERSTANDING_FOLD = (
    "把新材料融进对本 topic 的事实理解;凡改变图景处就重组/修正/推翻,而非末尾追加。\n"
    "维持一张去重的术语表;未确认的挂 ⚠️;只放中立事实,不写立场判断。\n"
    "仅对确实无关的部分不动。\n"
    "溯源(#99):章节证据范围〔S-…〕+ 关键声明短 ID;文末「来源索引」映射到 digest;"
    "正文不重复完整 references/.../digest.md 路径。\n"
    "文末维护一节『未来待办』:汇总待核事实、数据缺口与需补充/待获取的材料,随确认进度增删。"
)

DEFAULT_ASSESSMENT_FOLD = (
    "沉淀立场与判断,引用上游 understanding 的事实;随新材料演进、可推翻旧判断。\n"
    "不与 understanding 的中立事实混。\n"
    "溯源(#99):判断优先〔依据:F-…〕链到事实锚点;直接〔S-…〕仅作例外并说明原因;"
    "文末「依据事实索引」(及必要时「来源索引」);正文不堆叠完整 digest 路径。\n"
    "文末维护一节『未来待办』:列待验证/可被推翻的判断,与下一步该核实或推进的行动,随新材料更新。"
)


class NormalizeConfig(BaseModel):
    # 默认关:prose 是可选的人读档案;digest 恒从 transcript(信息上界),不依赖 prose
    enabled: bool = False
    prompt: str = DEFAULT_NORMALIZE_PROMPT


class DigestConfig(BaseModel):
    enabled: bool = True  # journal 预设关掉;引擎读此字段
    prompt: str = DEFAULT_DIGEST_PROMPT


class Pipeline(BaseModel):
    normalize: NormalizeConfig = Field(default_factory=NormalizeConfig)
    digest: DigestConfig = Field(default_factory=DigestConfig)


class Target(BaseModel):
    path: str
    layer: str = "fact"
    fold_protocol: str = ""
    depends_on: list[str] = Field(default_factory=list)


def _default_targets() -> list[Target]:
    # #153:默认只产事实层;judgment target 若仍写在旧 constitution 中则运行时跳过。
    return [
        Target(
            path="understanding.md",
            layer="fact",
            fold_protocol=DEFAULT_UNDERSTANDING_FOLD,
        ),
    ]


_AUDIO_EXTS = (".m4a", ".wav", ".mp3", ".aac", ".flac", ".ogg", ".mp4", ".m4v", ".mov", ".webm")
# 二进制/结构化文档(#15):markitdown 统吃 → 单一 document role,doc2text 转 source_text。
_DOCUMENT_EXTS = (".docx", ".pptx", ".xlsx", ".pdf")
# 图片:作附件 form 挂在会议下,不转文本、由多模态 agent 在 digest 时 Read 看图(#44)。
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".heic")


def _default_roles_by_ext() -> dict[str, str]:
    return {
        **{e: "audio" for e in _AUDIO_EXTS},
        **{e: "document" for e in _DOCUMENT_EXTS},
        **{e: "attachment" for e in _IMAGE_EXTS},
    }


class Transform(BaseModel):
    """声明一条资源转换:consumes role(s) → produces role,由 backend 执行。"""

    name: str
    consumes: list[str]
    produces: str
    backend: str = "asr-stub"


def _default_transforms() -> list[Transform]:
    # backend=whisper:声明"用本机 whisper 转写";具体命令由本机配置(machine.resolve_asr)解析。
    # backend=markitdown:二进制(docx/pptx/xlsx/pdf)进程内转 source_text(#15),无需机器配置。
    return [
        Transform(
            name="asr", consumes=["audio"], produces="transcript", backend="whisper"
        ),
        Transform(
            name="doc2text",
            consumes=["document"],
            produces="source_text",
            backend="markitdown",
        ),
    ]


class SourceClass(BaseModel):
    """一类源的认识论地位:显示标签 + fold 语义(由 constitution 声明,引擎不硬编码)。

    fold=True:作离散事件折叠进 target,内容 hash 驱动收敛(stream/观测)。
    fold=False:作只读参考层,agent 按需 Read,不 digest、不进 fold-delta(corpus/基线)。
    """

    label: str
    hint: str = ""
    fold: bool = True


def _default_source_classes() -> dict[str, SourceClass]:
    # stream(观测):会议/事件流,折叠;corpus(基线):权威参考资料,只读参考层不折叠。
    return {
        "stream": SourceClass(
            label="观测",
            hint="会议/事件流;逐条融入,判断随之演进、可推翻旧判断。",
            fold=True,
        ),
        "corpus": SourceClass(
            label="基线",
            hint=(
                "权威参考资料;与观测冲突时以基线为准,"
                "用基线校正专名/术语(同音变体回归规范名),并作术语权威基线。"
            ),
            fold=False,
        ),
    }


class GlossaryEntry(BaseModel):
    """领域真名册的一条:真名 = 各环节参考的锚;note 给模型 grounding;aka 可选变体。"""

    name: str
    note: str = ""
    aka: list[str] = Field(default_factory=list)  # 曾误识别/同音变体,纯参考
    tags: list[str] = Field(default_factory=list)  # 轻量分组(#71),可选


class Constitution(BaseModel):
    topic: str = "main"
    kind: str = "topic"  # 建仓填法名;运行时读 digest.enabled / targets / review_input
    pipeline: Pipeline = Field(default_factory=Pipeline)
    roles_by_ext: dict[str, str] = Field(default_factory=_default_roles_by_ext)
    default_role: str = "transcript"  # 无匹配扩展名时兜底
    body_roles: list[str] = Field(  # DigestRule 取正文的 role(优先序)
        # #33:digest 恒从 transcript(信息上界);prose 是旁挂的人读档案,不进 digest 路径
        default_factory=lambda: ["transcript", "source_text"]
    )
    transforms: list[Transform] = Field(default_factory=_default_transforms)
    source_classes: dict[str, SourceClass] = Field(  # 源分层语义(corpus/stream)
        default_factory=_default_source_classes
    )
    default_class: str = "stream"  # add 不指定时的兜底归类
    targets: list[Target] = Field(default_factory=_default_targets)
    review_input: bool = True  # 材料是否进时段回顾原料
    glossary: list[GlossaryEntry] = Field(default_factory=list)  # 领域真名册(#20)
    include_tags: list[str] | None = None  # None=兼容 home 成员;[]=无成员;非空=任一 Tag 命中

    def live_targets(self) -> list[Target]:
        """活 target:跳过判断层(#153)。journal（含现网「总结」）不 fold。"""
        from kairo.kind import KIND_JOURNAL, resolve_kind

        if resolve_kind(self.kind, self.topic) == KIND_JOURNAL:
            return []
        return [t for t in self.targets if t.layer != "judgment"]

    def glossary_reference(self) -> str:
        """仅 workspace 层真名册 → 权威参考段;空表 ""。

        注入请优先用 Workspace.glossary_reference()(含 machine/root 合并,#71)。
        """
        from kairo.glossary import format_glossary_reference

        return format_glossary_reference(self.glossary)


# ---- reference manifest (references/<id>/manifest.yaml) ----


class Form(BaseModel):
    role: str  # audio | transcript | note | source_text
    location: str
    hash: str
    origin: str = "added"


class ArchiveBinding(BaseModel):
    """会话归档绑定(#136)。旧 manifest 无此键 → 非归档。"""

    key: str
    version: int
    form_index: int
    body_sha256: str


def _coerce_time_str(value):
    """yaml 无引号日期/时间戳 → isoformat 字符串；脏类型 → None。datetime 须先于 date。"""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


class Manifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = ""
    # 认识论归类:corpus(基线)/ stream(观测)。yaml 键为 `class`;旧 manifest 无此键 → stream。
    source_class: str = Field(default="stream", alias="class")
    forms: list[Form] = Field(default_factory=list)
    archive: ArchiveBinding | None = None
    occurred_at: str | None = None
    added_at: str | None = None

    @field_validator("occurred_at", "added_at", mode="before")
    @classmethod
    def _time_fields(cls, value):
        return _coerce_time_str(value)


# ---- reconcile state (.kairo/state.json) ----

# #98: Digest/Compose provider 失败的稳定 reason;普通 step 不自动重试。
REASON_PROVIDER_FAILED = "provider-failed"


class FailureDiagnostic(BaseModel):
    """工作项级安全诊断(#98)。可选字段;旧 state 缺失时按无诊断兼容。

    summary 已脱敏/截断,不承诺原始异常保真;禁止持久化密钥/完整 prompt/原始 JSON。
    """

    stage: str  # digest | compose
    provider: str = ""
    summary: str = ""


class KnowledgeDiagnostic(BaseModel):
    """一次产物实际采用的知识匹配诊断；只保存计数与稳定 id，绝不保存 prompt。"""

    matched_entry_ids: list[str] = Field(default_factory=list)
    ambiguities: int = 0
    truncated: int = 0
    skipped: int = 0
    available: bool = True
    error_code: str = ""
    safe_summary: str = ""


class ProductState(BaseModel):
    input_hash: str
    produced_by: dict[str, str] | None = None
    status: str = "ok"
    reason: str | None = None
    diagnostic: FailureDiagnostic | None = None  # #98;旧 state 无此键
    glossary_hash: str | None = None  # #163;缺省=旧产物
    knowledge_hash: str | None = None  # #182;仅 advisory，不进入 input_hash
    knowledge_diagnostic: KnowledgeDiagnostic | None = None
    knowledge_generation: str = ""  # 每次实际消费知识的规则运行生成；仅用于 Run 诊断隔离。


class TargetState(BaseModel):
    depends_on: list[str] = Field(default_factory=list)
    compose_config_hash: str = ""
    output_hash: str = ""
    produced_by: dict[str, str] | None = None
    folded: dict[str, str] = Field(default_factory=dict)
    last_major_folded: dict[str, str] = Field(default_factory=dict)
    upstream_hash: dict[str, str] = Field(default_factory=dict)
    corpus_stamp: str = ""  # 折叠时 corpus 参考层版本戳;漂移 → advisory 提示手动 recompute
    status: str = "ok"  # ok | blocked
    reason: str | None = None  # manual-edit | provider-failed | …
    diagnostic: FailureDiagnostic | None = None  # #98;旧 state 无此键
    retry_reason: str | None = None  # provider-failed 前的触发语义;Run 重试后清空
    glossary_hash: str | None = None  # #163;缺省=旧产物
    knowledge_hash: str | None = None  # #182;仅 advisory，不进入 input_hash
    knowledge_diagnostic: KnowledgeDiagnostic | None = None
    knowledge_generation: str = ""


class State(BaseModel):
    products: dict[str, ProductState] = Field(default_factory=dict)
    targets: dict[str, TargetState] = Field(default_factory=dict)
