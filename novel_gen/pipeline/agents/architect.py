# -*- coding: utf-8 -*-
"""ArchitectAgent：剧情骨架 Agent（总纲/分卷大纲 + 关键节点）。

REQ-多Agent协作分层优化：由「主题与人物 Agent（ThemeCharacterAgent）+ 世界观与
规则 Agent（WorldRulesAgent）+ 节奏与伏笔 Agent（PacingAgent）」协同生产底层锚点
与节奏结构；本 Agent 收敛为骨架生产者（大纲），负责编排三个子 Agent 产出并合并。

提示词文件：prompts/architect.md
"""
from __future__ import annotations

from ...pipeline.stages import OutlineGenerator, WorldGenerator
from ...research.schemas import ResearchReport
from ...store.schemas import Outline, World
from .base import BaseAgent
from .theme_character import ThemeCharacterAgent
from .world_rules import WorldRulesAgent
from .pacing import PacingAgent


class ArchitectAgent(BaseAgent):
    role_name = "architect"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)
        persona = self.persona_prefix()
        self._outline_gen = OutlineGenerator(cfg, llm, store, role_prompt=persona)
        self._world_gen = WorldGenerator(cfg, llm, store, role_prompt=persona)
        # 多Agent生产层细分：主题人物 / 世界观规则 / 节奏伏笔 三个专项 Agent
        self.theme = ThemeCharacterAgent(cfg, llm, store)
        self.rules = WorldRulesAgent(cfg, llm, store)
        self.pacing = PacingAgent(cfg, llm, store)

    def run_outline(self, research: ResearchReport | None, silent: bool = False) -> Outline:
        outline = self.store.load_outline()
        if outline is not None and outline.chapters:
            # 骨架阶段：补齐关键节点/分卷节奏（幂等，只补缺失）
            outline, _ = self.pacing.plan_outline(outline)
            self.store.save_outline(outline)
            return outline
        self.logger.info("大纲生成开始（架构师）")
        outline = self._outline_gen.run(research, silent=silent)
        # 节奏与伏笔 Agent：推导关键节点时间线 + 分卷节奏梯次（只补缺失）
        outline, _ = self.pacing.plan_outline(outline)
        self.store.save_outline(outline)
        self.logger.info("大纲生成完成，共 %d 章", len(outline.chapters))
        return outline

    def run_world(self, silent: bool = False) -> World:
        world = self.store.load_world()
        if world and (world.world_view or world.characters):
            # 已存在：仍做锚点认领规范化 + 伏笔回收闭环（幂等）
            world, _ = self.theme.apply_anchor(world)
            world, _ = self.rules.apply_anchor(world)
            outline = self.store.load_outline()
            if outline is not None:
                world, _ = self.pacing.plan_foreshadow(outline, world)
            self.store.save_world(world)
            return world
        self.logger.info("人物世界观设计开始（架构师）")
        outline = self.store.load_outline()
        research = self.store.load_research()
        world = self._world_gen.run(outline, research, silent=silent)
        # 主题与人物 Agent：认领主题命题/三层冲突/弧光/配角动机
        world, _ = self.theme.apply_anchor(world)
        # 世界观与规则 Agent：认领世界观/金手指五要素/设定禁忌
        world, _ = self.rules.apply_anchor(world)
        # 节奏与伏笔 Agent：伏笔回收闭环（只埋不收 → 卷末/全篇回收）
        world, _ = self.pacing.plan_foreshadow(outline or Outline(), world)
        self.store.save_world(world)
        self.logger.info("人物世界观设计完成（%d 个人物）", len(world.characters))
        return world
