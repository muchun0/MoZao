# -*- coding: utf-8 -*-
"""ArchitectAgent：总纲/分卷大纲 + 人物世界观设计。

提示词文件：prompts/architect.md
"""
from __future__ import annotations

from ...pipeline.stages import OutlineGenerator, WorldGenerator
from ...research.schemas import ResearchReport
from ...store.schemas import Outline, World
from .base import BaseAgent


class ArchitectAgent(BaseAgent):
    role_name = "architect"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)
        self._outline_gen = OutlineGenerator(cfg, llm, store)
        self._world_gen = WorldGenerator(cfg, llm, store)

    def run_outline(self, research: ResearchReport | None, silent: bool = False) -> Outline:
        outline = self.store.load_outline()
        if outline is not None and outline.chapters:
            return outline
        self.logger.info("大纲生成开始（架构师）")
        outline = self._outline_gen.run(research, silent=silent)
        self.logger.info("大纲生成完成，共 %d 章", len(outline.chapters))
        return outline

    def run_world(self, silent: bool = False) -> World:
        world = self.store.load_world()
        if world and (world.world_view or world.characters):
            return world
        self.logger.info("人物世界观设计开始（架构师）")
        world = self._world_gen.run(silent=silent)
        self.logger.info("人物世界观设计完成")
        return world
