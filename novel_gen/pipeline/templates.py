# -*- coding: utf-8 -*-
"""
提示词模板：各生成阶段的 system / user 提示词。

设计原则
- 每个阶段都把「调研报告」注入，确保全书方向受共性调研约束
- 用户自定义设定分两级注入：
    强约束（background/hero/characters/forbidden）→ 必须严格遵循，不得违背
    参考方向（plot_direction）→ 可在此基础上细化发挥
- 正文阶段额外注入：本章大纲目标 + 上一章结尾 + 记忆上下文（设定库+近期摘要）
- 章节输出约定为「正文 + 【摘要】」，摘要直接回写记忆库，不再额外调用模型
"""
from __future__ import annotations

from pathlib import Path

import random as _random

from ..config import NovelConfig
from ..store.schemas import Outline
from .style_anchor import PERSONAL_ANCHOR_HEADER, TEARDOWN_ANCHOR_HEADER, wrap_style_anchor


#: prompts/rules/ 目录下的纯文本规则块（§11.3 代码与数据分离）
_RULES_DIR = Path(__file__).resolve().parent / "agents" / "prompts" / "rules"
_RULE_CACHE: dict[str, str] = {}


def _load_rule(name: str) -> str:
    """加载 rules/{name}.md 纯文本规则块；失败返回空串不阻塞（mtime 缓存）。"""
    if name in _RULE_CACHE:
        return _RULE_CACHE[name]
    try:
        text = (_RULES_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    except Exception:
        text = ""
    _RULE_CACHE[name] = text
    return text


def _hard_constraints(cfg: NovelConfig) -> str:
    """强约束区块：背景/主角/预设人物/避雷，必须严格遵循。仅非空字段才显示。"""
    lines = []
    if cfg.background:
        lines.append(f"- 故事背景/世界观：{cfg.background}")
    if cfg.hero:
        lines.append(f"- 主角设定：{cfg.hero}")
    if cfg.characters:
        lines.append(f"- 预设人物（必须出现并符合设定）：{'；'.join(cfg.characters)}")
    if cfg.forbidden:
        lines.append(f"- 避雷（绝对不得出现）：{'；'.join(cfg.forbidden)}")
    if not lines:
        return ""
    return "【用户设定·强约束（必须严格遵循，不得违背）】\n" + "\n".join(lines)


def _pacing_guide(cfg: NovelConfig) -> str:
    """节奏控制引导：根据用户选择的爽点密度和剧情速度，注入对应节奏建议。"""
    parts = []
    density = getattr(cfg, "pacing_density", "medium")
    speed = getattr(cfg, "plot_speed", "medium")
    if density == "high":
        parts.append("爽点密度高：每章至少1个小爽点/小反转，压抑期不超过2章，节奏紧凑")
    elif density == "low":
        parts.append("爽点密度低：慢热型，重氛围铺垫和人物关系，前10章不急着给爽点，靠悬念和生活感吸引")
    else:
        parts.append("爽点密度适中：快2慢1交替，每2-3章一个小爽点")
    if speed == "fast":
        parts.append("剧情推进快：信息密度高，每章推进多个情节点，减少日常描写")
    elif speed == "slow":
        parts.append("剧情推进慢：细腻型，重细节和情绪，允许日常铺垫，不急于推进主线")
    else:
        parts.append("剧情推进适中：按标准网文节奏推进")
    return "【节奏控制】\n" + "；".join(parts)


def _soft_direction(cfg: NovelConfig) -> str:
    """参考方向区块：剧情走向，AI 可在此基础上细化。"""
    lines = []
    if cfg.plot_direction:
        lines.append(f"- 剧情走向：{cfg.plot_direction}")
    pacing = _pacing_guide(cfg)
    if pacing:
        lines.append(pacing)
    if not lines:
        return ""
    return "【用户设定·参考方向（可在此基础上细化发挥）】\n" + "\n".join(lines)


def _chapter_constraints(cfg: NovelConfig) -> str:
    """正文阶段的精简强约束提醒（背景+主角+避雷），每章注入防跑偏。"""
    lines = []
    if cfg.background:
        lines.append(f"背景：{cfg.background}")
    if cfg.hero:
        lines.append(f"主角：{cfg.hero}")
    if cfg.forbidden:
        lines.append(f"避雷（不得出现）：{'；'.join(cfg.forbidden)}")
    if not lines:
        return ""
    return "【强约束提醒】" + "｜".join(lines)


def _de_ai_rules() -> str:
    """去AI味写作约束（基于 human-signal 模式库提炼，适用于小说正文）。"""
    return _load_rule("de_ai")


def _output_discipline() -> str:
    """正文输出纪律（硬约束）：防止同章多稿拼接、版本并列、元文字开头与中途截断。"""
    return _load_rule("output_discipline")


def _writing_constraints() -> str:
    """正文写作约束（内容/写作类，防止「金手指不自洽、细节漂移、钩子太晚、母题口癖」）。"""
    return _load_rule("writing_constraints")


def _ending_requirements() -> str:
    """结局硬要求：无论长篇短篇，全书必须有完整结局。"""
    return _load_rule("ending_requirements")


# ---------- 防同质化常驻 System 提示词（用户提供，转译为程序内规则） ----------

def _anti_similarity_system(stage: str = "chapter") -> str:
    """防同质化常驻 System 提示词。stage 决定注入深度（outline/world/chapter）。"""
    core = _load_rule("anti_similarity_core")
    blacklist = f"【全网套路永久黑名单（严格禁止，违规即重写）】\n{_load_rule('anti_similarity_blacklist')}"
    extra = _load_rule(f"anti_similarity_{stage}")
    return core + "\n\n" + blacklist + "\n\n" + extra

def _skill_append(cfg: NovelConfig, stage: str = "") -> str:
    """用户可控内容拼入 System Prompt 前的统一组装（REQ-技能统一，复用 collect_project_blocks）。

    custom_prompt / skill_prompt_append 均为用户可写内容，若含注入话术
    （忽略以上指令/修改系统设定等）可篡改系统约束，拼入前统一清洗。
    stage 参数预留（项目级快照无阶段元数据，始终注入），保持既有输出逐字节一致。
    """
    from ..pipeline.skills import collect_project_blocks
    text, _manifest = collect_project_blocks(cfg, stage)
    return text

def _character_depth_rules(stage: str = "chapter") -> str:
    """人物立体化写作约束：解决人物扁平、情绪干瘪、千人一面。"""
    return _load_rule(f"character_depth_{stage}")



# ---------- 番茄平台写作纪律（基于《番茄小说平台专属写作技巧指南》提炼） ----------

def _fanqie_rules(stage: str = "chapter") -> str:
    """番茄平台写作纪律：黄金三章开篇 / 防崩文大纲 / 轻量世界观力量体系 / 节奏爽点断章 / 移动端排版。"""
    return _load_rule(f"fanqie_{stage}")


def expand_outline_prompt(
    cfg: NovelConfig,
    user_input: str,
    batch: tuple[int, int, str] | None = None,
) -> tuple[str, str]:
    """AI 扩写模式：把用户提供的大纲/简要内容拆解为结构化章节大纲。

    - batch=None：只生成总纲（title/intro/core_conflict/main_line/volumes，不含 chapters）
    - batch=(start_no, end_no, prev_summary)：分批生成该区间章节，输出 {"chapters": [...]}
    分批是为了避免一次性输出过长 JSON 被 max_tokens 截断（DeepSeek 上限 8192）。
    """
    system = (
        "你是一位擅长「扩写」的小说主编。用户会给你一段故事大纲或简要内容，"
        "你的任务是把它整理成一份可直接逐章扩写的结构化章节大纲。"
        "严格遵循用户的设定与剧情走向，不得擅自改变用户的核心创意与人物关系，"
        "只在用户内容基础上细化展开、补充必要的起承转合。"
    )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("outline")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("outline")
    hard = _hard_constraints(cfg)
    soft = _soft_direction(cfg)
    common_head = (
        f"【目标】把下面的用户内容扩写为一部 {cfg.genre} 题材小说"
        f"（全书约 {cfg.total_words} 字，共 {cfg.num_chapters} 章，每章约 {cfg.chapter_words} 字）\n"
        f"【书名】{cfg.effective_title()}\n"
        f"【用户提供的核心创意/简介】{cfg.premise or '（未提供）'}\n\n"
        f"{hard}\n\n{soft}\n\n"
        f"【用户提供的大纲或简要内容】\n{user_input}\n\n"
    )
    if batch is not None:
        start_no, end_no, prev_summary = batch
        user = (
            common_head
            + f"【本次分批范围】你现在只设计全书第 {start_no}—{end_no} 章（共 {end_no - start_no + 1} 章）。\n"
            + (f"【前情提要（已规划章节的剧情摘要）】\n{prev_summary}\n\n" if prev_summary else "")
            + f"请注意：本章段是全书的一部分，要忠实于用户提供的内容脉络，与前情连贯、伏笔呼应。\n"
            + f"请输出 JSON：\n"
            + f"- chapters：恰好 {end_no - start_no + 1} 个章节对象的数组，每项包含字段：no(序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(埋设/回收的伏笔), emotion(情绪价值), transition(转场提示), tag(节点标签), cliffhanger(章末钩子)，章节标题必须互不相同、全书唯一，严禁重名章\n"
            + f"（本次只输出本区间章节，序号必须从 {start_no} 连续编号到 {end_no}，不要输出其他章节。）\n"
            + f"要求：\n"
            + f"1. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」；core_event/conflict/character_moves 必须写具体剧情细节，禁止留空或套话\n"
            + f"2. 用户预设的人物必须出现且符合设定；避雷元素绝对不得出现\n"
            + f"3. 章节剧情必须忠实于用户提供的内容脉络，只做细化与合理延展\n"
            + (f"4. 本批包含全书第 {cfg.num_chapters} 章（最后一章），它必须是完整的结局章：收束主线、伏笔与人物命运，并以「结尾升维」收尾；禁止烂尾/未完待续\n" if end_no >= cfg.num_chapters else "")
        )
    else:
        user = (
            common_head
            + f"请输出 JSON（只输出总纲结构，不生成具体章节）：\n"
            + f"- title：最终书名（沿用给定书名或从内容提炼）\n"
            + f"- intro：作品简介（3-5 句）\n"
            + f"- core_conflict：全书核心冲突（一句话）\n"
            + f"- main_line：全书主线脉络（3-5 句）\n"
            + f"- volumes：分卷数组，按用户大纲中的「第X卷」或剧情阶段归纳为3-5卷；若用户大纲未分卷则按起承转合划分。每卷字段：no, name(卷名), intro(本卷核心主题), main_conflict(本卷主要矛盾), character_growth(本卷人物成长), climax(本卷高潮), start_ch(起始章号), end_ch(结束章号)\n"
        )
    system += _skill_append(cfg)
    return system, user


# 题材时间线预设
TIMELINE_PRESETS = {
    "default": [{"name": "主线", "type": "顺叙", "time_flow": "正常"}],
    "玄幻": [
        {"name": "修炼主线", "type": "顺叙", "time_flow": "正常"},
        {"name": "秘境支线", "type": "读档", "time_flow": "内部时间流速不同"},
    ],
    "都市": [
        {"name": "现实主线", "type": "顺叙", "time_flow": "正常"},
        {"name": "情感/职场支线", "type": "顺叙", "time_flow": "正常"},
    ],
    "悬疑": [
        {"name": "查案主线", "type": "顺叙", "time_flow": "正常"},
        {"name": "真相暗线", "type": "圆环", "time_flow": "过去逐步揭露"},
    ],
    "科幻": [
        {"name": "现实主线", "type": "顺叙", "time_flow": "正常"},
        {"name": "梦境/循环支线", "type": "纯循环", "time_flow": "一夜=一生"},
        {"name": "历史暗线", "type": "圆环", "time_flow": "几十年前"},
    ],
    "西幻": [
        {"name": "冒险主线", "type": "顺叙", "time_flow": "正常"},
        {"name": "魔法/神谕支线", "type": "读档", "time_flow": "感知时间不同"},
    ],
}


def _get_timeline_preset(genre: str) -> list[dict]:
    """根据题材推荐时间线结构。"""
    for key, presets in TIMELINE_PRESETS.items():
        if key in genre.lower() or genre in key:
            return presets
    return TIMELINE_PRESETS["default"]


def timeline_design_prompt(cfg: NovelConfig, genre: str, title: str, intro: str, total_chapters: int) -> tuple[str, str]:
    """大纲生成后，设计时间线结构。低温 0.4。"""
    presets = _get_timeline_preset(genre)
    presets_text = "\n".join(
        f"- {p['name']}（{p['type']}，{p['time_flow']}）" for p in presets
    )
    system = (
        "你是一位资深网文结构编辑。用户给你一本小说的题材和简介，"
        "请设计这本书的时间线结构。必须有1条顺叙主线，可加1-2条支线。"
        "总时间线不超过3条。输出 JSON。"
    )
    user = (
        f"【书名】{title}\n"
        f"【题材】{genre}\n"
        f"【简介】{intro[:800]}\n"
        f"【总章节数】{total_chapters}\n\n"
        f"【推荐时间线（可调整）】\n{presets_text}\n\n"
        "请输出 JSON：\n"
        '{"timelines": [\n'
        '  {"name": "主线名", "type": "顺叙/圆环/读档/纯循环", "time_flow": "时间流速", "description": "这条线讲什么"}\n'
        ']}'
    )
    return system, user


def timeline_hint_for_chapter(timelines: list, chapter_timeline: str) -> str:
    """给单章生成注入时间线提示。"""
    if not timelines or not chapter_timeline:
        return ""
    tl = next((t for t in timelines if t.name == chapter_timeline), None)
    if not tl:
        return ""
    return (
        f"【本章时间线】{tl.name}（{tl.type}）\n"
        f"【时间流速】{tl.time_flow}\n"
        f"【这条线的定位】{tl.description}\n"
    )


def unit_design_prompt(cfg: NovelConfig, genre: str, title: str, intro: str,
                       total_chapters: int, timelines: list) -> tuple[str, str]:
    """时间线设计后，按3章一个副本拆分叙事单元。低温 0.4。"""
    timelines_text = "\n".join(
        f"- {t.name}（{t.type}）：{t.description}" for t in timelines
    ) if timelines else "- 主线（顺叙）"
    system = (
        "你是一位资深网文结构编辑。用户给你一本小说的时间线结构，"
        "请把全书按每3章一个副本单元拆分。每个副本是一个完整的小事件，"
        "有起因→经过→结果三段式。输出 JSON。"
    )
    user = (
        f"【书名】{title}\n"
        f"【题材】{genre}\n"
        f"【简介】{intro[:600]}\n"
        f"【总章节数】{total_chapters}\n"
        f"【时间线结构】\n{timelines_text}\n\n"
        "请按每3章一个副本拆分，输出 JSON：\n"
        '{"units": [\n'
        '  {"no": 1, "name": "副本名", "type": "梦境/现实/暗线", "chapters": [1,2,3],\n'
        '   "core_payoff": "这个副本的核心结局/记忆点",\n'
        '   "key_nodes": ["节点1", "节点2", "节点3"],\n'
        '   "harvest": "主角收获（残留/线索/认知/关系）",\n'
        '   "next_hook": "埋下一副本的钩子"}\n'
        ']}'
    )
    return system, user


def unit_hint_for_chapter(units: list, chapter_no: int) -> str:
    """给单章生成注入副本上下文。"""
    if not units:
        return ""
    unit = next((u for u in units if chapter_no in u.chapters), None)
    if not unit:
        return ""
    # 判断本章在副本中的位置
    idx = unit.chapters.index(chapter_no)
    position = ["起因段", "经过段", "结果段"][idx] if idx < 3 else "经过段"
    return (
        f"【当前副本】{unit.name}（第{unit.no}个副本，{unit.type}）\n"
        f"【副本核心落点】{unit.core_payoff}\n"
        f"【本章位置】{position}\n"
        f"【本副本收获】{unit.harvest}\n"
    )




def anti_cliche_prompt(cfg: NovelConfig, genre: str, title: str, intro: str,
                       research_block: str) -> tuple[str, str]:
    """调研后，反套路改造+元素叠加+人设标签设计。低温 0.4。"""
    system = (
        "你是一位爆款网文策划编辑。用户给你题材和调研数据，"
        "请做三件事：1.反套路改造核心冲突和人物关系 2.推荐2-3个跨维度加成元素 "
        "3.设计主角身份标签和反差特质、主CP关系。输出 JSON。"
    )
    user = (
        f"【书名】{title}\n"
        f"【题材】{genre}\n"
        f"【简介】{intro[:500]}\n"
        f"【调研数据】{research_block[:2000]}\n\n"
        "请输出 JSON：\n"
        '{"anti_cliche": {\n'
        '  "conflict_rewrite": "核心冲突改造（从被动改主动）",\n'
        '  "relationship_rewrite": "关系改造（从内斗改抱团）",\n'
        '  "value_rewrite": "价值改造（从单赢改双向救赎）"\n'
        '},\n'
        '"elements": ["元素1", "元素2", "元素3"],\n'
        '"protagonist": {"label": "身份标签", "contrast": "反差特质", "arc": "成长弧光"},\n'
        '"main_cp": {"name": "CP名", "contrast": "反差互补点", "dynamic": "关系张力"}\n'
        '}'
    )
    return system, user


def anti_cliche_hint(anti_cliche: dict) -> str:
    """注入正文生成的反套路约束。"""
    if not anti_cliche:
        return ""
    lines = []
    ac = anti_cliche.get("anti_cliche", {})
    if ac:
        lines.append("【反套路约束】")
        if ac.get("conflict_rewrite"):
            lines.append(f"- 冲突：{ac['conflict_rewrite']}")
        if ac.get("relationship_rewrite"):
            lines.append(f"- 关系：{ac['relationship_rewrite']}")
        if ac.get("value_rewrite"):
            lines.append(f"- 价值：{ac['value_rewrite']}")
    p = anti_cliche.get("protagonist", {})
    if p:
        lines.append(f"【主角标签】{p.get('label','')}（反差：{p.get('contrast','')}）")
    cp = anti_cliche.get("main_cp", {})
    if cp:
        lines.append(f"【主CP】{cp.get('name','')}：{cp.get('dynamic','')}")
    elements = anti_cliche.get("elements", [])
    if elements:
        lines.append(f"【叠加元素】{'、'.join(elements)}")
    return "\n".join(lines)


def editor_review_text_prompt(cfg: NovelConfig, chapter_no: int,
                              chapter_title: str, content: str,
                              prev_summaries: list) -> tuple[str, str]:
    """正文阶段编辑审稿：模拟资深网文编辑审阅刚写完的3章。低温 0.3。"""
    summaries_text = "\n".join(
        f"第{len(prev_summaries)-i}章：{s}" for i, s in enumerate(reversed(prev_summaries[-3:]))
    )
    system = (
        "你是一位资深网文编辑，正在审阅刚写完的3章正文。"
        "你要判断这3章能不能留住读者、有没有退稿风险。"
        "按 JSON 格式输出审稿结果。"
    )
    user = (
        f"【刚写完的3章摘要】\n{summaries_text}\n\n"
        f"【第{chapter_no}章正文片段】\n{content[:4000]}\n\n"
        "请从以下维度审稿，输出 JSON：\n"
        '{\n'
        '  "score": 1-10的商业潜力评分,\n'
        '  "hook_strong": true/false（开篇钩子够不够强）,\n'
        '  "protagonist_active": true/false（主角是否主动）,\n'
        '  "emotion_payoff": true/false（情绪回报是否到位）,\n'
        '  "chapter_hook": true/false（章末有没有留悬念）,\n'
        '  "reader_churn_risk": "低/中/高",\n'
        '  "issues": ["问题1", "问题2"],\n'
        '  "suggestions": ["建议1", "建议2"]\n'
        '}'
    )
    return system, user




def outline_prompt(
    cfg: NovelConfig,
    research_block: str,
    batch: tuple[int, int, str] | None = None,
) -> tuple[str, str]:
    """构造大纲生成提示词。

    batch: (start_no, end_no, 前情提要) —— 非 None 时分批生成该区间章节；
    用于超长篇（150 章级）单次输出放不下时，按区间分批生成后合并。
    """
    system = (
        "你是一位经验丰富的网络小说主编兼金牌写手，深谙平台爆款规律与读者心理。"
        "你的任务是根据『题材共性调研报告』与『用户设定』，为用户设计一份可直接落地成文的"
        "全书大纲。用户的强约束（背景/主角/预设人物/避雷）必须严格遵循，不得违背；"
        "剧情走向为参考方向，可在此基础上细化。大纲要兼顾爽感节奏与结构完整性，杜绝过时套路。"
    )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("outline")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("outline")
    hard = _hard_constraints(cfg)
    soft = _soft_direction(cfg)

    if batch is not None:
        start_no, end_no, prev_summary = batch
        user = (
            f"【目标】创作一本 {cfg.genre} 题材小说"
            f"（全书约 {cfg.total_words} 字，共 {cfg.num_chapters} 章，每章约 {cfg.chapter_words} 字）\n"
            f"【书名】{cfg.effective_title()}\n"
            f"【核心创意/简介】{cfg.premise or '（未提供，按题材常规发挥）'}\n"
            f"【文风要求】{cfg.style or '（未指定，按调研建议混合）'}\n\n"
            f"{hard}\n\n{soft}\n\n"
            f"【题材共性调研报告】\n{research_block}\n\n"
            f"【本次分批范围】你现在只设计全书第 {start_no}—{end_no} 章（共 {end_no - start_no + 1} 章）。\n"
            f"{('【前情提要（已规划章节的剧情摘要）】\n' + prev_summary + '\n\n') if prev_summary else ''}"
            f"请注意：本章段是全书的一部分，要与前情连贯、伏笔呼应，并推动全书主线。\n"
            f"请输出 JSON：\n"
            f"- chapters：恰好 {end_no - start_no + 1} 个章节对象的数组，每项包含字段：no(序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(埋设/回收的伏笔), emotion(情绪价值), transition(转场提示), tag(节点标签), cliffhanger(章末钩子，本章末尾抛给读者的强钩子), volume_no(所属分卷编号，必须落在对应分卷的 start_ch~end_ch 区间内)，章节标题必须互不相同、全书唯一，严禁重名章\n"
            f"（本次只输出本区间章节，序号必须从 {start_no} 连续编号到 {end_no}，不要输出其他章节。）\n"
            f"大纲要求：\n"
            f"1. 每章除 goal 外，必须完整填写所有结构化字段：core_event(核心事件，30字以上), conflict(冲突设计，20字以上), character_moves(人物动线，20字以上), foreshadowing(伏笔/回收，无则填无), emotion(情绪价值，如爽点/虐点/悬念), transition(转场提示，与前后章如何衔接), tag(节点标签)。禁止任何字段留空或只填\"无\"，core_event和conflict必须写具体剧情，不能套话\n"
            f"2. 章节内部与跨章要有起承转合：冲突、伏笔、情绪递进；每章目标尽量不重复\n"
            f"3. 必须包含用户预设的主角与人物；避雷元素绝对不得出现；剧情走向需在本区间体现\n"
            f"{('4. 本批包含全书第 ' + str(cfg.num_chapters) + ' 章（最后一章），它必须是完整的结局章：goal 写清如何收束主线冲突与关键伏笔、完成主角人物弧光闭环（与开篇对照）、给出情绪落点（允许有代价/遗憾/两难），并以「结尾升维」（技术→哲学/产品→人性/事件→时代）收尾\n' if end_no >= cfg.num_chapters else '')}"
            f"{('5' if end_no >= cfg.num_chapters else '4')}. 防同质化：剧情分叉避开Top1热门套路，优先次优解/有遗憾/两难抉择；每章目标要体现代价与人物抉择"
            f"{'；结局允许不完美但必须完整（禁止烂尾/未完待续）' if end_no >= cfg.num_chapters and cfg.anti_similarity else ''}"
        )
    else:
        user = (
            f"【目标】创作一本 {cfg.genre} 题材小说"
            f"（全书约 {cfg.total_words} 字，共 {cfg.num_chapters} 章，每章约 {cfg.chapter_words} 字）\n"
            f"【书名】{cfg.effective_title()}\n"
            f"【核心创意/简介】{cfg.premise or '（未提供，按题材常规发挥）'}\n"
            f"【文风要求】{cfg.style or '（未指定，按调研建议混合）'}\n\n"
            f"{hard}\n\n{soft}\n\n"
            f"【题材共性调研报告】\n{research_block}\n\n"
            f"请输出 JSON（三级大纲结构）：\n"
            f"- title：最终书名（如可用则沿用给定书名）\n"
            f"- intro：作品简介（3-5 句，含卖点与情绪钩子）\n"
            f"- core_conflict：全书核心冲突（一句话，L1总纲层）\n"
            f"- main_line：全书主线脉络（3-5句概括起承转合，L1总纲层）\n"
            f"- volumes：分卷数组，将全书按剧情阶段分为3-5卷，每卷字段：no, name(卷名), intro(本卷核心主题), main_conflict(本卷主要矛盾), character_growth(本卷人物成长), climax(本卷高潮), start_ch(起始章), end_ch(结束章)\n"
            f"- chapters：恰好 {cfg.num_chapters} 个章节对象的数组（L3章节小纲层），每项包含字段：no(序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(埋设/回收的伏笔), emotion(情绪价值), transition(转场提示), tag(节点标签), cliffhanger(章末钩子，本章末尾抛给读者的强钩子), volume_no(所属分卷编号，必须落在对应分卷的 start_ch~end_ch 区间内)，章节标题必须互不相同、全书唯一，严禁重名章\n"
            f"大纲要求：\n"
            f"1. 三级结构：先定总纲（core_conflict+main_line），再分卷（volumes），最后章节小纲（chapters）；章节归属对应分卷的start_ch~end_ch区间\n"
            f"2. 开篇 3 章内立住主角、抛出核心冲突与悬念（遵循调研的开篇策略）\n"
            f"3. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」；core_event/conflict/character_moves 必须写具体剧情细节，禁止留空或套话\n"
            f"4. 整体要有起承转合：开局→升级→中段冲突与伏笔→高潮→收束；每章目标尽量不重复\n"
            f"5. 伏笔与人物关系要跨章节连贯\n"
            f"6. 必须包含用户预设的主角与人物；避雷元素绝对不得出现；剧情走向需在大纲中体现\n"
            f"7. 全书第 {cfg.num_chapters} 章（最后一章）必须是完整的结局章：goal 必须写清如何收束主线冲突与关键伏笔、"
            f"完成主角的人物弧光闭环（与开篇形成对照）、给出情绪落点（允许有代价/遗憾/两难，不强行圆满），"
            f"并以「结尾升维」（技术→哲学/产品→人性/事件→时代）收尾\n"
            f"{'8. 防同质化：剧情分叉避开Top1热门套路，优先次优解/有遗憾/两难抉择；结局允许不完美但必须完整（禁止烂尾/未完待续）；每章目标要体现代价与人物抉择' if cfg.anti_similarity else ''}"
        )
    system += _skill_append(cfg)
    return system, user


def world_prompt(
    cfg: NovelConfig, outline: Outline, research_block: str
) -> tuple[str, str]:
    system = (
        "你是一位设定严谨的小说世界观架构师。基于大纲、题材调研与用户设定，"
        "设计本书的世界观、人物卡与伏笔清单，作为全书记忆库供逐章写作引用。"
        "用户预设的主角与人物必须保留并符合其设定，可在此基础上补充其他角色。"
    )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("world")
    if cfg.character_depth:
        system += "\n\n" + _character_depth_rules("world")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("world")
    hard = _hard_constraints(cfg)
    outline_text = "\n".join(
        f"第{c.no}章《{c.title}》：{c.goal}" for c in outline.chapters[:20]
    ) + (f"\n……（后略，共 {len(outline.chapters)} 章）" if len(outline.chapters) > 20 else "")
    user = (
        f"【题材】{cfg.genre}\n"
        f"【书名/简介】{outline.title}｜{outline.intro}\n"
        f"{hard}\n\n"
        f"{('【题材共性调研报告】\n' + research_block + '\n\n') if research_block else ''}"
        f"【大纲（节选）】\n{outline_text}\n\n"
        f"请输出 JSON：\n"
        f"- world_view：世界观设定（2-4 句：世界格局/力量体系/时代背景/核心矛盾），必须符合用户给定的故事背景\n"
        f"- world_reveals：世界观逐步揭示计划数组（跟随主角视角逐步展开，禁止一次写全）。"
        f"每项 {{\"foreshadow_ch\": 铺垫最早章（从此章起正文可埋线索/暗示，须 < from_ch）, "
        f"\"hint\": 铺垫要点（揭示前正文应出现的异常/线索/疑惑，主角与读者可感知的\"不对劲\"，但不得说出真相，1-2 句具体）, "
        f"\"from_ch\": 主角正式得知真相的起始章, \"content\": 真相（主角明确获知的世界观片段，主角视角、具体）, "
        f"\"keywords\": 真相独有锚定词数组（1-5 个专有名词/关键短语，正文出现即视为揭示；不得与 hint 用词重叠，防止铺垫期暗示被误判为剧透）}}；"
        f"主角初始已知的 from_ch=1；foreshadow_ch 必须小于 from_ch（至少提前 2-3 章埋铺垫），"
        f"真相要有前文线索/伏笔层层铺垫引出，严禁到某一章凭空解锁；后续阶段按剧情推进递增（如铺垫8/揭示15、铺垫20/揭示30…），全书规划 3-6 个阶段；"
        f"全部阶段 content 合并后必须与 world_view 总纲完全一致、不新增矛盾设定；"
        f"未到 from_ch 的真相不得提前透露，只能按 hint 写暗示\n"
        f"- characters：3-8 个人物对象数组，每项含字段：name(姓名), role(定位主角/主角团/反派/关键配角), desc(一句话概括), appearance(外貌特征), personality(性格核心), desire(核心欲望), wound(软肋/创伤), arc(人物弧光), speech_style(说话方式/口头禅), relations(与其他角色关系)；"
        f"用户预设的主角与人物必须包含在内且描述符合设定，可补充其他角色；描述要具体，能支撑剧情行为逻辑\n"
        f"- foreshadowing：2-5 条贯穿全文的伏笔/悬念数组，每项 {{\"id\":伏笔编号, \"name\":伏笔名, "
        f"\"buried_ch\":埋点章号, \"reveal_ch\":回收章号, \"level\":\"small\"(章内)/\"medium\"(卷内)/\"large\"(全篇), "
        f"\"content\":伏笔内容}}；每条必须同时给出埋点与回收章号，禁止只埋不收（只埋不收视为设计缺陷）\n"
        f"- mechanism_rules：金手指/核心机制的规则卡（本书有金手指/系统/独特设定则必填，否则空字符串）。"
        f"必须写到无歧义、可自洽，至少覆盖：①机制名称与语义（每个数字/状态的确切含义，禁止'感知敏锐'类模糊表述）；"
        f"②结算规则（何时增减、何时重置、与历史状态如何衔接，杜绝自相矛盾）；③代价/成长（每次使用代价递增、上限/终局条件）；"
        f"④正文要求（首章必须把机制实际用起来、兑现标题，数字与规则全书一致）；"
        f"⑤数字账本写法：若机制有可视化数字（数值/进度/累积状态），给出正文中数字的准确写法"
        f"（如'剩余次数：3'）并定义每个数字的当前语义与递减/递增规则；"
        f"⑥时间词语义：标题或机制含'今日''每日'等时间词时，必须明确是否每日重置、跨天如何结算"
        f"（如'每日子时重置；一日内按机制卡递减'），禁止标题口径与机制冲突；"
        f"⑦多数字关系：存在多套数字（如剩余次数与累积进度）时必须交代两者关系与各自语义，"
        f"禁止并列无关联；⑧历史衔接：开篇已有历史痕迹（旧疤/累积状态/已有消耗）必须与当前数字自洽"
        f"（如累积进度=累计触发次数，则剩余=上限-累计），禁止开篇状态与机制数字矛盾\n"
        f"- theme_proposition：核心主题命题（一句话内核，全书剧情与人物关键选择最终回归的主题，如'失去一切后如何重新定义自我'）\n"
        f"- conflict_matrix：三层冲突矩阵数组，每项 {{\"level\":\"personal\"(个人内心)/\"interpersonal\"(人际对抗)/\"world\"(世界层面), "
        f"\"description\":该层核心冲突}}，三层各一条且层层递进\n"
        f"- protagonist_arc：主角完整弧光卡 {{\"belief\":开篇信念, \"flaw\":核心缺陷, "
        f"\"change_nodes\":[关键转变节点数组, 至少2个], \"final_insight\":最终认知}}\n"
        f"- character_motives：核心配角动机数组，每项 {{\"name\":姓名, \"goal\":核心目标, "
        f"\"behavior_logic\":行为逻辑, \"link_to_protagonist\":与主角的核心关联}}，至少覆盖主要配角与反派\n"
        f"- goldfinger_rules：金手指规则说明书五要素 {{\"trigger\":触发条件, \"upper_limit\":能力上限, "
        f"\"lower_limit\":能力下限, \"cost\":使用代价, \"growth_path\":成长路径}}（无金手指则空对象；有则五要素必填且无歧义）\n"
        f"- setting_taboos：设定禁忌清单数组（明确禁止出现的吃设定行为/能力，如'复活无代价''时间可随意倒流'）\n"
        f"- enemies：前期实际出场的怪物/敌对生物图鉴数组（3-6 个，只列前 10 章会写到的，不要大而全百科）。"
        f"每项 {{\"name\":名称, \"form\":外形解剖（有无固定形态、几条肢体、有无眼睛/口器/触须/核心，写死一句话，禁止'形态模糊'这类模糊词）, "
        f"\"senses\":感知方式（触须振动/视觉/热感，写死；后续正文不得出现与感知方式矛盾的写法，如靠触须就不能写'视线盲区'）, "
        f"\"behavior\":行为模式（巡逻编队/伏击/食腐/对死人的反应）, \"kill\":击杀条件与弱点, \"tier\":层级（浅层常见/精英/首领）}}；"
        f"形态一经写死，全书不得自相矛盾\n"
        f"- locations：前期实际出场的地点数组（3-6 个，只列前 10 章会写到的）。"
        f"每项 {{\"name\":地点名, \"terrain\":地形结构, \"light\":光线规则（地下无光/光源是什么/天光在何处，写死；后续正文不得与光线规则矛盾，如地下不得写暮色/晚霞）, "
        f"\"sound_smell\":声音气味, \"exits\":连通与出口（哪里是死路/通哪，写死）, \"danger\":危险来源}}；"
        f"空间关系一经写死，正文不得矛盾\n"
        f"- mechanism_baseline：机制初始值对象（若 mechanism_rules 含可视化数字/进度条，必填；否则空对象）。"
        f"把机制卡里的数字落成结构化'当前标准答案'，字段：{{\"scar_start\":代价标记起点（如旧痕/消耗起点）, "
        f"\"scar_per_death\":代价累积标记的每次触发增量（无则留空）, "
        f"\"scar_current\":代价标记当前累计长度/次数, \"scar_end\":终局位置（代价蔓延至何处=失败/终局）, "
        f"\"panel_format\":机制数字的唯一写法（如'剩余次数：N/上限'，禁止再发明别的格式）, \"reset_rule\":每日/跨天重置规则}}；"
        f"这是正文里所有数字的唯一来源，写手不得自行发明数字；与 mechanism_rules 必须自洽\n"
        f"- clock：时钟规则对象（每章都要遵守）。字段：{{\"day_cycle\":日周期（何时重置/晨昏/夜色节点）, "
        f"\"travel\":关键地点间路程耗时（写死，如'城门到集市约一刻钟'）, "
        f"\"light_rule\":光线与时间对应关系（哪里有光哪里没光、什么时间什么天色）}}；"
        f"正文里'过了多久''还剩几小时'必须与此一致，不得时间不推进却重复写'还有N小时'\n"
        f"- factions：前期出场的势力/组织数组（2-4 个）。每项 {{\"name\":势力名, \"goal\":目标, \"members\":关键成员, \"relation_to_mc\":与主角关系}}；"
        f"主角遇到该势力成员时，行为必须符合此卡\n"
        f"- style_taboos：语言风格禁忌数组（5-10 条，本书绝对禁用的出戏词，如'走位''视野CD''刷新''开挂'等现代游戏术语、时代错乱物品、作者内心吐槽）\n"
        f"- items：本书重要道具/物品卡数组（作品含关键道具则产出 1-5 个，否则空数组）。"
        f"每项 {{\"name\":道具名, \"origin\":来源/来历, \"cost\":使用代价（每次使用的消耗）, "
        f"\"restriction\":限制/触发条件（不能无限制使用，明确边界，禁'万能''无所不能'模糊表述）, "
        f"\"effect\":作用/能力（明确边界）}}；道具的静态属性一经写死全文一致，动态归属由章级状态账本承接，不得把道具写成万能工具\n"
        f"- power_system：等级/境界体系卡对象（作品为升级流/有明确境界战力体系则产出，否则空对象）。"
        f"字段：{{\"levels\":[{{\"name\":境界名, \"range\":数值区间或阶段描述, "
        f"\"requirement\":突破到本境界的条件, \"capability\":本境界能力/战力上限（写死）}}], "
        f"\"protagonist_level\":主角当前境界, \"progression_rule\":突破规则（如何升级/是否跳跃/瓶颈限制）}}；"
        f"境界阶梯由低到高写全、数值区间无歧义，长篇后期战力提升必须在此框架内递增，禁止凭空跳阶/战力崩坏\n"
        f"- terminology：术语统一表数组（本书有专有名词/译名/招式名/地名/特殊称呼则产出，否则空数组）。"
        f"每项 {{\"name\":标准写法（正文统一用此）, \"aliases\":[别名/译名数组（正文禁止出现这些写法）], "
        f"\"definition\":定义/说明}}；同一专有名词全文只能一种写法，防译名/招式名/地名前后不一致\n"
        f"设定要求：与大纲严格一致，人物动机清晰，伏笔在后期能回收，避雷元素不得出现。"
        f"{('每个重要人物的 desc 必须按「人物立体化·角色卡要求」写全：身份标签/经历背景/性格核心/欲望软肋/行为范式/压力反应，保证人物有辨识度、行为有依据。' if cfg.character_depth else '')}"
    )
    system += _skill_append(cfg)
    return system, user



# ============================================================
# 高质量模式：动态模块 + 场景级生成
# ============================================================

_VIEWPOINT_MODULES = [
    "限知视角：只写主角所见所感，他不知道的事情不要写",
    "限知视角：本章聚焦配角视角，主角的内心活动通过外部行为推测",
    "对话体为主：本章用大量对话推进，对话带潜台词，人物不说心里话",
    "日记/独白体：本章穿插主角的内心独白或记录，冷静克制",
]

_SENSE_MODULES = [
    "本章侧重视觉与空间感：光影、距离、物体轮廓、环境布局",
    "本章侧重听觉：脚步、风声、对话回声、沉默中的微响",
    "本章侧重触觉与身体感：温度、疼痛、疲惫、衣物摩擦",
    "本章侧重时间感：节奏变慢或变快，用细节体现时间流逝",
]

_RHYTHM_MODULES = [
    "短句为主：每句不超过15字，节奏快，动作密集",
    "长短句交替：用长句铺陈情绪，短句制造冲击",
    "对话占比70%：本章以对话推动，描写压缩到最少",
    "慢节奏过渡：本章允许日常感，用细节和对话铺垫，不追求强冲突",
]

_RANDOM_VARS_BY_GENRE = {
    "default": [
        "本章出现一个与主线无关但有生活感的小细节",
        "本章主角说错一句话，事后才意识到不妥",
        "本章有一次长时间沉默，用动作填充，不写心理",
        "本章出现一个反高潮：预期的爽点被一件小事打断",
    ],
    "玄幻": [
        "本章出现一枚不起眼的灵石/丹药细节，后续可能成为伏笔",
        "本章主角遇到一个修为远低于他但态度嚣张的路人",
        "本章穿插一段宗门规矩/修炼常识，自然融入不硬讲",
        "本章有一次灵力运转不畅的身体反应",
    ],
    "都市": [
        "本章出现一个微信聊天记录片段",
        "本章有一个加班/通勤/外卖的生活细节",
        "本章主角说错一句话，事后才意识到",
        "本章出现一个职场/商场上的客套话，话里有话",
    ],
    "悬疑": [
        "本章出现一个看似无关的物证/线索",
        "本章主角注意到一个不合常理的细节，但暂时不解释",
        "本章有一段对话，其中一人明显在隐瞒",
        "本章环境描写中出现一个微妙的异常",
    ],
}


_LAST_DYNAMIC: dict[str, str] = {}


def _pick_no_repeat(pool: list[str], key: str, rng) -> str:
    """P2-3：随机选一个动态模块，且与上一章同类选择不同（key 按 题材+模块类型 区分）。"""
    prev = _LAST_DYNAMIC.get(key)
    cands = [c for c in pool if c != prev] or pool
    pick = rng.choice(cands)
    _LAST_DYNAMIC[key] = pick
    return pick


def _pick_dynamic_module(chapter_no: int, genre: str, seed: int | None = None) -> str:
    """P2-3：动态模块按章随机 + 相邻章节去重（打破轮询规律，模型不可预测套路）。

    seed 非 None 时走固定随机序列（可复现调试，不参与相邻去重缓存）。
    """
    rng = _random.Random(seed) if seed is not None else _random
    pool = _RANDOM_VARS_BY_GENRE.get(genre, _RANDOM_VARS_BY_GENRE["default"])
    if seed is not None:
        vp = rng.choice(_VIEWPOINT_MODULES)
        sense = rng.choice(_SENSE_MODULES)
        rhythm = rng.choice(_RHYTHM_MODULES)
        var = rng.choice(pool)
    else:
        vp = _pick_no_repeat(_VIEWPOINT_MODULES, f"{genre}|vp", rng)
        sense = _pick_no_repeat(_SENSE_MODULES, f"{genre}|sense", rng)
        rhythm = _pick_no_repeat(_RHYTHM_MODULES, f"{genre}|rhythm", rng)
        var = _pick_no_repeat(pool, f"{genre}|var", rng)
    lines = [
        "【本章动态模块】",
        f"- 视角：{vp}",
        f"- 感官：{sense}",
        f"- 节奏：{rhythm}",
        f"- 随机小变量：{var}",
        "注意：动态模块只做微调，不颠覆整体叙事底色；不要在正文中提及这些指令。",
    ]
    return "\n".join(lines)


def _style_reference(cfg: NovelConfig) -> str:
    """P2-1 few-shot 文风锚定：风格角色携带的参考文风片段（模仿选词/句式/节奏/语感，严禁抄袭内容）。"""
    ref = (getattr(cfg, "style_role_reference", "") or "").strip()
    if not ref:
        return ""
    return (
        "【参考文风样例（只模仿选词、句式、节奏与语感，严禁抄袭具体内容与情节）】\n"
        + ref[:3000]
    )


def action_plan_prompt(cfg, chapter_no, chapter_title, chapter_goal, memory_context):
    """P1-1 人物行动表先行：正文生成前先规划本章人物行动，写手必须落实。"""
    system = (
        "你是一位小说分镜策划。用户给你一章的目标与前情记忆，"
        "你的任务是规划本章人物的行动表，让人物言行有动机、剧情不空转。"
        "严格按 JSON 格式输出，不要额外解释。"
    )
    user = (
        f"【第{chapter_no}章《{chapter_title}》】\n"
        f"【本章目标】{chapter_goal}\n\n"
        f"【前情记忆（含人物动态状态）】\n{memory_context if memory_context else '（首章）'}\n\n"
        "请为本章出场人物规划行动表，每人包含：\n"
        "- name: 人物名\n"
        "- goal: 本章目标（具体、可行动）\n"
        "- key_actions: 2-4 个关键行动（动词驱动，能推进剧情或冲突）\n"
        "- emotion: 情绪状态（结合前情，用一句话描述）\n"
        "- dialogue_intent: 对话意图（这一章他想通过对话达成什么/试探什么/隐瞒什么）\n\n"
        '输出 JSON：{"characters": [{"name": "...", "goal": "...", "key_actions": ["..."], "emotion": "...", "dialogue_intent": "..."}]}'
    )
    return system, user


def split_scenes_prompt(cfg, chapter_no, chapter_title, chapter_goal, prev_tail="", core_event="", moves="", book_canon=""):
    # REQ-scene-plan-align：moves 非空 = 事件清单已由大纲 character_moves 确定性派生
    # （硬骨架），模型只做「补结构化字段 + 拆场景」，禁止增删/改写/合并事件；
    # moves 为空 = 原路径，模型自行规划互斥事件清单。
    if moves and str(moves).strip():
        step1 = (
            "== 第一步：按下方【事件清单】逐条补充结构化字段（events） ==\n"
            "每个事件输出：id（沿用下方编号）、desc（沿用下方原文，禁止修改）、"
            "conflict（冲突对象，如\"三头凶兽\"）、action（动作类型，如\"救援/围攻/击杀/逃离\"）、"
            "final_state（本事件结束时的机制状态增量，按本书机制卡，如\"剩余次数 2/3\"）。\n"
            "硬规则：\n"
            "1. 事件清单 = 人物动线骨架的逐拍切分：禁止新增骨架之外的独立事件、"
            "禁止合并/省略/改写骨架拍；同一『冲突对象 + 动作类型』本章至多一次\n"
            "2. 事件之间必须因果推进：前一个事件的结果 = 后一个事件的起点；"
            "禁止第 2 个事件重新回到第 1 个事件的开头状态重演一遍（哪怕换了地点、换了措辞）\n"
            "3. 禁止把同一事件拆成两个事件：能发生的独立事件不足时，宁可少拆（最少 2 个场景），"
            "也禁止把\"被围-犹豫-冲上去救\"这类同一过程拆成多个场景\n"
            "4. 机制数字规划到事件：final_state 必须写明事件结束时机制数字 X/X 与累积状态位置"
            "（机制数字/累积状态/伤势），且每个事件之间必须有增量变化——禁止两个事件结束时间一数值/同一状态\n"
            "5. 同类事件本章至多一次：同一类剧情动作（救援、被围、对峙、追击、脱困）只允许完整发生一次；"
            "后续若仍需同一人物面对同一批敌人，只能作为承接上一次结果的余波/升级出现"
        )
        skeleton_block = (
            "【本章事件清单（已由编辑按人物动线确定，硬骨架，逐条对应一个场景）】\n"
            f"{moves}\n"
            "硬规则：JSON 的 events 必须逐字沿用下方 id 与 desc（禁止增删/合并/改写）；"
            "scenes 数量必须与 events 数量完全一致（每条事件 = 一个场景，逐条对应，"
            "禁止多拆、少拆、合并场景）；scene_event_ref 必须逐字引用下方 id。"
        )
    else:
        step1 = (
            "== 第一步：规划本章事件清单（events，N 个事件 = N 个场景） ==\n"
            "每个事件输出结构化字段：id（事件编号）、desc（一句话：人物 + 冲突对象 + 动作 + 结果）、"
            "conflict（冲突对象，如\"三头凶兽\"）、action（动作类型，如\"救援/围攻/击杀/逃离\"）、"
            "final_state（本事件结束时的机制状态增量，按本书机制卡，如\"剩余次数 2/3\"）。\n"
            "硬规则：\n"
            "1. 事件必须两两互斥：同一『冲突对象 + 动作类型』本章至多一次（如只救一次、只被围一次）；"
            "【本章核心事件】必须被完整覆盖且只覆盖一次\n"
            "2. 事件之间必须因果推进：前一个事件的结果 = 后一个事件的起点；"
            "禁止第 2 个事件重新回到第 1 个事件的开头状态重演一遍（哪怕换了地点、换了措辞）\n"
            "3. 禁止把同一事件拆成两个事件：能发生的独立事件不足时，宁可少拆（最少 2 个场景），"
            "也禁止把\"被围-犹豫-冲上去救\"这类同一过程拆成多个场景\n"
            "4. 机制数字规划到事件：final_state 必须写明事件结束时机制数字 X/X 与累积状态位置"
            "（机制数字/累积状态/伤势），且每个事件之间必须有增量变化——禁止两个事件结束时间一数值/同一状态\n"
            "5. 同类事件本章至多一次：同一类剧情动作（救援、被围、对峙、追击、脱困）只允许完整发生一次；"
            "后续若仍需同一人物面对同一批敌人，只能作为承接上一次结果的余波/升级出现"
        )
        skeleton_block = ""
    system = (
        "你是一位小说结构编辑。用户会给你一章的梗概，"
        "你的任务分两步：①先确定/补充本章的【互斥事件清单】（几个事件就拆几个场景，1 事件 = 1 场景）；"
        "②再按事件清单拆成 2-4 个连续场景（场景数 = 事件数，由本章总字数反推：总字数 ÷ 1200 取整，"
        "最少 2 个；2500 字章 → 约 2-3 个场景，禁止为了凑场景数超写），"
        "每个场景约 700-1400 字，并同时规划本章出场人物的行动表（写手必须逐条落实，人物言行与行动表一致）。"
        "严格按 JSON 格式输出，不要额外解释。"
    )
    user = (
        f"【本章】第{chapter_no}章《{chapter_title}》\n"
        f"【本章目标】{chapter_goal}\n"
        f"【本章核心事件（事件清单必须完整覆盖，不得偏离或遗漏）】\n{core_event if core_event else '（无）'}\n"
        f"【上一章结尾（本章起点：第一个场景必须从这之后的场景/时间线/人物状态自然接续）】\n"
        f"{prev_tail if prev_tail else '（首章或无前章）'}\n\n"
        f"【本章目标总字数】{cfg.chapter_words} 字\n\n"
        + step1
        + "\n\n== 第二步：按事件清单拆场景（scenes） ==\n"
        "场景数 = 事件数。每个场景包含：\n"
        "- location: 地点\n"
        "- scene_event_ref: 必须逐字引用 events 中的事件编号（\"事件1\"…\"事件N\"），"
        "禁止自行改写或换措辞描述事件——一个事件编号只能被一个场景引用\n"
        "- emotion: 这个场景的情绪目标\n"
        "- anchors: 必须保留的细节\n"
        "- time_marker: 本场景的时间锚点（与上一场景的时间间隔与标记，如'同日午后''三天后''翌日清晨'；"
        "若机制规则含每日重置，跨天场景必须写明重置后的机制数字）\n"
        f"- target_words: 该场景目标字数（硬约束：全部场景 target_words 之和必须等于 {cfg.chapter_words}，"
        f"允许 ±10%；单个场景不得超过 1400 字。禁止为了凑场景数或凑字数超写；"
        f"下方示例中的数值仅示格式，实际必须按『本章总字数 ÷ 场景数』分配）\n\n"
        "场景连续性硬规则：第一个场景必须承接上一章结尾的场景与人物状态，"
        "从上一章结束的地方接着展开；后续场景沿剧情推进自然过渡，禁止跳跃式换场。\n"
        "事件互斥硬规则（防同事件两版本拼接）：\n"
        "1. 场景之间的 scene_event_ref 必须互斥：同一事件只能被一个场景完整覆盖，"
        "禁止两个场景写同一件事的两个版本/两段同构描述\n"
        "2. 场景间不得共用同一『事件+人物+动作』组合：若两个场景引用到同一事件，必须合并为一个场景"
        "或让时间线明确错开（一个承接另一个的结果，而不是重复过程）\n"
        "3. 每个场景的事件一句话能说清『新发生的、推进剧情的事』；如果两个场景换个说法是同一件事，即视为重叠\n"
        "时间硬规则：\n"
        "1. 场景时间必须顺延，禁止无标记的时间倒挂（先写结果再补前因时，必须拆出'回想'场景并标记）\n"
        "2. 同一章内的关键事件（如多次死亡、多次同地遇袭）不得压成一句话摘要，"
        "每次发生→后果必须分配完整场景/段落，权重均衡，禁止第一次展开、后续一笔带过\n"
        "3. 场景内机制数字（按本书机制卡声明）的推进必须与事件次数严格对应：按机制卡规则增减或重置，"
        "跨天重置按机制卡规则结算\n\n"
        "事件去重与机制规划硬规则（防'同一事件循环写多遍'与'数字结算错位'）：\n"
        "1. 同类事件本章至多一次：同一类剧情动作（救援、被围、对峙、追击、脱困）在本章场景中"
        "只允许完整发生一次；后续场景若仍需同一人物再面对同一批敌人，只能作为承接上一次结果的"
        "余波/升级出现（敌人已换位置、人已脱险、代价更大、局势升级），禁止把同一事件的过程再写一遍\n"
        "2. 机制数字规划到场景：每个含机制结算的场景（次数增减、进度推进、状态重置），"
        "必须在 time_marker 或事件引用中明确标注'本场景结束时机制数字为 X/X'；"
        "全章机制数字序列只允许严格递减（或按机制卡重置），禁止两个场景结束时出现同一数值\n"
        "3. 专有名词锁定：本世界已有的器物/能力/组织/服饰名词（如某件器物、某组织服饰）在事件/anchors "
        "中固定同一称谓，后续场景沿用，禁止换词或近义替代\n\n"
        "【本章人物行动表】再为本章出场人物规划行动表（并入本 JSON 的 characters_action 字段），"
        "每人包含：name（人物名）、goal（本章目标，具体可行动）、key_actions（2-4 个动词驱动的关键行动，"
        "能推进剧情或冲突）、emotion（情绪状态一句话）、dialogue_intent（对话意图：想通过对话达成/试探/隐瞒什么）。\n\n"
        '输出 JSON：{"events": [{"id": "事件1", "desc": "...", "conflict": "...", '
        '"action": "...", "final_state": "..."}, ...], '
        '"scenes": [{"location": "...", "scene_event_ref": "事件N", "emotion": "...", '
        '"anchors": ["..."], "time_marker": "...", "target_words": ' + str(max(800, int(cfg.chapter_words or 0) // 2)) + '}...], '
        '"characters_action": [{"name": "...", "goal": "...", '
        '"key_actions": ["..."], "emotion": "...", "dialogue_intent": "..."}]}'
    )
    # REQ-scene-plan-align：骨架块置顶（最高权重），模型必须先看到事件清单再规划场景
    if skeleton_block:
        user = skeleton_block + "\n\n" + user
    # P0-7 小说级记忆：设定账本（新名词规范/永久纪律/机制基线）注入拆场景层
    if book_canon:
        user = user + "\n\n" + book_canon
    return system, user


def scene_split_review_prompt(cfg, chapter_no, chapter_title, chapter_goal,
                              events_text: str, scenes_text: str = "") -> tuple[str, str]:
    """P0-10 拆场景评审（2026-10-10 混合方案①）：骨架产出后独立评审 agent。

    治 v7 第 5 章药铺三重拼接根因：拆场景把同一「进店→诊脉→试探→给药包→离开」
    过程拆成 3 个 desc 不同（查药粉/诊脉/探脉枕）但语义同构的事件，desc 相似度
    预筛（>0.55）逃过、正文节拍单遍 3 拍又逃过 4 拍阈值——规划层就埋了重复。

    本 agent 只判「事件两两是否语义同构」（同一过程换措辞/换切入点），输出
    重复对清单；代码按清单确定性合并（不重复调用模型重拆，省 token、可复现）。
    正常递进拍（冲突升级、结果不同、因果承接）不算重复——防误杀大纲自身节奏。
    """
    system = (
        "你是小说结构编辑，负责检查本章拆场景规划是否存在「同一事件被拆成多个"
        "语义同构场景」的规划缺陷。\n"
        "判定规则：\n"
        "1. 两个事件如果本质是「同一人物在同一个地方做同一件事，只是换措辞/换切入点/"
        "换细节重写」→ 判重复（如：进店查看 / 进店试探 / 进店细查，"
        "同一过程三遍）\n"
        "2. 两个事件如果因果递进（后一事件从上一事件的结果继续：冲突升级/结果不同/"
        "换地点换对手/新信息揭示）→ 不判重复\n"
        "3. 宁可漏报不误杀：拿不准的不列\n"
        "只输出合法 JSON：{\"duplicate_pairs\":[{\"i\":0,\"j\":2,"
        "\"reason\":\"同是进店试探过程，仅换措辞\"}],\"note\":\"\"}。"
        "没有重复输出 {\"duplicate_pairs\":[]}。不要输出代码块标记、解释或省略号。"
    )
    user = (
        f"第{chapter_no}章《{chapter_title}》本章目标：{chapter_goal}\n"
        "【本章事件清单（每个事件 = 一个场景）】\n"
        f"{events_text}\n"
        + (f"【场景分配】\n{scenes_text}\n" if scenes_text else "")
        + "请检查事件两两是否语义同构（同一过程换措辞拆多遍），输出重复对清单。"
    )
    return system, user


def rewrite_diagnose_prompt(content: str) -> tuple[str, str]:
    """P0-10 重写前诊断（2026-10-10 混合方案②）：重写前独立审稿判重复结构。

    治 v4 教训（重写 1 把拼接写得更严重：第四轮第 5 章 2 个问题→4 个问题）——
    原链路是「重写 → 复检」，重写 agent 在"信息真空"里自由发挥，自校验查不出
    同构场景/拼接。本 agent 在重写**前**独立诊断正文的重复结构（多稿拼接/
    对话循环），输出明确「删除哪段、保留哪段」，重写 prompt 按诊断执行——
    把"自由重写"变成"按清单删除+合并"，从概率上杜绝越改越差。
    """
    system = (
        "你是小说质检编辑，只诊断不改写。阅读正文，定位两类结构缺陷：\n"
        "1. 多稿拼接/重复版本：同一场景或事件（同一地点/同一批人/同一过程）"
        "换措辞写了 ≥2 遍——标记其中重复的版本段落（action=删除），保留最完整的一遍\n"
        "2. 对话循环：同一组问答/对峙对话（同角色、近同措辞）重复 ≥2 次——"
        "标记重复轮次（action=删除）\n"
        "输出规则：\n"
        "- start/end 为字符索引（0-based，正文从 0 开始）；每个 finding 的 start<end\n"
        "- 每个缺陷独立一条；拼接按「保留段之外的重复段」输出（每条 action=删除）\n"
        "- 拿不准的不要报（宁可漏报不误删正常递进内容）\n"
        "只输出合法 JSON：{\"findings\":[{\"type\":\"拼接|对话循环\",\"start\":0,"
        "\"end\":200,\"action\":\"删除\",\"reason\":\"与第 X 段同一过程仅换措辞\"}],"
        "\"note\":\"\"}。无缺陷输出 {\"findings\":[]}。不要代码块标记、解释、省略号。"
    )
    user = f"【待重写正文】\n{content}\n\n请诊断上述正文中的多稿拼接与对话循环，输出删除清单。"
    return system, user


def characters_action_fill_prompt(cfg, chapter_no, chapter_title, chapter_goal,
                                  core_event="", events_text="", prev_tail=""):
    """P0-4 characters_action 单字段补全（2026-10-09 生成端第三轮）。

    拆场景大 JSON 长输出截断通常只丢尾部 characters_action（"Expecting ',' delimiter"
    实证）；本 prompt 让模型**只输出 characters_action 数组**（小调用、低截断风险），
    成功后并入拆场景结果——不再整段重试（省 token），行动表措辞层约束真正生效。
    """
    system = (
        "你是一位小说结构编辑。根据本章事件清单与人物，输出本章人物的行动表 JSON 数组。"
        "严格按 JSON 数组格式输出，不要额外解释、不要代码块标记。"
    )
    user = (
        f"【本章】第{chapter_no}章《{chapter_title}》\n"
        f"【本章目标】{chapter_goal}\n"
        f"【本章核心事件】\n{core_event if core_event else '（无）'}\n"
        f"【本章事件清单（事件=场景，逐条推进，禁止同一事件重复演绎）】\n"
        f"{events_text if events_text else '（无）'}\n"
        f"【上一章结尾】\n{prev_tail if prev_tail else '（无）'}\n\n"
        "输出要求：\n"
        "1. 为本章出场人物规划行动表，每人一条，含：name（人物名）、"
        "goal（本章目标，具体可行动）、key_actions（2-4 个动词驱动的关键行动，"
        "能推进剧情或冲突）、emotion（情绪状态一句话）、"
        "dialogue_intent（对话意图：想通过对话达成/试探/隐瞒什么）\n"
        "2. key_actions 必须与事件清单逐条对应：每个事件至多被一个 key_action 覆盖，"
        "禁止同一个人物对同一事件安排多个 key_action（防行动表诱导跨场景重复落实）\n"
        "3. 只输出 JSON 数组，不要输出其他文字\n\n"
        '示例：[{"name": "主角", "goal": "查清来者底细", '
        '"key_actions": ["试探盘问", "留意对方举动"], '
        '"emotion": "警惕", "dialogue_intent": "隐瞒昨夜的遭遇"}]'
    )
    return system, user


def _panel_baseline_block(panel_baseline: str) -> str:
    """机制数字背景参考块（软约束·不要求展示）。

    2026-10-17 弱化（REQ-机制数字软提示与通用化）：机制数字是背景账本，不是描写
    对象。正文按剧情自然呈现，不要求刻意展示面板；若正文出现数字须与账本一致；
    结算瞬间若写数字按方向规则增减；无结算时无需展示。空基线 → 空串（不注入）。
    """
    if not panel_baseline:
        return ""
    return (
        f"\n【机制数字·背景参考（软约束：结算时为准，正文无需刻意展示）】\n"
        f"以下是账本算好的机制数字当前值，供你心里有数，不要求你在正文里罗列或结算：\n"
        f"{panel_baseline}\n"
        f"纪律：① 正文按剧情自然呈现——想看面板/确认状态可以写（如'他扫了眼面板，还剩一次'），"
        f"不涉及结算的章节不要刻意展示数字；"
        f"② 若正文出现面板数字，须与以上背景值一致；"
        f"③ 真发生结算（死亡/复活/升级/加点等）时，结算瞬间才显示结算前/后两个值，"
        f"其余时刻用叙述带过，不要反复罗列数字。\n"
    )


def scene_prompt(cfg, chapter_no, chapter_title, scene_idx, scene, memory_context, prev_tail, dynamic_module, core_event="", scene_state_hint="", force_new_progression=False, forbid_event="", personal_anchor="", teardown_anchor="", event_progress="", done_beats="", consumed_events="", upcoming_events="", panel_baseline="", panel_violations="", book_pitfalls="", book_canon="", state_anchor="", user_memory_block="", chapter_design=""):
    system = (
        "你是一位高产的网络小说签约写手。你正在写长篇小说的其中一个场景。"
        "严格遵守世界设定与人物性格，不推翻前文既定事实。"
        "只写这个场景，不要提前写后续场景的内容。"
    )
    if cfg.de_ai:
        system += "你的文字要有真人质感：对白有潜台词、叙事有详略、人物有犹豫。"
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("chapter")
    if cfg.character_depth:
        system += "\n\n" + _character_depth_rules("chapter")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("chapter")
    if dynamic_module:
        system += "\n\n" + dynamic_module
    ref = _style_reference(cfg)
    if ref:
        system += "\n\n" + ref
    if personal_anchor:
        system += "\n\n" + wrap_style_anchor(PERSONAL_ANCHOR_HEADER, personal_anchor)
    if getattr(cfg, "teardown_style_anchor", True):
        anchor = wrap_style_anchor(TEARDOWN_ANCHOR_HEADER, teardown_anchor)
        if anchor:
            system += "\n\n" + anchor
    system += _skill_append(cfg)
    system += "\n\n" + _output_discipline()
    system += "\n\n" + _writing_constraints()
    anchors = scene.get("anchors", [])
    anchors_str = "；".join(anchors) if anchors else "（无特殊锚点）"
    scene_ev = str(scene.get("event") or "").strip()
    final_state = str(scene.get("final_state") or "").strip()
    force_block = (
        "\n本场景重试强制规则（上一条输出与上一场景高度重复，已作废）：\n"
        "1. 本场景正文必须以『上一场景结局之后』的时点/动作为起点续写，"
        "禁止重演上一场景任何已写情节（连'伏看-犹豫-碎石滚落-对视'这类起手式都禁止重复）\n"
        "2. 只写本场景唯一事件，禁止把上一场景事件再叙述一遍\n"
    ) if force_new_progression else ""
    forbid_block = (
        f"\n【重试强制规则（上一条输出把已发生事件又写了一遍，已作废）】\n"
        f"1. 下列事件【已完成且必须在正文中视为既定事实】，你上一条输出把它重演了："
        f"『{forbid_event}』——本次禁止以任何形式再次发生/再次描写（包括换地点、换措辞、"
        f"换细节重写一遍，连'犹豫-碎石滚落-对视-冲过去'这类起手式都禁止重复）\n"
        f"2. 本场景只写本场景唯一事件；上一事件之后人物已经离开/脱险/换了处境，"
        f"必须从该事件结局之后续写新的情节\n"
    ) if forbid_event else ""
    done_beats_block = (
        f"\n【本章已发生节拍序列（硬约束：以下节拍组合已在前面的场景正文中完整发生过，"
        f"本场景正文禁止再次出现相同顺序的节拍组合——同一情节换措辞/换细节演绎即重演，"
        f"必须改写为与前面场景完全不同的新进展；若确有'应被压缩'的旧进展，用一句叙述带过"
        f"而不是重演完整过程）】\n{done_beats}\n"
    ) if done_beats else ""
    # P0-1 事件消费账本（2026-10-09 生成端第三轮，对标 AgentWrite $STEP$ 指针制）：
    # 已消费事件只许一句话回响；后续事件预览只许铺垫提及，禁提前消费完整流程。
    consumed_block = (
        f"\n【本章已消费事件（硬约束：以下事件已在本章前面的场景正文中完整发生并结束，"
        f"本场景禁止再次发生/禁止展开其完整流程——换地点、换措辞、换细节再写一遍"
        f"同样视为重演；如需呼应，只用一句话带过结果）】\n{consumed_events}\n"
    ) if consumed_events else ""
    upcoming_block = (
        f"\n【本章后续事件预览（禁提前消费：以下事件留给各自的场景推进，本场景正文"
        f"禁止写出它们的完整流程、动作、冲突或结果；只可做一句铺垫性提及，"
        f"不得展开演绎）】\n{upcoming_events}\n"
    ) if upcoming_events else ""
    # P0-3 面板数字确定性闭环（2026-10-09 生成端第三轮）：权威基线注入 + 违反方向规则重试。
    # 2026-10-17 机制数字软提示与通用化（REQ-机制数字软提示与通用化）：基线降为
    # 「背景参考·不要求刻意展示」——正文按需自然呈现，结算时为准，不再逼模型写面板。
    panel_baseline_block = _panel_baseline_block(panel_baseline)
    panel_violation_block = (
        f"\n【重试强制规则（上一条正文中机制数字违反方向规则，已作废）】\n"
        f"{panel_violations}\n"
    ) if panel_violations else ""
    # P0-7 小说级记忆：本作历史错误（踩坑账本 Top-N，场景生成纪律）
    book_pitfalls_block = (
        f"\n{book_pitfalls}\n"
    ) if book_pitfalls else ""
    # P0-8 小说级记忆：机制基线注入（背景参考，正文按需呈现，不要求刻意展示）
    book_canon_block = (
        f"\n{book_canon}\n"
    ) if book_canon else ""
    # P0-10 机制数字链式承接（背景参考：代码按上一章末值链式承接，结算时为准，不要求刻意展示）
    state_anchor_block = (
        f"\n{state_anchor}\n"
    ) if state_anchor else ""
    # 用户级记忆库：跨书偏好/纪律/文风锚（软约束，来自用户历史作品自动沉淀）
    user_memory_block_out = (
        f"\n{user_memory_block}\n"
    ) if user_memory_block else ""
    user = (
        f"【第{chapter_no}章《{chapter_title}》· 场景 {scene_idx}】\n"
        f"【地点】{scene.get('location', '')}\n"
        f"【本场景唯一事件（场景只写这一个事件，从上一场景结局之后开始；"
        f"上一场景的事件已完整发生并结束，禁止重演/复述）】{scene_ev if scene_ev else scene.get('event', '')}\n"
        f"{'【本场景结束时的机制状态（final_state，参考：仅在发生结算事件时落实该增量，'
        f'无结算则保持上一场景状态即可，正文不要求刻意展示机制数字）】' + final_state + '\n' if final_state else ''}"
        f"【情绪目标】{scene.get('emotion', '')}\n"
        f"【必须保留的细节】{anchors_str}\n"
        f"【本场景目标字数】{scene.get('target_words', 1200)} 字\n\n"
        f"【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】\n{core_event if core_event else '（无）'}\n\n"
        f"{'【本章设计卡（正向创作指引·软约束：按此设计写本章，不偏离戏剧核心）】\n'
        + chapter_design + '\n\n' if chapter_design else ''}"
        f"{'【事件进度（硬约束：本章事件按编号顺序各对应一个场景、各只推进一次；'
        f'本场景只允许推进本场景唯一事件的正文，禁止把后续事件的完整流程提前写完——'
        f'后续事件（如死亡-复活-对峙-包扎-离场等完整循环）必须留给各自场景推进，'
        f'即使本场景事件是前置节拍（进入/扫描/试探），也只写该节拍本身及与下一事件的直接衔接）】\n'
        f'{event_progress}\n\n' if event_progress else ''}"
        f"【前情记忆】\n{memory_context if memory_context else '（首章）'}\n\n"
        f"【上一章节结尾（衔接用）】\n{prev_tail if prev_tail else '（无）'}\n\n"
        f"{'【本章已推进状态（后续场景必须延续，禁止回退或重复叙述已发生事实）】\n' + scene_state_hint + '\n\n' if scene_state_hint else ''}"
        f"{'【本场景时间锚点】' + scene.get('time_marker', '') + '\n\n' if scene.get('time_marker') else ''}"
        f"{force_block}"
        f"{forbid_block}"
        f"{done_beats_block}"
        f"{consumed_block}"
        f"{upcoming_block}"
        f"{panel_baseline_block}"
        f"{panel_violation_block}"
        f"{book_pitfalls_block}"
        f"{book_canon_block}"
        f"{state_anchor_block}"
        f"{user_memory_block_out}"
        "写作要求：\n"
        "1. 只写这个场景的正文，正文里不要出现'场景切换''过渡'等元文字；"
        "不输出【摘要】，不输出场景编号，直接输出正文\n"
        "2. 场景衔接（硬约束）：本章第一个场景必须从【上一章节结尾】的场景、时间、人物状态"
        "直接续写，开头 1-2 句自然承接；后续场景必须自然承接上一场景结尾（人物、地点、事件顺延），"
        "事件在时间线上位于上一场景之后，禁止硬切换场、禁止回到更早时点重演上一场景已写情节、"
        "禁止把本章后续事件的完整流程提前写完（本场景只写本场景唯一事件，后续事件留给各自场景）\n"
        "3. 视角（硬约束）：只写主角在场所见所感，禁止上帝视角；主角独处/昏迷时只写他自己的体感"
        "（声音、触感、疼痛、记忆），禁止写需要旁观者的客观描写\n"
        "4. 情绪表达（演七分、说三分）：情绪以动作、微表情、生理反应承载（攥紧拳头、指节发白、"
        "半天没说话）；但**铺垫到位后必须直白点破**——本场景至少 1 处直白交代"
        "（内心判断/因果解释/情绪点破之一，如'他怕了'），点破处紧跟一个动作或身体反应作支撑；"
        "禁止全程零内心、只会'嗯/行'，也禁止情绪词连发堆砌与无画面抒情\n"
        "5. 白描与明喻（正向技法）：景物/动作做服务性描写，抓 1-2 个标志特征+微动作，景随人动，"
        "环境是人物心境的镜子；白描重在传神、很少用比喻——'像……'明喻每场景至多 1-2 处，"
        "其余用动词链直写；短句负责节奏（'他没回头。''门开了。'），长句不超过 50 字\n"
        "6. 对话与潜台词：对话单独成段，一句对话一段；对话要承载推进与潜台词（口是心非、"
        "话留半句、答非所问）；同一句台词、同一轮对话（连续问答）只出现一次，需要强调时"
        "用递进改写（加新信息、改变环境或代价），禁止原样复读\n"
        "7. 机制数字结算（硬约束）：机制卡声明的结算事件（增减/重置）发生时数字只出现一次且即时结算——"
        "结算瞬间显示结算前/结算后数值；"
        "同一数字不得在两个不同结算点原样重复（同值跨段复现=未结算，禁止）；"
        "**除非结算瞬间，正文不得出现数字行**，确认状态改用叙述带过（如'他看了一眼面板，还剩一次'）\n"
        "8. 术语与状态口径（硬约束）：专有名词（武器/能力/服饰/组织名）必须沿用【前情记忆】"
        "【本章已推进状态】已出现的称谓，禁止换词/缩写/近义替代（以本书术语表固定写法为准）；"
        "伤势/距离/消耗等累积状态每次用累积口径（'伤口已至小臂中段，比上次更长'），"
        "禁止同一状态多处从起点重新计量导致数值冲突；"
        "同一角色本章外貌/服装/代称全局唯一（如'青袍短发'这类标记全书一个代称），禁止同一角色出现第二套描述\n"
        "9. 事件只推进本场景（硬约束）：本场景只写本场景唯一事件；【本章已发生事件】列出的事件"
        "（人物+冲突对象+结果）已经发生且只发生一次，本场景禁止再次描写同一事件（同人物+同冲突"
        "+同结果=重复，换地点/换细节重写也算重复），呼应用一句话带过；同模式动作不重复"
        "（第二次必须改变环境、代价、敌人或结果），连续死亡（同章内或相邻章）死因必须不同\n"
        "10. 字数与时间纪律（硬约束）：正文控制在【本场景目标字数】±15% 以内，不写与本场景事件"
        "无关的铺垫与余韵；跨天/隔夜必须写明时间标记（'三天后''翌日''当晚'），闪回必须用"
        "'回想''那天'等提示且每章至多 1 处，禁止无标记的时间倒挂；"
        "若场景结束需输出摘要，必须写本章最终结算后的机制状态（如'剩余次数减至 2/3'），"
        "禁止只写开篇/第一次事件的状态\n"
    )
    return system, user


