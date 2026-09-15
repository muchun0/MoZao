# -*- coding: utf-8 -*-
"""WriterAgent：逐章正文生成。

职责：
1. 根据章节节点计划生成正文
2. 高质量模式：场景级拆分 → 逐场景生成 → 统一润色
3. 字数兜底扩写
4. 生成章节摘要供记忆引擎使用
"""
from __future__ import annotations

from ...pipeline.stages import ChapterWriter
from ...pipeline.memory import Memory
from ...research.schemas import ResearchReport
from ...store.schemas import Chapter
from .base import BaseAgent


class WriterAgent(BaseAgent):
    role_name = "writer"
    role_description = (
        "你是一位资深网文写手。你的工作是把章节大纲变成有画面感的正文："
        "短句为主、段落轻薄、对话带潜台词、情绪通过动作和细节侧面展现，不直白喊情绪。"
        "你保持人设统一、伏笔不丢、节奏张弛有度，每章结尾留钩子。"
        "你不做结构规划，只负责写好当前这一章。"
    )

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
        """写一章正文（复用现有 ChapterWriter）。"""
        self.logger.info("第%d章开始（写手）", chapter_no)
        chapter = self._writer.write(
            chapter_no, plan_title, plan_goal,
            research, silent=silent, is_final=is_final,
        )
        self.logger.info("第%d章完成（%d字）", chapter_no, len(chapter.content))
        return chapter
