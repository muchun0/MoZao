# -*- coding: utf-8 -*-
"""
墨造核心层·请求/响应模型：全部 Pydantic 请求模型。

从 core.py 拆出（业务域拆分，行为零变化）。仅承载「HTTP 请求模型定义」一个内聚概念，
不包含任何业务逻辑；路由与门面经 `from ..core import *` 取用。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LLMReq(BaseModel):
    provider: str = "deepseek"
    model: str = ""
    base_url: str = ""
    temperature: float = 0.8
    top_p: float = 0.8
    max_tokens: int = 8000


class SearchReq(BaseModel):
    provider: str = ""      # "" / bocha / serper
    api_key: str = ""


class ProjectCreateReq(BaseModel):
    title: str = Field(..., min_length=1)
    genre: str = "玄幻"
    total_words: int = 100_000
    chapter_words: int = 2500
    style: str = ""
    premise: str = ""
    background: str = ""
    hero: str = ""
    characters: list[str] = []
    plot_direction: str = ""
    forbidden: list[str] = []
    confirm: bool = False
    memory_window: int = 5
    mode: str = "auto"              # auto=现有工作流；expand=AI扩写（提供大纲/简要内容）
    expand_input: str = ""          # 扩写模式必填：用户提供的大纲或简要内容
    user_content: str = ""          # 续写/扩写已有章节：用户手写正文（粘贴）；上传走 /api/parse-content 预览后由前端带回
    content_type: str = ""          # expand 模式输入类型：""=大纲/梗概；"chapters"=已有分章正文（续写）；"chapters_expand"=扩写已有章节
    expand_targets: list[int] = []  # 扩写范围：章节号列表，空=全部
    llm: LLMReq = Field(default_factory=LLMReq)
    search: SearchReq = Field(default_factory=SearchReq)
    skill_id: int = 0               # 写作风格 skill id，0=不选
    style_role_id: int = 0          # 写作风格角色 id（style_roles），0=不选
    pacing_density: str = "medium"  # 爽点密度
    plot_speed: str = "medium"      # 剧情速度
    custom_prompt: str = ""         # 用户自定义提示词
    with_research: bool = False     # expand 模式可选：生成前先做一次题材调研（产出 research.json + 沉淀拆书库）；auto 模式始终调研，此字段忽略


class OutlineImportReq(BaseModel):
    """项目详情页「重新导入大纲」：以用户传入为准，覆盖前自动备份。"""
    text: str = Field(..., min_length=1)


class RegisterReq(BaseModel):
    username: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=4)
    email: str = ""          # 邮箱（必填，全站唯一，格式校验）
    code: str = ""           # 邮箱验证码（必填，先"获取验证码"）
    invite_code: str = ""   # 上级邀请码（选填）


class SendCodeReq(BaseModel):
    email: str = ""


class ResetPasswordReq(BaseModel):
    email: str = ""
    code: str = ""
    new_password: str = Field(..., min_length=4)


class BindReferrerReq(BaseModel):
    invite_code: str = Field(..., min_length=1)


class RechargeAdminReq(BaseModel):
    amount: float = Field(..., gt=0)


class RefundApplyReq(BaseModel):
    amount: float = Field(..., gt=0)
    reason: str = ""


class RefundHandleReq(BaseModel):
    action: str  # approve 确认已退 / reject 拒绝
    admin_note: str = ""


class LoginReq(BaseModel):
    username: str
    password: str


class SkillReq(BaseModel):
    name: str
    description: str = ""
    prompt_append: str = ""
    is_active: int = 1
    sort_order: int = 0
    role: str = ""  # 空=全局；researcher/architect/writer/reviewer=专属角色
    applicable_stages: str = ""  # 适用阶段，逗号分隔；空=全阶段（REQ-技能统一 阶段一）


class StyleRoleReq(BaseModel):
    name: str = ""
    description: str = ""
    role_prompt: str = ""
    is_active: int = 1
    sort_order: int = 0
    reference_text: str = ""   # P2-1 参考文风片段（few-shot 文风锚定，2-3 段样例）


class StyleRoleSkillReq(BaseModel):
    skill_type: str = "admin"   # admin=平台写作技能 / user=我的技能
    skill_id: int = 0


class AgentSetReq(BaseModel):
    discount: float = 0.8


class UserLlmReq(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class OutlineChapterReq(BaseModel):
    title: str = ""
    goal: str = ""
    core_event: str = ""
    conflict: str = ""
    character_moves: str = ""
    foreshadowing: str = ""
    emotion: str = ""
    transition: str = ""
    tag: str = ""
    volume_no: int = 0  # 所属分卷编号（0=未分卷），前端拖拽/手动调整后随保存落盘


class OutlineVolumeReq(BaseModel):
    no: int = 0
    name: str = ""
    intro: str = ""
    main_conflict: str = ""
    character_growth: str = ""
    climax: str = ""
    start_ch: int = 0
    end_ch: int = 0


class OutlineSaveReq(BaseModel):
    title: str = ""
    intro: str = ""
    chapters: list[OutlineChapterReq] = []
    volumes: list[OutlineVolumeReq] = []


class ChapterEditReq(BaseModel):
    title: str = ""
    content: str = ""
    summary: str = ""   # 可留空：留空时自动用 LLM 重新生成摘要
    regen_summary: bool = True   # 是否在正文变更后重新生成摘要


__all__ = [
    "LLMReq", "SearchReq", "ProjectCreateReq", "OutlineImportReq",
    "RegisterReq", "SendCodeReq", "ResetPasswordReq", "BindReferrerReq",
    "RechargeAdminReq", "RefundApplyReq", "RefundHandleReq", "LoginReq",
    "SkillReq", "StyleRoleReq", "StyleRoleSkillReq", "AgentSetReq", "UserLlmReq",
    "OutlineChapterReq", "OutlineVolumeReq", "OutlineSaveReq", "ChapterEditReq",
]
