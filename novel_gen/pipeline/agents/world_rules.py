# -*- coding: utf-8 -*-
"""WorldRulesAgent：世界观与规则 Agent（设定体系生产者）。

REQ-多Agent协作分层优化：负责「世界观核心法则 + 金手指规则说明书（强制五要素）+
设定禁忌清单 + 世界观揭示计划」的认领与结构化规范化。是全文的「底层宪法」，
只写自身领域，不越权修改主题/人物字段。

提示词文件：prompts/world_rules.md
"""
from __future__ import annotations

from ...store.schemas import (GoldfingerRules, World, WorldReveal)
from .base import BaseAgent


class WorldRulesAgent(BaseAgent):
    """世界观与规则 Agent：认领并规范化世界观/金手指/设定禁忌底层锚点。"""

    role_name = "world_rules"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)

    def apply_anchor(self, world: World) -> tuple[World, dict]:
        """认领世界观与规则锚点，规范化后写回 World。

        仅结构化规范化（trim / 空值回退），不做臆测补全；金手指五要素缺失交由
        StageGate 准出门禁判定并触发重生成。返回 (world, rules_summary)。
        """
        world.world_view = (getattr(world, "world_view", "") or "").strip()
        world.mechanism_rules = (getattr(world, "mechanism_rules", "") or "").strip()

        gf = getattr(world, "goldfinger_rules", None)
        if gf is not None and not isinstance(gf, GoldfingerRules):
            gf = GoldfingerRules.from_dict(gf if isinstance(gf, dict) else {})
        if gf is not None:
            for f in ("trigger", "upper_limit", "lower_limit", "cost", "growth_path"):
                setattr(gf, f, (getattr(gf, f, "") or "").strip())
        world.goldfinger_rules = gf

        world.setting_taboos = [
            (t or "").strip() for t in (getattr(world, "setting_taboos", None) or []) if (t or "").strip()
        ]

        reveals = []
        for r in (getattr(world, "world_reveals", None) or []):
            rr = WorldReveal.from_dict(r) if isinstance(r, dict) else None
            if rr is not None and (getattr(rr, "content", "") or "").strip():
                reveals.append(rr)
        world.world_reveals = reveals

        summary = {
            "world_view": bool(world.world_view),
            "mechanism_rules": bool(world.mechanism_rules),
            "goldfinger_rules": gf.is_complete() if gf is not None else False,
            "setting_taboos": len(world.setting_taboos),
            "world_reveals": len(world.world_reveals),
        }
        return world, summary
