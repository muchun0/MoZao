# -*- coding: utf-8 -*-
"""SPC 控制图（P2-2，docs/REQ-门禁质检正向影响.md P2-2）。

用 I-MR（个体值 + 移动极差）控制图检测「质量骤降」信号：
- 一次通过率 / 违规率 / 重写轮数等逐章指标画控制图
- 偏离均值 ± 3σ 控制限的点 = 质量异常信号（方向由调用方按指标性质解释：
  通过率这类越高越好，低于 LCL 即骤降；重写轮数/违规率这类越低越好，
  高于 UCL 即恶化）

确定性、零依赖，纯函数可测。样本不足 2 时返回无信号的安全结构。
"""
from __future__ import annotations

from typing import List, Optional


def _d2_for_mr() -> float:
    """I-MR 图常数：移动极差（n=2）的 d2 = 1.128。"""
    return 1.128


def i_mr_chart(points: List[float]) -> dict:
    """个体值-移动极差控制图。

    参数 points: 按章节顺序的指标值序列（如 [通过率, 通过率, ...]）。
    返回 {n, mean, sigma_est, ucl, lcl, signals: [超出控制限的索引]}；
    n < 2 或全等（sigma=0）时 signals=[]（无异常信号）。
    """
    pts = [float(p) for p in (points or [])]
    n = len(pts)
    if n < 2:
        return {"n": n, "mean": None, "sigma_est": None,
                "ucl": None, "lcl": None, "signals": []}
    mean = sum(pts) / n
    mrs = [abs(pts[i] - pts[i - 1]) for i in range(1, n)]
    mrbar = sum(mrs) / len(mrs)
    sigma = (mrbar / _d2_for_mr()) if mrbar > 0 else 0.0
    ucl = mean + 3 * sigma
    lcl = mean - 3 * sigma
    signals = []
    for i, x in enumerate(pts):
        # 仅当 sigma>0 时才能判定偏离；sigma=0 表示无波动（不判定异常）
        if sigma > 0 and (x > ucl or x < lcl):
            signals.append(i)
    return {"n": n, "mean": mean, "sigma_est": sigma,
            "ucl": ucl, "lcl": lcl, "signals": signals}


def quality_spc_report(metrics: dict) -> dict:
    """聚合 store.load_quality_metrics() 为三个指标的 SPC 报告。

    metrics: {chapter_no: {passed_once, rewrite_rounds, compliance_hit}}。
    返回 {by_chapter: {...}, passed_once: i_mr_chart(...), rewrite_rounds: ...,
          compliance_hit: ...}；无数据时各图 signals=[]。
    """
    ordered = []
    for k in sorted(metrics or {}, key=lambda s: int(s) if str(s).isdigit() else 0):
        m = metrics[k]
        if not isinstance(m, dict):
            continue
        ordered.append({
            "chapter": k,
            "passed_once": 1 if m.get("passed_once") else 0,
            "rewrite_rounds": int(m.get("rewrite_rounds") or 0),
            "compliance_hit": 1 if m.get("compliance_hit") else 0,
        })
    return {
        "by_chapter": ordered,
        "passed_once": i_mr_chart([o["passed_once"] for o in ordered]),
        "rewrite_rounds": i_mr_chart([o["rewrite_rounds"] for o in ordered]),
        "compliance_hit": i_mr_chart([o["compliance_hit"] for o in ordered]),
    }
