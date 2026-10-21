# -*- coding: utf-8 -*-
"""小墨AI 确定性卡片（零 LLM）：质检卡 / 问题清单 / 版本对比 / 正向评估。

口径红线（REQ §5.2.1 + §5.2.2）：
  - review.json 的 score 只表述为「质检扣分（缺陷分）」，不给"质量分"；
  - 正向指标只给「实测值 vs 阈值 + 达标/未达标」，不合成任何评分；
  - 阈值一律引用 ai_taste 常量，本模块不复制数值（AGENTS.md §11.3）。
"""
from __future__ import annotations

from ..logger import get_logger

logger = get_logger("assistant")

__all__ = ["build_cards", "build_positive_card"]

#: 正向指标口径：名称 + 阈值说明 + 判定函数（阈值全部来自 ai_taste 常量）
POSITIVE_NOTICE = "破折号密度 / 面板数字重复本期未接入确定性检测，不作评估。"


def _quality_card(snap: dict) -> dict:
    review = snap.get("review") or {}
    qc = review.get("quality_check") or {}
    return {
        "type": "quality",
        "title": "章节质检",
        "chapter": snap.get("chapter_no"),
        "chapter_title": snap.get("title") or "",
        "words": len(snap.get("content") or ""),
        "auto_revised": bool(review.get("auto_revised")),
        "defect_score": qc.get("score"),
        "passed": qc.get("passed"),
        "hook_score": qc.get("hook_score"),
        "has_record": bool(review),
        "notice": "质检扣分（缺陷分）· 仅反映缺陷扣减，不代表质量评价",
    }


def _issue_card(snap: dict) -> dict | None:
    issues = ((snap.get("review") or {}).get("issues") or [])
    if not issues:
        return None
    order = {"大改": 0, "中改": 1, "小改": 2}
    items = []
    for idx, it in enumerate(sorted(issues, key=lambda i: order.get(i.get("level"), 3)), 1):
        items.append({
            "id": f"Q-{idx}",
            "level": it.get("level") or "小改",
            "type": it.get("type") or "",
            "detail": it.get("detail") or "",
        })
    return {"type": "issues", "title": "质检问题清单", "chapter": snap.get("chapter_no"),
            "items": items}


def _version_card(store, chapter_no: int) -> dict | None:
    """版本对比事实：当前选中版 vs 最近一个未选中版（只给数字，不生成 diff 文本）。"""
    try:
        versions = store.list_versions(chapter_no)
    except Exception:
        logger.warning("版本列表读取失败（降级无版本卡）: chapter=%s", chapter_no)
        return None
    if len(versions) < 2:
        return None
    current = next((v for v in versions if v.get("selected")), versions[-1])
    others = [v for v in versions if v.get("v") != current.get("v")]
    if not others:
        return None
    prev = others[-1]
    cq = current.get("quality") or {}
    pq = prev.get("quality") or {}
    return {
        "type": "versions", "title": "版本对比", "chapter": chapter_no,
        "current": {"v": current.get("v"), "words": current.get("words"),
                    "defect_score": cq.get("score"), "big_issues": cq.get("issue_count")},
        "previous": {"v": prev.get("v"), "words": prev.get("words"),
                     "defect_score": pq.get("score"), "big_issues": pq.get("issue_count")},
        "word_delta": (current.get("words") or 0) - (prev.get("words") or 0),
        "defect_delta": ((cq.get("score") or 0) - (pq.get("score") or 0))
        if cq and pq else None,
    }


def _metric_rows(metrics: dict) -> list[dict]:
    from novel_gen.pipeline.ai_taste import (  # 阈值唯一事实来源
        _FILLER_LIMIT, _SENT_CV_TOO_HIGH, _SENT_CV_TOO_LOW, _TTR_TOO_LOW,
        SIMILE_DENSITY_LIMIT,
    )
    if not metrics:
        return []
    cv = metrics.get("sentence_cv") or 0
    ttr = metrics.get("ttr") or 0
    filler = metrics.get("filler_ratio") or 0
    simile = metrics.get("simile_per_1k") or 0
    rows = [
        {"key": "sentence_cv", "name": "句长波动系数", "value": cv,
         "threshold": f"{_SENT_CV_TOO_LOW}–{_SENT_CV_TOO_HIGH}",
         "ok": _SENT_CV_TOO_LOW <= cv <= _SENT_CV_TOO_HIGH,
         "hint": "过低=句式趋同，过高=节奏失控"},
        {"key": "ttr", "name": "词汇多样性 TTR", "value": ttr,
         "threshold": f"≥{_TTR_TOO_LOW}", "ok": ttr >= _TTR_TOO_LOW,
         "hint": "过低=用词单调"},
        {"key": "filler_ratio", "name": "填充词占比", "value": filler,
         "threshold": f"≤{_FILLER_LIMIT}", "ok": filler <= _FILLER_LIMIT,
         "hint": "过高=口头禅式连接词堆砌"},
        {"key": "simile_per_1k", "name": "明喻密度（/千字）", "value": simile,
         "threshold": f"<{SIMILE_DENSITY_LIMIT}", "ok": simile < SIMILE_DENSITY_LIMIT,
         "hint": "超限建议收敛为每场景 1–2 处"},
    ]
    return rows


def build_positive_card(positive: dict, chapter_no: int) -> dict:
    """正向评估卡：指标实测值 + 阈值 + 达标情况 + 范本摘要（不含合成分数）。"""
    positive = positive or {}
    metrics = positive.get("metrics") or {}
    zd = positive.get("zero_direct")
    return {
        "type": "positive",
        "title": "正向指标（警示线参考，非质量评分）",
        "chapter": chapter_no,
        "metrics": _metric_rows(metrics),
        "zero_direct": None if zd is None else {
            "ok": not zd,
            "hint": "全文无一处直白点破（整章级判断，不定位场景），"
                    "可在情绪铺垫到位处补一句点破 + 动作/生理支撑",
        },
        "goods": positive.get("goods_items") or [],
        "errors": positive.get("errors") or [],
        "notice": POSITIVE_NOTICE,
    }


def build_cards(store, snap: dict, positive: dict) -> list[dict]:
    """汇总一次回答要带的全部确定性卡片（顺序：质检 → 问题 → 版本 → 正向）。"""
    cards = [_quality_card(snap)]
    issue = _issue_card(snap)
    if issue:
        cards.append(issue)
    ver = _version_card(store, int(snap.get("chapter_no") or 0))
    if ver:
        cards.append(ver)
    if positive:
        cards.append(build_positive_card(positive, int(snap.get("chapter_no") or 0)))
    return cards