def polish_chapter_prompt(cfg, chapter_no, chapter_title, chapter_text):
    system = (
        "你是一位小说润色编辑。用户给你一章正文，"
        "你的任务是通读后微调文风：统一语感、修正重复句式、让段落衔接更自然。"
        "绝对不要改变情节、不要增删剧情、不要改人物对话含义。"
        "只输出润色后的完整正文。"
    )
    system += "\n\n" + _output_discipline()
    user = (
        f"【第{chapter_no}章《{chapter_title}》】\n"
        f"{chapter_text}\n\n"
        "请润色：统一语感、修正重复用词和句式、段落衔接更自然。"
        "不要改情节，不要增删内容。输出润色后的完整正文。"
    )
    return system, user


def verify_prompt(cfg, recent_summaries, world):
    system = "你是小说 continuity 编辑。请核对以下内容是否前后一致，输出 JSON。"
    summaries_block = "\n".join(
        f"第{i+1}章：{s}" for i, s in enumerate(recent_summaries)
    )
    user = (
        f"【世界观/设定】\n{world[:2000]}\n\n"
        f"【最近3章摘要】\n{summaries_block}\n\n"
        "请检查：\n"
        "1. 主角人设是否一致\n"
        "2. 已埋伏笔是否合理\n"
        "3. 时间线是否矛盾\n"
        "4. 人物关系是否正确\n\n"
        '输出 JSON：{"issues": [{"problem": "...", "suggestion": "..."}], "ok": true/false}'
    )
    return system, user


