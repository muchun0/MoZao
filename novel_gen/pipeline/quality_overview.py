# -*- coding: utf-8 -*-
"""章节质量总览（R12，只读、零 LLM、零写盘）。

数据来源：`chapter_versions.json` 中每章「当前选中版本」的 `quality`
（与版本弹窗完全同源）。只读一次索引 + 一次章节目录 glob，
不逐章加载版本存档或正文，避免「每章读全部版本」的性能问题。

约定：
- 严重度直接复用 R2 体检口径（`LEVEL_SEVERITY` / `SEVERITY_RANK`），
  作者可见等级词只有 大改 / 中改 / 小改 / 无问题 / 未质检；
- 没有质检记录的章返回 `has_quality=False`，不伪造 0 分；
- `rank` 由后端统一计算（1 = 最需要先修），前端不另造排序口径；
- 任何读取异常都降级为空结果 + `data_gaps` 说明，不抛 5xx。
"""
from __future__ import annotations

from .consistency_report import (
    LEVEL_SEVERITY, SEVERITY_RANK, _selected_quality, chapter_numbers,
)

#: 严重度 → 作者可见等级词（与 R2 体检页 / 版本弹窗一致）
SEVERITY_LABELS = {
    "high": "大改", "medium": "中改", "low": "小改",
    "info": "无问题", "none": "未质检",
}

#: 单章最多返回的问题类型数（控制响应体）
MAX_TOP_ISSUES = 3

#: 单条问题示例描述的截断长度
DETAIL_CLIP = 80


def _clip(text: str, limit: int = DETAIL_CLIP) -> str:
    """压缩空白并截断长描述，避免把整段原文塞进列表接口。"""
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[:limit] + "…"


def _score(value) -> int | None:
    """质检分转整数；缺失/非法一律 None（不拿 0 冒充低质量）。"""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _issue_counts(issues: list[dict]) -> dict:
    """按 R2 严重度分桶统计大改/中改/小改（未知级别不计入，但仍计入总数）。"""
    counts = {"high": 0, "medium": 0, "low": 0}
    for it in issues:
        sev = LEVEL_SEVERITY.get(str(it.get("level") or ""))
        if sev:
            counts[sev] += 1
    return counts


def _top_issues(issues: list[dict], limit: int = MAX_TOP_ISSUES) -> list[dict]:
    """按问题类型聚合（取最高严重度 + 条数），让作者知道「差在哪类问题」。"""
    groups: dict[str, dict] = {}
    for it in issues:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("type") or "").strip() or "未标注问题类型"
        sev = LEVEL_SEVERITY.get(str(it.get("level") or ""), "low")
        group = groups.get(kind)
        if group is None:
            groups[kind] = {
                "type": kind, "severity": sev, "level": SEVERITY_LABELS[sev],
                "count": 1, "detail": _clip(it.get("detail")),
            }
        else:
            group["count"] += 1
            # 同类问题取最严重的一条作为示例
            if SEVERITY_RANK.get(sev, 0) > SEVERITY_RANK.get(group["severity"], 0):
                group["severity"] = sev
                group["level"] = SEVERITY_LABELS[sev]
                group["detail"] = _clip(it.get("detail"))
    items = sorted(groups.values(),
                   key=lambda x: (-SEVERITY_RANK.get(x["severity"], 0), -x["count"], x["type"]))
    return items[:limit]


def _row(no: int, rec) -> dict:
    """组装单章质量摘要（不含标题/字数，由调用方与章节列表合并）。"""
    quality = _selected_quality(rec)
    has_quality = bool(quality)
    issues = quality.get("issues") if isinstance(quality.get("issues"), list) else []
    counts = _issue_counts(issues)
    recorded = quality.get("issue_count")
    issue_count = len(issues)
    if isinstance(recorded, int) and recorded >= 0:
        issue_count = max(issue_count, recorded)
    if not has_quality:
        severity = "none"
    elif counts["high"]:
        severity = "high"
    elif counts["medium"]:
        severity = "medium"
    elif counts["low"]:
        severity = "low"
    else:
        severity = "info"
    passed = quality.get("passed") if isinstance(quality.get("passed"), bool) else None
    return {
        "no": no,
        "selected_version": rec.get("selected") if isinstance(rec, dict) else None,
        "has_quality": has_quality,
        "score": _score(quality.get("score")),
        "passed": passed,
        "issue_count": issue_count,
        "severity": severity,
        "severity_label": SEVERITY_LABELS[severity],
        # 「需要处理」= 质检未通过或存在大改；未质检章不进入此列
        "needs_attention": bool(has_quality and (passed is False or counts["high"] > 0)),
        "counts": counts,
        "top_issues": _top_issues(issues),
        "rank": None,
    }


def _sort_key(row: dict) -> tuple:
    """最差优先：大改优先 → 大改数 → 分数低 → 中改数 → 小改数 → 章号；未质检排最后。

    分数只作为同等大改数下的次序依据：实测存在「分数 100 但仍有多处大改」的记录
    （score 为独立评分维度、passed 才是门禁），因此不能让分数压过严重度等级。
    """
    return (
        -SEVERITY_RANK.get(row["severity"], -1),
        -row["counts"]["high"],
        row["score"] if row["score"] is not None else 101,
        -row["counts"]["medium"],
        -row["counts"]["low"],
        row["no"],
    )


def build_quality_overview(store) -> dict:
    """构建章节质量总览（只读）。返回 chapters 按章号升序 + rank 最差优先序号。"""
    try:
        numbers = chapter_numbers(store)
    except Exception:
        numbers = []
    data_gaps: list[str] = []
    index: dict = {}
    try:
        index = store.load_chapter_versions() or {}
    except Exception:
        data_gaps.append("读取章节版本索引失败，本次已降级为无质检数据")
        index = {}

    rows = [_row(no, index.get(str(no)) or {}) for no in numbers]
    scored = sorted((r for r in rows if r["has_quality"]), key=_sort_key)
    for i, row in enumerate(scored, start=1):
        row["rank"] = i
    scored_count = len(scored)
    unscored_count = len(rows) - scored_count

    if not numbers:
        data_gaps.append("还没有章节正文，暂无质量可比较")
    elif not scored:
        data_gaps.append("本书暂无章节质检记录（历史章可能生成于质检上线前），无法比较章节质量")
    elif unscored_count:
        data_gaps.append(
            f"{unscored_count} 章没有质检记录（历史章可能生成于质检上线前），未参与最差比较")

    worst = None
    if scored:
        head = scored[0]
        worst = {
            "no": head["no"], "score": head["score"], "severity": head["severity"],
            "severity_label": head["severity_label"], "issue_count": head["issue_count"],
            "counts": head["counts"],
        }
    return {
        "chapter_count": len(rows),
        "scored_count": scored_count,
        "unscored_count": unscored_count,
        "worst": worst,
        "chapters": rows,
        "data_gaps": data_gaps,
    }
