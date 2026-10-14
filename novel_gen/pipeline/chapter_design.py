# -*- coding: utf-8 -*-
"""章级设计卡（REQ-chapter-design 一期）。

从 ChapterPlan（章级 conflict/emotion/goal/core_event/character_moves/cliffhanger）
+ World.conflict_matrix 确定性派生逐章「正向创作设计卡」，写章前幂等生成并注入写手 prompt。

设计原则（依据 2026-10-14 能力评估）：
- 已在 memory.build_context 注入的字段（theme_proposition / protagonist_arc /
  character_motives / goldfinger_rules / setting_taboos）不再重复进设计卡，避免浪费 token。
- 真·未接线的高价值主源是章级 conflict / emotion（每条 60-150 字优质文本），
  以及 World.conflict_matrix（三层冲突标尺）。
- 纯 Python 前置确定性派生（零 LLM）；任何异常返回空 dict / 空串（调用方降级，行为与改造前一致）。
- 关系模板是「开放指引」，不是硬性白名单枚举——绝不据此误杀模板外的人物关系。

用法：build_chapter_designs(store, world, outline) -> dict（空 dict=调用方跳过落盘）
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# 主角—群像关系类型词典（20 种，开放模板/指引，非硬性白名单枚举）。
# 仅用于提示写手"本章这段关系可以怎么写"，措辞上必须声明"开放参考、不限于此"。
RELATION_TEMPLATES: list[str] = [
    "并肩作战：共同面对外部威胁，在协作中建立或加深信任",
    "对立试探：立场/利益相左，互相试探底线与真实意图",
    "师徒/传承：一方引领、点拨另一方成长，信息与能力单向流动",
    "保护与被保护：强势一方主动遮掩风险，弱势一方后知后觉",
    "误会隔阂：因信息不对称产生误解，对话中暗藏未说破的提防",
    "利益交换：各取所需的合作，表面客气、各自盘算代价",
    "压迫与反抗：一方居高临下施压，另一方隐忍寻找突破口",
    "旧怨重逢：带着过往恩怨重新照面，一言一行都牵动旧账",
    "惺惺相惜：不打不相识，交锋后生出对手间的认可",
    "依附与利用：弱势一方借势而上，强势一方把人当棋子",
    "牺牲托孤：一方为另一方承担代价，把希望/秘密托付出去",
    "立场摇摆：在两方之间犹疑，本章做出或暴露真实倾向",
    "照镜子/对照：两个处境相似的角色互为镜像，反衬各自选择",
    "隐藏身份：一方对另一方有所隐瞒，对话需拿捏分寸",
    "情感觉醒：长期关系本章发生质变（信任→动摇 / 疏远→靠近）",
    "上下级博弈：命令与执行的张力，表面服从下有各自算盘",
    "外部合力：为对付共同敌人临时结盟，内部仍有保留",
    "救赎/被救赎：一方的行为成为另一方转变的契机",
    "价值观碰撞：两人对同一件事看法根本对立，对话即观念交锋",
    "告别/离场：关系本章走到阶段终点，以一次对话收束",
]

# 本章对白可承担的功能（开放清单，提示用，不做枚举校验）。
DIALOGUE_FUNCTIONS: list[str] = [
    "推进剧情（对话直接引发下一步行动）",
    "塑造人物（口吻、性格、人物关系外化）",
    "制造冲突（言语交锋/误会/对峙，张力升级）",
    "揭示信息（交代背景/伏笔/人物动机）",
    "传递主题（借对话点出本章主题落点）",
    "调节节奏（张弛之间的喘息、幽默或留白）",
]

# 冲突矩阵层级 → 冲突阶梯档（低/中/顶）。个人层最贴近主角日常=低起，世界层=顶。
_LAYER_TIER = {
    "个人": "低（个人层：主角自身的欲求/缺陷/选择）",
    "人际": "中（人际层：主角与他人/群体的对抗）",
    "世界": "顶（世界层：体制/环境/命运的结构性压力）",
}


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def build_chapter_designs(store, world, outline) -> dict:
    """确定性构建全书逐章设计卡。任何异常返回空 dict（调用方跳过落盘，行为零退化）。"""
    try:
        if world is None or outline is None or not getattr(outline, "chapters", None):
            return {}
        chapters = []
        for plan in outline.chapters:
            no = int(getattr(plan, "no", 0) or 0)
            if no <= 0:
                continue
            chapters.append({"no": no, "design": derive_card(plan, world, outline)})
        return {"version": 1, "chapters": chapters}
    except Exception:
        logger.exception("章级设计卡派生失败（降级为空，不注入）")
        return {}


def derive_card(plan, world, outline=None) -> dict:
    """派生单章设计卡 A1–A7。每字段独立回退，空字段不进 dict。"""
    d: dict = {}
    goal = _s(getattr(plan, "goal", ""))
    if goal:
        d["goal"] = goal
    conflict = _derive_conflict(world, plan)
    if conflict:
        d["conflict"] = conflict
    tp = _derive_turning_point(plan)
    if tp:
        d["turning_point"] = tp
    emo = _derive_emotion(plan, outline)
    if emo:
        d["emotion"] = emo
    cliff = _s(getattr(plan, "cliffhanger", ""))
    if cliff:
        d["cliffhanger"] = cliff
    chars = _derive_appearing_chars(world, plan)
    if chars:
        d["characters"] = chars
    dlg = _derive_dialogue_functions(plan)
    if dlg:
        d["dialogue_functions"] = dlg
    return d


def _derive_conflict(world, plan) -> dict:
    """A2：本章冲突核心（主源 plan.conflict）+ conflict_matrix 三层下钻作升级标尺。"""
    core = _s(getattr(plan, "conflict", ""))
    ladder: list[dict] = []
    this_layer = ""
    matrix = list(getattr(world, "conflict_matrix", None) or []) if world else []
    blob = f"{core} {_s(getattr(plan, 'core_event', ''))}"
    for layer in matrix:
        level = _s(getattr(layer, "level", ""))
        desc = _s(getattr(layer, "description", ""))
        if not desc:
            continue
        tier = _LAYER_TIER.get(level, f"（{level or '未分层'}）")
        ladder.append({"tier": tier, "desc": desc})
        # 粗匹配本章冲突落在哪一层：该层描述关键词命中本章文本
        if not this_layer and desc and any(k and k in blob for k in desc[:20]):
            this_layer = tier
    out: dict = {}
    if core:
        out["core"] = core
    if ladder:
        out["ladder"] = ladder
        out["this_layer"] = this_layer or "（按本章 conflict 定位，未命中具体层）"
    return out


def _derive_turning_point(plan) -> dict:
    """A3：本章关键转折（core_event + tag）。"""
    core = _s(getattr(plan, "core_event", ""))
    tag = _s(getattr(plan, "tag", ""))
    if not core and not tag:
        return {}
    return {"tag": tag, "event": core[:160]}


def _derive_emotion(plan, outline) -> dict:
    """A4：本章情绪落点（主源 plan.emotion；emotion_curve 命中区间作跨章配比参照）。"""
    note = _s(getattr(plan, "emotion", ""))
    no = int(getattr(plan, "no", 0) or 0)
    curve: dict | None = None
    beats = list(getattr(outline, "emotion_curve", None) or []) if outline else []
    for b in beats:
        try:
            if int(getattr(b, "ch_from", 0) or 0) <= no <= int(getattr(b, "ch_to", 0) or 0):
                curve = {
                    "type": _s(getattr(b, "emotion_type", "")),
                    "intensity": int(getattr(b, "intensity", 3) or 3),
                    "note": _s(getattr(b, "note", "")),
                }
                break
        except Exception:
            continue
    out: dict = {}
    if note:
        out["note"] = note
    if curve:
        out["curve"] = curve
    return out


def _derive_appearing_chars(world, plan) -> list[dict]:
    """A6：本章出场角色清单 + 戏份级别。主角必在；配角按文本命中。"""
    text = " ".join([
        _s(getattr(plan, "goal", "")),
        _s(getattr(plan, "core_event", "")),
        _s(getattr(plan, "character_moves", "")),
    ])
    out: list[dict] = []
    chars = list(getattr(world, "characters", None) or []) if world else []
    for c in chars:
        name = _s(getattr(c, "name", ""))
        if not name:
            continue
        if name in text:
            role = _s(getattr(c, "role", ""))
            weight = "main" if ("主角" in role or "男主" in role or "女主" in role) else "supporting"
            out.append({"name": name, "role": role, "weight": weight})
    # 文本里出现、但不在 world.characters 里的人名（过场/临时角色）不强行识别为对象，
    # 避免误判；关系模板另附开放指引（见 render_block）。
    return out


def _derive_dialogue_functions(plan) -> list[str]:
    """A7：本章对白应承担的功能（启发式，开放清单，不枚举校验）。"""
    blob = " ".join([
        _s(getattr(plan, "conflict", "")),
        _s(getattr(plan, "core_event", "")),
        _s(getattr(plan, "emotion", "")),
    ])
    funcs = ["推进剧情（对话直接引发下一步行动）", "塑造人物（口吻、性格、人物关系外化）"]
    if any(k in blob for k in ("冲突", "对峙", "交锋", "误会", "争吵", "谈判", "质问")):
        funcs.append("制造冲突（言语交锋/误会/对峙，张力升级）")
    if any(k in blob for k in ("揭示", "揭秘", "真相", "伏笔", "交代", "说明", "坦白")):
        funcs.append("揭示信息（交代背景/伏笔/人物动机）")
    return funcs


def render_block(design: dict) -> str:
    """把单章 design dict 渲染成写手 prompt 注入块（软约束指引，空字段不渲染）。"""
    if not design:
        return ""
    lines: list[str] = ["【本章设计卡（正向创作指引·软约束：按此设计写本章，不偏离戏剧核心）】"]
    goal = _s(design.get("goal"))
    if goal:
        lines.append(f"- 本章目标：{goal}")
    conf = design.get("conflict") or {}
    if conf.get("core"):
        lines.append(f"- 本章冲突核心（本章对抗的张力点，必须贯穿全章）：{conf['core']}")
    if conf.get("this_layer") and conf.get("ladder"):
        lines.append(f"- 冲突升级标尺（本章冲突落在此层，较日常应升级）：{conf['this_layer']}")
    tp = design.get("turning_point") or {}
    if tp.get("event"):
        tag = f"〔{tp['tag']}〕" if tp.get("tag") else ""
        lines.append(f"- 本章关键转折{tag}：{tp['event']}")
    emo = design.get("emotion") or {}
    if emo.get("note"):
        lines.append(f"- 本章情绪落点（该压则压、该放则放）：{emo['note']}")
    if emo.get("curve"):
        c = emo["curve"]
        lines.append(f"  · 全本情绪配比参照：{c.get('type','')}（强度 {c.get('intensity',3)}/5）"
                     f"{('：' + c['note']) if c.get('note') else ''}")
    chars = design.get("characters") or []
    if chars:
        weight_cn = {"main": "主角", "supporting": "戏份角色", "extra": "过场"}
        desc = "；".join(f"{c['name']}（{weight_cn.get(c.get('weight',''),'戏份角色')}）" for c in chars)
        lines.append(f"- 本章出场角色：{desc}")
        lines.append("  · 人物关系为开放参考（不限于此）：可从以下关系类型中择贴合者展开——"
                     + "、".join(RELATION_TEMPLATES[:3]))
    dlg = design.get("dialogue_functions") or []
    if dlg:
        lines.append("- 本章对白应承担的功能：" + "；".join(dlg))
    cliff = _s(design.get("cliffhanger"))
    if cliff:
        lines.append(f"- 章末钩子（结尾须兑现）：{cliff}")
    return "\n".join(lines) if len(lines) > 1 else ""


def reconcile_event_ledger(planned_ids: list, consumed_ids: list) -> list[str]:
    """后校验弱覆盖：规划事件 vs 正文已消费事件对账。返回 warning 文案列表（只留痕，不阻断）。"""
    warnings: list[str] = []
    try:
        planned = {str(x) for x in (planned_ids or []) if str(x)}
        consumed = {str(x) for x in (consumed_ids or []) if str(x)}
        missing = sorted(planned - consumed)
        extra = sorted(consumed - planned)
        if missing:
            warnings.append(f"规划事件本章未被正文消费：{missing}")
        if extra:
            warnings.append(f"正文消费了规划外事件：{extra}")
    except Exception:
        logger.exception("事件账本对账失败（跳过）")
    return warnings