def verify_combined_prompt(cfg, chapter_no, recent_summaries, world_text, chapter_text="",
                           reader_persona=""):
    """REQ-token优化 P1-5：一致性/读者/编辑三类校验合并为一次调用，替代 3 连独立调用，
    输入不再把世界观与摘要重复喂 3 遍。

    P0-1：reader_persona 传入时，把 ReaderAgent 的角色底色（reader.md + reader 专属技能）
    注入到 reader 检查要求之前，让读者视角审校遵循「读者四问 + 分级 + 弃书红线」；
    不改变输出 JSON schema，保持单次调用（不增 token 调用数）。
    """
    reader_block = ""
    if reader_persona:
        reader_block = (
            f"\n\n【读者视角检查要求（角色底色）】\n{reader_persona}\n"
            "以下 reader 检查必须按上述读者角色执行：只查『读者会不会爽/会不会追/会不会弃书』，"
            "不查逻辑硬伤；按小改/中改/大改标级，命中弃书红线（主角全程被动/整章无情绪高点/"
            "章末流水账/情绪与处境严重不符）必须标大改。"
        )
    system = (
        "你是小说 continuity 总编辑。请基于世界观设定与最近几章摘要，"
        "一次性完成三类检查并输出一个 JSON。只输出 JSON，不要额外解释。"
    )
    summaries_block = "\n".join(
        f"第{i+1}章：{s}" for i, s in enumerate(recent_summaries)
    )
    user = (
        f"【世界观/设定】\n{world_text[:2000]}\n\n"
        f"【最近几章摘要】\n{summaries_block}\n\n"
        + (f"【最新一章正文】\n{chapter_text[:2000]}\n\n" if chapter_text else "")
        + "请执行三类检查：\n"
        "1. consistency：主角人设/已埋伏笔/时间线/人物关系是否前后一致\n"
        "2. reader：读者视角是否有信息传递问题（哪里看不懂/人物分不清/默认读者知道没讲过的事）"
        + reader_block
        + "\n3. editor：编辑视角给最新一章评分（满分10）与弃书风险（低/中/高）\n\n"
        '输出 JSON：{"issues": [{"problem": "...", "suggestion": "..."}], '
        '"reader": {"confused": bool, "issues": [{"problem": "...", "suggestion": "..."}]}, '
        '"editor": {"score": 0-10, "reader_churn_risk": "低/中/高", '
        '"issues": [{"problem": "...", "suggestion": "..."}]}}'
    )
    return system, user


