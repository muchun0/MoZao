# -*- coding: utf-8 -*-
"""创作 Agent 角色包：按职责拆分的多智能体架构。

角色：
- ResearcherAgent  题材调研 + 反套路改造
- ArchitectAgent   总纲/分卷大纲 + 人物世界观设计
- WriterAgent      逐章正文生成
- ReviewerAgent    审校 + 修订
- StylistAgent     写作风格角色（风格师）：承载选定的文风人设 + 挂载技能

后续可给每个 Agent 挂载专属 skill（prompt 片段），按角色切换风格。
"""
from .base import BaseAgent
from .researcher import ResearcherAgent
from .architect import ArchitectAgent
from .writer import WriterAgent
from .reviewer import ReviewerAgent
from .reader import ReaderAgent
from .stylist import StylistAgent

__all__ = [
    "BaseAgent",
    "ResearcherAgent",
    "ArchitectAgent",
    "WriterAgent",
    "ReviewerAgent",
    "ReaderAgent",
    "StylistAgent",
]
