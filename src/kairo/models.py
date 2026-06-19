"""kairo 数据模型(pydantic)。"""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_DIGEST_PROMPT = "为这条 reference 写一份忠实纪要,保留要点,可溯源。"

DEFAULT_UNDERSTANDING_FOLD = (
    "把新材料融进对本 topic 的事实理解;凡改变图景处就重组/修正/推翻,而非末尾追加。\n"
    "维持一张去重的术语表;未确认的挂 ⚠️;每段标来源。只放中立事实,判断进 assessment。\n"
    "仅对确实无关的部分不动。"
)

DEFAULT_ASSESSMENT_FOLD = (
    "沉淀立场与判断,引用上游 understanding 的事实(标来源);随新材料演进、可推翻旧判断。\n"
    "不与 understanding 的中立事实混。"
)


class DigestConfig(BaseModel):
    prompt: str = DEFAULT_DIGEST_PROMPT


class Pipeline(BaseModel):
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


class Constitution(BaseModel):
    topic: str = "main"
    pipeline: Pipeline = Field(default_factory=Pipeline)
    targets: list[Target] = Field(default_factory=_default_targets)


# ---- reference manifest (references/<id>/manifest.yaml) ----


class Form(BaseModel):
    role: str  # audio | transcript | note | source_text
    location: str
    hash: str
    origin: str = "added"


class Manifest(BaseModel):
    id: str
    title: str = ""
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
    status: str = "ok"  # ok | blocked
    reason: str | None = None  # manual-edit | …


class State(BaseModel):
    products: dict[str, ProductState] = Field(default_factory=dict)
    targets: dict[str, TargetState] = Field(default_factory=dict)
