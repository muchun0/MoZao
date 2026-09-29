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
from ..skills import build_role_skill_block, role_default_stages
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
    def stages(self) -> list[str]:
        """角色默认适用阶段（REQ-技能统一联动接线点）；未知角色回退全阶段。"""
        return role_default_stages(self.role_name)

    @property
    def role_prompt(self) -> str:
        """从 prompts/{role}.md 加载角色身份描述（懒加载，只读一次）。"""
        if self._role_prompt is None:
            p = _PROMPTS_DIR / f"{self.role_name}.md"
            self._role_prompt = p.read_text(encoding="utf-8") if p.exists() else ""
        return self._role_prompt

    def _record_skill_manifest(self, stage, manifest: list) -> None:
        """把本次技能注入清单落 agent_trace（结构化归因）。有 recorder 才记，异常吞掉。"""
        try:
            rec = getattr(self.llm, "recorder", None)
            if rec is not None and getattr(rec, "record_agent_step", None):
                rec.record_agent_step("skill_injection", extra={
                    "role": self.role_name,
                    "stage": stage if isinstance(stage, str) else stage,
                    "skills": manifest,
                })
        except Exception:
            pass

    def _role_skill_append(self, stage=None) -> str:
        """统一注入管线（REQ-技能统一）：管理员全局skill + 管理员角色专属skill + 当前用户自定义skill，
        按角色默认阶段路由，产出 manifest 落 trace。

        stage=None 时按 self.stages（角色默认阶段）路由；显式传 stage（str/list）则按指定阶段。
        数据库不可用时返回空串（不阻塞生成主流程）。
        """
        try:
            if stage is None:
                stage = self.stages
            body, manifest = build_role_skill_block(self.role_name, self.store, stage)
            self._record_skill_manifest(stage, manifest)
            return body
        except Exception as e:
            self.logger.warning("加载角色 skill 失败（忽略）: %s", e)
            return ""

    def persona_prefix(self, stage=None) -> str:
        """角色底色：角色 prompt + 角色专属 skill（DB），供外部阶段拼接到 system 之前。

        与 _system_with_role 的区别：本方法不接收任务指令、也不追加项目级 skill
        ——项目级 skill 由各阶段提示词函数自行追加，此处重复追加会导致两份。
        """
        parts = [self.role_prompt]
        role_skill = self._role_skill_append(stage)
        if role_skill:
            parts.append("## 本角色专属写作要求\n" + role_skill)
        return "\n\n".join(p for p in parts if p)

    def _system_with_role(self, extra: str = "", stage=None) -> str:
        """拼接：角色 prompt + 角色专属skill + 全局skill + 额外指令。"""
        parts = [self.role_prompt]
        role_skill = self._role_skill_append(stage)
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
