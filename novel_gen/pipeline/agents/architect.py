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
from .ending import EndingAgent
from .opening import OpeningAgent
from .dialogue import DialogueAgent


class ArchitectAgent(BaseAgent):
    role_name = "architect"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)
        arch_persona = self.persona_prefix()
        # 多Agent生产层细分：主题人物 / 世界观规则 / 节奏伏笔 三个专项 Agent
        self.theme = ThemeCharacterAgent(cfg, llm, store)
        self.rules = WorldRulesAgent(cfg, llm, store)
        self.pacing = PacingAgent(cfg, llm, store)
        self.ending = EndingAgent(cfg, llm, store)
        self.opening = OpeningAgent(cfg, llm, store)
        self.dialogue = DialogueAgent(cfg, llm, store)
        # 接线（P0-2）：三个细分 Agent 内部零 LLM 调用，其 persona（角色 prompt + 专属
        # 技能）此前从未被读取。这里把细分角色要求作为 system 上下文注入到对应的 LLM
        # 生成阶段，让模型生成时遵守细分职责，再由 apply_anchor/plan_* 确定性兜底规范。
        #   - 世界观阶段：架构师 + 主题人物 + 世界观规则（theme/world_rules 均产出 World 字段）
        #   - 大纲阶段：架构师 + 节奏伏笔（pacing 产出 Outline.key_nodes/volume_pacing）
        world_persona = "\n\n".join(
            p for p in [arch_persona, self.theme.persona_prefix(), self.rules.persona_prefix()] if p
        )
        outline_persona = "\n\n".join(
            p for p in [arch_persona, self.pacing.persona_prefix()] if p
        )
        self._outline_gen = OutlineGenerator(cfg, llm, store, role_prompt=outline_persona)
        self._world_gen = WorldGenerator(cfg, llm, store, role_prompt=world_persona)

    def run_outline(self, research: ResearchReport | None, silent: bool = False) -> Outline:
        outline = self.store.load_outline()
        if outline is not None and outline.chapters:
            # 骨架阶段：补齐关键节点/分卷节奏（幂等，只补缺失）
            outline, _ = self.pacing.plan_outline(outline)
            # P1-1 开篇 Agent：前三章强开篇设计（前 3 章 core_event 非空则幂等复用）
            outline, _ = self.opening.apply(outline)
            self.store.save_outline(outline)
            return outline
        self.logger.info("大纲生成开始（架构师）")
        outline = self._outline_gen.run(research, silent=silent)
        # 节奏与伏笔 Agent：推导关键节点时间线 + 分卷节奏梯次（只补缺失）
        outline, _ = self.pacing.plan_outline(outline)
        # P1-1 开篇 Agent：前三章强开篇设计（黄金三章决定生死）
        outline, _ = self.opening.apply(outline)
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
            # P1-2 结局 Agent：终局设计卡（已存在则幂等复用）
            world, _ = self.ending.apply(world, outline)
            world, _ = self.dialogue.build_guideline(world)
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
        # P1-2 结局 Agent：终局设计卡（防烂尾）
        world, _ = self.ending.apply(world, outline)
        # P2-1 对白 Agent：对话风格指南（口吻区分+节奏）
        world, _ = self.dialogue.build_guideline(world)
        self.store.save_world(world)
        self.logger.info("人物世界观设计完成（%d 个人物）", len(world.characters))
        return world
