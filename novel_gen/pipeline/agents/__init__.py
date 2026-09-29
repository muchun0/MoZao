# -*- coding: utf-8 -*-
"""创作 Agent 角色包：按职责拆分的多智能体架构。

角色（REQ-多Agent协作分层优化）：
- ResearcherAgent         题材调研 + 反套路改造
- ThemeCharacterAgent     主题与人物 Agent（底层锚点）：主题命题/三层冲突/弧光/配角动机
- WorldRulesAgent         世界观与规则 Agent（底层锚点）：世界观/金手指五要素/设定禁忌
- ArchitectAgent          剧情骨架 Agent：总纲/分卷大纲 + 关键节点
- PacingAgent             节奏与伏笔 Agent：关键节点/分卷节奏/伏笔回收闭环
- WriterAgent             逐章正文生成
- ReviewerAgent           审校 + 修订
- StylistAgent            写作风格角色（风格师）
"""
from .base import BaseAgent
from .researcher import ResearcherAgent
from .theme_character import ThemeCharacterAgent
from .world_rules import WorldRulesAgent
from .architect import ArchitectAgent
from .pacing import PacingAgent
from .writer import WriterAgent
from .reviewer import ReviewerAgent
from .reader import ReaderAgent
from .stylist import StylistAgent

__all__ = [
    "BaseAgent",
    "ResearcherAgent",
    "ThemeCharacterAgent",
    "WorldRulesAgent",
    "ArchitectAgent",
    "PacingAgent",
    "WriterAgent",
    "ReviewerAgent",
    "ReaderAgent",
    "StylistAgent",
]