def revise_locate_prompt(cfg, chapter_no, chapter_title, chapter_goal, content):
    """步骤1：通读定位，输出问题清单 JSON。低温 0.3。"""
    system = (
        "你是一位资深网文编辑，正在审阅刚写完的一章。"
        "你只做问题定位，不改写正文。按 JSON 格式输出问题清单。"
    )
    user = (
        f"【第{chapter_no}章《{chapter_title}】】\n"
        f"【本章目标】{chapter_goal}\n\n"
        f"【本章正文】\n{content[:6000]}\n\n"
        "请通读后找出以下类型的问题：\n"
        "1. 节奏拖沓：大段描写/无意义日常/对话不推进剧情\n"
        "2. 对话无潜台词：人物把心里话直接说出来\n"
        "3. 情绪表达干瘪：用了抽象词\n"
        "4. 段落过长：超过5行的大段描写\n"
        "5. 缺少钩子：结尾没有悬念/冲突/新信息\n"
        "6. 主角被动：主角全程被推着走\n"
        "7. 语句重复：高频重复的词或句式\n\n"
        '输出 JSON：{"issues": [{"pos": "段X", "type": "类型", "detail": "问题", "level": "小改"}], "has_hook": true, "main_char_active": true}'
    )
    return system, user


def revise_apply_prompt(cfg, chapter_no, chapter_title, content, small_issues):
    """步骤3：分层修改，只改小改 issues。中温 0.7。"""
    system = (
        "你是一位网文润色编辑。用户给你一章正文和一份小改问题清单。"
        "你的任务是只改清单里列出的问题，不改剧情走向、不改人物对话含义。"
        "保持原文字数基本不变（±10%）。只输出润色后的完整正文。"
    )
    system += "\n\n" + _output_discipline()
    issues_text = chr(10).join(
        f"- [{it.get('type','')}] {it.get('pos','')}：{it.get('detail','')}"
        for it in small_issues
    )
    user = (
        f"【第{chapter_no}章《{chapter_title}】】\n"
        f"【需要修改的问题】\n{issues_text}\n\n"
        f"【原文】\n{content}\n\n"
        "请只改上述问题，保留剧情和人物，输出修改后的完整正文。"
    )
    return system, user


