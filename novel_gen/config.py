# -*- coding: utf-8 -*-
"""
配置层：加载 config.yaml，支持环境变量覆盖 API Key。

设计要点
- 所有可调参数集中在 YAML 配置，不改代码即可调参
- API Key 不写死在代码里：支持配置文件 / 环境变量两级读取
- 章节数由「目标总字数 / 单章字数」自动换算，向上取整
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 内置题材列表（题材可选：允许自定义，不在此列表内也放行）
GENRES = ["玄幻", "仙侠", "都市", "科幻", "历史", "悬疑", "言情", "游戏", "奇幻", "体育", "军事", "自定义"]

# 各 Provider 的默认模型与默认 base_url（OpenAI 兼容协议）
PROVIDER_DEFAULTS: dict[str, dict] = {
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "doubao": {  # 火山方舟（豆包），模型名形如 ep-xxx 或 doubao-seed-1-6-250615
        "model": "doubao-seed-1-6-250615",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "env_key": "ARK_API_KEY",
    },
    "openai": {
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "mock": {  # 本地假 Provider：不联网，返回占位文本，用于无 Key 冒烟测试
        "model": "mock-model",
        "base_url": "",
        "env_key": "",
    },
}

# 调研阶段联网搜索源默认地址
SEARCH_PROVIDER_DEFAULTS: dict[str, dict] = {
    "bocha": {
        "api_url": "https://api.bochaai.com/v1/web-search",
        "env_key": "BOCHA_API_KEY",
        "header_key": "Authorization",
        "header_value": "Bearer {key}",
    },
    "serper": {
        "api_url": "https://google.serper.dev/search",
        "env_key": "SERPER_API_KEY",
        "header_key": "X-API-KEY",
        "header_value": "{key}",
    },
}


@dataclass
class LLMConfig:
    provider: str = "deepseek"      # deepseek / doubao / openai / mock
    model: str = ""                 # 留空则取 PROVIDER_DEFAULTS 的默认模型
    api_key: str = ""               # 留空则读环境变量
    base_url: str = ""              # 留空则取 PROVIDER_DEFAULTS 的默认地址
    temperature: float = 0.8        # 生成随机度：大纲/人物偏低，正文偏高
    top_p: float = 0.0              # 0 表示不设置（由 API 默认）；防同质化建议 0.75-0.85
    max_tokens: int = 4096          # 单次响应上限（单章输出建议 >= 4000）


@dataclass
class SearchConfig:
    """调研阶段的联网搜索源配置。留空 provider 则只使用内置知识库 + LLM 先验知识。"""
    provider: str = ""      # "" / "bocha"(博查) / "serper"(Google via serper)
    api_key: str = ""
    api_url: str = ""       # 留空取该 provider 默认地址
    count: int = 5          # 每个 query 返回的搜索结果条数


@dataclass
class AgentGuardConfig:
    """Agent 生产级加固配置（阶段1）。

    - enabled=False 时本模块全部防护关闭（等同改造前行为），用于紧急降级。
    - input_check / output_check / quality_check 独立开关，可分别关闭。
    - max_loops / max_tokens 为单任务级兜底阈值；scene_limits 按业务场景细化。
    - 阈值可从环境变量覆盖：AGENT_GUARD_MAX_LOOPS / AGENT_GUARD_MAX_TOKENS / AGENT_GUARD_ENABLED 等。
    """

    enabled: bool = True            # 总开关
    max_loops: int = 200            # 单任务最大 LLM 调用次数（全局兜底，防死循环）
    max_tokens: int = 2_000_000     # 单任务累计 Token 消耗上限（防成本爆炸）
    input_check: bool = True        # 模块3：输入注入检测
    output_check: bool = True       # 模块3：输出基础合规校验
    quality_check: bool = True      # 模块4：ReaderAgent 结果自校验
    scene_limits: dict = field(default_factory=lambda: {
        "research": 30,    # 调研
        "outline": 40,     # 大纲（含分批）
        "world": 25,       # 人物/世界观
        "chapter": 150,    # 单章正文（含扩写/摘要兜底）——11章高质量模式每章约4-6次调用，留重试余量
        "revise": 30,      # 单章改稿/重写（高质量模式按章触发）
        "review": 25,      # 单章审校/校验（高质量模式按章触发）
    })


@dataclass
class NovelConfig:
    title: str = "未命名小说"
    genre: str = "玄幻"             # 题材，可自定义
    total_words: int = 100_000      # 单本目标总字数
    chapter_words: int = 2500       # 单章目标字数
    style: str = ""                 # 文风要求（如「轻松诙谐」「冷峻写实」），留空由模型自由发挥
    premise: str = ""               # 核心创意/一句话简介，留空则仅按题材生成
    # ---- 用户自定义设定（扩展字段）----
    background: str = ""            # 故事背景/时代/世界观（强约束，必须严格遵循）
    hero: str = ""                  # 主角设定：姓名/身份/性格/目标（强约束）
    characters: list[str] = field(default_factory=list)  # 配角/反派预设（强约束，可留空让 AI 补）
    plot_direction: str = ""        # 剧情走向/关键情节/必须出现的桥段（参考方向，AI 可细化）
    forbidden: list[str] = field(default_factory=list)   # 避雷/不想出现的元素（强约束）
    # ---- 质量增强开关 ----
    editor_review: bool = True       # 大纲生成后执行编辑视角质检，不通过则自动修正一次
    de_ai: bool = True               # 正文生成时注入去AI味写作约束
    anti_similarity: bool = True     # 防同质化：常驻System提示词，拒绝套路/人物内在矛盾/有代价/只展示不叙述
    character_depth: bool = True     # 人物立体化：角色卡一致性 + 选择代价 + 生活体感细节 + 出场标签/对话辨识度
    fanqie_style: bool = True        # 番茄节奏：黄金三章开篇纪律 + 爽点四环节 + 章末断章 + 移动端排版 + 轻量世界观/力量体系
    confirm: bool = False           # True：每阶段生成后暂停等待确认；False：自动推进
    memory_window: int = 5          # 生成当前章时回填最近 N 章摘要，控制上下文成本
    memory_direct_read_chapters: int = 1    # 前文直读：直读最近 N 章正文（0=关闭，保持纯摘要；P0-1）
    memory_direct_read_chars: int = 2000    # 每章直读正文最大字符数（取章节尾部；P0-1）
    # ---- 模式选择 ----
    mode: str = "auto"              # auto=现有工作流（调研→大纲→人物→逐章）；expand=AI扩写（用用户提供的大纲/简要内容直接扩写）
    expand_input: str = ""          # 扩写模式的输入：用户提供的大纲/简要内容（mode=expand 时必填）
    content_type: str = ""          # expand 模式下的输入类型：""=大纲/梗概（纯扩写）；"chapters"=已有分章正文（续写）；"chapters_expand"=扩写已有章节
    expand_targets: list = field(default_factory=list)  # 扩写范围：章节号列表，空=全部已有章节
    with_research: bool = False     # expand 模式可选项：生成前先做一次爆款共性调研（产出 research.json + 沉淀拆书库，并作为扩写参考）；auto 模式始终调研，此字段忽略
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    skill_prompt_append: str = ""     # 管理员配置的写作风格 skill 追加内容
    style_role_id: int = 0            # 写作风格角色（style_roles.id），0=不选
    style_role_prompt: str = ""       # 风格角色人设+挂载技能组装后的提示词（创建项目时快照）
    high_quality_mode: bool = False   # 高质量模式：场景级生成 + 动态模块 + 参数分层，3元/万字
    # ---- 节奏控制参数 ----
    pacing_density: str = "medium"    # 爽点密度：low(慢热)/medium(适中)/high(密集)
    plot_speed: str = "medium"        # 剧情推进速度：slow(细腻)/medium(适中)/fast(快节奏)
    custom_prompt: str = ""           # 用户自定义提示词模板（追加到system末尾）
    agent_guard: AgentGuardConfig = field(default_factory=AgentGuardConfig)  # 生产级加固配置

    @property
    def num_chapters(self) -> int:
        """目标总字数 ÷ 单章字数，向上取整，至少 1 章。"""
        return max(1, math.ceil(self.total_words / self.chapter_words))

    def effective_title(self) -> str:
        return self.title.strip() or "未命名小说"


def _apply_provider_defaults(llm: LLMConfig) -> LLMConfig:
    """用 Provider 内置默认值补齐未填字段，并回填环境变量中的 API Key。"""
    defaults = PROVIDER_DEFAULTS.get(llm.provider, {})
    if not llm.model:
        llm.model = defaults.get("model", "")
    if not llm.base_url:
        llm.base_url = defaults.get("base_url", "")
    if not llm.api_key:
        env_key = defaults.get("env_key", "")
        if env_key:
            llm.api_key = os.environ.get(env_key, "") or ""
    return llm




def validate_config(cfg: NovelConfig) -> list[str]:
    """配置校验，返回问题列表；空列表表示通过。"""
    issues: list[str] = []
    # 字数完全由用户决定，不做下限建议（按字数收费即可）
    if cfg.chapter_words < 20:
        issues.append("chapter_words 过小（<20），单章字数不合理")
    if cfg.memory_window < 0:
        issues.append("memory_window 不能为负数")
    if cfg.llm.provider not in PROVIDER_DEFAULTS:
        issues.append(f"未知 provider: {cfg.llm.provider}，可选 {list(PROVIDER_DEFAULTS)}")
    if cfg.llm.provider != "mock" and not cfg.llm.api_key:
        issues.append(f"provider[{cfg.llm.provider}] 缺少 api_key：请设置环境变量 "
                      f"{PROVIDER_DEFAULTS.get(cfg.llm.provider, {}).get('env_key', '')}")
    guard = getattr(cfg, "agent_guard", None)
    if guard is not None:
        if guard.max_loops < 1:
            issues.append("agent_guard.max_loops 必须 >= 1")
        if guard.max_tokens < 1:
            issues.append("agent_guard.max_tokens 必须 >= 1")
    return issues


def apply_guard_env_overrides(guard: AgentGuardConfig) -> AgentGuardConfig:
    """用环境变量覆盖 AgentGuardConfig（生产可按 .env 动态调阈值，不改代码）。

    支持：AGENT_GUARD_ENABLED / AGENT_GUARD_MAX_LOOPS / AGENT_GUARD_MAX_TOKENS
        / AGENT_GUARD_INPUT_CHECK / AGENT_GUARD_OUTPUT_CHECK / AGENT_GUARD_QUALITY_CHECK
        / AGENT_GUARD_SCENE_<SCENE>（如 AGENT_GUARD_SCENE_CHAPTER=150，覆盖场景限额）
    """
    def _bool_env(name: str, default: bool) -> bool:
        v = os.environ.get(name, "")
        return {"1": True, "true": True, "0": False, "false": False}.get(v.strip().lower(), default)

    def _int_env(name: str, default: int) -> int:
        v = os.environ.get(name, "").strip()
        try:
            return int(v) if v else default
        except ValueError:
            return default

    guard.enabled = _bool_env("AGENT_GUARD_ENABLED", guard.enabled)
    guard.max_loops = _int_env("AGENT_GUARD_MAX_LOOPS", guard.max_loops)
    guard.max_tokens = _int_env("AGENT_GUARD_MAX_TOKENS", guard.max_tokens)
    guard.input_check = _bool_env("AGENT_GUARD_INPUT_CHECK", guard.input_check)
    guard.output_check = _bool_env("AGENT_GUARD_OUTPUT_CHECK", guard.output_check)
    guard.quality_check = _bool_env("AGENT_GUARD_QUALITY_CHECK", guard.quality_check)
    # 场景级限额：AGENT_GUARD_SCENE_<SCENE>，如 AGENT_GUARD_SCENE_CHAPTER=150
    for scene in list(guard.scene_limits):
        guard.scene_limits[scene] = _int_env(f"AGENT_GUARD_SCENE_{scene.upper()}", guard.scene_limits[scene])
    return guard

