# -*- coding: utf-8 -*-
"""StylistAgent：写作风格角色（风格师）。

写作风格在项目里被抽象成一个独立的 agent 角色：
- 每个「风格角色」（style_roles 表，如"爱潜水的乌贼·克苏鲁悬疑克制风"）
  有自己的角色人设（role_prompt），并可挂载多个写作技能（style_role_skills）。
- 创建项目时选定风格角色后，组装结果快照进 cfg.style_role_prompt，
  本项目所有正文生成（含扩写/续写）都会把风格角色人设拼进系统提示词。

提示词文件：prompts/stylist.md（基础底色，风格角色人设优先叠加在其后）
"""
from __future__ import annotations

from .base import BaseAgent


class StylistAgent(BaseAgent):
    role_name = "stylist"

    @property
    def is_active(self) -> bool:
        """是否选了风格角色（创建项目时快照进 config）。"""
        return bool(getattr(self.cfg, "style_role_prompt", "").strip()
                    or getattr(self.cfg, "style_role_id", 0))

    @property
    def role_prompt(self) -> str:
        """风格角色人设 = 基础底色 prompts/stylist.md + 创建项目时组装的
        DB 风格角色人设（含挂载技能）。未选风格角色时只返回基础底色。"""
        parts = [super().role_prompt]
        txt = (getattr(self.cfg, "style_role_prompt", "") or "").strip()
        if txt:
            parts.append(txt)
        return "\n\n".join(p for p in parts if p)
