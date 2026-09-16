# -*- coding: utf-8 -*-
"""WriterAgent：逐章正文生成。

提示词文件：prompts/writer.md
"""
from __future__ import annotations

from ...pipeline.stages import ChapterWriter
from ...pipeline.memory import Memory
from ...research.schemas import ResearchReport
from ...store.schemas import Chapter
from .base import BaseAgent


class WriterAgent(BaseAgent):
    role_name = "writer"

    def __init__(self, cfg, llm, store, memory: Memory):
        super().__init__(cfg, llm, store)
        self._writer = ChapterWriter(cfg, llm, store, memory)

    def write_chapter(
        self,
        chapter_no: int,
        plan_title: str,
        plan_goal: str,
        research: ResearchReport | None,
        silent: bool = False,
        is_final: bool = False,
    ) -> Chapter:
        self.logger.info("第%d章开始（写手）", chapter_no)
        chapter = self._writer.write(
            chapter_no, plan_title, plan_goal,
            research, silent=silent, is_final=is_final,
        )
        self.logger.info("第%d章完成（%d字）", chapter_no, len(chapter.content))
        return chapter
