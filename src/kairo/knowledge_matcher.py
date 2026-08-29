"""可替换的知识匹配器契约及首版 Aho-Corasick 实现。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from kairo.knowledge import KnowledgeEntry, normalize_term, semantic_hash


GENERIC_TERMS = frozenset({"系统", "平台", "服务", "数据", "项目"})


@dataclass(frozen=True)
class MatchBudget:
    max_entries: int = 8
    max_chars: int = 1800


@dataclass(frozen=True)
class KnowledgeMatch:
    entry: KnowledgeEntry
    term: str
    is_title: bool
    start: int
    end: int


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

    def __init__(self, entries: list[KnowledgeEntry]):
        self._entries: dict[str, KnowledgeEntry] = {}
        self._terms: dict[str, list[tuple[str, bool]]] = {}
        self._nodes: list[_Node] = [_Node()]
        self.version = ""
        self.refresh(entries)

    @property
    def entries(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(self._entries.values())

    def refresh(self, entries: list[KnowledgeEntry], semantic_version: str | None = None) -> str:
        self._entries = {entry.id: entry for entry in entries if entry.status == "confirmed"}
        terms: dict[str, list[tuple[str, bool]]] = {}
        for entry in self._entries.values():
            title = normalize_term(entry.title)
            if _eligible(title):
                terms.setdefault(title, []).append((entry.id, True))
            for alias in entry.aliases:
                term = normalize_term(alias.value)
                if alias.auto_match and _eligible(term):
                    terms.setdefault(term, []).append((entry.id, False))
        self._terms = terms
        self._nodes = [_Node()]
        for term in terms:
            state = 0
            for char in term:
                state = self._nodes[state].children.setdefault(char, len(self._nodes))
                if state == len(self._nodes):
                    self._nodes.append(_Node())
            self._nodes[state].terms.append(term)
        queue: deque[int] = deque()
        for state in self._nodes[0].children.values():
            queue.append(state)
        while queue:
            parent = queue.popleft()
            for char, state in self._nodes[parent].children.items():
                queue.append(state)
                failure = self._nodes[parent].fail
                while failure and char not in self._nodes[failure].children:
                    failure = self._nodes[failure].fail
                self._nodes[state].fail = self._nodes[failure].children.get(char, 0)
                self._nodes[state].terms.extend(self._nodes[self._nodes[state].fail].terms)
        self.version = semantic_version or semantic_hash(entries)
        return self.version

    def suggest(self, terms: list[str]) -> dict[str, str]:
        """候选去重/合并建议复用同一归一化与歧义视图。"""
        answer: dict[str, str] = {}
        for raw in terms:
            term = normalize_term(raw)
            owners = {entry_id for entry_id, _ in self._terms.get(term, [])}
            answer[raw] = "unknown" if not owners else "ambiguous" if len(owners) > 1 else "known"
        return answer

    def match(self, text: str, *, budget: MatchBudget = MatchBudget()) -> MatchResult:
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
                owners = self._terms[term]
                unique = {entry_id for entry_id, _ in owners}
                if len(unique) != 1:
                    ambiguities.add(term)
                    continue
                entry_id, is_title = owners[0]
                raw.append(KnowledgeMatch(self._entries[entry_id], term, is_title, start, end))
        # Keep an entry once, choosing its best deterministic match.
        best: dict[str, KnowledgeMatch] = {}
        for hit in raw:
            prior = best.get(hit.entry.id)
            if prior is None or (hit.is_title, len(hit.term), -hit.start) > (prior.is_title, len(prior.term), -prior.start):
                best[hit.entry.id] = hit
        ordered = sorted(
            best.values(),
            key=lambda hit: (
                0 if hit.entry.scope == "workspace" else 1,
                0 if hit.is_title else 1,
                -len(hit.term),
                hit.entry.title,
                hit.entry.id,
            ),
        )
        selected: list[KnowledgeMatch] = []
        used = 0
        for hit in ordered:
            rendered = f"{hit.entry.title}\n{hit.entry.description}".strip()
            if len(selected) >= budget.max_entries or used + len(rendered) > budget.max_chars:
                continue
            selected.append(hit)
            used += len(rendered)
        return MatchResult(
            matches=tuple(selected),
            ambiguities=tuple(sorted(ambiguities)),
            skipped_terms=tuple(sorted(term for term in self._terms if not _eligible(term))),
            truncated_count=len(ordered) - len(selected),
            version=self.version,
        )


def format_knowledge_context(result: MatchResult) -> str:
    if not result.matches:
        return ""
    lines = [
        "\n\n[领域知识上下文]",
        "以下条目仅作参考，不能替代本次材料证据；冲突时保留材料说法并标明待核。",
    ]
    for hit in result.matches:
        entry = hit.entry
        lines.append(f"- {entry.title}（{entry.scope}）：{entry.description}".rstrip("："))
    return "\n".join(lines) + "\n"
