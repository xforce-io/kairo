"""kairo 数据模型(pydantic)。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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
    "- 可溯源;不要编造正文没有的事实"
)

# prose 是可选的人读档案(默认关),只服务可读性、不进 digest 路径,故按可读优化、不必无损。
DEFAULT_NORMALIZE_PROMPT = (
    "把这份机器转写的誊录整理成忠实、流畅、易读的全文:补标点、合理分段、"
    "纠正明显的同音/识别错误、合并重复的口水与寒暄。\n"
    "忠实于原意,不增删事实、不加评论;这是供人通读的全文,不是纪要,不要概括成摘要。"
)

DEFAULT_UNDERSTANDING_FOLD = (
    "把新材料融进对本 topic 的事实理解;凡改变图景处就重组/修正/推翻,而非末尾追加。\n"
    "维持一张去重的术语表;未确认的挂 ⚠️;每段标来源。只放中立事实,判断进 assessment。\n"
    "仅对确实无关的部分不动。\n"
    "文末维护一节『未来待办』:汇总待核事实、数据缺口与需补充/待获取的材料,随确认进度增删。"
)

DEFAULT_ASSESSMENT_FOLD = (
    "沉淀立场与判断,引用上游 understanding 的事实(标来源);随新材料演进、可推翻旧判断。\n"
    "不与 understanding 的中立事实混。\n"
    "文末维护一节『未来待办』:列待验证/可被推翻的判断,与下一步该核实或推进的行动,随新材料更新。"
)


class NormalizeConfig(BaseModel):
    # 默认关:prose 是可选的人读档案;digest 恒从 transcript(信息上界),不依赖 prose
    enabled: bool = False
    prompt: str = DEFAULT_NORMALIZE_PROMPT


class DigestConfig(BaseModel):
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
    # 拓扑序:understanding(事实)在前,assessment(判断)depends_on 它。
    return [
        Target(
            path="understanding.md",
            layer="fact",
            fold_protocol=DEFAULT_UNDERSTANDING_FOLD,
        ),
        Target(
            path="assessment.md",
            layer="judgment",
            fold_protocol=DEFAULT_ASSESSMENT_FOLD,
            depends_on=["understanding.md"],
        ),
    ]


_AUDIO_EXTS = (".m4a", ".wav", ".mp3", ".aac", ".flac", ".ogg")
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


class Constitution(BaseModel):
    topic: str = "main"
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
    glossary: list[GlossaryEntry] = Field(default_factory=list)  # 领域真名册(#20)

    def glossary_reference(self) -> str:
        """真名册 → 一段权威参考(注入 Digest/Compose persona);空表返回空串(零行为变化)。"""
        if not self.glossary:
            return ""
        lines = []
        for e in self.glossary:
            line = f"- {e.name}"
            if e.note:
                line += f" —— {e.note}"
            if e.aka:
                line += f"(亦作:{'/'.join(e.aka)})"
            lines.append(line)
        return (
            "\n\n[领域真名册](权威参考;下列为本领域规范名,产出时一律用规范名,"
            "勿用变体/别名;遇含糊提及按此锚定)\n" + "\n".join(lines)
        )


# ---- reference manifest (references/<id>/manifest.yaml) ----


class Form(BaseModel):
    role: str  # audio | transcript | note | source_text
    location: str
    hash: str
    origin: str = "added"


class Manifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str = ""
    # 认识论归类:corpus(基线)/ stream(观测)。yaml 键为 `class`;旧 manifest 无此键 → stream。
    source_class: str = Field(default="stream", alias="class")
    forms: list[Form] = Field(default_factory=list)


# ---- reconcile state (.kairo/state.json) ----


class ProductState(BaseModel):
    input_hash: str
    produced_by: dict[str, str] | None = None
    status: str = "ok"
    reason: str | None = None


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
    reason: str | None = None  # manual-edit | …


class State(BaseModel):
    products: dict[str, ProductState] = Field(default_factory=dict)
    targets: dict[str, TargetState] = Field(default_factory=dict)
