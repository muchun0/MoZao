# -*- coding: utf-8 -*-
"""L2 评委二：plot（情节结构，TECH §6.3，本期新增）。

评审大纲核心事件兑现 / 事件推进 / 场景同构 / 节奏松紧 / 章末断章张力。
输入叠加本章 plan_title / plan_goal / core_event / beat_gate 快照（只读引用，不新算）。

触发与降频（§6.3）：
- `plot_reviewer` 总开关关闭 → unavailable，不发起调用；
- 高质量模式每章执行；普通模式每 `plot_reviewer_every` 章一次（chapter_no % every == 1，
  与 consistency 降频口径对齐），未触发 → unavailable、note="降频跳过"，不计入总分。

失败降级 unavailable，不阻塞其他评委与主链路（I-5）。
"""
from __future__ import annotations

import logging

from ....llm.json_parse import parse_json_obj
from .base import BaseReviewer, ReviewContext, ReviewerResult
from ..text_tools import _load_persona, _load_review_prompt

logger = logging.getLogger("novel_gen.pipeline.quality")


def _should_run(cfg, chapter_no: int) -> bool:
    """高质量每章；普通模式每 every 章一次（锚定第 every 章：3、6、9…，避开第 1 章，
    与旧调用计数契约兼容）。"""
    hq = bool(getattr(cfg, "high_quality_mode", False))
    every = max(1, int(getattr(cfg, "plot_reviewer_every", 3) or 3))
    return hq or chapter_no % every == 0


class PlotReviewer(BaseReviewer):
    key = "plot"
    label = "情节结构"

    def _enabled(self, ctx: ReviewContext) -> bool:
        if ctx.llm is None:
            return False
        if not bool(getattr(ctx.cfg, "plot_reviewer", True)):
            return False
        return _should_run(ctx.cfg, ctx.chapter_no)

    def _call_llm(self, ctx: ReviewContext) -> dict:
        persona = _load_persona("reviewer")
        brief = _load_review_prompt("plot")
        system = (persona + "\n\n" if persona else "") + brief
        beat_gate = ctx.beat_gate or {}
        beat_lines = ""
        if beat_gate:
            beat_lines = (
                "\n【本章节拍覆盖快照】\n"
                + "\n".join(f"- {k}: {v}" for k, v in beat_gate.items())
            )
        user = (
            f"【第{ctx.chapter_no}章《{ctx.plan_title}》】\n"
            f"【本章目标】{ctx.plan_goal}\n"
            + (f"【本章大纲核心事件】{ctx.core_event}\n" if ctx.core_event else "")
            + beat_lines + "\n\n"
            f"【本章正文】\n{ctx.content[:6000]}\n\n"
            "请按情节结构评委要求输出 JSON。"
        )
        raw = ctx.llm.chat(system, user, temperature=0.0, max_tokens=1000)
        return parse_json_obj(raw)

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
                "type": f"情节-{it.get('type', '')}",
                "detail": detail,
                "level": it.get("level", "中改"),
                "evidence": str(it.get("evidence", "") or detail),
                "pos": str(it.get("pos", "") or ""),
                "suggestion": str(it.get("suggestion") or "").strip(),
                "fix_suggestion": str(it.get("fix_suggestion") or it.get("suggestion") or "").strip(),
                "expected_gain": int(it.get("expected_gain") or 0),
                "rule_id": "plot.structure",
            })
        return {
            "score": data.get("score"),
            "issues": issues,
            "strengths": [str(s) for s in (data.get("strengths") or [])],
            "missing_core_events": [str(s) for s in (data.get("missing_core_events") or [])],
        }

    def _ok(self, parsed: dict) -> ReviewerResult:
        result = super()._ok(parsed)
        result.dimension.extra = {"missing_core_events": parsed.get("missing_core_events", [])}
        return result
