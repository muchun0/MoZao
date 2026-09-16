# -*- coding: utf-8 -*-
"""
Agent 基类：所有创作角色的公共接口。

每个 Agent 对应一个创作角色，独立持有：
- prompts/{role}.md  角色身份和写作规则（Markdown，可编辑）
- 专属 skill（从数据库 writing_skills 表按 role 过滤）
- 用户自定义 skill（从 user_skills 表按项目owner过滤）

后续要调整某个角色的风格，直接改对应的 .md 文件或在管理页
加该角色的专属 skill 即可，不用动代码。
"""
from __future__ import annotations

from abc import ABC
from pathlib import Path

from ...config import NovelConfig
from ...llm.base import LLMClient
from ...store.project import ProjectStore
from webapp.logger import get_logger


_PROMPTS_DIR = Path(__file__).parent / "prompts"


class BaseAgent(ABC):
    """所有创作 Agent 的基类。"""

    #: 角色名（子类覆盖），对应 prompts/{role}.md 和 skills.role
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

    def _role_skill_append(self) -> str:
        """加载：管理员全局skill + 管理员角色专属skill + 当前用户自定义skill。"""
        try:
            from webapp import db
            blocks = []
            # 管理员全局 skill（role=''）
            for sk in db.list_skills(only_active=True, role=""):
                if sk.get("prompt_append"):
                    blocks.append(f"【{sk['name']}】\n{sk['prompt_append']}")
            # 管理员角色专属 skill
            for sk in db.list_skills(only_active=True, role=self.role_name):
                if sk.get("prompt_append"):
                    blocks.append(f"【{sk['name']}】\n{sk['prompt_append']}")
            # 用户自定义 skill（通过项目owner反查user_id）
            try:
                pid = self.store.dir.name
                for usk in db.list_user_skills_by_project(pid):
                    if usk.get("prompt_append"):
                        # role为空=所有角色生效；role匹配=只对该角色生效
                        if not usk.get("role") or usk["role"] == self.role_name:
                            blocks.append(f"【用户自定义：{usk['name']}】\n{usk['prompt_append']}")
            except Exception as ue:
                self.logger.warning("加载用户skill失败（忽略）: %s", ue)
            return "\n\n".join(blocks)
        except Exception as e:
            self.logger.warning("加载角色 skill 失败（忽略）: %s", e)
            return ""

    def _system_with_role(self, extra: str = "") -> str:
        """拼接：角色 prompt + 角色专属skill + 全局skill + 额外指令。"""
        parts = [self.role_prompt]
        role_skill = self._role_skill_append()
        if role_skill:
            parts.append("## 本角色专属写作要求\n" + role_skill)
        if extra:
            parts.append(extra)
        # 项目级选的 skill（用户创建时选的写作风格）
        from ...pipeline.templates import _skill_append
        parts.append(_skill_append(self.cfg))
        return "\n\n".join(p for p in parts if p)

    def run(self, **kwargs):
        """执行本角色的核心任务（子类可按需覆盖）。"""
        return None
