# -*- coding: utf-8 -*-
"""OpeningAgent：开篇 Agent（黄金三章设计）。

P1-1：黄金三章决定生死，过去前三章和普通章节共用 writer 与同一套大纲。
本 Agent 在大纲生成后，专门为前三章设计强开篇（每章具体 core_event + 强化 goal），
回填 Outline.chapters[0:3]，使 writer 写前 3 章有强开篇锚点（core_event 已有注入机制）。

提示词文件：prompts/opening.md
"""
from __future__ import annotations

from ...pipeline.templates import opening_prompt
from ...store.schemas import Outline
from .base import BaseAgent


class OpeningAgent(BaseAgent):
    role_name = "opening"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)

    def apply(self, outline: Outline) -> tuple[Outline, dict]:
        """为大纲前三章设计强开篇并回填 core_event/goal。

        幂等：前 3 章均已含非空 core_event 则复用（不重复调 LLM）。LLM 失败时静默
        保留 outline 原状（异常降级，不阻塞生成）。返回 (outline, summary)。
        """
        chapters = list(getattr(outline, "chapters", None) or [])
        first3 = chapters[:3]
        if not first3:
            return outline, {"cached": True, "skipped": "no_chapters"}
        if all(getattr(c, "core_event", "") for c in first3):
            return outline, {"cached": True}
        try:
            sys_o, user_o = opening_prompt(self.cfg, outline)
            system = self._system_with_role(sys_o)
            data = self.llm.chat_json(system, user_o, temperature=0.6, max_tokens=1500)
            updates = data.get("chapters") if isinstance(data, dict) else None
            updated = 0
            if isinstance(updates, list):
                by_no = {int(u.get("no", 0)): u for u in updates if isinstance(u, dict)}
                for c in first3:
                    u = by_no.get(int(getattr(c, "no", 0)))
                    if not u:
                        continue
                    if u.get("core_event"):
                        c.core_event = str(u["core_event"]).strip()
                        updated += 1
                    if u.get("goal"):
                        c.goal = str(u["goal"]).strip()
            return outline, {"cached": False, "updated": updated}
        except Exception as e:
            self.logger.warning("开篇设计失败（降级跳过）: %s", e)
            return outline, {"cached": False, "error": True}
