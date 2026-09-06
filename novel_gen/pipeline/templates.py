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

from ..config import NovelConfig
from ..store.schemas import Outline


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


def _soft_direction(cfg: NovelConfig) -> str:
    """参考方向区块：剧情走向，AI 可在此基础上细化。"""
    if not cfg.plot_direction:
        return ""
    return f"【用户设定·参考方向（可在此基础上细化发挥）】\n- 剧情走向：{cfg.plot_direction}"


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
    return (
        "【去AI味写作约束】\n"
        "1. 避免连接词堆砌：少用'然而/因此/不仅…而且/值得注意的是/综上所述'，用动作和场景自然推进\n"
        "2. 避免强行升华和结尾金句：收在具体动作、事实或朴素判断上，不硬贴人生感悟\n"
        "3. 对白有潜台词：人物不把话说透，允许打断、重复、半截话和言外之意\n"
        "4. 叙事有详略：关键场景慢镜头展开，过渡一笔带过，不要每句都服务主线\n"
        "5. 人物有犹豫和矛盾：不要全程正确、因果太顺，允许误判、后知后觉和小卡顿\n"
        "6. 用具体动作和细节代替抽象情绪：写'他攥紧了拳头'而非'他内心充满愤怒'\n"
        "7. 保留留白：不解释所有动机，不替角色分析内心，让读者自己体会\n"
        "8. 句长有变化：冲突和压迫处用短句，观察和情绪收束处用稍长句"
    )


def _ending_requirements() -> str:
    """结局硬要求：无论长篇短篇，全书必须有完整结局，且结尾升维到更大参照物。

    「结尾升维」指：把故事从具体情节上升到更大的参照物——技术升到哲学、
    产品升到人性、事件升到时代——用人物视角与具体场景自然带出，给结局
    超越故事本身的余味。同时守住防同质化：允许有缺憾/留白/两难的收束，
    但必须是「收束后的结局」，禁止烂尾、禁止断在悬念上、禁止强行大团圆。
    """
    return (
        "【结局硬要求】全书必须有明确结局，禁止烂尾、禁止'未完待续'、禁止断在悬念上草草收场。\n"
        "最后一章（结局章）必须做到：\n"
        "① 收束主线冲突、关键伏笔与主要人物命运，形成完整闭环；\n"
        "② 结尾升维——把故事从具体情节上升到更大的参照物（技术→哲学、产品→人性、事件→时代），"
        "通过人物视角与具体场景、细节自然带出，让结局有超越故事本身的余味；\n"
        "③ 结局允许有缺憾、留白、两难或无力感（与防同质化一致），但必须是'收束后的结局'，"
        "而非开放式断章；严禁强行大团圆、强行金句、空洞说教式升华。"
    )


# ---------- 防同质化常驻 System 提示词（用户提供，转译为程序内规则） ----------

# 全网套路黑名单：所有阶段通用禁止
_SIM_BLACKLIST = (
    "打脸流（当众碾压/全场震惊/众人哗然）、无脑爽点（越级秒杀/凭空顿悟/突然变强/绝境爆发）、"
    "俗套桥段（拍卖会抢宝/秘境捡漏/宗门刁难/路人嘲讽/恶霸找茬）、"
    "畸形人设（无脑倒贴/痴情舔狗/恶毒女配工具人/反派降智）、"
    "机械升级（任务/打怪/刷副本/积分/系统签到）、"
    "烂俗结尾（完美结局/全员圆满/恩怨一次性清算）、"
    "强行巧合（刚好路过/刚好听见/刚好救下）"
)


