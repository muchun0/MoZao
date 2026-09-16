# -*- coding: utf-8 -*-
"""
Agent 基类：所有创作角色的公共接口。

每个 Agent 对应一个创作角色，独立持有：
- prompts/{role}.md  角色身份和写作规则（Markdown，可编辑）
- skills（从数据库按 role 过滤）专属风格片段

后续要调整某个角色的风格，直接改对应的 .md 文件即可，
不用动代码。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ...config import NovelConfig
from ...llm.base import LLMClient
from ...store.project import ProjectStore
from webapp.logger import get_logger


_PROMPTS_DIR = Path(__file__).parent / "prompts"


class BaseAgent(ABC):
    """所有创作 Agent 的基类。"""

    #: 角色名（子类覆盖），对应 prompts/{role}.md
    role_name: str = "base"

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.logger = get_logger(f"agent.{self.role_name}")
        self._role_prompt: str | None = None  # 懒加载

    @property
    def role_prompt(self) -> str:
        """从 prompts/{role}.md 加载角色身份描述（懒加载，只读一次）。"""
        if self._role_prompt is None:
            p = _PROMPTS_DIR / f"{self.role_name}.md"
            self._role_prompt = p.read_text(encoding="utf-8") if p.exists() else ""
        return self._role_prompt

    def _system_with_role(self, extra: str = "") -> str:
        """拼接：角色 prompt + 用户自定义 + 写作风格 skill。"""
        parts = [self.role_prompt]
        if extra:
            parts.append(extra)
        # 注入写作风格 skill 和自定义提示词（全局）
        from ...pipeline.templates import _skill_append
        parts.append(_skill_append(self.cfg))
        return "\n\n".join(p for p in parts if p)

    @abstractmethod
    def run(self, **kwargs):
        """执行本角色的核心任务，返回产物或 None。"""
        ...
