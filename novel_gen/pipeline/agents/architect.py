# -*- coding: utf-8 -*-
"""ArchitectAgent：总大纲 + 分卷规划 + 人物世界观设计。

职责：
1. 根据调研结果生成三级大纲（总纲→分卷→章节节点）
2. 设计人物档案（三层逻辑：表层标签/中层经历/深层动机）
3. 设计世界观/力量体系/时间线
4. 伏笔布局规划
"""
from __future__ import annotations

from ...pipeline.stages import OutlineGenerator, WorldGenerator
from ...pipeline.memory import Memory
from ...research.schemas import ResearchReport
from ...store.schemas import Outline, World
from .base import BaseAgent


class ArchitectAgent(BaseAgent):
    role_name = "architect"
    role_description = (
        "你是一位资深网文架构师。你的工作是设计整本书的骨架："
        "总纲一句话概括核心冲突，分卷规划每个阶段的大高潮，"
        "章节节点明确每章的目标/核心事件/冲突/情绪。"
        "人物设计遵循三层逻辑——表层标签建立印象、中层经历解释性格、深层动机驱动行为。"
        "你追求结构严谨、伏笔密集、节奏张弛有度，但不写正文。"
    )

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)
        self._outline_gen = OutlineGenerator(cfg, llm, store)
        self._world_gen = WorldGenerator(cfg, llm, store)

    def run_outline(self, research: ResearchReport | None, silent: bool = False) -> Outline:
        """生成大纲（复用现有 OutlineGenerator，注入角色身份）。"""
        outline = self.store.load_outline()
        if outline is not None and outline.chapters:
            return outline
        self.logger.info("大纲生成开始（架构师）")
        # 用现有 OutlineGenerator，但 system prompt 已通过 _skill_append 注入
        outline = self._outline_gen.run(research, silent=silent)
        self.logger.info("大纲生成完成，共 %d 章", len(outline.chapters))
        return outline

    def run_world(self, silent: bool = False) -> World:
        """生成人物/世界观（复用现有 WorldGenerator）。"""
        world = self.store.load_world()
        if world and (world.world_view or world.characters):
            return world
        self.logger.info("人物世界观设计开始（架构师）")
        world = self._world_gen.run(silent=silent)
        self.logger.info("人物世界观设计完成")
        return world

    def run_editor_review(self, silent: bool = False):
        """编辑质检（架构师自检大纲质量）。"""
        from ...pipeline.editor import EditorReviewer
        return EditorReviewer(self.llm).run(self.cfg, self.store, silent=silent)