def _anti_similarity_system(stage: str = "chapter") -> str:
    """防同质化常驻 System 提示词。

    基于用户提供的「纯生成防同质化终极 System 提示词」转译为程序规则。
    stage 决定注入深度：outline 侧重差异化分叉与代价；world 侧重人物内在矛盾；
    chapter 侧重只展示不叙述 + 黑名单 + 私有细节。全文常驻「核心创作准则」与「黑名单」。
    """
    core = (
        "你是高差异化原创小说生成引擎，专为全自动纯AI创作设计。\n"
        "核心铁律：永远优先选择小众、次概率、非常规剧情路径，拒绝全网高频网文套路。\n"
        "【核心创作准则】\n"
        "1. 拒绝概率最高套路：所有剧情分支优先选低出现率、反模板路径，禁止无脑选大众爽文/升级/冲突桥段\n"
        "2. 人物永远带内在矛盾：每个角色必须有执念+软肋+阴暗面+行为悖论；"
        "人物行为由性格矛盾驱动，不由剧情爽点驱动；禁止全员智商在线、全员工具人、反派纯坏、主角完美\n"
        "3. 剧情必须有代价与沉没成本：禁止无代价胜利、巧合救场、天降机缘、主角光环；"
        "所有收益必须对应明确代价（人情/身体/心理/未来隐患）\n"
        "4. 只展示、不直白叙述：禁止'他很愤怒/他很无奈/他震惊'式直白心理描写；"
        "情绪全部通过动作、微表情、生理反应、语气、细节环境侧面体现\n"
    )
    blacklist = f"【全网套路永久黑名单（严格禁止，违规即重写）】\n{_SIM_BLACKLIST}"
    if stage == "outline":
        extra = (
            "【差异化强制指令（防同质化核心）】\n"
            "每次剧情分叉主动避开Top1热门套路结局，优先选择：次优解剧情、有遗憾的剧情、两难抉择剧情、"
            "低频次叙事路径。大纲开篇禁止套路化打脸；允许平淡铺垫、留白、无收益日常；"
            "结局允许失败、遗憾、无力、不完美，拒绝流水线爽文闭环。"
            "每章 goal 要体现代价与人物抉择，而非单纯升级打脸。"
        )
    elif stage == "world":
        extra = (
            "【人物设定要求】\n"
            "每个人物（含主角）都必须有执念、软肋、阴暗面与行为悖论，禁止标签化扁平人设；"
            "人物动机由性格矛盾驱动；配角要有功能与血肉，反派要有立场与苦衷而非纯坏。"
        )
    else:  # chapter
        extra = (
            "【叙事文风强制规则】\n"
            "1. 有限第三人称，禁止上帝视角泛滥\n"
            "2. 节奏拒绝全程高能，允许平淡、留白、迂回、无收益日常铺垫\n"
            "3. 短句为主、段落轻薄，拒绝长难句堆砌\n"
            "4. 对话带潜台词、带隐瞒、带口是心非，人物不说心里话\n"
            "5. 每章植入专属私有细节（物件/习惯/民俗/隐痛/小动作），避免全网通用文字\n"
            "6. 克制：不煽情、不鸡汤、不说教、不解释世界观，让剧情自然呈现\n"
            "【全自动续写硬性规则】\n"
            "1. 续写前读取前文人设、前文伏笔、世界底层规则、禁止清单\n"
            "2. 绝不崩坏人设，不因剧情需要强行改变人物性格与行为逻辑\n"
            "3. 绝不重复桥段，自动规避已写过的冲突、反转、相遇、套路\n"
            "4. 每章新增微小伏笔，禁止单线流水账\n"
            "5. 剧情推进以人物抉择为核心，不以打怪升级为核心\n"
            "【最终输出规范】\n"
            "只输出正文；文风统一连贯；无套路、无模板、无AI痕迹、无重复剧情；逻辑自洽、伏笔闭环。"
        )
    return core + "\n\n" + blacklist + "\n\n" + extra