def revise_verify_prompt(cfg, chapter_no, content, original_issues):
    """步骤4：回读校验。低温 0.3。"""
    system = (
        "你是一位质量校验编辑。用户给你改后正文和原问题清单。"
        "确认原问题是否解决，以及是否引入了新问题。输出 JSON。"
    )
    issues_text = chr(10).join(
        f"- [{it.get('type','')}] {it.get('detail','')}"
        for it in original_issues
    )
    user = (
        f"【原问题清单】\n{issues_text}\n\n"
        f"【改后正文】\n{content[:6000]}\n\n"
        '输出 JSON：{"resolved_count": N, "new_issues": ["..."], "ok": true/false}'
    )
    return system, user


def opening_prompt(cfg, outline):
    """开篇 Agent 提示词（P1-1）：为大纲前三章设计强开篇（每章 core_event + goal）。"""
    head_text = "\n".join(
        f"第{c.no}章《{c.title}》：{c.goal}" for c in outline.chapters[:8]
    )
    system = (
        "你是一位擅长网文开篇的主编。前 3 章是追读率分水岭，你要把大纲前三章的"
        "具体事件与本章目标设计到极致，让写手照此写出强开篇。只输出 JSON，"
        "只改前 3 章，其他章节保持原样。"
    )
    user = (
        f"【题材】{cfg.genre}\n"
        f"【书名/简介】{outline.title}｜{outline.intro}\n"
        f"【大纲前 8 章】\n{head_text}\n\n"
        "请为前三章设计强开篇，JSON 字段如下：\n"
        '{"chapters": [{"no": 1, "core_event": "本章具体发生的事件（正文必须覆盖，禁抽象）", '
        '"goal": "本章目标（强化，突出开篇钩子）"}, {"no": 2, ...}, {"no": 3, ...}]}\n'
        "设计要求：\n"
        "1. 第 1 章：500 字内立住主角（欲望/困境/人格钩子），核心冲突首章点燃，抛必追悬念；忌背景铺垫\n"
        "2. 第 2 章：冲突升级，主角做出第一个主动选择，世界观/金手指第一层自然揭示\n"
        "3. 第 3 章：形成小闭环 + 章末强钩子（冲突一触即发/真相即将揭晓/危险悄然降临）\n"
        "红线：前 3 章节奏快、少设定堆砌、主角每章至少一个主动选择、每章章末必须有钩子；"
        "与用户强约束（背景/主角/避雷）严格一致。"
    )
    return system, user


