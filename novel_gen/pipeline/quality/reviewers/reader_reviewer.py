# -*- coding: utf-8 -*-
"""L2 评委一：reader（读者体验，TECH §6.2）。

迁移来源：旧 `QualityChecker._reader_llm_check` + `reader_review_prompt`。
只评读者追更体验：代入感 / 爽点闭环 / 章末钩子 / 阅读障碍 / 主角主动性；
人物一致性作为该评委专项（本期不单独设 character 评委，D-2）。

专项保留：同构场景、对话循环、数字口径复核（reader prompt 内专项指令）。
`hook_score`（1–5）与旧口径一致；`score` 为附加 0–100 信号，本期不接管总分（D-1）。
"""
from __future__ import annotations

import logging

from ....llm.json_parse import parse_json_obj
from ...templates import reader_review_prompt
from .base import BaseReviewer, ReviewContext, ReviewerResult

logger = logging.getLogger("novel_gen.pipeline.quality")

#: hook_score 1–5 → 附加维度分 0–100（仅展示信号，不参与旧倒扣总分）
_HOOK_TO_SCORE = {1: 20, 2: 40, 3: 60, 4: 80, 5: 100}


class ReaderReviewer(BaseReviewer):
    key = "reader"
    label = "读者体验"

    def _enabled(self, ctx: ReviewContext) -> bool:
        return ctx.llm is not None

    def _call_llm(self, ctx: ReviewContext) -> dict:
        if ctx.guard is not None:
            ctx.guard.enter_scene("review")
        world = None
        if ctx.store is not None:
            try:
                world = ctx.store.load_world()
            except Exception:
                logger.warning("[quality] reader 评委加载 world 失败（降级无人物卡）", exc_info=True)
        char_block = ""
        if world and getattr(world, "characters", None):
            char_block = "；".join(
                f"{c.name}({c.role or ''}：{(c.desc or '')[:60]})"
                for c in world.characters[:12] if getattr(c, "name", None)
            )
        system, user = reader_review_prompt(
            ctx.cfg, ctx.chapter_no, ctx.plan_title, ctx.plan_goal, ctx.content
        )
        if char_block:
            user = (
                user.replace("【本章正文】",
                             "【人物卡】\n" + char_block + "\n\n【本章正文】")
                + "\n额外校验：正文中的人物言行是否符合【人物卡】设定？"
                "有无性格/身份/称呼与人物卡冲突？（冲突则加入 issues，type='人物一致性'）"
            )
        raw = ctx.llm.chat(system, user, temperature=0.3, max_tokens=1200)
        return parse_json_obj(raw)

    def _parse(self, raw: dict) -> dict:
        data = raw if isinstance(raw, dict) else {}
        hook = data.get("追更欲")
        hook_score = None
        if isinstance(hook, (int, float)) and 1 <= int(hook) <= 5:
            hook_score = int(hook)
        score = _HOOK_TO_SCORE.get(hook_score) if hook_score is not None else None
        return {
            "score": score,
            "hook_score": hook_score,
            "reader_feel": str(data.get("一句话感受") or data.get("reader_feel") or ""),
            "issues": [
                {
                    "pos": it.get("pos", ""),
                    "type": f"读者-{it.get('type', '')}",
                    "detail": it.get("detail", ""),
                    "level": it.get("level", "小改"),
                    "evidence": str(it.get("evidence", "") or ""),
                    "suggestion": str(it.get("suggestion") or "").strip(),
                    "fix_suggestion": str(it.get("suggestion") or "").strip(),
                }
                for it in (data.get("issues") or []) if isinstance(it, dict)
            ],
            "strengths": [str(s) for s in (data.get("strengths") or [])],
        }

    def _ok(self, parsed: dict) -> ReviewerResult:
        result = super()._ok(parsed)
        result.dimension.note = parsed.get("reader_feel", "")
        result.dimension.extra = {"hook_score": parsed.get("hook_score")}
        return result
