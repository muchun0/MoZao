# -*- coding: utf-8 -*-
"""L2 维度评委公共框架（TECH §6.1）。

职责：
- 统一上下文 `ReviewContext`：checker 把显式参数传给评委，评委不反向访问 checker
  内部可变状态（§11.10：禁止嵌套函数 locals() 取状态）；
- 统一结果类型 `DimensionResult` / `ReviewerResult` / `ReviewerIssue`；
- `BaseReviewer` 模板方法：`review()` 包裹 `_call_llm()` + `_parse()` + `_ok()`，
  任何异常 → 维度 `available=False, score=None`，不阻塞其他评委与主链路（I-5）；
- 级别 closed enum 归一化、证据纪律校验（无证据问题不输出，TECH §6.6）。

评委只评自己的维度，不跨维度下结论（MTS 防串味）；temperature 由各评委在
`_call_llm` 内按现状口径设置（reader 0.3 / consistency 0.2 / prose 沿用 ai_taste）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ....llm.base import LLMClient
from ....store.project import ProjectStore
from ....config import NovelConfig

logger = logging.getLogger("novel_gen.pipeline.quality")

#: 合法级别闭集（TECH §6.6：level 只能取这三档）
VALID_LEVELS = ("大改", "中改", "小改")


def normalize_level(raw: Any, default: str = "小改") -> str:
    """非法/缺失级别归一到闭集；未知级别回落 `default`（默认小改）。"""
    lv = str(raw or "").strip()
    return lv if lv in VALID_LEVELS else default


@dataclass
class ReviewerIssue:
    """单个评委问题（L2 标准形态，L3 再转对外扁平 dict）。"""
    type: str
    detail: str
    level: str = "小改"
    evidence: str = ""
    pos: str = ""
    suggestion: str = ""
    fix_suggestion: str = ""
    expected_gain: int = 0

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "detail": self.detail,
            "level": self.level,
            "evidence": self.evidence,
            "pos": self.pos,
            "suggestion": self.suggestion,
            "fix_suggestion": self.fix_suggestion,
            "expected_gain": self.expected_gain,
        }


@dataclass
class DimensionResult:
    """单个维度的评审快照；`score=None` 表示该维度不可用（失败/开关关闭/降频跳过）。"""
    key: str
    label: str
    score: float | None = None
    available: bool = True
    issues: list[ReviewerIssue] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    note: str = ""
    reviewer: str = ""
    #: 专项附加信号（hook_score / ai_signals / missing_core_events 等），不进统一 schema
    extra: dict = field(default_factory=dict)


@dataclass
class ReviewerResult:
    dimension: DimensionResult
    raw_issues: list[ReviewerIssue] = field(default_factory=list)


@dataclass
class ReviewContext:
    """评委评审所需的显式上下文（由 checker 组装，不携带 checker 内部可变状态）。"""
    chapter_no: int
    plan_title: str
    plan_goal: str
    content: str
    summary: str = ""
    core_event: str = ""
    baseline: dict | None = None
    cfg: NovelConfig | None = None
    llm: LLMClient | None = None
    store: ProjectStore | None = None
    guard: Any = None
    memory: Any = None
    #: 本章大纲/节拍快照（只读引用，供 plot 评委；不新算）
    beat_gate: dict | None = None


class ReviewerProtocol(Protocol):
    key: str
    label: str

    def review(self, ctx: ReviewContext) -> ReviewerResult: ...


class BaseReviewer:
    """评委模板基类：子类实现 `_call_llm` 与 `_parse`，`review` 负责降级兜底。"""

    key: str = ""
    label: str = ""

    def review(self, ctx: ReviewContext) -> ReviewerResult:
        """统一入口：开关关闭 / 异常 → unavailable 占位结果，绝不向外抛（I-5）。"""
        if not self._enabled(ctx):
            return ReviewerResult(
                DimensionResult(key=self.key, label=self.label, available=False,
                                score=None, note="开关关闭", reviewer=self.key))
        try:
            raw = self._call_llm(ctx)
            parsed = self._parse(raw)
            return self._ok(parsed)
        except Exception as exc:  # noqa: BLE001 —— 评委失败必须降级，不阻塞主链路
            logger.exception("reviewer %s 评审失败，标记 unavailable（ch=%s）",
                             self.key, ctx.chapter_no)
            return ReviewerResult(
                DimensionResult(key=self.key, label=self.label, available=False,
                                score=None, note="评审失败已降级", reviewer=self.key))

    # ---- 子类实现 ----
    def _enabled(self, ctx: ReviewContext) -> bool:
        """开关/降频判定；默认开启。子类按自身开关覆写。"""
        return True

    def _call_llm(self, ctx: ReviewContext) -> Any:
        """发起 LLM 调用，返回原始文本（或已解析对象）。子类必须实现。"""
        raise NotImplementedError

    def _parse(self, raw: Any) -> dict:
        """把原始返回解析为标准 dict（score/issues/strengths/...）。子类必须实现。"""
        raise NotImplementedError

    def _ok(self, parsed: dict) -> ReviewerResult:
        """解析成功后组装 ReviewerResult。子类可覆写以填充专项字段。"""
        issues = [
            ReviewerIssue(
                type=str(it.get("type", "")),
                detail=str(it.get("detail", "")),
                level=normalize_level(it.get("level")),
                evidence=str(it.get("evidence", "") or ""),
                pos=str(it.get("pos", "") or ""),
                suggestion=str(it.get("suggestion", "") or ""),
                fix_suggestion=str(it.get("fix_suggestion") or it.get("suggestion") or ""),
                expected_gain=int(it.get("expected_gain") or 0),
            )
            for it in (parsed.get("issues") or []) if isinstance(it, dict)
        ]
        score = parsed.get("score")
        score_val: float | None = None
        if isinstance(score, (int, float)):
            score_val = max(0.0, min(100.0, float(score)))
        dim = DimensionResult(
            key=self.key, label=self.label, score=score_val, available=True,
            issues=issues, strengths=[str(s) for s in (parsed.get("strengths") or [])],
            reviewer=self.key,
        )
        return ReviewerResult(dimension=dim, raw_issues=issues)