def ending_prompt(cfg, outline, world):
    """结局卡设计提示词（P1-2）：基于主题/弧光/大纲关键节点，产出终局设计卡 JSON。"""
    outline_text = "\n".join(
        f"第{c.no}章《{c.title}》：{c.goal}" for c in outline.chapters
    )
    theme = getattr(world, "theme_proposition", "") or ""
    arc = getattr(world, "protagonist_arc", None)
    arc_block = ""
    if arc is not None:
        _parts = [p for p in [getattr(arc, "belief", ""), getattr(arc, "flaw", "")]
                  if p]
        if getattr(arc, "final_insight", ""):
            _parts.append(f"最终认知：{arc.final_insight}")
        if _parts:
            arc_block = "；".join(_parts)
    gf = getattr(world, "goldfinger_rules", None)
    gf_end = ""
    if gf is not None and (getattr(gf, "growth_path", "") or getattr(gf, "upper_limit", "")):
        gf_end = f"成长路径：{gf.growth_path}；能力上限：{gf.upper_limit}"
    system = (
        "你是一位擅长终局设计的小说主编。基于主题命题、主角弧光与大纲关键节点，"
        "为全书设计一张结局卡，作为末几章写手的终局锚点。只输出 JSON。"
    )
    user = (
        f"【书名/简介】{outline.title}｜{outline.intro}\n"
        f"【核心主题命题】{theme}\n"
        + (f"【主角弧光】{arc_block}\n" if arc_block else "")
        + (f"【金手指终局条件】{gf_end}\n" if gf_end else "")
        + f"【大纲关键节点】\n{outline_text}\n\n"
        "请设计结局卡，JSON 字段如下：\n"
        "- end_state：终局世界状态（核心冲突如何解决、旧秩序如何变化，1-2 句具体）\n"
        "- theme_landing：主题落点（结局如何回归核心主题命题）\n"
        "- protagonist_choice：主角最终选择（必须符合弧光最终认知，是弧光落点）\n"
        "- character_fates：主要人物归宿数组（含反派，每人一句）\n"
        "- tone：结局基调（悲/喜/开放式/遗憾，与作品基调一致）\n"
        "要求：结局呼应开篇激励事件与首章困境；伏笔 large 级在结局前回收；"
        "结局与主题、弧光、金手指终局条件自洽，不得烂尾或自然收束失控。"
        "只输出 JSON：{\"end_state\": \"\", \"theme_landing\": \"\", "
        "\"protagonist_choice\": \"\", \"character_fates\": [], \"tone\": \"\"}"
    )
    return system, user


def reader_review_prompt(cfg, chapter_no, chapter_title, chapter_goal, content,
                         book_pitfalls: str = ""):
    """读者视角审校：从追更体验角度找问题，只定位不改写。低温 0.3。

    P0-10（混合方案③）：注入本作历史错误（pitfalls 记忆）+ 三项专项检查
    （同构场景/对话循环/数字口径）——v7 ch5 三重拼接与 ch3 对话循环是读者
    视角最敏感的"注水感"来源，须显式点名检查（历轮读者审校对同构场景
    检出率不足，需专项指令引导）。
    """
    system = (
        "你是一位挑剔的网文追更读者，在手机上刷文，3秒决定要不要划走。"
        "你只做读者体验问题定位，不改写正文。按 JSON 输出问题清单。"
    )
    user = (
        f"【第{chapter_no}章《{chapter_title}】】\n"
        f"【本章目标】{chapter_goal}\n\n"
        f"【本章正文】\n{content[:6000]}\n\n"
        "请从读者追更体验角度找出问题：\n"
        "1. 代入感：主角情绪反应像不像真人？有没有出戏的地方？\n"
        "2. 爽点闭环：这章有没有完整的压抑→铺垫→爆发→余韵？爽点够不够？\n"
        "3. 章末钩子：结尾有没有让读者想看下一章？还是流水账就结束了？\n"
        "4. 阅读障碍：有没有大段设定堆砌？长句绕口？节奏拖沓想快进？\n"
        "5. 主角主动性：主角是主动选择推进剧情，还是全程被动挨打？\n\n"
        "6. 同构场景（专项）：同一场景/同一过程（同地点同批人，仅换措辞）"
        "是否写了 ≥2 遍？——读者视角的注水感头号来源，如「进店试探→成交→离开」"
        "写三遍；发现请标记每段的起止行（level 至少中改）\n"
        "7. 对话循环（专项）：同一组问答/对峙对话（同角色、近同措辞）是否重复 ≥2 次？"
        "如「你不对劲→对方否认」反复多轮；发现请标记重复轮次（level 至少中改）\n"
        "8. 机制数字口径（专项）：本书机制卡声明的数值/进度/累积状态（如次数、进度）在本章内前后是否口径一致"
        "（无冲突、无自相矛盾）；发现冲突请标记位置（level 大改）\n"
        + (f"\n【本作历史错误（本书踩坑账本——重点检查这些是否又犯了）】\n{book_pitfalls}\n" if book_pitfalls else "")
        + '\n输出 JSON：{"issues": [{"pos": "段X", "type": "类型", "detail": "读者感受",'
          '"level": "小改/中改/大改", "suggestion": "针对该问题的具体改法（一句话，指出怎么写更好）"}], "'
          '"追更欲": 1-5, "一句话感受": "..."}\n"'
        "level 判定：小改=句子级瑕疵；中改=某段不爽但还能看；大改=整章想划走。"
    )
    return system, user
def blind_check_prompt(cfg, chapter_no, recent_summaries, world_text):
    """读者视角自检：三问。低温 0.3。"""
    system = (
        "你是一位第一次读这本小说的读者。你不知道作者是谁、不知道大纲。"
        "请根据最近3章的摘要，回答三个问题。输出 JSON。"
    )
    summaries_text = chr(10).join(
        f"第{i+1}章：{s}" for i, s in enumerate(recent_summaries)
    )
    user = (
        f"【世界观】\n{world_text[:1500]}\n\n"
        f"【最近3章摘要】\n{summaries_text}\n\n"
        "作为读者，请回答：\n"
        "1. 主角是谁？（一句话：身份+性格）\n"
        "2. 他想干什么？（核心目标）\n"
        "3. 他在做什么？（当前行动）\n\n"
        '输出 JSON：{"who": "...", "wants": "...", "doing": "...", "confused": true/false, "issues": ["..."}'
    )
    return system, user




def chapter_prompt(
    cfg: NovelConfig,
    chapter_no: int,
    chapter_title: str,
    chapter_goal: str,
    research_block: str,
    memory_context: str,
    prev_tail: str,
    is_final: bool = False,
    chapter_core_event: str = "",
    chapter_events: str = "",
    chapter_design: str = "",
    personal_anchor: str = "",
    teardown_anchor: str = "",
    user_memory_block: str = "",
    panel_baseline: str = "",
) -> tuple[str, str]:
    system = (
        "你是一位高产的网络小说签约写手，擅长按大纲与既定设定稳定产出正文。"
        "你严格遵守世界设定与人物性格，绝不推翻前文既定事实；"
        "用户的强约束（背景/主角/避雷）每章都必须遵守；"
        "每章都要有推进、有情绪起伏、结尾留钩子。"
        "爽点设计：本章必须至少有1个小反转/小揭秘/小甜饼/小打脸，均匀分布，"
        "不要等大高潮才释放情绪；用连续小起伏替代单个大高潮。"
    )
    if cfg.de_ai:
        system += (
            "你的文字要有真人作者的质感：对白有潜台词、叙事有详略、人物有犹豫，"
            "避免AI腔（连接词堆砌、强行升华、句式整齐、每句都服务主线）。"
        )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("chapter")
    if cfg.character_depth:
        system += "\n\n" + _character_depth_rules("chapter")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("chapter")
    constraints = _chapter_constraints(cfg)
    de_ai_block = _de_ai_rules() if cfg.de_ai else ""
    ending_block = _ending_requirements() if is_final else ""
    # ---- REQ-超长篇攻坚 S4.1 前缀缓存对齐 ----
    # 稳定块（同书跨章逐字节一致：单章字数硬指标 + 硬约束 + 题材共性参考）前置到 user 最前，
    # 变化块（章号/目标/事件/记忆/衔接）置后 → 同书所有章共享最大稳定前缀，命中 DeepSeek 前缀缓存价。
    # prefix_cache_aligned=False 时保持原组装顺序（稳定块在中部，向后兼容）。
    aligned = getattr(cfg, "prefix_cache_aligned", True)
    stable_blocks = (
        f"【单章字数·硬指标】本章正文（不含【摘要】行）必须达到 {cfg.chapter_words} 字以上。"
        f"若正文不足，必须通过增加场景细节、人物对话交锋、心理挣扎与行动过程来充实，"
        f"严禁草草收尾、严禁提前结束章节。字数低于目标视为不合格，会被退回重写。\n"
        f"{constraints}\n\n"
        f"{('【题材共性参考】\n' + research_block + '\n') if research_block else ''}"
    )
    dynamic_blocks = (
        f"【本章】第{chapter_no}章《{chapter_title}》\n"
        f"【本章目标】{chapter_goal}\n"
        f"【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】\n{chapter_core_event if chapter_core_event else '（无）'}\n"
        f"{('【本章事件序列（必须逐事件覆盖、每事件只写一遍，按编号顺序推进剧情：'
        '从事件1到事件N依次发生，后一事件从上一事件的结果之后续写，'
        '禁止任何事件重复出现（包括换地点、换措辞重写同一事件），'
        '禁止回到上一事件开头状态重演）】\n' + chapter_events + '\n') if chapter_events else ''}"
        f"{('【本章设计卡（正向创作指引·软约束：按此设计写本章，不偏离戏剧核心）】\n'
        + chapter_design + '\n') if chapter_design else ''}"
        f"{('【全书结局章】本章是本书最后一章，必须给出完整结局。\n' + ending_block + '\n') if is_final else ''}"
        f"【前情记忆】\n{memory_context if memory_context else '（本作首章，无前情）'}\n\n"
        f"【上一章结尾（衔接用，保持文气连贯）】\n{prev_tail if prev_tail else '（本作首章）'}\n\n"
        f"{_panel_baseline_block(panel_baseline)}"
        f"写作要求：\n"
        f"1. 严格按本章目标推进剧情，不跑题、不提前揭穿后续伏笔\n"
        f"2. 人物言行符合人物卡设定；对话要有性格辨识度\n"
        f"3. 开头 1-2 句自然衔接前情；本章内部完成至少一次推进/转折；"
        f"{'本章是结局章：结尾必须收束全书主线与关键伏笔，并以「结尾升维」收尾（技术→哲学/产品→人性/事件→时代，通过人物视角与具体细节自然带出，不喊口号），不能留悬念断章' if is_final else '结尾留钩子引向下章'}\n"
        f"3.5 本章每个事件/冲突场面（死亡-复活、被围、救人、逃离、追击等）只允许完整发生一次；"
        f"禁止同一事件/同一段落以任何形式重复两次及以上（含换地点、换措辞重写同一事件）；"
        f"事件之间必须因果推进，后一事件从上一事件的结果之后续写，禁止回到上一事件开头状态重演\n"
        f"4. 语言符合调研建议的文风混合方向；避免重复用词与套路化表达；避雷元素绝对不得出现\n"
        f"4.5 签约硬指标：对话占比≥60%（描写段≤3行）；每300字一个小钩子；每800字主角≥2次主动决策；"
        f"第1章第一句直接进冲突（仅第1章适用）；前3章内必须出第一个小高潮；"
        f"情绪词少而准：关键情绪节点允许直接点破（'他很愤怒'），点破处必须紧跟动作/微表情/生理反应，禁止连发堆砌\n"
        f"{('5. 防同质化：本章冲突与反转不得与已写章节重复；收益必须对应代价；情绪只通过动作/细节侧面体现，禁止直白心理描写' + chr(10)) if cfg.anti_similarity else ''}"
        f"{'6' if cfg.anti_similarity else '5'}. 输出格式（必须严格遵循，不要省略）：先输出正文，正文结束后【必须】另起一行"
        f"输出【摘要】加 1-2 句本章剧情摘要（用于记忆库）；即使以收束句结尾，也必须在之后补上【摘要】行。"
    )
    if aligned:
        user = stable_blocks + "\n\n" + dynamic_blocks
    else:
        user = (
            f"【本章】第{chapter_no}章《{chapter_title}》\n"
            f"【本章目标】{chapter_goal}\n"
            f"【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】\n{chapter_core_event if chapter_core_event else '（无）'}\n"
            f"{('【本章事件序列（必须逐事件覆盖、每事件只写一遍，按编号顺序推进剧情：'
            '从事件1到事件N依次发生，后一事件从上一事件的结果之后续写，'
            '禁止任何事件重复出现（包括换地点、换措辞重写同一事件），'
            '禁止回到上一事件开头状态重演）】\n' + chapter_events + '\n') if chapter_events else ''}"
            f"{('【本章设计卡（正向创作指引·软约束：按此设计写本章，不偏离戏剧核心）】\n'
            + chapter_design + '\n') if chapter_design else ''}"
            f"{('【全书结局章】本章是本书最后一章，必须给出完整结局。\n' + ending_block + '\n') if is_final else ''}"
            f"【单章字数·硬指标】本章正文（不含【摘要】行）必须达到 {cfg.chapter_words} 字以上。"
            f"若正文不足，必须通过增加场景细节、人物对话交锋、心理挣扎与行动过程来充实，"
            f"严禁草草收尾、严禁提前结束章节。字数低于目标视为不合格，会被退回重写。\n"
            f"{constraints}\n\n"
            f"{('【题材共性参考】\n' + research_block + '\n\n') if research_block else ''}"
            f"【前情记忆】\n{memory_context if memory_context else '（本作首章，无前情）'}\n\n"
            f"【上一章结尾（衔接用，保持文气连贯）】\n{prev_tail if prev_tail else '（本作首章）'}\n\n"
            f"{_panel_baseline_block(panel_baseline)}"
            f"写作要求：\n"
            f"1. 严格按本章目标推进剧情，不跑题、不提前揭穿后续伏笔\n"
            f"2. 人物言行符合人物卡设定；对话要有性格辨识度\n"
            f"3. 开头 1-2 句自然衔接前情；本章内部完成至少一次推进/转折；"
            f"{'本章是结局章：结尾必须收束全书主线与关键伏笔，并以「结尾升维」收尾（技术→哲学/产品→人性/事件→时代，通过人物视角与具体细节自然带出，不喊口号），不能留悬念断章' if is_final else '结尾留钩子引向下章'}\n"
            f"3.5 本章每个事件/冲突场面（死亡-复活、被围、救人、逃离、追击等）只允许完整发生一次；"
            f"禁止同一事件/同一段落以任何形式重复两次及以上（含换地点、换措辞重写同一事件）；"
            f"事件之间必须因果推进，后一事件从上一事件的结果之后续写，禁止回到上一事件开头状态重演\n"
            f"4. 语言符合调研建议的文风混合方向；避免重复用词与套路化表达；避雷元素绝对不得出现\n"
            f"4.5 签约硬指标：对话占比≥60%（描写段≤3行）；每300字一个小钩子；每800字主角≥2次主动决策；"
            f"第1章第一句直接进冲突（仅第1章适用）；前3章内必须出第一个小高潮；"
            f"情绪词少而准：关键情绪节点允许直接点破（'他很愤怒'），点破处必须紧跟动作/微表情/生理反应，禁止连发堆砌\n"
            f"{('5. 防同质化：本章冲突与反转不得与已写章节重复；收益必须对应代价；情绪只通过动作/细节侧面体现，禁止直白心理描写' + chr(10)) if cfg.anti_similarity else ''}"
            f"{'6' if cfg.anti_similarity else '5'}. 输出格式（必须严格遵循，不要省略）：先输出正文，正文结束后【必须】另起一行"
            f"输出【摘要】加 1-2 句本章剧情摘要（用于记忆库）；即使以收束句结尾，也必须在之后补上【摘要】行。"
        )
    ref = _style_reference(cfg)
    if ref:
        system += "\n\n" + ref
    if personal_anchor:
        system += "\n\n" + wrap_style_anchor(PERSONAL_ANCHOR_HEADER, personal_anchor)
    if getattr(cfg, "teardown_style_anchor", True):
        anchor = wrap_style_anchor(TEARDOWN_ANCHOR_HEADER, teardown_anchor)
        if anchor:
            system += "\n\n" + anchor
    if user_memory_block:
        system += "\n\n" + user_memory_block
    system += _skill_append(cfg)
    system += "\n\n" + _output_discipline()
    system += "\n\n" + _writing_constraints()
    return system, user


