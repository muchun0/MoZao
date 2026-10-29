# -*- coding: utf-8 -*-
"""L2 评委三：consistency（逻辑一致性，TECH §6.4）。

迁移来源：旧 `QualityChecker._consistency_llm_check`（章内前后矛盾）。
rule_consistency 与 panel anchor 作为该评委内部专项，本期仍由 checker 旧编排产出，
在 L3 聚合时归入 consistency 维度（§6.4 合并为同一 DimensionResult 的后续迭代项）。

只找「两处描写不能同时为真」的硬矛盾，不评文笔、不评节奏；失败降级 unavailable。
"""
from __future__ import annotations

import logging

from ....llm.json_parse import parse_json_obj
from ....config import NovelConfig
from .base import BaseReviewer, ReviewContext, ReviewerResult
from ..text_tools import _load_persona as _persona

logger = logging.getLogger("novel_gen.pipeline.quality")

#: 与旧 `_consistency_llm_check` 逐字一致的判定标准与排除条款（测试断言原文存在，勿改）
_CONSISTENCY_SYSTEM = (
    "你是严谨的小说文字编辑，通读本章后只找「前后自相矛盾的硬伤」，不评文笔、不评节奏。\n"
    "只输出 JSON：{\"contradictions\":[{\"type\":\"身体状态/行为模式/方位指代/比喻/事件重复\","
    "\"detail\":\"具体矛盾：前一处…后一处…\",\"suggestion\":\"统一为…的具体改法\"}]}。\n"
    "判定标准：两处描写不能同时为真才算矛盾；比喻措辞不同但喻体相同不算；"
    "随剧情合理变化的伤口/状态不算矛盾；没有矛盾就输出空数组。\n"
    "特别注意⑤同一事件重复：同一人物面对同一冲突对象、实际做出同一件事（救/围/杀/逃/挡等行为动作）"
    "且结果相同，即使换了地点或改了细节，也算'同一事件两版本'（如先写'主角冲下去救同伴引开追兵'"
    "后又写一遍'坡地上同伴又被围、主角再次冲下去救'）——报 type=事件重复，"
    "detail 必须引用两处【实际发生的行为原句】，写明共同的人物/冲突/结果。\n"
    "【事件重复排除条款·必须遵守】以下情况一律【不得】判为事件重复：\n"
    "a) 同一冲突对象下，人物的'内心决定/犹豫/退缩念头'（如'决定绕走'、'想不救'、'打算撤退'）"
    "与后续'实际行动'（如'冲出去救人'）属于'抉择→行动'的因果弧光，两者是先后承接关系、"
    "可以同时为真，不是同一事件的两个版本；\n"
    "b) 未执行的念头、计划、心理活动（未实际发生的行为）不算'事件发生'；"
    "只有实际发生的动作才构成事件；\n"
    "c) 若无法引用两处【实际发生行为】的原句，不得输出事件重复。"
)


def _should_run(cfg: NovelConfig | None, chapter_no: int) -> bool:
    """沿用旧 `_check_consistency` 降频口径：高质量每章；普通按开关 + every 章。"""
    if cfg is None:
        return False
    hq = bool(getattr(cfg, "high_quality_mode", False))
    every = max(1, int(getattr(cfg, "consistency_llm_every", 3) or 3))
    return hq or (bool(getattr(cfg, "consistency_llm_check", False)) and chapter_no % every == 1)


class ConsistencyReviewer(BaseReviewer):
    key = "consistency"
    label = "逻辑一致性"

    def _enabled(self, ctx: ReviewContext) -> bool:
        return ctx.llm is not None and bool(ctx.content) and _should_run(ctx.cfg, ctx.chapter_no)

    def _call_llm(self, ctx: ReviewContext) -> dict:
        persona = _persona("reviewer")
        sys = (persona + "\n\n" if persona else "") + _CONSISTENCY_SYSTEM
        user = (
            f"第{ctx.chapter_no}章正文：\n{ctx.content[:12000]}\n\n"
            "请输出上述 JSON。"
        )
        raw = ctx.llm.chat(sys, user, temperature=0.2, max_tokens=800)
        return parse_json_obj(raw)

    def _parse(self, raw: dict) -> dict:
        data = raw if isinstance(raw, dict) else {}
        issues = []
        for it in (data.get("contradictions") or []):
            if not isinstance(it, dict):
                continue
            detail = str(it.get("detail", "")).strip()
            if not detail:
                continue
            issues.append({
                "type": f"自相矛盾-{it.get('type', '')}",
                "detail": detail,
                "level": "大改",
                "evidence": str(it.get("evidence", "") or detail),
                "suggestion": str(it.get("suggestion") or "").strip(),
                "fix_suggestion": str(it.get("suggestion") or "").strip(),
                "rule_id": "consistency.contradiction",
            })
        return {"score": None, "issues": issues, "strengths": []}