def expand_outline_prompt(
    cfg: NovelConfig,
    user_input: str,
) -> tuple[str, str]:
    """AI 扩写模式：把用户提供的大纲/简要内容拆解为结构化章节大纲。

    - 用户给了分章大纲：AI 整理并规范为 chapters
    - 用户只给了简要内容/梗概：AI 按目标章节数自动拆分扩写
    """
    system = (
        "你是一位擅长「扩写」的小说主编。用户会给你一段故事大纲或简要内容，"
        "你的任务是把它整理成一份可直接逐章扩写的结构化章节大纲。"
        "严格遵循用户的设定与剧情走向，不得擅自改变用户的核心创意与人物关系，"
        "只在用户内容基础上细化展开、补充必要的起承转合。"
    )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("outline")
    hard = _hard_constraints(cfg)
    soft = _soft_direction(cfg)
    user = (
        f"【目标】把下面的用户内容扩写为一部 {cfg.genre} 题材小说"
        f"（全书约 {cfg.total_words} 字，共 {cfg.num_chapters} 章，每章约 {cfg.chapter_words} 字）\n"
        f"【书名】{cfg.effective_title()}\n"
        f"【用户提供的核心创意/简介】{cfg.premise or '（未提供）'}\n\n"
        f"{hard}\n\n{soft}\n\n"
        f"【用户提供的大纲或简要内容】\n{user_input}\n\n"
        f"请输出 JSON：\n"
        f"- title：最终书名（沿用给定书名或从内容提炼）\n"
        f"- intro：作品简介（3-5 句）\n"
        f"- chapters：恰好 {cfg.num_chapters} 个章节对象的数组，每项 {{\"no\":序号,\"title\":章名,\"goal\":本章剧情目标}}\n"
        f"拆分与扩写要求：\n"
        f"1. 若用户内容已分章，则按其剧情脉络整理为连续章节，补充每章 goal；不足目标章数时按剧情自然延展补足\n"
        f"2. 若用户只给了简要内容/梗概（未分章），按起承转合自动拆分为 {cfg.num_chapters} 章，每章承接上一章\n"
        f"3. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」\n"
        f"4. 用户预设的人物必须出现且符合设定；避雷元素绝对不得出现\n"
        f"5. 章节剧情必须忠实于用户提供的内容脉络，只做细化与合理延展，不得偏离用户核心创意\n"
        f"6. 全书最后一章必须是完整结局章：收束主线、伏笔与人物命运，并以「结尾升维」（技术→哲学/产品→人性/事件→时代）收尾；"
        f"除非用户内容明确要求开放或系列化结尾，否则禁止烂尾/未完待续\n"
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
            f"- chapters：恰好 {end_no - start_no + 1} 个章节对象的数组，每项 {{\"no\":序号,\"title\":章名,\"goal\":本章剧情目标}}\n"
            f"（本次只输出本区间章节，序号必须从 {start_no} 连续编号到 {end_no}，不要输出其他章节。）\n"
            f"大纲要求：\n"
            f"1. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」\n"
            f"2. 章节内部与跨章要有起承转合：冲突、伏笔、情绪递进；每章目标尽量不重复\n"
            f"3. 必须包含用户预设的主角与人物；避雷元素绝对不得出现；剧情走向需在本区间体现\n"
            f"{('4. 本批包含全书第 ' + str(cfg.num_chapters) + ' 章（最后一章），它必须是完整的结局章：goal 写清如何收束主线、伏笔与人物命运，并以「结尾升维」（技术→哲学/产品→人性/事件→时代）收尾\n' if end_no >= cfg.num_chapters else '')}"
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
            f"请输出 JSON：\n"
            f"- title：最终书名（如可用则沿用给定书名）\n"
            f"- intro：作品简介（3-5 句，含卖点与情绪钩子）\n"
            f"- chapters：恰好 {cfg.num_chapters} 个章节对象的数组，每项 {{\"no\":序号,\"title\":章名,\"goal\":本章剧情目标}}\n"
            f"大纲要求：\n"
            f"1. 开篇 3 章内立住主角、抛出核心冲突与悬念（遵循调研的开篇策略）\n"
            f"2. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」\n"
            f"3. 整体要有起承转合：开局→升级→中段冲突与伏笔→高潮→收束；每章目标尽量不重复\n"
            f"4. 伏笔与人物关系要跨章节连贯\n"
            f"5. 必须包含用户预设的主角与人物；避雷元素绝对不得出现；剧情走向需在大纲中体现\n"
            f"6. 全书第 {cfg.num_chapters} 章（最后一章）必须是完整的结局章，goal 必须写清如何收束主线、伏笔与人物命运，并以「结尾升维」（技术→哲学/产品→人性/事件→时代）收尾\n"
            f"{'7. 防同质化：剧情分叉避开Top1热门套路，优先次优解/有遗憾/两难抉择；结局允许不完美但必须完整（禁止烂尾/未完待续）；每章目标要体现代价与人物抉择' if cfg.anti_similarity else ''}"
        )
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
        f"- characters：3-8 个人物对象数组，每项 {{\"name\":姓名,\"role\":定位(主角/主角团/反派/关键配角),\"desc\":性格与背景}}；"
        f"用户预设的主角与人物必须包含在内且描述符合设定，可补充其他角色；描述要具体，能支撑剧情行为逻辑\n"
        f"- foreshadowing：2-5 条贯穿全文的伏笔/悬念（每条一句）\n"
        f"设定要求：与大纲严格一致，人物动机清晰，伏笔在后期能回收，避雷元素不得出现。"
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
) -> tuple[str, str]:
    system = (
        "你是一位高产的网络小说签约写手，擅长按大纲与既定设定稳定产出正文。"
        "你严格遵守世界设定与人物性格，绝不推翻前文既定事实；"
        "用户的强约束（背景/主角/避雷）每章都必须遵守；"
        "每章都要有推进、有情绪起伏、结尾留钩子。"
    )
    if cfg.de_ai:
        system += (
            "你的文字要有真人作者的质感：对白有潜台词、叙事有详略、人物有犹豫，"
            "避免AI腔（连接词堆砌、强行升华、句式整齐、每句都服务主线）。"
        )
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("chapter")
    constraints = _chapter_constraints(cfg)
    de_ai_block = _de_ai_rules() if cfg.de_ai else ""
    ending_block = _ending_requirements() if is_final else ""
    user = (
        f"【本章】第{chapter_no}章《{chapter_title}》\n"
        f"【本章目标】{chapter_goal}\n"
        f"{('【全书结局章】本章是本书最后一章，必须给出完整结局。\n' + ending_block + '\n') if is_final else ''}"
        f"【单章字数·硬指标】本章正文（不含【摘要】行）必须达到 {cfg.chapter_words} 字以上。"
        f"若正文不足，必须通过增加场景细节、人物对话交锋、心理挣扎与行动过程来充实，"
        f"严禁草草收尾、严禁提前结束章节。字数低于目标视为不合格，会被退回重写。\n"
        f"{constraints}\n\n"
        f"{('【题材共性参考】\n' + research_block + '\n\n') if research_block else ''}"
        f"【前情记忆】\n{memory_context if memory_context else '（本作首章，无前情）'}\n\n"
        f"【上一章结尾（衔接用，保持文气连贯）】\n{prev_tail if prev_tail else '（本作首章）'}\n\n"
        f"写作要求：\n"
        f"1. 严格按本章目标推进剧情，不跑题、不提前揭穿后续伏笔\n"
        f"2. 人物言行符合人物卡设定；对话要有性格辨识度\n"
        f"3. 开头 1-2 句自然衔接前情；本章内部完成至少一次推进/转折；"
        f"{'本章是结局章：结尾必须收束全书主线与关键伏笔，并以「结尾升维」收尾（技术→哲学/产品→人性/事件→时代，通过人物视角与具体细节自然带出，不喊口号），不能留悬念断章' if is_final else '结尾留钩子引向下章'}\n"
        f"4. 语言符合调研建议的文风混合方向；避免重复用词与套路化表达；避雷元素绝对不得出现\n"
        f"{('5. 防同质化：本章冲突与反转不得与已写章节重复；收益必须对应代价；情绪只通过动作/细节侧面体现，禁止直白心理描写' + chr(10)) if cfg.anti_similarity else ''}"
        f"{'6' if cfg.anti_similarity else '5'}. 输出格式（必须严格遵循，不要省略）：先输出正文，正文结束后【必须】另起一行"
        f"输出【摘要】加 1-2 句本章剧情摘要（用于记忆库）；即使以收束句结尾，也必须在之后补上【摘要】行。"
    )
    return system, user
