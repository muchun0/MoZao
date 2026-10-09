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


# =====================================================================
# 功能开关注册表（唯一事实来源）
# ---------------------------------------------------------------------
# 所有功能开关/可调参数的默认值集中在这里，按功能模块分组。
# 新增开关：1) 在 FEATURE_FLAGS 注册一行；2) NovelConfig 加同名字段（默认值引用
# feature_default(key)）。save_config / load_config 会自动遍历注册表落盘与读取，
# 无需再手改 store 两处持久化逻辑。
# 模块约定：memory=记忆 / writing=正文生成 / review=审校质检 / guard=成本防护
#（guard 子项由 AgentGuardConfig 承载，落盘在 config.json 的 agent_guard 段，不进顶层）
# =====================================================================


@dataclass(frozen=True)
class FeatureFlag:
    module: str          # 功能模块：memory / writing / review / guard
    key: str             # NovelConfig 字段名
    default: object      # 默认值
    desc: str            # 说明


FEATURE_FLAGS: list[FeatureFlag] = [
    # ---- memory：记忆与长文一致性 ----
    FeatureFlag("memory", "memory_window", 5, "生成当前章时回填最近 N 章摘要，控制上下文成本"),
    FeatureFlag("memory", "memory_direct_read_chapters", 1, "前文直读：直读最近 N 章正文（0=关闭，保持纯摘要；P0-1）"),
    FeatureFlag("memory", "memory_direct_read_chars", 1200, "每章直读正文最大字符数（取章节尾部；P0-1；REQ-token优化 P0-5：2000→1200 降输入）"),
    FeatureFlag("memory", "character_state_tracking", True, "每章正文落盘后更新人物动态状态快照（P0-2）"),
    FeatureFlag("memory", "character_state_llm", True, "是否用 LLM 抽取状态细节（False=仅确定性出场记录，零 LLM 成本）"),
    FeatureFlag("memory", "chapter_state_tracking", True, "章间机制/世界状态账本：每章落盘后抽取章末状态（数字/生死/物品/时间），供下一章承接，防数字断链与死而复生（REQ-章内自洽与机制账本）"),
    FeatureFlag("memory", "chapter_state_llm", True, "是否用 LLM 抽取章末/场景状态（False=仅确定性面板数字正则，零 LLM 成本）"),
    FeatureFlag("memory", "lorebook_trim", True, "按章触发注入（Lorebook 式）：预测出场人物，只注入相关角色卡/伏笔，锁定项永不裁剪（P1-3）"),
    FeatureFlag("memory", "scene_state_mode", "auto", "场景间状态抽取模式（REQ-token优化 P0-1）：auto=高质量模式 LLM/普通模式确定性正则；llm=全部 LLM；deterministic=全部确定性正则（零 LLM）"),
    FeatureFlag("memory", "state_extract_frequency", 3, "章末/人物状态 LLM 抽取频率（REQ-token优化 P0-4）：每 N 章抽 1 次 LLM，其余章走确定性；1=每章"),
    FeatureFlag("memory", "setting_cards", True, "分章设定卡索引（REQ-token优化 P1-1）：world 后确定性构建 setting_cards.json，按章只注入相关设定子集，构建失败回退全量注入"),
    FeatureFlag("memory", "scene_delta_inject", True, "场景 prompt 分层注入（REQ-token优化 P1-2）：首场景注入全量记忆，后续场景只注入增量块（已推进状态+人物卡子集+场景大纲），降重复输入"),
    FeatureFlag("memory", "reveal_tracking", True, "揭示执行跟踪：每章检测 world_reveals 真相关键词是否按计划在正文兑现（提前剧透/到点未揭示），落 reveal_status.json，全书完成汇总报告；无关键词旧数据跳过检测"),
    # ---- writing：正文生成质量 ----
    FeatureFlag("writing", "de_ai", True, "正文生成时注入去AI味写作约束"),
    FeatureFlag("writing", "anti_similarity", True, "防同质化：常驻System提示词，拒绝套路/有代价/只展示不叙述"),
    FeatureFlag("writing", "character_depth", True, "人物立体化：角色卡一致性 + 选择代价 + 生活体感 + 对话辨识度"),
    FeatureFlag("writing", "fanqie_style", True, "番茄节奏：黄金三章 + 爽点闭环 + 断章 + 移动端排版"),
    FeatureFlag("writing", "high_quality_mode", False, "高质量模式：场景级生成 + 动态模块 + 参数分层，3元/万字"),
    FeatureFlag("writing", "pacing_density", "medium", "爽点密度：low(慢热)/medium(适中)/high(密集)"),
    FeatureFlag("writing", "plot_speed", "medium", "剧情推进速度：slow(细腻)/medium(适中)/fast(快节奏)"),
    FeatureFlag("writing", "action_plan_first", False, "正文前独立生成人物行动表（REQ-token优化 P0-2：默认并入拆场景输出，不再独立调用；True=恢复独立调用）"),
    FeatureFlag("writing", "scene_split", True, "场景级生成：单章目标≥2000 字自动拆场景逐段写（普通模式也可用；P1-2）"),
    FeatureFlag("writing", "scene_plan_align", True, "拆场景规划对齐大纲人物动线（REQ-scene-plan-align）：事件清单从大纲 character_moves 逐拍切分确定性派生，LLM 只做场景切分与细节补充；moves 缺失/派生失败自动回退原 LLM 规划路径"),
    FeatureFlag("writing", "polish_hq_only", True, "整章润色仅高质量模式启用（REQ-token优化 P0-3：普通模式跳过润色，省整章输入+输出；False=普通模式也润色）"),
    FeatureFlag("writing", "teardown_style_anchor", True, "拆书库题材文风锚（REQ-双锚风格锚定）：写手阶段注入同题材爆款文风与手法（弱约束，只借鉴语感严禁抄袭）"),
    # ---- review：审校质检 ----
    FeatureFlag("review", "editor_review", True, "大纲生成后执行编辑视角质检，不通过则自动修正一次"),
    FeatureFlag("review", "ai_taste_check", True, "AI 痕迹量化检测：成文后按确定性规则给分，低于阈值扣分产出 issue（P1-4）"),
    FeatureFlag("review", "ai_taste_threshold", 60, "AI 痕迹分阈值（0-100），低于则触发扣分（P1-4）"),
    FeatureFlag("review", "final_review", False, "全书终审：整本书写完后的终审编辑（高质量模式默认开启；P2-2）"),
    FeatureFlag("review", "stage_gate", True, "多Agent阶段门禁：每阶段产出后准出校验，不合格自动重生成（REQ 多Agent协作分层优化）"),
    FeatureFlag("review", "stage_gate_retries", 2, "阶段门禁最大自动重生成轮次，超限由总控仲裁放行（REQ 多Agent协作分层优化）"),
    FeatureFlag("review", "consistency_llm_check", False, "章内前后矛盾 LLM 自检（REQ-token优化 P1-3）：默认仅高质量模式开启；普通模式按 consistency_llm_every 降频兜底"),
    FeatureFlag("review", "consistency_llm_every", 3, "章内矛盾 LLM 自检降频（REQ-章内自洽与机制账本·扩展）：普通模式每 N 章抽 1 次（1=每章；高质量模式不受此限）"),
    FeatureFlag("review", "targeted_rewrite", True, "定点重写（REQ-token优化 P1-4）：质检触发重写时只重写问题段落窗口，不整章重出；无定位点退回复核回整章"),
    FeatureFlag("review", "verify_combined", True, "一致性校验合并（REQ-token优化 P1-5）：每 N 章一次调用输出一致性/读者/编辑三类结果，替代 3 连调用"),
    FeatureFlag("review", "verify_every", 5, "一致性校验触发频率（REQ-token优化 P1-5）：每 N 章一次（原每 3 章）"),
    FeatureFlag("review", "editor_diff_revise", True, "编辑质检修正改 diff 式（REQ-token优化 P1-6）：只输出需改字段，Python 合并回大纲，不重吐全大纲 JSON"),
    # ---- guard：成本防护（AgentGuardConfig 承载，环境变量可覆盖）----
    FeatureFlag("guard", "enabled", True, "AgentGuard 总开关"),
    FeatureFlag("guard", "max_loops", 200, "单任务最大 LLM 调用次数"),
    FeatureFlag("guard", "max_tokens", 2_000_000, "单任务累计 Token 上限"),
    FeatureFlag("guard", "input_check", True, "输入注入检测"),
    FeatureFlag("guard", "output_check", True, "输出基础合规校验"),
    FeatureFlag("guard", "quality_check", True, "章级 ReaderAgent 结果自校验（agent_guard.quality_check）"),
]


