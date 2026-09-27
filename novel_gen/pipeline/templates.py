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

import random as _random

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
    """结局硬要求：无论长篇短篇，全书必须有完整结局。

    基于《好小说结局的写作技巧》提炼升级：结局核心本质 = 完成人物弧光 +
    收束核心冲突 + 给出情绪落点，回答开篇抛出的核心问题；不强行圆满、不无故烂尾。
    「结尾升维」指把故事从具体情节上升到更大的参照物（技术→哲学、产品→人性、
    事件→时代），用人物视角与具体场景自然带出，给结局超越故事本身的余味。
    """
    return (
        "【结局硬要求】全书最后一章必须是完整结局，禁止烂尾、禁止'未完待续'、禁止断在悬念上草草收场。\n"
        "结局核心本质：完成人物弧光、收束核心冲突、给出情绪落点——即回答开篇抛出的核心问题"
        "（主角想要什么、恐惧什么），不强行圆满、也不无故烂尾。\n\n"
        "【四类合格结局形态】（按故事自然走向选其一，必须符合对应标准）\n"
        "① 圆满结局：幸福必须带代价——主角得到想要的，同时失去一部分（如复仇成功但永远失去至亲、"
        "夺回故土却回不到过去的平静）；冲突根源真正解决而非暂时压制；人物成长看得见，和开篇形成对照\n"
        "② 悲剧结局：核心追求彻底落空、理想破灭，但人格得到完成；悲剧由人物性格与选择一步步走向必然，"
        "绝非作者强行赐死；死亡/失败要有象征意义，留给读者回味，不是单纯制造痛苦\n"
        "③ 开放式结局：主线冲突必须彻底了结，只把次要走向留给想象（如反派伏法、危机解除，"
        "主角是否与爱人走到一起、未来过什么生活不写）；严禁拿'开放式'当写不完、圆不上漏洞的借口\n"
        "④ 现实向苦涩结局：愿望实现一部分、一部分永远遗憾，有得有失（如挣脱了苦难但过往创伤无法抹平、"
        "赢了博弈却和重要的人走散），尊重现实逻辑\n\n"
        "【写作要点】\n"
        "1. 锚定开篇提问：结局必须回应开篇主角最想要什么、最恐惧什么——参照【前情记忆】中开篇章节的主角状态做对照；"
        "开篇写'复仇'，结局就要回答复仇之后他变成了什么样、复仇有没有救赎他；开篇写'寻找自我'，结局就不要只落在打败敌人\n"
        "2. 人物弧光闭环：主角相比开篇必须有明确变化（信念是否改变、学会或失去了什么），"
        "哪怕外部世界变化不大，人的内在要有变化；没有人物变化的结局，故事就是白写\n"
        "3. 冲突分层收束：主线坑（核心反派、核心矛盾、主角最大执念）必须全部闭环；"
        "次要支线与路人小伏笔可选择放弃，不贪心硬填无关小坑\n"
        "4. 控制结局节奏：真相与关键信息尽量在最后的高潮阶段释放，尾声减少新信息输入，"
        "用来处理情绪与人物归宿而非疯狂补设定；结构参考：高潮决战（解决冲突）→ 尾声1尘埃落定"
        "（人物当下处境）→ 尾声2回望（情绪余味）\n"
        "5. 配角归宿：重要配角要有符合其性格逻辑的对应结局，禁止为了主角结局强行牺牲或洗白配角\n"
        "6. 情绪落点优先：不借角色嘴巴讲大道理、不大段输出作者价值观，把道理藏进人物选择与场景细节，"
        "用画面、行为代替旁白总结\n"
        "7. 结尾升维：从具体情节上升到更大的参照物（技术→哲学、产品→人性、事件→时代），"
        "通过人物视角与具体场景、细节自然带出，让结局有超越故事本身的余味；"
        "结局允许有缺憾、留白、两难或无力感（与防同质化一致），但必须是'收束后的结局'\n\n"
        "【烂尾黑名单（严格禁止）】\n"
        "- 机械降神：前文完全没有铺垫的力量或人物突然出现解决危机\n"
        "- 反转成瘾：结局反复反转、为意外而意外，违背人物动机\n"
        "- 进度崩坏：几十万字铺垫却在两三章内草草收束，或结局拖沓几十章迟迟不落地\n"
        "- 善恶简单报应：好人全部善终、坏人全部凄惨，忽略人性复杂\n"
        "- 强行大团圆、强行金句、空洞说教式升华\n\n"
        "【尾声收尾参考】（最后几百字二选一）\n"
        "· 回望式：回到故事开篇的经典意象或场景（旧城楼、旧物），多年后主角再立于此处，以景物烘托心境\n"
        "· 向前看式：不写遥远未来，只写当下一瞬间（如晚风袭来，他握紧手中的东西，迈步走向前路），留下余韵；"
        "不要写'他们从此幸福过完一生'式的空泛交代\n\n"
        "【结局自检】（成文前逐条核对）开篇核心诉求已回答？人物相比开篇有明确变化？"
        "主线冲突真正解决而非暂时搁置？所有关键转折前文有铺垫、无凭空设定？"
        "代价与收获匹配、没有白白得来的幸福？没有大段说教、观点靠情节展现？"
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

def _skill_append(cfg: NovelConfig) -> str:
    """用户可控内容拼入 System Prompt 前的组装（模块3：经注入过滤兜底）。

    custom_prompt / skill_prompt_append 均为用户可写内容，若含注入话术
    （忽略以上指令/修改系统设定等）可篡改系统约束，拼入前统一清洗。
    """
    from ..safety import sanitize_system_text
    parts = []
    txt = (getattr(cfg, "skill_prompt_append", "") or "").strip()
    if txt:
        clean = sanitize_system_text(txt)
        if clean:
            parts.append("\n\n【写作风格 Skill（必须严格遵循）】\n" + clean)
    cp = (getattr(cfg, "custom_prompt", "") or "").strip()
    if cp:
        clean_cp = sanitize_system_text(cp)
        if clean_cp:
            parts.append("\n\n【用户自定义提示词】\n" + clean_cp)
    return "".join(parts)

# ---------- 人物立体化（基于番茄课程《告别千人一面：带你写出有血有肉的人物》提炼） ----------
def _character_depth_rules(stage: str = "chapter") -> str:
    """人物立体化写作约束：解决人物扁平、情绪干瘪、千人一面。

    四个技法（对应课程四方法）：
    ① 角色卡一致性：行为必须能从经历/性格/欲望/处境解释得通，禁止人设漂移；
    ② 选择加成本：重要胜利必须付真实代价（牺牲/风险/两难取舍），禁止白捡的赢；
    ③ 生活体感：用具体身体反应与日常细节承载情绪，禁止直白情绪宣告；
    ④ 出场标签 + 对话辨识度：新角色登场带「他是谁/跟谁有关/为何此刻重要」，
       不同身份性格的角色说话方式必须不同。
    stage=world 侧重角色卡要素；stage=chapter 侧重正文技法的落地。
    """
    if stage == "world":
        return (
            "【人物立体化·角色卡要求（人设一致性）】\n"
            "每个重要人物（含主角、反派、关键配角）的角色卡必须写清以下要素，缺一不可：\n"
            "1. 身份标签：他是谁、在故事里扮演什么位置（如：外门刺头/执法堂长老/贴身丫鬟）\n"
            "2. 经历背景：过去发生了什么塑造了他（一句即可，能解释他的行为逻辑）\n"
            "3. 性格核心：1-2 个鲜明特质，拒绝脸谱化（禁止'善良/邪恶'这种空词，要写成行为模式）\n"
            "4. 欲望与软肋：他最想要什么、最怕失去什么（驱动剧情抉择）\n"
            "5. 行为范式：专属小动作/口头禅/习惯（如：紧张时摩挲刀柄、说话爱带'老子'）\n"
            "6. 压力反应：长期压力下的身体细节（胃疼/失眠/走神/眼睛发红）\n"
            "所有人物必须各不相同——说话方式、行为习惯、压力反应都要有辨识度，读者不看名字也能分辨是谁。"
        )
    return (
        "【人物立体化写作约束（让角色有血有肉、情绪落到读者心里）】\n"
        "1. 人设一致性：本章人物的每个行为与选择，都必须能从他的经历、性格、欲望和当下处境中解释得通；"
        "禁止为推进剧情强行改变性格；若必须改变，要先给足铺垫与理由\n"
        "2. 选择加成本：主角的每一次重要胜利都要付出真实代价——牺牲某样东西、承担风险、或两难取舍"
        "（救A就救不了B、选这条路就得罪某人）；禁止轻飘飘的白捡胜利，让读者觉得他在'拼命'而非'走流程'\n"
        "3. 生活体感：用具体身体反应和日常细节承载情绪，禁止'他悲痛欲绝/他很委屈/他震惊'式直白情绪宣告。"
        "例：罚跪后起身膝盖一麻、扶着门框才站稳；姜汤喝太急被呛得偏头咳了一声、眼圈泛红；"
        "受伤后爬楼梯喘气、连续熬夜后眼睛发红——用一两个具体反应让读者自己感受到情绪\n"
        "4. 出场标签：本章若有新角色登场，必须同时带出三要素——他是谁、跟谁有关系、此刻为什么重要；"
        "避免一次性抛出过多新名字（每章新角色不超过 2-3 个）\n"
        "5. 对话辨识度：不同性格、身份、背景的人说话方式必须不同；"
        "刺头说话带混不吝的冲劲、上位者说话有威压、谨小慎微的人说话爱留余地——"
        "读者不看名字也能分辨是谁在开口，禁止所有角色一个腔调"
    )



# ---------- 番茄平台写作纪律（基于《番茄小说平台专属写作技巧指南》提炼） ----------

def _fanqie_rules(stage: str = "chapter") -> str:
    """番茄平台写作纪律：黄金三章开篇 / 防崩文大纲 / 轻量世界观力量体系 / 节奏爽点断章 / 移动端排版。

    基于番茄官方作家课堂与头部作者经验提炼，stage 决定注入深度：
    outline 侧重黄金三章设计 + 三幕式/卷纲 + 节奏规划；world 侧重人物三层逻辑 + 轻量世界观力量体系；
    chapter 侧重开篇纪律 + 爽点闭环 + 章末断章 + 语言排版。
    """
    if stage == "outline":
        return (
            "【番茄平台·开篇与结构纪律】\n"
            "1. 黄金三章设计（前三章是留存筛选器，零铺垫、快切入、强钩子）：\n"
            "   - 第1章：300字内爆发核心冲突并引出主角与金手指/核心卖点；开篇第一句直接进入动作或对抗场景，"
            "禁止环境铺垫/背景介绍/世界观说明/回忆杀；场景聚焦单一现场，出场人物不超过2个\n"
            "   - 第2章：700字内通过主角应对冲突的行为/选择/对话立住人设，禁止直白旁白介绍性格；推进小半步剧情，给出第一个小型反馈验证能力\n"
            "   - 第3章：放大矛盾强度，制造新阻碍迫使主角做出不可逆选择；结尾留「小反转+大疑问」双重钩子；明确短期行动目标\n"
            "2. 防崩文结构：全书主线必须能用一句话概括；三幕式（开端抛矛盾立目标→发展层层受阻起伏→结局合理收束）；"
            "长书按卷规划：每卷一个核心冲突+一个阶段性大高潮，卷末完成一次身份/实力跃迁，提前规划关键节点防中期散架\n"
            "3. 节奏规划：快2慢1交替（2章冲突/打斗/反转配1章日常/情感/设定），战斗章不连续超2章，日常章不连续超3章；"
            "压抑期不超过3章，压抑后必须给出情绪释放；爽点按四环节规划（压抑→铺垫→爆发→余韵），每章 goal 尽量落到某个情绪环节"
        )
    if stage == "world":
        return (
            "【番茄平台·人设与世界观纪律】\n"
            "1. 人物三层逻辑：表层（外在形象/习惯动作/口头禅，建立第一印象）+ 中层（身世背景/成长经历，解释性格成因）"
            "+ 深层（核心价值观/最深的渴望，驱动行为决策），三层都要落到人物卡\n"
            "2. 塑造四技巧：给人物填过往（言行有逻辑支撑，不无缘无故善恶）；明确目标与动机（主角贯穿始终的核心目标，"
            "反派配角也有自己的动机，不为推动剧情强行行动）；加反差特质（核心标签配一个反差属性，如杀伐果断的魔尊怕老婆）；"
            "拒绝工具人配角（配角有独立人设与利益诉求）\n"
            "3. 世界观轻量化：禁止大段堆砌设定，遵循「写到哪铺到哪」，设定随剧情自然抛出；新增设定先问能否制造冲突/推动情节，"
            "没用的直接砍掉；先搭核心框架，历史地理人文细节等需要时再补，避免「上课感」\n"
            "4. 力量体系标准：等级层级清晰可量化（晋级标准明确）；战力差距具象化（用参照物：一个武师能打五十个士兵）；"
            "提前定死力量天花板，防后期战力崩坏"
        )
    return (
        "【番茄平台·正文写作纪律】\n"
        "1. 开篇纪律：每章第一句尽量直接进入动作或对抗（尤其全书首章），禁止环境铺垫/世界观说明/回忆杀开场；"
        "场景聚焦，单场戏出场人物不宜过多\n"
        "2. 节奏：快2慢1交替——冲突/打斗/反转连续不超过2章，日常/情感/设定连续不超过3章；拒绝注水，每段都推进情绪或剧情\n"
        "3. 爽点闭环：压抑（制造憋屈感，不超3章）→铺垫（暗中准备积蓄）→爆发（高光集中释放，桥段避开全网套路黑名单）"
        "→余韵（围观者反应/态度转变放大爽感）；禁止只有爆发没有铺垫的空中楼阁\n"
        "4. 章末断章：每章结尾留明确悬念，让读者带着焦虑感合上书；有效类型：冲突一触即发/真相即将揭晓/危险悄然降临/反转前夕；"
        "禁止流水账结尾（主角吃饭睡觉就断章）\n"
        "5. 语言与排版（移动端阅读标准）：每段控制在3-5行，多用短句少用长难句；对话单独成段，清晰区分说话人；"
        "精准描写——所有描写必须服务剧情或人物，删除无关环境描写/冗余对话/无效修饰；拒绝过度华丽文笔拖慢节奏；"
        "少用生僻字与专业术语\n"
        "6. 雷区红线：禁止节奏注水（强行水字数）、人设崩塌（行为违背前期设定）、强行降智（为剧情让角色智商掉线）、"
        "巧合过多（凡事靠巧合推动）、反派全程送经验\n"
        "【番茄签约硬指标（直接影响能否签约，必须逐条满足）】\n"
        "A. 对话占比：正文对话占比不低于 60%，描写性段落（环境/外貌/心理）每段不超过 3 行；对话单独成段，一句对话一段\n"
        "B. 章内小钩子：每 300 字至少一个小钩子（悬念/反转/情绪爆点/新信息），让读者不停滑手机；"
        "钩子类型轮换：悬念式（不可解释的现象）/反差式（身份与行为落差）/冲突式（直接高强度冲突）/对话式（爆炸性对话）\n"
        "C. 主角主动决策：每 800 字至少 2 次主角主动决策或行动，禁止主角全程被动反应/被人推着走；"
        "主角要做选择、要反抗、要争取，而不是只会'他皱了皱眉''他心中一动'\n"
        "D. 开篇第一句（仅第1章）：第一句必须有冲击力——直接进动作/对抗/冲突/悬念，"
        "禁止'天气很好''夕阳西下''十年前'式开场；参考范式：'刀尖抵住她喉结时，系统弹窗亮了''离婚协议签完第三分钟，前夫助理打来电话'\n"
        "E. 前3章小高潮：第3章结尾前必须出现第一个小高潮（第一次爽点释放/第一次反转/第一次翻盘），"
        "不能前3章还在铺垫世界观\n"
        "F. 删除抽象情绪词：禁止'他很愤怒/他很震惊/他悲痛欲绝/他无奈'这类直接宣告，"
        "全部用动作+微表情+生理反应替代（攥拳/后退/声音发紧/指甲掐进掌心）\n"
        "G. 段落节奏：每段 1-3 句话，不超过 5 行；长句拆短；逗号多的句子砍成短句"
    )


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
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("outline")
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
        f"- core_conflict：全书核心冲突（一句话）\n"
        f"- main_line：全书主线脉络（3-5 句）\n"
        f"- volumes：分卷数组，按用户大纲中的「第X卷」或剧情阶段归纳为3-5卷；若用户大纲未分卷则按起承转合划分。每卷字段：no, name(卷名), intro(本卷核心主题), main_conflict(本卷主要矛盾), character_growth(本卷人物成长), climax(本卷高潮), start_ch(起始章号), end_ch(结束章号)\n"
        f"- chapters：恰好 {cfg.num_chapters} 个章节对象的数组，每项包含字段：no(序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(埋设/回收的伏笔), emotion(情绪价值), transition(转场提示), tag(节点标签)，章节标题必须互不相同、全书唯一，严禁重名章\n"
        f"拆分与扩写要求：\n"
        f"1. 若用户内容已分章，则按其剧情脉络整理为连续章节，补充每章 goal；不足目标章数时按剧情自然延展补足\n"
        f"2. 若用户只给了简要内容/梗概（未分章），按起承转合自动拆分为 {cfg.num_chapters} 章，每章承接上一章\n"
        f"3. 每章 goal 一句话说清「本章发生什么、达成什么情绪/爽点、留下什么钩子」；core_event/conflict/character_moves 必须写具体剧情细节，禁止留空或套话\n"
        f"4. 用户预设的人物必须出现且符合设定；避雷元素绝对不得出现\n"
        f"5. 章节剧情必须忠实于用户提供的内容脉络，只做细化与合理延展，不得偏离用户核心创意\n"
        f"6. 全书最后一章必须是完整结局章：收束主线、伏笔与人物命运，并以「结尾升维」（技术→哲学/产品→人性/事件→时代）收尾；"
        f"除非用户内容明确要求开放或系列化结尾，否则禁止烂尾/未完待续\n"
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
            f"- chapters：恰好 {end_no - start_no + 1} 个章节对象的数组，每项包含字段：no(序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(埋设/回收的伏笔), emotion(情绪价值), transition(转场提示), tag(节点标签)，章节标题必须互不相同、全书唯一，严禁重名章\n"
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
            f"- chapters：恰好 {cfg.num_chapters} 个章节对象的数组（L3章节小纲层），每项包含字段：no(序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(埋设/回收的伏笔), emotion(情绪价值), transition(转场提示), tag(节点标签)，章节标题必须互不相同、全书唯一，严禁重名章\n"
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
        f"- characters：3-8 个人物对象数组，每项含字段：name(姓名), role(定位主角/主角团/反派/关键配角), desc(一句话概括), appearance(外貌特征), personality(性格核心), desire(核心欲望), wound(软肋/创伤), arc(人物弧光), speech_style(说话方式/口头禅), relations(与其他角色关系)；"
        f"用户预设的主角与人物必须包含在内且描述符合设定，可补充其他角色；描述要具体，能支撑剧情行为逻辑\n"
        f"- foreshadowing：2-5 条贯穿全文的伏笔/悬念（每条一句）\n"
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


def split_scenes_prompt(cfg, chapter_no, chapter_title, chapter_goal):
    system = (
        "你是一位小说结构编辑。用户会给你一章的梗概，"
        "你的任务是把它拆成 3-5 个连续场景，每个场景 1000-1500 字。"
        "严格按 JSON 格式输出，不要额外解释。"
    )
    user = (
        f"【本章】第{chapter_no}章《{chapter_title}》\n"
        f"【本章目标】{chapter_goal}\n"
        f"【本章目标总字数】{cfg.chapter_words} 字\n\n"
        "请拆成 3-5 个场景，每个场景包含：\n"
        "- location: 地点\n"
        "- event: 发生什么\n"
        "- emotion: 这个场景的情绪目标\n"
        "- anchors: 必须保留的细节\n"
        f"- target_words: 该场景目标字数（总和应接近 {cfg.chapter_words}）\n\n"
        '输出 JSON：{"scenes": [...]}'
    )
    return system, user


def scene_prompt(cfg, chapter_no, chapter_title, scene_idx, scene, memory_context, prev_tail, dynamic_module):
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
    system += _skill_append(cfg)
    anchors = scene.get("anchors", [])
    anchors_str = "；".join(anchors) if anchors else "（无特殊锚点）"
    user = (
        f"【第{chapter_no}章《{chapter_title}》· 场景 {scene_idx}】\n"
        f"【地点】{scene.get('location', '')}\n"
        f"【事件】{scene.get('event', '')}\n"
        f"【情绪目标】{scene.get('emotion', '')}\n"
        f"【必须保留的细节】{anchors_str}\n"
        f"【本场景目标字数】{scene.get('target_words', 1200)} 字\n\n"
        f"【前情记忆】\n{memory_context if memory_context else '（首章）'}\n\n"
        f"【上一章节结尾（衔接用）】\n{prev_tail if prev_tail else '（无）'}\n\n"
        "写作要求：\n"
        "1. 只写这个场景的内容，不要写场景之间的过渡总结\n"
        "2. 对话占比≥60%，描写段≤3行\n"
        "3. 对话单独成段，一句对话一段\n"
        "4. 禁止'他很愤怒/他很震惊'式抽象情绪词，用动作+微表情替代\n"
        "5. 字数达到目标即可，不要拖长\n"
        "6. 不要输出【摘要】，不要输出场景编号，直接输出正文"
    )
    return system, user


def polish_chapter_prompt(cfg, chapter_no, chapter_title, chapter_text):
    system = (
        "你是一位小说润色编辑。用户给你一章正文，"
        "你的任务是通读后微调文风：统一语感、修正重复句式、让段落衔接更自然。"
        "绝对不要改变情节、不要增删剧情、不要改人物对话含义。"
        "只输出润色后的完整正文。"
    )
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


def reader_review_prompt(cfg, chapter_no, chapter_title, chapter_goal, content):
    """读者视角审校：从追更体验角度找问题，只定位不改写。低温 0.3。"""
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
        '输出 JSON：{"issues": [{"pos": "段X", "type": "类型", "detail": "读者感受", "level": "小改/中改/大改"}], '
        '"追更欲": 1-5, "一句话感受": "..."}\n'
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
        f"4.5 签约硬指标：对话占比≥60%（描写段≤3行）；每300字一个小钩子；每800字主角≥2次主动决策；"
        f"第1章第一句直接进冲突（仅第1章适用）；前3章内必须出第一个小高潮；"
        f"禁止'他很愤怒/他很震惊'式抽象情绪词，全部用动作+微表情替代\n"
        f"{('5. 防同质化：本章冲突与反转不得与已写章节重复；收益必须对应代价；情绪只通过动作/细节侧面体现，禁止直白心理描写' + chr(10)) if cfg.anti_similarity else ''}"
        f"{'6' if cfg.anti_similarity else '5'}. 输出格式（必须严格遵循，不要省略）：先输出正文，正文结束后【必须】另起一行"
        f"输出【摘要】加 1-2 句本章剧情摘要（用于记忆库）；即使以收束句结尾，也必须在之后补上【摘要】行。"
    )
    ref = _style_reference(cfg)
    if ref:
        system += "\n\n" + ref
    system += _skill_append(cfg)
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
        f"- characters：3-8 个人物对象数组，每项 {{\"name\":姓名,\"role\":定位,\"desc\":性格与背景}}；"
        f"主角与已出场人物必须包含，描述要与正文言行一致\n"
        f"- foreshadowing：2-5 条从正文中存在的伏笔/悬念（每条一句；正文没有悬念时提炼潜在冲突）\n"
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
        f"- chapters：恰好 {remain_chapters} 个章节对象的数组，每项包含字段：no(相对序号), title(章名), goal(一句话剧情), core_event(核心事件), conflict(冲突设计), character_moves(人物动线), foreshadowing(伏笔), emotion(情绪价值), transition(转场提示), tag(标签)，章节标题必须互不相同、与已有章节也不得重名\n"
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
) -> tuple[str, str]:
    """扩写已有章节：保留原有剧情/对话/人设，扩充细节到目标字数。"""
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
    if cfg.anti_similarity:
        system += "\n\n" + _anti_similarity_system("chapter")
    if cfg.fanqie_style:
        system += "\n\n" + _fanqie_rules("chapter")
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
