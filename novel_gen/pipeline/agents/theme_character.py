# -*- coding: utf-8 -*-
"""ThemeCharacterAgent：主题与人物 Agent（底层锚点生产者）。

REQ-多Agent协作分层优化：负责「核心主题命题 + 三层冲突矩阵 + 主角弧光卡 +
配角动机卡」四大底层锚点的认领与结构化规范化。只写自身领域，不越权修改
世界观/规则字段（越权修改由总控 Orchestrator 仲裁）。

提示词文件：prompts/theme_character.md
"""
from __future__ import annotations

from ...store.schemas import (ArcCard, CharacterMotive, ConflictLayer, World)
from .base import BaseAgent


class ThemeCharacterAgent(BaseAgent):
    """主题与人物 Agent：从世界观/大纲产出中认领并规范化主题与人物底层锚点。"""

    role_name = "theme_character"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)

    def apply_anchor(self, world: World) -> tuple[World, dict]:
        """认领主题与人物锚点，规范化后写回 World。

        仅结构化规范化（trim / 空值回退），不做臆测补全——缺项交由 StageGate
        准出门禁判定并触发重生成。返回 (world, anchor_summary) 供 trace/报告。
        """
        theme = (getattr(world, "theme_proposition", "") or "").strip()
        cm = [ConflictLayer.from_dict(c) if not isinstance(c, ConflictLayer) else c
              for c in (getattr(world, "conflict_matrix", None) or [])]
        cm = [c for c in cm if (c.description or "").strip()]

        arc = getattr(world, "protagonist_arc", None)
        if arc is not None and not isinstance(arc, ArcCard):
            arc = ArcCard.from_dict(arc if isinstance(arc, dict) else {})
        if arc is not None:
            arc.belief = (arc.belief or "").strip()
            arc.flaw = (arc.flaw or "").strip()
            arc.final_insight = (arc.final_insight or "").strip()
            arc.change_nodes = [x for x in (arc.change_nodes or []) if (x or "").strip()]

        motives = [CharacterMotive.from_dict(m) if not isinstance(m, CharacterMotive) else m
                   for m in (getattr(world, "character_motives", None) or [])]
        motives = [m for m in motives if (m.name or "").strip()]

        world.theme_proposition = theme
        world.conflict_matrix = cm
        world.protagonist_arc = arc
        world.character_motives = motives

        summary = {
            "theme_proposition": theme,
            "conflict_layers": len(cm),
            "protagonist_arc": bool(arc and (arc.belief or arc.flaw)),
            "character_motives": len(motives),
        }
        return world, summary
