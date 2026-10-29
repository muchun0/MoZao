# -*- coding: utf-8 -*-
"""L3 聚合器（TECH §7）：合并 L1 确定性问题与 L2 评委结果，去重、归类、排序、
生成改稿建议清单与维度快照，并沿用旧倒扣口径出门禁结论。

行为约束（D-1 / I-1）：
- 旧 `score` 倒扣口径与门禁判定本期不变；`dimensions.*.score` 仅作附加信号，不接管总分；
- 对外 issue dict 字段只增不删（§4.3）；旧 `reviewer_layered=False` 路径走 `finalize_legacy`；
- 诊断回喂（diag_feedback）口径与旧 `_finalize_issues` 逐字一致。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import constants as C
from .reviewers.base import DimensionResult, ReviewerResult

logger = logging.getLogger("novel_gen.pipeline.quality")

#: 级别排序权重（大改在前）
_LEVEL_ORDER = {"大改": 0, "中改": 1, "小改": 2}


# ---------- 维度归类（TECH §5.2：L1 规则 → 维度；有序匹配，先命中先归） ----------

#: (type 关键词前缀, 维度)；顺序即优先级，合规优先单独归类。
_L1_DIMENSION_RULES: list[tuple[str, str]] = [
    ("合规-", "compliance"),
    ("结构-字数", "reader"),
    ("对话轮重复", "reader"),
    ("结尾完整性", "plot"),
    ("情节-大纲核心事件", "plot"),
    ("情节-同事件循环重演", "plot"),
    ("章内大量自重复", "consistency"),
    ("内容重复", "consistency"),
    ("自相矛盾", "consistency"),
    ("规则一致性", "consistency"),
    ("机制数字", "consistency"),
    ("机制字段", "consistency"),
    ("摘要一致性", "consistency"),
    ("章内同句复读", "prose"),
    ("AI痕迹", "prose"),
    ("文风漂移", "prose"),
    ("文风同质化", "prose"),
]


def classify_dimension(issue: dict, reviewer_key: str = "") -> str:
    """把一条 issue 归到四维度之一（compliance 单列）。L2 issue 优先用评委 key。"""
    if reviewer_key:
        return reviewer_key
    t = str(issue.get("type") or "")
    for prefix, dim in _L1_DIMENSION_RULES:
        if t.startswith(prefix) or prefix in t:
            return dim
    return "reader"


def _norm_evidence(text: str) -> str:
    """证据归一化：去标点空白，供跨层重复合并判定（TECH §7.1-2）。"""
    return re.sub(r"[\s，。！？、；：“”\"‘’「」（）()…—\-]", "", str(text or ""))


def _diagnose(issues: list[dict], diag_feedback: bool) -> None:
    """诊断回喂：确定性维度补 fix_suggestion / expected_gain；LLM 已带建议不覆盖。

    与旧 `_finalize_issues` 口径逐字一致（失败静默，不阻塞）。
    """
    if not diag_feedback:
        return
    try:
        from ..diagnosis import suggest_for as _sf
        for it in issues:
            if it.get("fix_suggestion"):
                continue
            fb = _sf(it)
            if fb:
                it["fix_suggestion"] = fb.get("fix_suggestion", "")
                if fb.get("expected_gain"):
                    it["expected_gain"] = fb.get("expected_gain")
    except Exception:
        logger.warning("[quality] 诊断回喂失败（降级跳过，不阻塞）", exc_info=True)


@dataclass
class AggregateResult:
    """L3 聚合产物；`dimensions` / `recommendations` / `layers` 为本期新增附加字段。"""
    issues: list[dict]
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    recommendations: list[dict] = field(default_factory=list)
    score: int = 100
    passed: bool = True
    hook_score: int | None = None
    reader_score: float | None = None
    compliance_rejected: bool = False
    has_major: bool = False
    layers: dict = field(default_factory=dict)


def finalize_legacy(issues: list[dict], score: int, diag_feedback: bool = True
                    ) -> tuple[bool, list[dict], int]:
    """旧口径收尾（行为零变化）：(type, detail) 去重 → 级别排序 → 门禁 → 诊断回喂。

    供 `reviewer_layered=False` 紧急回退路径与旧 `_finalize_issues` 委托使用。
    """
    seen, uniq = set(), []
    for it in issues:
        k = (it.get("type"), it.get("detail"))
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    uniq.sort(key=lambda x: _LEVEL_ORDER.get(x.get("level"), 2))
    passed = not any(x.get("level") == "大改" for x in uniq)
    _diagnose(uniq, diag_feedback)
    return passed, uniq, score


def _merge_cross_layer(issues: list[dict]) -> list[dict]:
    """跨层重复合并：归一化证据相同 → 合并一条，级别取高，source 列表保留。

    合并主键 = 归一化证据；证据为空时退化为 (type, detail) 精确去重（与旧口径一致）。
    """
    merged: list[dict] = []
    index: dict[str, int] = {}
    for it in issues:
        ev = _norm_evidence(it.get("evidence"))
        key = ev or f"{it.get('type', '')}|{it.get('detail', '')}"
        if ev and key in index:
            existing = merged[index[key]]
            # 级别取高
            if _LEVEL_ORDER.get(it.get("level"), 2) < _LEVEL_ORDER.get(existing.get("level"), 2):
                existing["level"] = it.get("level")
            # source 列表保留两层来源
            srcs = existing.setdefault("sources", [existing.get("source", "deterministic")])
            new_src = it.get("source", "deterministic")
            if new_src not in srcs:
                srcs.append(new_src)
            continue
        index[key] = len(merged)
        merged.append(dict(it))
    return merged


def _build_recommendations(issues: list[dict]) -> list[dict]:
    """按 级别 → 维度权重 → expected_gain 生成优先级改稿建议清单（TECH §7.3）。"""
    ranked = sorted(
        issues,
        key=lambda x: (
            -C.LEVEL_RANK.get(x.get("level"), 0),
            -C.REVIEW_DIMENSION_WEIGHTS.get(x.get("dimension") or classify_dimension(x), 0.0),
            -(x.get("expected_gain") or 0),
        ),
    )
    recs: list[dict] = []
    for i, it in enumerate(ranked[:C.RECOMMENDATION_LIMIT], start=1):
        recs.append({
            "dimension": it.get("dimension") or classify_dimension(it),
            "type": it.get("type", ""),
            "problem": it.get("detail", ""),
            "fix_suggestion": it.get("fix_suggestion", ""),
            "expected_gain": it.get("expected_gain", 0),
            "evidence": it.get("evidence", ""),
            "source": it.get("source", "deterministic"),
            "rule_id": it.get("rule_id", ""),
            "priority": i,
        })
    return recs


def dimension_to_dict(dim: DimensionResult) -> dict:
    """DimensionResult → 对外附加字段 dict（§4.4 dimensions 快照）。"""
    return {
        "label": dim.label,
        "score": dim.score,
        "available": dim.available,
        "issues": [ri.to_dict() for ri in dim.issues],
        "strengths": list(dim.strengths),
        "note": dim.note,
        "extra": dict(dim.extra),
    }


def aggregate(l1_issues: list[dict],
              reviewer_results: list[ReviewerResult],
              score: int,
              diag_feedback: bool = True,
              hook_score: int | None = None,
              reader_score: float | None = None,
              compliance_rejected: bool = False) -> AggregateResult:
    """L3 主流程：合并两层 issue → 去重合并 → 归类维度 → 排序 → 诊断回喂 → 门禁与快照。

    L1 issue 由调用方先带好 `dimension/rule_id/evidence/source`；L2 issue 从
    ReviewerResult 展开并标注 `source=llm:<key>`。
    """
    # 1) 收集 L2 issue 并打标
    dimensions: dict[str, DimensionResult] = {}
    llm_issues: list[dict] = []
    available_dims: list[str] = []
    failed_dims: list[str] = []
    for rr in reviewer_results:
        dim = rr.dimension
        dimensions[dim.key] = dim
        if dim.available:
            available_dims.append(dim.key)
            for ri in rr.raw_issues:
                d = ri.to_dict()
                d["dimension"] = dim.key
                d["source"] = f"llm:{dim.key}"
                llm_issues.append(d)
        else:
            failed_dims.append(dim.key)

    # 2) 合并 L1 + L2，跨层证据去重合并
    all_issues = list(l1_issues) + llm_issues
    all_issues = _merge_cross_layer(all_issues)

    # 3) 维度归类（L1 兜底归类；L2 已带 key）
    for it in all_issues:
        it.setdefault("dimension", classify_dimension(it))

    # 4) 级别排序（大改在前；同级稳定保持原顺序）
    all_issues.sort(key=lambda x: _LEVEL_ORDER.get(x.get("level"), 2))

    # 5) 诊断回喂
    _diagnose(all_issues, diag_feedback)

    # 6) 门禁（本期口径不变：无大改即 passed；compliance 透传）
    has_major = any(x.get("level") == "大改" for x in all_issues)
    passed = not has_major

    # 7) 建议清单
    recommendations = _build_recommendations(all_issues)

    # 8) 层统计
    layers = {
        "deterministic": {"issue_count": len(l1_issues)},
        "llm": {"available_dimensions": available_dims, "failed_dimensions": failed_dims},
    }

    return AggregateResult(
        issues=all_issues,
        dimensions=dimensions,
        recommendations=recommendations,
        score=score,
        passed=passed,
        hook_score=hook_score,
        reader_score=reader_score,
        compliance_rejected=compliance_rejected,
        has_major=has_major,
        layers=layers,
    )
