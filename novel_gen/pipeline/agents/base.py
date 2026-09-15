# -*- coding: utf-8 -*-
"""
Agent 基类：所有创作角色的公共接口。

每个 Agent 对应一个创作角色，持有 cfg / llm / store，
通过 run() 执行本角色负责的阶段，产物落盘到项目目录。
后续可给每个 Agent 挂载专属 skill（prompt 片段），按角色切换风格。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ...config import NovelConfig
from ...llm.base import LLMClient
from ...store.project import ProjectStore
from webapp.logger import get_logger


class BaseAgent(ABC):
    """所有创作 Agent 的基类。"""

    #: 角色名（子类覆盖），用于日志和 skill 匹配
    role_name: str = "base"
    #: 角色描述（子类覆盖），写在 system prompt 里锚定身份
    role_description: str = ""

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.logger = get_logger(f"agent.{self.role_name}")

    def _system_with_role(self, extra: str = "") -> str:
        """拼接角色身份 + 额外指令，作为 system prompt 前缀。"""
        parts = [self.role_description]
        if extra:
            parts.append(extra)
        # 注入写作风格 skill 和自定义提示词
        from .templates import _skill_append
        parts.append(_skill_append(self.cfg))
        return "\n\n".join(p for p in parts if p)

    @abstractmethod
    def run(self, **kwargs):
        """执行本角色的核心任务，返回产物或 None。"""
        ...
