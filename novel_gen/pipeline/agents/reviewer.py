# -*- coding: utf-8 -*-
"""ReviewerAgent：审校 + 修订。

提示词文件：prompts/reviewer.md
"""
from __future__ import annotations

from .base import BaseAgent


class ReviewerAgent(BaseAgent):
    role_name = "reviewer"

    def revise_chapter(self, chapter_no: int, plan_title: str, plan_goal: str,
                       content: str, silent: bool = False) -> str:
        """根据审校结果修订正文（高质量模式自动改稿）。"""
        from ...pipeline.reviser import ChapterReviser
        reviser = ChapterReviser(self.cfg, self.llm, self.store,
                                 role_prompt=self.persona_prefix())
        return reviser.revise(chapter_no, plan_title, plan_goal, content, silent=silent)
