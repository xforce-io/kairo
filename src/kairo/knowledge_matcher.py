"""可替换的知识匹配器契约及首版 Aho-Corasick 实现。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from kairo.knowledge import KnowledgeEntry, normalize_term, semantic_hash


GENERIC_TERMS = frozenset({"系统", "平台", "服务", "数据", "项目"})
_CACHE: dict[str, "KnowledgeMatcher"] = {}


@dataclass(frozen=True)
class MatchBudget:
    max_entries: int = 8
    max_chars: int = 1800


@dataclass(frozen=True)
class KnowledgeMatch:
    entry: KnowledgeEntry
    normalized_term: str
    display_term: str
    is_title: bool
    start: int
    end: int

    @property
    def term(self) -> str:
        """兼容旧调用方；展示与 renderer 始终使用原始词。"""
        return self.display_term


@dataclass(frozen=True)
class MatchResult:
    matches: tuple[KnowledgeMatch, ...] = ()
    ambiguities: tuple[str, ...] = ()
    skipped_terms: tuple[str, ...] = ()
    truncated_count: int = 0
    version: str = ""


@dataclass
class _Node:
    children: dict[str, int] = field(default_factory=dict)
    fail: int = 0
    terms: list[str] = field(default_factory=list)


def _eligible(term: str) -> bool:
    if term in GENERIC_TERMS:
        return False
    ascii_count = sum(ch.isascii() and ch.isalnum() for ch in term)
    cjk_count = sum("\u4e00" <= ch <= "\u9fff" for ch in term)
    # Pure ASCII/number needs 3; CJK needs 2. Mixed words need either threshold.
    return ascii_count >= 3 or cjk_count >= 2


def _ascii_boundary(text: str, start: int, end: int, term: str) -> bool:
    if not any(ch.isascii() and ch.isalnum() for ch in term):
        return True
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    return not ((before.isascii() and (before.isalnum() or before == "_")) or (after.isascii() and (after.isalnum() or after == "_")))


class KnowledgeMatcher:
    """业务层依赖的稳定匹配语义，不泄露 AC 节点。"""

    def __init__(self, entries: list[KnowledgeEntry], *, semantic_version: str | None = None):
        self._entries: dict[str, KnowledgeEntry] = {}
        self._ownership: dict[str, list[tuple[str, bool]]] = {}
        self._terms: dict[str, list[tuple[str, bool]]] = {}
        self._nodes: list[_Node] = [_Node()]
        self.version = ""
        self._replace(entries, semantic_version)

    @property
    def entries(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(entry.model_copy(deep=True) for entry in self._entries.values())

    def _replace(self, entries: list[KnowledgeEntry], semantic_version: str | None = None) -> str:
        # 先在局部变量建立不可变快照，构建完成后一次性替换缓存。
        confirmed = {entry.id: entry.model_copy(deep=True) for entry in entries if entry.status == "confirmed"}
        ownership: dict[str, list[tuple[str, bool, str]]] = {}
        eligible: dict[str, list[tuple[str, bool, str]]] = {}
        for entry in confirmed.values():
            title = normalize_term(entry.title)
            ownership.setdefault(title, []).append((entry.id, True, entry.title))
            if _eligible(title):
                eligible.setdefault(title, []).append((entry.id, True, entry.title))
            for alias in entry.aliases:
                term = normalize_term(alias.value)
                ownership.setdefault(term, []).append((entry.id, False, alias.value))
                if alias.auto_match and _eligible(term):
                    eligible.setdefault(term, []).append((entry.id, False, alias.value))
        nodes = [_Node()]
        for term in eligible:
            state = 0
            for char in term:
                state = nodes[state].children.setdefault(char, len(nodes))
                if state == len(nodes):
                    nodes.append(_Node())
            nodes[state].terms.append(term)
        queue: deque[int] = deque()
        for state in nodes[0].children.values():
            queue.append(state)
        while queue:
            parent = queue.popleft()
            for char, state in nodes[parent].children.items():
                queue.append(state)
                failure = nodes[parent].fail
                while failure and char not in nodes[failure].children:
                    failure = nodes[failure].fail
                nodes[state].fail = nodes[failure].children.get(char, 0)
                nodes[state].terms.extend(nodes[nodes[state].fail].terms)
        self._entries, self._ownership, self._terms, self._nodes = confirmed, ownership, eligible, nodes
        self.version = semantic_version or semantic_hash(entries)
        return self.version

    def refresh(self, entries: list[KnowledgeEntry], semantic_version: str | None = None) -> "KnowledgeMatcher":
        """发布新快照，而非修改已被正在运行任务持有的 matcher。"""
        return KnowledgeMatcher(entries, semantic_version=semantic_version)

    def suggest(self, terms: list[str]) -> dict[str, str]:
        """候选去重/合并建议复用同一归一化与歧义视图。"""
        answer: dict[str, str] = {}
        for raw in terms:
            term = normalize_term(raw)
            owners = {entry_id for entry_id, _, _ in self._ownership.get(term, [])}
            answer[raw] = "unknown" if not owners else "ambiguous" if len(owners) > 1 else f"merge:{next(iter(owners))}"
        return answer

    def match(self, text: str, *, scope: str | None = None, budget: MatchBudget = MatchBudget()) -> MatchResult:
        normalized = normalize_term(text)
        state = 0
        raw: list[KnowledgeMatch] = []
        ambiguities: set[str] = set()
        for index, char in enumerate(normalized):
            while state and char not in self._nodes[state].children:
                state = self._nodes[state].fail
            state = self._nodes[state].children.get(char, 0)
            for term in self._nodes[state].terms:
                start = index + 1 - len(term)
                end = index + 1
                if not _ascii_boundary(normalized, start, end, term):
                    continue
                # scope 是业务契约的一部分：先裁剪 owner，再判断是否真歧义。
                owners = [
                    owner for owner in self._terms[term]
                    if scope is None or self._entries[owner[0]].scope == scope
                ]
                if not owners:
                    continue
                unique = {entry_id for entry_id, _, _ in owners}
                if len(unique) != 1:
                    ambiguities.add(term)
                    continue
                entry_id, is_title, display = owners[0]
                entry = self._entries[entry_id]
                raw.append(KnowledgeMatch(entry.model_copy(deep=True), term, display, is_title, start, end))
        # Keep an entry once, choosing its best deterministic match.
        best: dict[str, KnowledgeMatch] = {}
        for hit in raw:
            prior = best.get(hit.entry.id)
            if prior is None or (hit.is_title, len(hit.normalized_term), -hit.start) > (prior.is_title, len(prior.normalized_term), -prior.start):
                best[hit.entry.id] = hit
        ordered = sorted(
            best.values(),
            key=lambda hit: (
                0 if hit.entry.scope == "workspace" else 1,
                0 if hit.is_title else 1,
                -len(hit.normalized_term),
                normalize_term(hit.entry.title),
                hit.entry.id,
            ),
        )
        selected: list[KnowledgeMatch] = []
        used = 0
        # 预算按最终序列化片段计费（固定头也计入），稳定排序后的前缀一旦放不下即截断。
        used = len(_render_context(()))
        for hit in ordered:
            rendered = _render_context((hit,))[len(_render_context(())):]
            if len(selected) >= budget.max_entries or used + len(rendered) > budget.max_chars:
                break
            selected.append(hit)
            used += len(rendered)
        return MatchResult(
            matches=tuple(selected),
            ambiguities=tuple(sorted(ambiguities)),
            skipped_terms=tuple(sorted(
                term for term, owners in self._ownership.items()
                if term not in self._terms and (scope is None or any(self._entries[owner[0]].scope == scope for owner in owners))
            )),
            truncated_count=len(ordered) - len(selected),
            version=self.version,
        )


def format_knowledge_context(result: MatchResult) -> str:
    return _render_context(result.matches) if result.matches else ""


def _render_context(matches: tuple[KnowledgeMatch, ...]) -> str:
    """唯一的上下文 renderer；预算与最终输出必须逐字符相同。"""
    return _context_header() + "".join(_context_line(hit) for hit in matches)


def _context_header() -> str:
    return "\n\n[领域知识上下文]\n以下条目仅作参考，不能替代本次材料证据；冲突时保留材料说法并标明待核。\n"


def _context_line(hit: KnowledgeMatch) -> str:
    entry = hit.entry
    source = "、".join(item.path for item in entry.sources) if entry.sources else "无出处"
    return f"- {entry.title}（{entry.scope}；命中：{hit.term}；出处：{source}）：{entry.description}".rstrip("：") + "\n"


def matcher_for(entries: list[KnowledgeEntry]) -> KnowledgeMatcher:
    """按语义版本复用不可变索引；调用方只持有返回 snapshot。"""
    version = semantic_hash(entries)
    matcher = _CACHE.get(version)
    if matcher is None:
        matcher = KnowledgeMatcher(entries, semantic_version=version)
        _CACHE.clear()
        _CACHE[version] = matcher
    return matcher