def feature_default(key: str) -> object:
    """按 key 取开关默认值（NovelConfig 字段默认值引用，保证单一事实来源）。"""
    for f in FEATURE_FLAGS:
        if f.key == key:
            return f.default
    raise KeyError(f"FEATURE_FLAGS 中未注册开关: {key}（新增开关请先在 config.py 注册）")


def feature_modules() -> list[str]:
    """返回已注册的功能模块（保持注册顺序、去重）。"""
    seen: list[str] = []
    for f in FEATURE_FLAGS:
        if f.module not in seen:
            seen.append(f.module)
    return seen


def list_feature_flags(module: str | None = None) -> list[FeatureFlag]:
    """按模块筛选开关清单（module=None 返回全部）。"""
    return [f for f in FEATURE_FLAGS if module is None or f.module == module]


def _coerce_feature(raw, default):
    """把 config.json 里的原始值按默认值类型转换；缺失/None 用默认值。"""
    if raw is None:
        return default
    if isinstance(default, bool):
        return bool(raw)
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return str(raw) or default
    return raw


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
    # ---- 质量增强开关（默认值见 FEATURE_FLAGS 注册表；新增开关先注册）----
    editor_review: bool = feature_default("editor_review")       # 大纲生成后执行编辑视角质检，不通过则自动修正一次
    de_ai: bool = feature_default("de_ai")                       # 正文生成时注入去AI味写作约束
    anti_similarity: bool = feature_default("anti_similarity")   # 防同质化：常驻System提示词，拒绝套路/人物内在矛盾/有代价/只展示不叙述
    character_depth: bool = feature_default("character_depth")   # 人物立体化：角色卡一致性 + 选择代价 + 生活体感细节 + 出场标签/对话辨识度
    fanqie_style: bool = feature_default("fanqie_style")         # 番茄节奏：黄金三章开篇纪律 + 爽点四环节 + 章末断章 + 移动端排版 + 轻量世界观/力量体系
    confirm: bool = False           # True：每阶段生成后暂停等待确认；False：自动推进
    # ---- memory：记忆与长文一致性（P0-1/P0-2，默认值见 FEATURE_FLAGS）----
    memory_window: int = feature_default("memory_window")        # 生成当前章时回填最近 N 章摘要，控制上下文成本
    memory_direct_read_chapters: int = feature_default("memory_direct_read_chapters")  # 前文直读：直读最近 N 章正文（0=关闭，保持纯摘要）
    memory_direct_read_chars: int = feature_default("memory_direct_read_chars")        # 每章直读正文最大字符数（取章节尾部）
    character_state_tracking: bool = feature_default("character_state_tracking")       # 每章正文落盘后更新人物动态状态快照
    character_state_llm: bool = feature_default("character_state_llm")                 # 是否用 LLM 抽取状态细节（False=仅确定性出场记录）
    chapter_state_tracking: bool = feature_default("chapter_state_tracking")           # 章间机制/世界状态账本：每章落盘后抽取章末状态供下一章承接
    chapter_state_llm: bool = feature_default("chapter_state_llm")                     # 是否用 LLM 抽取章末/场景状态（False=仅确定性面板数字正则）
    lorebook_trim: bool = feature_default("lorebook_trim")                             # 按章触发注入（Lorebook 式裁剪，P1-3）
    scene_state_mode: str = feature_default("scene_state_mode")                        # 场景间状态抽取模式：auto/llm/deterministic（REQ-token优化 P0-1）
    state_extract_frequency: int = feature_default("state_extract_frequency")          # 章末/人物状态 LLM 抽取频率：每 N 章 1 次（REQ-token优化 P0-4）
    setting_cards: bool = feature_default("setting_cards")                             # 分章设定卡索引（REQ-token优化 P1-1）
    scene_delta_inject: bool = feature_default("scene_delta_inject")                   # 场景 prompt 分层注入（REQ-token优化 P1-2）
    reveal_tracking: bool = feature_default("reveal_tracking")                         # 揭示执行跟踪（REQ-揭示执行跟踪）：每章检测真相锚定词兑现，落 reveal_status.json
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
    style_role_reference: str = ""    # P2-1 few-shot 文风锚定：风格角色携带的参考文风片段（创建项目时快照，注入正文）
    # ---- 节奏控制参数（默认值见 FEATURE_FLAGS）----
    pacing_density: str = feature_default("pacing_density")      # 爽点密度：low(慢热)/medium(适中)/high(密集)
    plot_speed: str = feature_default("plot_speed")              # 剧情推进速度：slow(细腻)/medium(适中)/fast(快节奏)
    action_plan_first: bool = feature_default("action_plan_first")  # 正文前独立生成人物行动表（REQ-token优化 P0-2：默认并入拆场景输出）
    scene_split: bool = feature_default("scene_split")              # 场景级生成下沉普通模式（单章≥2000字自动拆；P1-2）
    scene_plan_align: bool = feature_default("scene_plan_align")    # 拆场景规划对齐大纲人物动线（REQ-scene-plan-align）
    polish_hq_only: bool = feature_default("polish_hq_only")        # 整章润色仅高质量模式（REQ-token优化 P0-3）
    teardown_style_anchor: bool = feature_default("teardown_style_anchor")  # 拆书库题材文风锚：写手阶段注入同题材爆款文风与手法（REQ-双锚风格锚定）
    ai_taste_check: bool = feature_default("ai_taste_check")        # AI 痕迹量化检测开关（P1-4）
    ai_taste_threshold: int = feature_default("ai_taste_threshold") # AI 痕迹分阈值（P1-4）
    final_review: bool = feature_default("final_review")             # 全书终审：整本书写完后的终审编辑（高质量模式默认开启；P2-2）
    stage_gate: bool = feature_default("stage_gate")                 # 多Agent阶段门禁：每阶段产出后准出校验（REQ 多Agent协作分层优化）
    stage_gate_retries: int = feature_default("stage_gate_retries")  # 阶段门禁最大自动重生成轮次（REQ 多Agent协作分层优化）
    consistency_llm_check: bool = feature_default("consistency_llm_check")  # 章内矛盾 LLM 自检：默认仅高质量模式（REQ-token优化 P1-3）
    consistency_llm_every: int = feature_default("consistency_llm_every")   # 章内矛盾 LLM 自检降频：普通模式每 N 章 1 次（REQ-章内自洽与机制账本·扩展）
    targeted_rewrite: bool = feature_default("targeted_rewrite")     # 定点重写（REQ-token优化 P1-4）
    verify_combined: bool = feature_default("verify_combined")       # 一致性校验合并（REQ-token优化 P1-5）
    verify_every: int = feature_default("verify_every")              # 一致性校验频率：每 N 章一次（REQ-token优化 P1-5）
    editor_diff_revise: bool = feature_default("editor_diff_revise") # 编辑质检 diff 式修正（REQ-token优化 P1-6）
    high_quality_mode: bool = feature_default("high_quality_mode")  # 高质量模式：场景级生成 + 动态模块 + 参数分层，3元/万字
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


if __name__ == "__main__":
    # python -m novel_gen.config：按功能模块打印开关清单（集中查看/排查用）
    print("功能开关注册表（FEATURE_FLAGS）· 按模块分组\n")
    for _mod in feature_modules():
        print(f"[{_mod}]")
        for _f in list_feature_flags(_mod):
            print(f"  {_f.key:36s} = {_f.default!r:<8s} {_f.desc}")
        print()

