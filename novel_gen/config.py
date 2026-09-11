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

import yaml

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
    # ---- 模式选择 ----
    mode: str = "auto"              # auto=现有工作流（调研→大纲→人物→逐章）；expand=AI扩写（用用户提供的大纲/简要内容直接扩写）
    expand_input: str = ""          # 扩写模式的输入：用户提供的大纲/简要内容（mode=expand 时必填）
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)

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


def load_config(path: str | Path) -> NovelConfig:
    """从 YAML 文件加载配置；缺失文件时返回全默认配置（便于程序化使用）。"""
    path = Path(path)
    if not path.exists():
        return NovelConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    llm_raw = raw.get("llm", {}) or {}
    llm = LLMConfig(
        provider=llm_raw.get("provider", "deepseek"),
        model=llm_raw.get("model", ""),
        api_key=llm_raw.get("api_key", ""),
        base_url=llm_raw.get("base_url", ""),
        temperature=llm_raw.get("temperature", 0.8),
        top_p=llm_raw.get("top_p", 0.0),
        max_tokens=llm_raw.get("max_tokens", 4096),
    )
    _apply_provider_defaults(llm)

    search_raw = raw.get("search", {}) or {}
    search = SearchConfig(
        provider=search_raw.get("provider", ""),
        api_key=search_raw.get("api_key", "") or os.environ.get(
            SEARCH_PROVIDER_DEFAULTS.get(search_raw.get("provider", ""), {}).get("env_key", ""), ""
        ),
        api_url=search_raw.get("api_url", ""),
        count=int(search_raw.get("count", 5)),
    )

    cfg = NovelConfig(
        title=raw.get("title", "未命名小说"),
        genre=raw.get("genre", "玄幻"),
        total_words=int(raw.get("total_words", 100_000)),
        chapter_words=int(raw.get("chapter_words", 2500)),
        style=raw.get("style", ""),
        premise=raw.get("premise", ""),
        background=raw.get("background", ""),
        hero=raw.get("hero", ""),
        characters=[str(x) for x in (raw.get("characters") or [])],
        plot_direction=raw.get("plot_direction", ""),
        forbidden=[str(x) for x in (raw.get("forbidden") or [])],
        editor_review=bool(raw.get("editor_review", True)),
        de_ai=bool(raw.get("de_ai", True)),
        anti_similarity=bool(raw.get("anti_similarity", True)),
        character_depth=bool(raw.get("character_depth", True)),
        fanqie_style=bool(raw.get("fanqie_style", True)),
        confirm=bool(raw.get("confirm", False)),
        memory_window=int(raw.get("memory_window", 5)),
        mode=raw.get("mode", "auto"),
        expand_input=raw.get("expand_input", ""),
        llm=llm,
        search=search,
    )
    return cfg


def validate_config(cfg: NovelConfig) -> list[str]:
    """配置校验，返回问题列表；空列表表示通过。"""
    issues: list[str] = []
    # 字数完全由用户决定，不做下限建议（按档位收费即可）
    if cfg.chapter_words < 20:
        issues.append("chapter_words 过小（<20），单章字数不合理")
    if cfg.memory_window < 0:
        issues.append("memory_window 不能为负数")
    if cfg.llm.provider not in PROVIDER_DEFAULTS:
        issues.append(f"未知 provider: {cfg.llm.provider}，可选 {list(PROVIDER_DEFAULTS)}")
    if cfg.llm.provider != "mock" and not cfg.llm.api_key:
        issues.append(f"provider[{cfg.llm.provider}] 缺少 api_key：请在 config.yaml 填写或设置环境变量 "
                      f"{PROVIDER_DEFAULTS.get(cfg.llm.provider, {}).get('env_key', '')}")
    return issues


# 短篇小说范围（仅用于分档参考；已取消字数下限硬约束，字数完全由用户决定）
SHORT_NOVEL_WORDS_UPPER = 25_000   # ≤ 此值视为短篇（与用户定义「2.5万字以内」一致）


def check_short_novel_hard_constraint(cfg: NovelConfig) -> str:
    """短篇字数硬约束：已按用户要求取消（字数由用户自行决定，仅按档位收费）。

    保留函数签名以兼容既有调用点（CLI / Web），但恒返回空字符串表示通过。
    历史版本曾强制短篇（≤2.5 万）必须大于 7000 字，现已放开。
    """
    return ""