# ============================================================
# 已有内容续写 / 扩写（需求分析-已有内容续写与扩写 v1.0）
# ============================================================

def world_from_content_prompt(cfg: NovelConfig, chapters_block: str) -> tuple[str, str]:
    """续写模式：基于用户手写正文提炼世界观/人物卡/伏笔（保证设定与用户内容一致）。"""
    system = (
        "你是一位严谨的小说设定分析师。用户给你「已有正文」，"
        "你要从正文中提炼出可支撑续写的世界观、人物卡与伏笔清单，"
        "作为后续章节生成的全书记忆库。"
        "只提炼正文中确实存在或明确暗示的信息，不得凭空添加与正文矛盾的人设与设定；"
        "正文未提到但续写必需的背景可作合理推断并标注为推断。"
    )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("world")
    if cfg.character_depth:
        system += "\n\n" + _character_depth_rules("world")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("world")
    hard = _hard_constraints(cfg)
    user = (
        f"【题材】{cfg.genre}\n"
        f"【书名/简介】{cfg.effective_title()}｜{cfg.premise or '（未提供）'}\n"
        f"{hard}\n\n"
        f"【用户已有正文（节选，可能截断）】\n{chapters_block}\n\n"
        f"请输出 JSON：\n"
        f"- world_view：从正文提炼的世界观设定（2-4 句：时代背景/世界格局/力量体系/核心矛盾）\n"
        f"- world_reveals：世界观逐步揭示计划数组（跟随主角视角）。"
        f"每项 {{\"foreshadow_ch\": 铺垫最早章, \"hint\": 铺垫要点（正文已出现的暗示/线索）, "
        f"\"from_ch\": 主角正式得知真相的起始章, \"content\": 真相, "
        f"\"keywords\": 真相独有锚定词数组（1-5 个专有名词/关键短语，正文出现即视为揭示；不得与 hint 用词重叠）}}；"
        f"正文中已明确揭示的真相 from_ch 填 1；正文已出现暗示但未揭真相的，foreshadow_ch 填正文最早出现暗示的章节、from_ch 排到后续揭晓章；"
        f"正文尚未出现、作为后续悬念的设定按剧情需要排期；foreshadow_ch 必须 < from_ch（真相要有前文铺垫）；"
        f"全部阶段 content 合并后必须与 world_view 完全一致；未到 from_ch 的真相不得提前透露，只能按 hint 写暗示\n"
        f"- characters：3-8 个人物对象数组，每项 {{\"name\":姓名,\"role\":定位,\"desc\":性格与背景}}；"
        f"主角与已出场人物必须包含，描述要与正文言行一致\n"
        f"- foreshadowing：2-5 条从正文中存在的伏笔/悬念数组，每项 {{\"id\":编号, \"name\":伏笔名, "
        f"\"buried_ch\":埋点章号, \"reveal_ch\":回收章号, \"level\":\"small\"/\"medium\"/\"large\", "
        f"\"content\":伏笔内容}}；每条必须给出埋点与回收章号，正文没有悬念时提炼潜在冲突\n"
        f"- mechanism_rules：从正文提炼的金手指/核心机制规则卡（正文有金手指/系统/独特设定则必填，否则空字符串）。"
        f"必须无歧义、与正文一致，覆盖：机制语义（每个数字/状态的确切含义）、结算规则（何时增减/重置/衔接历史）、"
        f"代价与终局（代价递增/上限）、正文要求（首章兑现标题，数字与规则全书一致）、"
        f"数字账本写法（正文中数字的准确写法与每个数字语义）、时间词语义（'今日'等是否每日重置、跨天结算）、"
        f"多数字关系（多套数字必须交代关联）、历史衔接（开篇已有痕迹与当前数字自洽）\n"
        f"- theme_proposition：核心主题命题（一句话内核，从正文提炼）\n"
        f"- conflict_matrix：三层冲突矩阵数组，每项 {{\"level\":\"personal\"/\"interpersonal\"/\"world\", \"description\":该层冲突}}，三层各一条\n"
        f"- protagonist_arc：主角完整弧光卡 {{\"belief\":开篇信念, \"flaw\":核心缺陷, \"change_nodes\":[关键转变], \"final_insight\":最终认知}}\n"
        f"- character_motives：核心配角动机数组，每项 {{\"name\", \"goal\", \"behavior_logic\", \"link_to_protagonist\"}}\n"
        f"- goldfinger_rules：金手指五要素 {{\"trigger\", \"upper_limit\", \"lower_limit\", \"cost\", \"growth_path\"}}（无金手指则空对象）\n"
        f"- setting_taboos：设定禁忌清单数组（正文明确禁止出现的吃设定行为）\n"
        f"- items：从正文提炼的重要道具/物品卡数组（正文有关键道具则产出 1-5 个，否则空数组）。"
        f"每项 {{\"name\":道具名, \"origin\":来源, \"cost\":使用代价, \"restriction\":限制/边界, "
        f"\"effect\":作用（明确边界）}}；道具静态属性与正文一致，禁写成万能工具\n"
        f"- power_system：从正文提炼的等级/境界体系卡对象（正文有升级/境界战力体系则产出，否则空对象）。"
        f"字段：{{\"levels\":[{{\"name\":境界名, \"range\":数值区间, \"requirement\":突破条件, "
        f"\"capability\":能力/战力上限}}], \"protagonist_level\":主角当前境界, "
        f"\"progression_rule\":突破规则}}；境界阶梯与正文一致，防后期战力崩坏\n"
        f"要求：与用户正文严格一致，人物动机有据可查，避免凭空新增与正文矛盾的世界观。"
    )
    system += _skill_append(cfg)
    return system, user


def continue_outline_prompt(
    cfg: NovelConfig,
    user_chapters_block: str,
    remain_chapters: int,
) -> tuple[str, str]:
    """续写模式：基于用户已有章节（标题+剧情脉络），规划后续章节大纲。

    输出的 chapters 仅指「需要 AI 续写」的后续章节（第 N+1 章起），
    由上层拼接到用户章节之后形成完整大纲。
    """
    system = (
        "你是一位擅长续写的小说主编。用户已写了一部分章节，你需要规划接下来的章节大纲，"
        "让故事自然延续。续写必须：承接前文人物状态与剧情脉络，不推翻已写事实，"
        "主线清晰、每章有推进与钩子，最后一章是完整结局。"
    )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("outline")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("outline")
    hard = _hard_constraints(cfg)
    soft = _soft_direction(cfg)
    user = (
        f"【目标】为一部 {cfg.genre} 题材小说规划【后续 {remain_chapters} 章】的章节大纲"
        f"（全书目标约 {cfg.total_words} 字，续写单章约 {cfg.chapter_words} 字）\n"
        f"【书名】{cfg.effective_title()}\n"
        f"{hard}\n\n{soft}\n\n"
        f"【用户已写章节（标题+剧情脉络）】\n{user_chapters_block}\n\n"
        f"请输出 JSON（只输出后续章节）：\n"
        f"- chapters：恰好 {remain_chapters} 个章节对象的数组，每项包含字段：no(相对序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(伏笔), emotion(情绪价值), transition(转场提示), tag(标签), cliffhanger(章末钩子)，章节标题必须互不相同、与已有章节也不得重名\n"
        f"要求：\n"
        f"1. 第 1 章要自然承接用户最后章节的结尾，人物状态/情节无缝衔接\n"
        f"2. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」\n"
        f"3. 主线紧扣用户已写内容的核心冲突并推向高潮；最后一章必须是完整结局章（收束主线、伏笔与人物命运，结尾升维收尾）\n"
        f"4. 用户预设人物必须持续出现且符合设定；避雷元素绝对不得出现\n"
    )
    system += _skill_append(cfg)
    return system, user


def expand_chapter_prompt(
    cfg: NovelConfig,
    chapter_no: int,
    chapter_title: str,
    content: str,
    target_words: int,
    personal_anchor: str = "",
    teardown_anchor: str = "",
    user_memory_block: str = "",
) -> tuple[str, str]:
    """扩写已有章节：保留原有剧情/对话/人设，扩充细节到目标字数。

    REQ-双锚风格锚定：注入风格范文（_style_reference）+ 个人旧稿锚（personal_anchor）
    + 拆书库题材锚（teardown_anchor），全部为弱约束（只模仿语感，严禁抄袭内容）。
    """
    system = (
        "你是资深小说扩写编辑。用户给出「已有章节正文」，你要在【不改变情节走向、"
        "不删减已有内容、不改变人物性格与对话原意】的前提下，把正文扩写到目标字数。"
        "扩写手段：增加场景细节、环境氛围、动作过程、对话交锋、心理挣扎、"
        "过往回忆（不揭穿未来伏笔）与人物生活体感。"
        "禁止注水重复、禁止大段空泛景物堆砌、禁止改变剧情结论。"
    )
    if cfg.de_ai:
        system += (
            "扩写文字要有真人作者的质感：细节具体、对话有潜台词，"
            "避免AI腔（连接词堆砌、强行升华、句式整齐）。"
        )
    ref = _style_reference(cfg)
    if ref:
        system += "\n\n" + ref
    if personal_anchor:
        system += "\n\n" + wrap_style_anchor(PERSONAL_ANCHOR_HEADER, personal_anchor)
    if getattr(cfg, "teardown_style_anchor", True):
        anchor = wrap_style_anchor(TEARDOWN_ANCHOR_HEADER, teardown_anchor)
        if anchor:
            system += "\n\n" + anchor
    if user_memory_block:
        system += "\n\n" + user_memory_block
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("chapter")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("chapter")
    system += "\n\n" + _output_discipline()
    user = (
        f"【本章】第{chapter_no}章《{chapter_title}》\n"
        f"【目标字数】扩写后正文（不含任何标记）应达到约 {target_words} 字\n"
        f"【当前正文（{len(content)} 字）】\n{content}\n\n"
        f"请输出扩写后的完整正文：\n"
        f"1. 必须完整保留原有情节、人物言行与对话（可润色但不得改变原意）\n"
        f"2. 在原文基础上扩充：场景细节、对话交锋、动作过程、心理挣扎、氛围铺垫\n"
        f"3. 不改变情节走向、不提前揭穿后续伏笔、不引入与上下文矛盾的新设定\n"
        f"4. 只输出正文本身，不要输出摘要、不要输出任何前后缀说明\n"
    )
    system += _skill_append(cfg)
    return system, user


