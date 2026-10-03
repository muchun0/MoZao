# -*- coding: utf-8 -*-
"""EndingAgent：结局 Agent（终局设计）。

P1-2：基于主题命题/主角弧光/大纲关键节点，产出结局卡（终局状态/主题落点/
主角最终选择/人物归宿/基调），写入 world.ending_card，作为末几章写手的终局
锚点，防烂尾与自然收束失控。

提示词文件：prompts/ending.md
"""
from __future__ import annotations

from ...pipeline.templates import ending_prompt
from ...store.schemas import EndingCard, Outline, World
from .base import BaseAgent


class EndingAgent(BaseAgent):
    role_name = "ending"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)

    def apply(self, world: World, outline: Outline | None = None) -> tuple[World, dict]:
        """设计并写回结局卡。

        幂等：已存在非空结局卡则复用（不重复调 LLM）。LLM 失败时静默保留
        world 原状（异常由编排层吞掉，不阻塞生成主流程）。返回 (world, summary)。
        """
        existing = getattr(world, "ending_card", None)
        if existing is not None and (getattr(existing, "end_state", "") or getattr(existing, "protagonist_choice", "")):
            return world, {"cached": True}
        try:
            sys_e, user_e = ending_prompt(self.cfg, outline or Outline(), world)
            system = self._system_with_role(sys_e)
            data = self.llm.chat_json(system, user_e, temperature=0.6, max_tokens=1200)
            world.ending_card = EndingCard.from_dict(data) if isinstance(data, dict) else EndingCard()
            return world, {
                "cached": False,
                "end_state": bool(getattr(world.ending_card, "end_state", "")),
                "fates": len(getattr(world.ending_card, "character_fates", []) or []),
            }
        except Exception as e:
            self.logger.warning("结局卡设计失败（降级跳过）: %s", e)
            return world, {"cached": False, "error": True}
