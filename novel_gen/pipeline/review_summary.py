# -*- coding: utf-8 -*-
"""P0-3 全书质量汇总（纯函数，无 DB 依赖，便于测试）。

输入 review.json 的 items 与 verify_issues.json 的 issues，
输出全书维度统计：章节数、改稿问题总数/类型分布、质检平均分、校验问题统计。
"""
from __future__ import annotations


def build_review_summary(items: list, verify_issues: list) -> dict:
    """全书质量汇总。"""
    issue_total = sum(len(r.get("issues") or []) for r in items)
    issue_by_type: dict = {}
    for r in items:
        for it in r.get("issues") or []:
            t = str(it.get("type") or "其他")
            issue_by_type[t] = issue_by_type.get(t, 0) + 1
    scores = [
        float(r["quality_check"]["score"])
        for r in items
        if isinstance(r.get("quality_check"), dict)
        and r["quality_check"].get("score") is not None
    ]
    score_avg = round(sum(scores) / len(scores), 1) if scores else None
    verify_by_chapter: dict = {}
    for it in verify_issues:
        k = int(it.get("chapter", 0))
        verify_by_chapter[k] = verify_by_chapter.get(k, 0) + 1
    # 进度盘色带（与 Admin 质量报表四色一致）：未过=红 / 追更欲>=4=绿 / 自动重写=黄 / 通过=灰
    chapter_status = []
    for r in items:
        qc = r.get("quality_check") or {}
        ch = int(r.get("chapter", 0))
        passed = bool(qc.get("passed"))
        hook = qc.get("hook_score")
        auto = bool(r.get("auto_revised"))
        if not passed:
            color = "red"
        elif hook is not None and float(hook) >= 4:
            color = "green"
        elif auto:
            color = "yellow"
        else:
            color = "gray"
        chapter_status.append({
            "chapter": ch,
            "score": qc.get("score"),
            "passed": passed,
            "auto_revised": auto,
            "hook_score": hook,
            "color": color,
        })
    return {
        "chapter_count": len(items),
        "issue_total": issue_total,
        "issue_by_type": issue_by_type,
        "score_avg": score_avg,
        "verify_issue_total": len(verify_issues),
        "verify_by_chapter": verify_by_chapter,
        "chapter_status": chapter_status,
    }