def final_review_prompt(cfg: NovelConfig, outline: Outline, chapters_block: str,
                        world_text: str) -> tuple[str, str]:
    """P2-2 全书终审：整本书写完后的终审编辑，只查整本层面的结构性问题。

    输入全书章节标题+摘要脉络与设定库；输出全书级 issues（剧情连贯/伏笔回收/
    人物弧光/节奏结构/结局完整性）。不阻塞导出，问题先交付后呈现。
    """
    system = (
        "你是一位资深出版编辑，负责一本书付印前的最后终审。"
        "你通读全书章节脉络与设定，只找整本层面的结构性问题，不改写正文。"
        "按 JSON 格式输出终审结果。"
    )
    user = (
        f"【书名/简介】{outline.title}｜{outline.intro}\n"
        f"【题材】{cfg.genre}\n"
        f"【全书章节数】{len(outline.chapters)}\n\n"
        f"【全书章节脉络（每章一行：章节号·标题·摘要）】\n{chapters_block}\n\n"
        f"【世界观/人物设定】\n{world_text[:2500]}\n\n"
        "请从全书层面终审以下维度（聚焦整本结构，不要逐章挑细节）：\n"
        "1. 剧情连贯：章节衔接有无断裂、跳跃或前后矛盾\n"
        "2. 伏笔回收：前文埋设的关键伏笔是否全部回收，有无明显遗忘\n"
        "3. 人物弧光：主角与重要配角是否有完整、合理的成长/变化轨迹\n"
        "4. 节奏结构：全书节奏是否失衡（前紧后松/中期拖沓/高潮缺失）\n"
        "5. 结局完整性：结局是否完整收束主线与核心冲突，有无烂尾或强行团圆\n\n"
        '输出 JSON：{"issues": [{"type": "剧情连贯/伏笔回收/人物弧光/节奏结构/结局完整性", '
        '"problem": "具体问题（对应章节）", "suggestion": "改进建议"}], "ok": true/false}'
    )
    return system, user


# =====================================================================
# REQ-词表换LLM 批次一（2026-10-28）：4 处写死语义词表换 LLM 语义判断，
# 词表保留确定性兜底。prompt 全部收敛于此（AGENTS §11.3 代码数据分离）。
# 调用方（beats/beat_gate/scene_event_utils/validator）只消费规范 JSON，
# 任何解析失败/异常在调用方回退词表（logger.warning 留痕，不阻塞）。
# =====================================================================


def beats_extract_prompt(text: str) -> tuple[str, str]:
    """REQ-词表换LLM 批次一 beats.py：正文 → 规范节拍标签序列（重演检测输入）。

    原 `_BEAT_DICT` 词表只认固定行为词，换措辞（"他被问得哑口无言" vs "盘问"）
    即漏抽。LLM 按语义抽取节拍，但标签必须限定在规范集合内，保证下游
    LCS/循环检测（_beats_rewrite_detected/_beats_local_rewrite_detected/
    _beat_cycle_issues/_beats_scene_isomorphic）可消费。
    """
    system = (
        "你是小说结构分析员。把给定正文按时间顺序抽取「情节节拍序列」——"
        "每个节拍 = 正文中一个关键行为/剧情转折/特殊事件。\n"
        "节拍标签只能从以下规范集合中原样选用（不得自造、不得改写）：\n"
        "盘问（逼问/质问/试探）、戳穿（识破/看穿）、失控、死亡（咽气/毙命/断气，"
        "不包括普通倒下）、复活（醒来/睁眼/爬起/回神）、对峙（别动/盯着/冷笑）、"
        "包扎（按住/撕布/止血/换药）、装晕（装死）、离场（离开/跟上/消失/转身/离去）、"
        "约定（约在）。\n"
        "判定规则：\n"
        "1. 按正文出现的时间顺序输出，一个节拍只记一次（连续重复/换措辞重申同一行为不重复计数）\n"
        "2. 只有正文确实发生了该行为才记；没有对应行为的不要生造\n"
        "3. 宁缺毋滥：拿不准的节拍不输出；正文无关键行为时输出空列表\n"
        '只输出合法 JSON：{"beats": ["盘问", "死亡", "复活"]}；无节拍输出 '
        '{"beats": []}。不要输出代码块标记、解释或省略号。'
    )
    user = f"【待分析正文】\n{text}\n\n请抽取该正文的情节节拍序列。"
    return system, user


def beat_gate_coverage_prompt(content: str, planned_beats: list[str]) -> tuple[str, str]:
    """REQ-词表换LLM 批次一 beat_gate.py：整章一次 LLM 判各计划拍是否在正文兑现。

    替代 `_ACTION_SYNONYMS`/`_MOD_SYNONYMS` 近义词表的子串命中判定——近义改写
    （清晨↔蒙蒙亮、咬喉↔咬穿）与语序调整在词表路径漏判，LLM 按语义判。
    一次调用覆盖全部拍（成本：每章 1 次），输出与 planned_beats 一一对应。
    """
    beats_block = "\n".join(f"{i}. {b}" for i, b in enumerate(planned_beats))
    system = (
        "你是小说编辑，检查大纲「计划拍」是否在正文中兑现。\n"
        "对每个计划拍（一个动作/事件片段），判断其动作与限定语义是否在正文出现：\n"
        "- 换措辞、近义改写、语序调整都算兑现（如计划拍写「清晨出门」，正文写"
        "「天刚蒙蒙亮，他走出院门」算兑现）\n"
        "- 同一动作以更具体方式呈现算兑现（计划「前往」，正文写「踏入密林深处」算）\n"
        "- 计划拍的核心限定（时间/地点/对象）缺失则不算完整兑现\n"
        "- 正文完全没体现该拍的动作语义 → covered=false\n"
        "hit 列出正文中兑现该拍的语义点（动作/限定各一项即可），miss 列出缺失的语义点。\n"
        '只输出合法 JSON：{"beats": [{"covered": true, "hit": ["前往"], "miss": []}, '
        '{"covered": false, "hit": [], "miss": ["清晨"]}]}。'
        "beats 数组长度必须与计划拍数量一致、顺序一一对应。不要代码块标记、解释或省略号。"
    )
    user = (
        "【本章正文】\n" + (content[:3000] if content else "") + "\n\n"
        "【大纲计划拍】\n" + beats_block + "\n\n"
        "请逐拍判断是否在正文兑现，输出 covered/hit/miss。"
    )
    return system, user


def scene_event_derive_prompt(beats: list[str], names: tuple[str, ...] = ()) -> tuple[str, str]:
    """REQ-词表换LLM 批次一 scene_event_utils.py：事件语义槽 + 因果粘连（一次调用）。

    替代 `_SCENE_ACTION_NORM`（动作归一表）与 `_CAUSAL_BEAT_RE`（因果粘连正则）：
    - 每个节拍片段提取「冲突对象」与「归一化动作」（语义槽，供事件清单与互斥校验）；
    - 相邻片段因果粘连判定（后一片段是前一片段结果的直接延续 → 合并为同一事件）。
    """
    beats_block = "\n".join(f"{i}. {b}" for i, b in enumerate(beats))
    names_block = ("；".join(names)) if names else "（无固定名单，按文本实际人物/称谓）"
    system = (
        "你是小说结构编辑。给出一章从大纲人物动线切出的节拍片段列表，做两件事：\n"
        "1. 为每个片段提取语义槽：\n"
        "   - conflict（冲突对象）：片段中被救援/攻击/对峙/追杀的核心人物或对象，"
        "优先从给定名单中选准确名字；名单外按文本实际出现的人物名/身份称谓"
        "（如 学员/弟子/少年/少女）；没有明确对象输出空串\n"
        "   - action（归一化动作）：把片段的核心动作归一到规范动作集合——"
        "救援/围攻/击杀/逃离/撤离/追击/抵挡/偷袭/刺杀/对峙/埋伏/侦查/探索；"
        "无匹配输出空串\n"
        "2. 判定相邻片段因果粘连：后一片段（下标 i）是前一片段（下标 i-1）结果的"
        "直接延续（同一事件的自然推进，如「遭遇袭击→受伤昏迷→苏醒」），需要合并为"
        "同一事件时，把后一片段下标 i 加入 merge 数组；正常递进/换地点/新对手不算粘连。\n"
        '只输出合法 JSON：{"events": [{"conflict": "苏妄言", "action": "救援"}, '
        '{"conflict": "", "action": "探索"}], "merge": [2, 4]}。'
        "events 长度必须与片段数一致；merge 下标从 1 开始。不要代码块标记、解释或省略号。"
    )
    user = (
        "【节拍片段列表】\n" + beats_block + "\n\n"
        f"【冲突对象候选名单】{names_block}\n\n"
        "请提取每个片段的 conflict/action，并输出需要因果合并的相邻对。"
    )
    return system, user


def scene_rewrite_prompt(text: str, done_events: list[str]) -> tuple[str, str]:
    """REQ-词表换LLM 批次一 scene_event_utils.py：场景正文是否复写已发生事件。

    替代 `_SCENE_RESCUE_VERBS` + `_event_conflict` 称谓兜底的字符串判定——
    换措辞（救援→施救/帮/拉开）在词表路径漏检。输出命中事件下标，未命中 -1。
    宁可漏报不误杀（防正常递进/呼应误判为复写）。
    """
    ev_block = "\n".join(f"{i}. {e}" for i, e in enumerate(done_events)) if done_events else "（无）"
    system = (
        "你是小说质检编辑。判断一段场景正文是否「换措辞重写」了某个已发生事件。\n"
        "判定标准：正文与某已发生事件本质是同一人物 + 同一冲突 + 同一结果，"
        "仅措辞/切入点/细节不同（如已写「救出苏妄言」，正文又写「把苏妄言从废墟里拉出来」"
        "→ 复写）；正文若是在事件后续推进/新信息揭示/正常呼应（一句话带过），不算复写。\n"
        "宁可漏报不误杀：拿不准输出 -1。\n"
        '只输出合法 JSON：{"rewrite_of": -1} 或 {"rewrite_of": 0}（0=复写第1个已发生事件）。'
        "不要代码块标记、解释或省略号。"
    )
    user = (
        "【已发生事件清单】\n" + ev_block + "\n\n"
        "【待检查场景正文】\n" + (text[:3000] if text else "") + "\n\n"
        "该场景正文是否复写了某个已发生事件？输出命中下标或 -1。"
    )
    return system, user


def mechanism_check_prompt(mechanism: str) -> tuple[str, str]:
    """REQ-词表换LLM 批次一 validator.py：机制卡三要素 LLM 语义校验（每本 1 次）。

    替代 `_MECH_SEMANTIC`/`_MECH_SETTLE`/`_MECH_END` 信号词表——"今日剩余复活次数"
    这类含数字的语义描述旧词表会漏判为缺「机制语义」。missing 文案必须与词表
    兜底输出完全一致（调用方按固定文案判定三要素缺失）。
    """
    system = (
        "你是小说设定编辑。检查金手指机制卡是否覆盖以下三要素：\n"
        "1. 机制语义：每个数字/状态的确切含义（如「蚀痕=死亡累计次数」「今日=自然日」）\n"
        "2. 结算规则：何时增减/重置/与历史衔接（如「每死一次蚀痕+1」「次日清零」）\n"
        "3. 代价与终局：代价递增/上限/触发/永久（如「蚀痕满十死亡不可逆」「上限九次」）\n"
        "判定规则：\n"
        "- 机制卡对某要素有明确描述（含数字/状态的语义说明、结算动作、代价规则）就不算缺失\n"
        "- 只把「确实没有描述」的要素列入 missing，宁可少判不可多判\n"
        "- missing 只能使用以下三个固定文案之一：\n"
        '  "机制语义（每个数字/状态的确切含义）" / "结算规则（何时增减/重置/与历史衔接）" / '
        '"代价与终局（代价递增/上限/触发）"\n'
        '只输出合法 JSON：{"missing": ["结算规则（何时增减/重置/与历史衔接）"]}；'
        "三要素齐全输出 {\"missing\": []}。不要代码块标记、解释或省略号。"
    )
    user = f"【金手指机制卡】\n{mechanism}\n\n请判断该机制卡缺失哪些要素。"
    return system, user


def ai_taste_check_prompt(text: str) -> tuple[str, str]:
    """REQ-词表换LLM 批次二 ai_taste.py：AI 写作痕迹四维度 LLM 语义判定（整章 1 次）。

    替代 CONNECTIVE_WORDS/EMOTION_WORDS/_FILLER_WORDS/_DIRECT_PATTERN 词表的
    子串/正则命中判定——换措辞的 AI 腔（"事情推进得很顺利" vs 直接写"然而"）在
    词表路径漏判。输出四个维度的布尔命中；密度统计（每千字）与句式排比/段落
    雷同仍由确定性计算承担，不在此 prompt 范围。
    """
    system = (
        "你是网文编辑，判断一段正文是否露出 AI 写作痕迹。只看以下四个维度：\n"
        "1. connective_excess：连接词堆砌——「然而/因此/不仅…而且/值得注意的是/"
        "综上所述/与此同时/此外/总之/显而易见/事实上/确实/毕竟/更何况/由此可见/"
        "换句话说/换言之」这类书面连接词高频扎堆，读感像议论文转述而非网文叙事；"
        "少量使用且服务于转折、上下文自然不算\n"
        "2. emotion_excess：抽象情绪词直白宣告过度——「愤怒/震惊/无奈/开心/难过/"
        "恐惧/兴奋/悲伤/痛苦/幸福/激动/失望/害怕/紧张」等情绪词连发直说，缺动作/"
        "微表情承载；情绪词有动作支撑或只偶尔点破不算\n"
        "3. filler_overuse：填充词口头禅堆砌——「然后/于是/只见/当即/顿时/但是/"
        "然而/因此/不禁/果然/终于」等连接填充词高频复现，读感啰嗦机械；正常叙事"
        "节奏下少量不算\n"
        "4. zero_direct：全程演过头零直白——全文没有一处「他怕了/她恨得发颤」式"
        "直白点破（代词+情绪），情绪全靠动作暗示、读者要自己猜\n"
        "判定规则：维度确实成立才 true，宁可少判不可多判；拿不准输出 false。\n"
        '只输出合法 JSON：{"connective_excess": true, "emotion_excess": false, '
        '"filler_overuse": false, "zero_direct": false}。不要代码块标记、解释或省略号。'
    )
    user = (
        "【待检查正文】\n" + (text[:3000] if text else "") + "\n\n"
        "请判断该正文的 AI 写作痕迹维度命中情况。"
    )
    return system, user


def panel_anchor_check_prompt(seg: str) -> tuple[str, str]:
    """REQ-词表换LLM 批次二 quality.py：两处面板数字之间是否发生真实结算事件。

    替代 _PANEL_EVENT_ANCHORS（死了/死亡/复活/苏醒/睁眼/醒来/结算/重置）+
    _PANEL_EVENT_RECALL（看见/回忆/快死…）锚点+语境排除正则——换措辞的真实结算
    （"他咽了气"）在词表路径漏判，"她看见他死了/他应该已经死了"这类回忆转述误判。
    输出布尔+一句话理由（仅作诊断留痕，判定只取布尔）。
    """
    system = (
        "你是机制面板校对员。小说中同一面板数字在正文相隔较远处出现了两次，"
        "需要判断这两次出现之间的正文段里是否发生了「真实结算事件」——即相关人物"
        "的机制状态真实变化（如死亡、复活、苏醒、状态刷新、数值重置）。\n"
        "以下情况**不算**真实结算事件：\n"
        "- 回忆/转述/他人语境：只是看见/听说/梦见/感知某人死了，或复述他人死亡"
        "（如「她看见他死了」「他应该已经死了」）\n"
        "- 将死未死：「快死了/差点死」等未真正结算\n"
        "- 设定陈述/解说：只是说明面板机制本身（如「面板刷新的时候」）\n"
        '只输出合法 JSON：{"has_settle_event": true, "reason": "……"} 或 '
        '{"has_settle_event": false, "reason": "……"}。'
        "reason 一句话说明依据（≤40字）。不要代码块标记、解释或省略号。"
    )
    user = (
        "【两处面板数字之间的正文】\n" + (seg[:2000] if seg else "") + "\n\n"
        "这段正文里是否发生了真实结算事件？"
    )
    return system, user
