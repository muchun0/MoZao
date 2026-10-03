# -*- coding: utf-8 -*-
"""DialogueAgent：对白 Agent（对话节奏与人物口吻区分）。

P2-1：此前人物口吻只有 Character.speech_style 一个字符串字段，写正文时靠 writer
自觉，无专职角色负责对话节奏与口吻区分。本 Agent 聚合全书人物 speech_style 生成
对话风格指南（口吻区分 + 节奏规则），写入 world.dialogue_guideline，经 memory 注入
正文生成，保证人人一个调的问题被专职角色纠正。

当前为确定性聚合（零 LLM、可复现、不增 token 调用数），后续可叠加 LLM 精细化。

提示词文件：prompts/dialogue.md
"""
from __future__ import annotations

from ...store.schemas import World
from .base import BaseAgent


class DialogueAgent(BaseAgent):
    role_name = "dialogue"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)

    def build_guideline(self, world: World) -> tuple[World, dict]:
        """生成对话风格指南并写回 world.dialogue_guideline。

        幂等：已有非空指南则复用。返回 (world, summary)。
        """
        if getattr(world, "dialogue_guideline", "").strip():
            return world, {"cached": True}
        chars = getattr(world, "characters", None) or []
        lines = []
        for c in chars:
            name = (getattr(c, "name", "") or "").strip()
            style = (getattr(c, "speech_style", "") or "").strip()
            if name and style:
                lines.append(f"{name}：{style}")
        head = ("人物口吻区分：每人对话必须符合其人物卡 speech_style，禁止所有人物一个调子、"
                "禁止套话化表达；同场景对话中不同人物口吻要有辨识度。")
        if lines:
            head += "\n" + "\n".join(lines)
        head += ("\n对话节奏规则：短句多、节奏快、带潜台词（人物不说心里话）；"
                 "对话要有动作/神态/反应穿插，禁止大段对白无人反应；"
                 "关键冲突处对话简短有力，避免长篇说教。")
        world.dialogue_guideline = head
        return world, {"cached": False, "chars": len(lines)}
