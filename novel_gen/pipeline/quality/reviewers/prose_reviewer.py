# -*- coding: utf-8 -*-
"""L2 评委四：prose（文风 AI 感，TECH §6.5）。

迁移来源：旧 `_check_ai_and_style` 中的 `ai_taste` LLM 四维度判定。
- LLM 语义：连接词堆砌 / 情绪直白 / 填充口头禅 / 直白点破 / 句式雷同；
- 纯统计指标（明喻密度 / 排比 / 段落雷同）本期仍随 `ai_taste_issues` 一并产出，
  留在 L1 style_stats 的剥离属于后续迭代（§6.5：两层各司其职，不互相兜底语义定性）。

开关 `llm_semantic_ai_taste`、阈值 `ai_taste_threshold` 口径与现状一致；
LLM 失败沿用现状「回退词表 + 统计」，不在本评委内做语义硬判。
"""
from __future__ import annotations

import logging

from ...ai_taste import ai_taste_issues, score_ai_taste
from .base import BaseReviewer, ReviewContext, ReviewerResult

logger = logging.getLogger("novel_gen.pipeline.quality")


class ProseReviewer(BaseReviewer):
    key = "prose"
    label = "文风自然度"

    def _enabled(self, ctx: ReviewContext) -> bool:
        return bool(ctx.content) and getattr(ctx.cfg, "ai_taste_check", True)

    def _call_llm(self, ctx: ReviewContext) -> dict:
        use_llm = bool(getattr(ctx.cfg, "llm_semantic_ai_taste", True))
        threshold = int(getattr(ctx.cfg, "ai_taste_threshold", 60) or 60)
        # 复用现状一次判定口径：judge 与 score 共享，失败内部回退词表/统计
        issues_raw = ai_taste_issues(
            ctx.content, threshold=threshold, llm=ctx.llm, use_llm=use_llm)
        score = score_ai_taste(ctx.content, llm=ctx.llm, use_llm=use_llm)
        return {"score": score, "issues": issues_raw, "strengths": []}

    def _parse(self, raw: dict) -> dict:
        data = raw if isinstance(raw, dict) else {}
        issues = []
        for it in (data.get("issues") or []):
            if not isinstance(it, dict):
                continue
            detail = str(it.get("detail", "")).strip()
            if not detail:
                continue
            issues.append({
                "type": str(it.get("type", "")),
                "detail": detail,
                "level": it.get("level", "中改"),
                "evidence": str(it.get("evidence", "") or detail),
                "rule_id": "prose.ai_taste",
            })
        return {"score": data.get("score"), "issues": issues, "strengths": []}
