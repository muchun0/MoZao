# -*- coding: utf-8 -*-
"""
问题分级（issue_triage）：多Agent校验的通用问题模型与分级工具。

REQ-多Agent协作分层优化：逻辑校验 Agent 输出的问题清单统一为 IssueItem，
按 severity（fatal 致命 / major 严重 / minor 一般）分级，供 StageGate 阶段门禁
与最终质量报告复用。纯函数、无 Web 依赖、可复现单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field


#: 严重级别权重（数值越小越严重，用于排序）
SEVERITY_ORDER = {"fatal": 0, "major": 1, "minor": 2}


@dataclass
class IssueItem:
    """一条校验问题。"""
    severity: str            # fatal / major / minor
    scope: str               # goldfinger / theme / foreshadow / mainline / duplicate / volume / pacing / ooc ...
    desc: str                # 问题描述
    suggestion: str = ""     # 整改建议
    chapter_no: int | None = None  # 可选：涉及章节

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_ORDER:
            self.severity = "major"

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "scope": self.scope,
            "desc": self.desc,
            "suggestion": self.suggestion,
            "chapter_no": self.chapter_no,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IssueItem":
        return cls(
            severity=str(d.get("severity", "major")),
            scope=str(d.get("scope", "")),
            desc=str(d.get("desc", "")),
            suggestion=str(d.get("suggestion", "")),
            chapter_no=d.get("chapter_no"),
        )

    def __str__(self) -> str:
        tag = {"fatal": "致命", "major": "严重", "minor": "一般"}.get(self.severity, self.severity)
        return f"[{tag}] {self.scope}: {self.desc}"


def severity_key(issue: IssueItem) -> int:
    """按严重级别排序键（fatal 最前）。"""
    return SEVERITY_ORDER.get(issue.severity, 2)


def triage(issues: list[IssueItem]) -> dict[str, list[IssueItem]]:
    """把问题清单分级：{"fatal": [...], "major": [...], "minor": [...]}，各级内部保持输入顺序。"""
    out = {"fatal": [], "major": [], "minor": []}
    for it in issues:
        out.setdefault(it.severity, []).append(it)
    return out


def has_fatal(issues: list[IssueItem]) -> bool:
    """是否存在致命问题（StageGate 打回依据）。"""
    return any(it.severity == "fatal" for it in issues)


def fatal_count(issues: list[IssueItem]) -> int:
    return sum(1 for it in issues if it.severity == "fatal")


def sort_issues(issues: list[IssueItem]) -> list[IssueItem]:
    """按 severity 排序（fatal → major → minor），同级保持原顺序。"""
    return sorted(issues, key=lambda it: SEVERITY_ORDER.get(it.severity, 2))


def issues_to_dicts(issues: list[IssueItem]) -> list[dict]:
    return [it.to_dict() for it in issues]


def issues_from_dicts(items: list[dict]) -> list[IssueItem]:
    return [IssueItem.from_dict(d) for d in items if isinstance(d, dict)]
