# -*- coding: utf-8 -*-
"""数据结构定义：大纲 / 人物世界观 / 章节 / 章节摘要 / 项目状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Volume:
    """分卷大纲（二级大纲）。"""
    no: int = 0
    name: str = ""
    intro: str = ""          # 本卷核心主题/主线进展
    main_conflict: str = ""  # 本卷主要矛盾
    character_growth: str = "" # 本卷人物成长弧光
    climax: str = ""          # 本卷高潮节点
    start_ch: int = 0         # 起始章
    end_ch: int = 0           # 结束章

    def to_dict(self) -> dict:
        return {
            "no": self.no, "name": self.name, "intro": self.intro,
            "main_conflict": self.main_conflict, "character_growth": self.character_growth,
            "climax": self.climax, "start_ch": self.start_ch, "end_ch": self.end_ch,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Volume":
        return cls(
            no=int(d.get("no", 0)), name=d.get("name", ""), intro=d.get("intro", ""),
            main_conflict=d.get("main_conflict", ""), character_growth=d.get("character_growth", ""),
            climax=d.get("climax", ""), start_ch=int(d.get("start_ch", 0)), end_ch=int(d.get("end_ch", 0)),
        )


@dataclass
class Timeline:
    """时间线结构。"""
    name: str = ""
    type: str = "顺叙"
    time_flow: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "time_flow": self.time_flow, "description": self.description}

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        return cls(
            name=d.get("name", ""),
            type=d.get("type", "顺叙"),
            time_flow=d.get("time_flow", ""),
            description=d.get("description", ""),
        )


@dataclass
class ChapterPlan:
    """大纲中的单章计划（剧情节点）。"""
    no: int
    title: str = ""
    goal: str = ""  # 本章剧情目标/一句话剧情
    timeline: str = ""  # 属于哪条时间线
    # 结构化剧情节点字段（P0新增）
    core_event: str = ""       # 核心事件：本章主要发生的事
    conflict: str = ""         # 冲突设计：主要矛盾与冲突升级点
    character_moves: str = ""  # 人物动线：出场人物、人物行为与成长
    foreshadowing: str = ""    # 伏笔与回收：埋设的伏笔/回收前文伏笔
    emotion: str = ""          # 情绪价值：本节点带给读者的情绪（爽点/虐点/笑点/悬念）
    transition: str = ""        # 转场提示：与前后章节的衔接方式
    tag: str = ""              # 节点标签：高潮/过渡/伏笔/爽点/反转/虐点等
    volume_no: int = 0        # 所属分卷编号，0=未分配
    cliffhanger: str = ""      # 章末钩子（P2）：本章章末钩子的事前设计（冲突一触即发/真相将揭/危险降临），
                               # writer 写章末须兑现；reader 校验钩子是否有真正勾人效果

    def to_dict(self) -> dict:
        return {
            "no": self.no, "title": self.title, "goal": self.goal, "timeline": self.timeline,
            "core_event": self.core_event, "conflict": self.conflict,
            "character_moves": self.character_moves, "foreshadowing": self.foreshadowing,
            "emotion": self.emotion, "transition": self.transition, "tag": self.tag,
            "volume_no": self.volume_no, "cliffhanger": self.cliffhanger,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterPlan":
        return cls(
            no=int(d.get("no", 0)),
            title=d.get("title", ""),
            goal=d.get("goal", ""),
            timeline=d.get("timeline", ""),
            core_event=d.get("core_event", ""),
            conflict=d.get("conflict", ""),
            character_moves=d.get("character_moves", ""),
            foreshadowing=d.get("foreshadowing", ""),
            emotion=d.get("emotion", ""),
            transition=d.get("transition", ""),
            tag=d.get("tag", ""),
            volume_no=int(d.get("volume_no", 0)),
            cliffhanger=d.get("cliffhanger", ""),
        )


@dataclass
class StoryUnit:
    """副本单元（3章一个完整事件）。"""
    no: int = 0
    name: str = ""
    type: str = ""  # 梦境副本/现实副本/暗线副本
    chapters: list[int] = field(default_factory=list)
    core_payoff: str = ""
    key_nodes: list[str] = field(default_factory=list)
    harvest: str = ""
    next_hook: str = ""

    def to_dict(self) -> dict:
        return {
            "no": self.no,
            "name": self.name,
            "type": self.type,
            "chapters": self.chapters,
            "core_payoff": self.core_payoff,
            "key_nodes": self.key_nodes,
            "harvest": self.harvest,
            "next_hook": self.next_hook,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StoryUnit":
        return cls(
            no=int(d.get("no", 0)),
            name=d.get("name", ""),
            type=d.get("type", ""),
            chapters=[int(x) for x in d.get("chapters", [])],
            core_payoff=d.get("core_payoff", ""),
            key_nodes=list(d.get("key_nodes", [])),
            harvest=d.get("harvest", ""),
            next_hook=d.get("next_hook", ""),
        )


@dataclass
class EmotionBeat:
    """情绪曲线卡节点（P2）：全本爽点/虐点/悬念分布（跨章区间的情绪节奏设计）。
    单章 emotion 是字段，这里把全本的情绪类型/强度/间隔结构化，pacing 据此防情绪失衡。"""
    ch_from: int = 0
    ch_to: int = 0
    emotion_type: str = ""   # 爽点/虐点/悬念/笑点/平淡
    intensity: int = 3       # 强度 1-5
    note: str = ""           # 说明/触发点

    def to_dict(self) -> dict:
        return {"ch_from": self.ch_from, "ch_to": self.ch_to,
                "emotion_type": self.emotion_type, "intensity": self.intensity,
                "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "EmotionBeat":
        if not isinstance(d, dict):
            return cls()
        return cls(
            ch_from=int(d.get("ch_from", 0)), ch_to=int(d.get("ch_to", 0)),
            emotion_type=d.get("emotion_type", ""), intensity=int(d.get("intensity", 3)),
            note=d.get("note", ""),
        )


@dataclass
class Outline:
    """整本书的大纲（三级：总纲intro → 分卷volumes → 章节chapters）。"""
    title: str = ""
    intro: str = ""
    core_conflict: str = ""     # 总纲：核心冲突（L1总纲层）
    main_line: str = ""         # 总纲：主线脉络
    volumes: list[Volume] = field(default_factory=list)
    chapters: list[ChapterPlan] = field(default_factory=list)
    timelines: list[Timeline] = field(default_factory=list)
    units: list[StoryUnit] = field(default_factory=list)
    key_nodes: list[KeyNode] = field(default_factory=list)        # 全本关键节点时间线（REQ 骨架层）
    volume_pacing: list[VolumePacing] = field(default_factory=list)  # 分卷节奏梯次（REQ 节奏层）
    emotion_curve: list[EmotionBeat] = field(default_factory=list)   # 全本情绪曲线卡（P2 情绪层）

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "intro": self.intro,
            "core_conflict": self.core_conflict,
            "main_line": self.main_line,
            "volumes": [v.to_dict() for v in self.volumes],
            "chapters": [c.to_dict() for c in self.chapters],
            "timelines": [t.to_dict() for t in self.timelines],
            "units": [u.to_dict() for u in self.units],
            "key_nodes": [k.to_dict() for k in self.key_nodes],
            "volume_pacing": [v.to_dict() for v in self.volume_pacing],
            "emotion_curve": [e.to_dict() for e in self.emotion_curve],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Outline":
        return cls(
            title=d.get("title", ""),
            intro=d.get("intro", ""),
            core_conflict=d.get("core_conflict", ""),
            main_line=d.get("main_line", ""),
            volumes=[Volume.from_dict(v) for v in d.get("volumes", [])],
            chapters=[ChapterPlan.from_dict(c) for c in d.get("chapters", [])],
            timelines=[Timeline.from_dict(t) for t in d.get("timelines", [])],
            units=[StoryUnit.from_dict(u) for u in d.get("units", [])],
            key_nodes=[KeyNode.from_dict(k) for k in d.get("key_nodes", [])],
            volume_pacing=[VolumePacing.from_dict(v) for v in d.get("volume_pacing", [])],
            emotion_curve=[EmotionBeat.from_dict(e) for e in d.get("emotion_curve", [])],
        )


@dataclass
class Character:
    """结构化人物档案。"""
    name: str = ""
    role: str = ""           # 主角/配角/反派...
    desc: str = ""
    appearance: str = ""     # 外貌
    personality: str = ""    # 性格特质
    desire: str = ""         # 核心欲望/目标
    wound: str = ""          # 心理创伤/软肋
    arc: str = ""            # 人物弧光（开篇→结尾的变化）
    speech_style: str = ""   # 说话方式/口头禅
    relations: str = ""      # 人物关系（与其他角色）

    def to_dict(self) -> dict:
        return {
            "name": self.name, "role": self.role, "desc": self.desc,
            "appearance": self.appearance, "personality": self.personality,
            "desire": self.desire, "wound": self.wound, "arc": self.arc,
            "speech_style": self.speech_style, "relations": self.relations,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        def _s(v):
            if isinstance(v, dict):
                return " ".join(f"{k}:{_s(val)}" for k, val in v.items())
            if isinstance(v, list):
                return "；".join(_s(x) for x in v)
            return str(v) if v is not None else ""
        return cls(
            name=_s(d.get("name", "")), role=_s(d.get("role", "")), desc=_s(d.get("desc", "")),
            appearance=_s(d.get("appearance", "")), personality=_s(d.get("personality", "")),
            desire=_s(d.get("desire", "")), wound=_s(d.get("wound", "")), arc=_s(d.get("arc", "")),
            speech_style=_s(d.get("speech_style", "")), relations=_s(d.get("relations", "")),
        )


@dataclass
class WorldReveal:
    """世界观阶段揭示点：主角从某一章起才知道某段世界观（跟随主角视角逐步展开）。

    三段式揭示（铺垫 → 揭示 → 展开），避免"到点凭空解锁"：
    - foreshadow_ch: 铺垫最早章（从此章起正文可埋线索/暗示；< from_ch 才有铺垫期，
      等于 from_ch 表示无铺垫期直接揭示）
    - hint: 铺垫要点（揭示前正文应出现的异常/线索/疑惑，主角与读者可感知的"不对劲"，
      但不得说出真相；空则无铺垫）
    - from_ch: 主角正式得知真相的起始章
    - content: 真相（主角明确获知的世界观片段，主角视角、具体）
    - keywords: 真相独有锚定词（1-5 个专有名词/关键短语，正文出现即视为已揭示；
      供揭示执行跟踪确定性检测；不得与 hint 用词重叠，防止铺垫期暗示被误判为剧透；
      空=旧数据，跳过检测）
    """
    from_ch: int = 1
    content: str = ""
    foreshadow_ch: int | None = None  # None=无铺垫期（兼容旧数据），直接揭示
    hint: str = ""
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "from_ch": self.from_ch,
            "content": self.content,
            "foreshadow_ch": self.foreshadow_ch,
            "hint": self.hint,
            "keywords": list(self.keywords or []),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorldReveal":
        from_ch = int(d.get("from_ch", 1) or 1)
        foreshadow_ch = d.get("foreshadow_ch")
        if foreshadow_ch is None:
            foreshadow_ch = None
        else:
            foreshadow_ch = int(foreshadow_ch or from_ch)
        return cls(
            from_ch=from_ch,
            content=_norm_str(d.get("content", "")),
            foreshadow_ch=foreshadow_ch,
            hint=_norm_str(d.get("hint", "")),
            keywords=[str(k).strip() for k in (d.get("keywords") or []) if str(k).strip()],
        )


# ============================================================
# 多Agent协作分层 · 底层锚点 / 节奏伏笔 结构化数据
# （REQ-多Agent协作分层优化）全部 from_dict 兼容旧数据（缺字段回退默认），
# 保证旧项目断点续跑不崩。
# ============================================================


@dataclass
class ForeshadowItem:
    """单条伏笔（结构化，替代旧 string 列表）。

    - buried_ch: 埋点章号
    - reveal_ch: 回收章号；None=只埋不收（LogicValidator 判为致命）
    - level: 三级（small 章内 / medium 卷内 / large 全篇）
    - from_dict 兼容旧字符串/旧 dict（name=原文，level=medium，reveal_ch=None）
    """
    id: str = ""
    name: str = ""
    buried_ch: int = 0
    reveal_ch: int | None = None
    level: str = "medium"   # small / medium / large
    content: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "buried_ch": self.buried_ch, "reveal_ch": self.reveal_ch,
            "level": self.level, "content": self.content,
        }

    @classmethod
    def from_dict(cls, d) -> "ForeshadowItem":
        if isinstance(d, str):
            return cls(name=d, content=d)
        if isinstance(d, dict):
            return cls(
                id=_norm_str(d.get("id", "")),
                name=_norm_str(d.get("name", "")),
                buried_ch=int(d.get("buried_ch", 0) or 0),
                reveal_ch=(int(d["reveal_ch"]) if d.get("reveal_ch") is not None else None),
                level=_norm_str(d.get("level", "medium")) or "medium",
                content=_norm_str(d.get("content", "")),
            )
        return cls()

    def __str__(self) -> str:
        return self.name or self.content or ""


@dataclass
class ConflictLayer:
    """三层冲突矩阵的一层（个人 personal / 人际 interpersonal / 世界 world）。"""
    level: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "description": self.description}

    @classmethod
    def from_dict(cls, d) -> "ConflictLayer":
        if isinstance(d, str):
            return cls(description=_norm_str(d))
        if isinstance(d, dict):
            return cls(level=_norm_str(d.get("level", "")), description=_norm_str(d.get("description", "")))
        return cls()


@dataclass
class ArcCard:
    """主角完整人物弧光卡：开篇信念 → 核心缺陷 → 关键转变节点 → 最终认知。"""
    belief: str = ""                 # 开篇信念
    flaw: str = ""                   # 核心缺陷
    change_nodes: list[str] = field(default_factory=list)  # 关键转变节点
    final_insight: str = ""          # 最终认知

    def to_dict(self) -> dict:
        return {
            "belief": self.belief, "flaw": self.flaw,
            "change_nodes": list(self.change_nodes), "final_insight": self.final_insight,
        }

    @classmethod
    def from_dict(cls, d) -> "ArcCard":
        if not isinstance(d, dict):
            return cls()
        return cls(
            belief=_norm_str(d.get("belief", "")),
            flaw=_norm_str(d.get("flaw", "")),
            change_nodes=[_norm_str(x) for x in (d.get("change_nodes") or [])],
            final_insight=_norm_str(d.get("final_insight", "")),
        )


@dataclass
class CharacterMotive:
    """配角动机卡：核心目标、行为逻辑、与主角的核心关联。"""
    name: str = ""
    goal: str = ""
    behavior_logic: str = ""
    link_to_protagonist: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "goal": self.goal,
            "behavior_logic": self.behavior_logic, "link_to_protagonist": self.link_to_protagonist,
        }

    @classmethod
    def from_dict(cls, d) -> "CharacterMotive":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")),
                goal=_norm_str(d.get("goal", "")),
                behavior_logic=_norm_str(d.get("behavior_logic", "")),
                link_to_protagonist=_norm_str(d.get("link_to_protagonist", "")),
            )
        return cls()


@dataclass
class GoldfingerRules:
    """金手指规则说明书 · 强制五要素（缺任一字段 → StageGate 阶段一打回）。

    trigger      触发条件
    upper_limit  能力上限
    lower_limit  能力下限
    cost         使用代价
    growth_path  成长路径
    """
    trigger: str = ""
    upper_limit: str = ""
    lower_limit: str = ""
    cost: str = ""
    growth_path: str = ""

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger, "upper_limit": self.upper_limit,
            "lower_limit": self.lower_limit, "cost": self.cost, "growth_path": self.growth_path,
        }

    @classmethod
    def from_dict(cls, d) -> "GoldfingerRules":
        if not isinstance(d, dict):
            return cls()
        return cls(
            trigger=_norm_str(d.get("trigger", "")),
            upper_limit=_norm_str(d.get("upper_limit", "")),
            lower_limit=_norm_str(d.get("lower_limit", "")),
            cost=_norm_str(d.get("cost", "")),
            growth_path=_norm_str(d.get("growth_path", "")),
        )

    def is_complete(self) -> bool:
        """五要素是否齐全（阶段准出门禁用）。"""
        return all([self.trigger, self.upper_limit, self.lower_limit, self.cost, self.growth_path])


@dataclass
class KeyNode:
    """全本关键节点时间线（骨架层）：激励事件 / 中点 / 低谷 / 终极高潮等。"""
    kind: str = ""
    chapter_no: int = 0
    description: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "chapter_no": self.chapter_no, "description": self.description}

    @classmethod
    def from_dict(cls, d) -> "KeyNode":
        if isinstance(d, dict):
            return cls(
                kind=_norm_str(d.get("kind", "")),
                chapter_no=int(d.get("chapter_no", 0) or 0),
                description=_norm_str(d.get("description", "")),
            )
        return cls()


@dataclass
class VolumePacing:
    """分卷节奏梯次（节奏层）：爽点密度 / 高潮章 / 过渡区间。"""
    volume_no: int = 0
    payoff_density: str = ""   # low / medium / high
    climax_ch: int = 0         # 本卷高潮章
    transition_range: str = ""  # 过渡章节区间描述

    def to_dict(self) -> dict:
        return {
            "volume_no": self.volume_no, "payoff_density": self.payoff_density,
            "climax_ch": self.climax_ch, "transition_range": self.transition_range,
        }

    @classmethod
    def from_dict(cls, d) -> "VolumePacing":
        if isinstance(d, dict):
            return cls(
                volume_no=int(d.get("volume_no", 0) or 0),
                payoff_density=_norm_str(d.get("payoff_density", "medium")) or "medium",
                climax_ch=int(d.get("climax_ch", 0) or 0),
                transition_range=_norm_str(d.get("transition_range", "")),
            )
        return cls()


def _norm_str(v):
    """把 LLM 可能返回的 dict/list 等非标量规范化为字符串。"""
    if isinstance(v, dict):
        return " ".join(f"{k}:{_norm_str(val)}" for k, val in v.items())
    if isinstance(v, list):
        return "；".join(_norm_str(x) for x in v)
    return str(v) if v is not None else ""


def _list_to_dicts(items: list, cls):
    """列表元素兼容 str / dict / dataclass 对象 → dict（支持直接构造的旧数据）。"""
    out = []
    for x in items:
        if isinstance(x, cls):
            out.append(x.to_dict())
        elif isinstance(x, (str, dict)):
            out.append(cls.from_dict(x).to_dict())
    return out


@dataclass
class EnemyCard:
    """生物图鉴：前期实际出场的怪物/敌对生物一张卡（事前设计，防止写手自编形态）。"""
    name: str = ""
    form: str = ""          # 外形解剖：有无固定形态、肢体/器官/核心位置
    senses: str = ""        # 感知方式（触须振动/视觉/热感；决定能不能写"视线盲区"）
    behavior: str = ""      # 行为模式（巡逻编队/伏击/食腐/对死人反应）
    kill: str = ""          # 击杀条件/弱点
    tier: str = ""          # 层级（浅层常见/精英/首领）

    def to_dict(self) -> dict:
        return {"name": self.name, "form": self.form, "senses": self.senses,
                "behavior": self.behavior, "kill": self.kill, "tier": self.tier}

    @classmethod
    def from_dict(cls, d) -> "EnemyCard":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")), form=_norm_str(d.get("form", "")),
                senses=_norm_str(d.get("senses", "")), behavior=_norm_str(d.get("behavior", "")),
                kill=_norm_str(d.get("kill", "")), tier=_norm_str(d.get("tier", "")),
            )
        return cls()


@dataclass
class LocationCard:
    """地点卡：前期实际出场的场景一张卡（地形/光线/连通，防地下暮色、死路涌深处）。"""
    name: str = ""
    terrain: str = ""       # 地形结构
    light: str = ""         # 光线规则（地下无光/天光在何处/光源是什么）
    sound_smell: str = ""   # 声音气味
    exits: str = ""         # 连通与出口（哪里是死路/通哪）
    danger: str = ""        # 危险来源与层级

    def to_dict(self) -> dict:
        return {"name": self.name, "terrain": self.terrain, "light": self.light,
                "sound_smell": self.sound_smell, "exits": self.exits, "danger": self.danger}

    @classmethod
    def from_dict(cls, d) -> "LocationCard":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")), terrain=_norm_str(d.get("terrain", "")),
                light=_norm_str(d.get("light", "")), sound_smell=_norm_str(d.get("sound_smell", "")),
                exits=_norm_str(d.get("exits", "")), danger=_norm_str(d.get("danger", "")),
            )
        return cls()


@dataclass
class MechanismBaseline:
    """机制初始值：把机制卡里的数字落成结构化"当前值"，写手不得自行发明。"""
    scar_start: str = ""        # 旧痕起点
    scar_per_death: str = ""    # 每死一次延伸量
    scar_current: str = ""      # 旧痕当前累计（长度/次数）
    scar_end: str = ""          # 终局位置（蔓延至何处=蚀化）
    panel_format: str = ""     # 面板唯一写法（禁用其他格式）
    reset_rule: str = ""       # 每日重置规则

    def to_dict(self) -> dict:
        return {"scar_start": self.scar_start, "scar_per_death": self.scar_per_death,
                "scar_current": self.scar_current, "scar_end": self.scar_end,
                "panel_format": self.panel_format, "reset_rule": self.reset_rule}

    @classmethod
    def from_dict(cls, d) -> "MechanismBaseline":
        if not isinstance(d, dict):
            return cls()
        return cls(
            scar_start=_norm_str(d.get("scar_start", "")),
            scar_per_death=_norm_str(d.get("scar_per_death", "")),
            scar_current=_norm_str(d.get("scar_current", "")),
            scar_end=_norm_str(d.get("scar_end", "")),
            panel_format=_norm_str(d.get("panel_format", "")),
            reset_rule=_norm_str(d.get("reset_rule", "")),
        )


@dataclass
class ClockCard:
    """时钟规则：日周期/光线规则/地点间路程（静态部分；当前时间锚点在章级账本）。"""
    day_cycle: str = ""     # 日周期（何时刷新/晨昏/夜色）
    travel: str = ""        # 地点间路程耗时
    light_rule: str = ""    # 哪里有光哪里没光（防地下暮色）

    def to_dict(self) -> dict:
        return {"day_cycle": self.day_cycle, "travel": self.travel, "light_rule": self.light_rule}

    @classmethod
    def from_dict(cls, d) -> "ClockCard":
        if not isinstance(d, dict):
            return cls()
        return cls(
            day_cycle=_norm_str(d.get("day_cycle", "")),
            travel=_norm_str(d.get("travel", "")),
            light_rule=_norm_str(d.get("light_rule", "")),
        )


@dataclass
class FactionCard:
    """势力卡：前期出场的组织一张卡（目标/成员/与主角关系）。"""
    name: str = ""
    goal: str = ""
    members: str = ""
    relation_to_mc: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "goal": self.goal,
                "members": self.members, "relation_to_mc": self.relation_to_mc}

    @classmethod
    def from_dict(cls, d) -> "FactionCard":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")), goal=_norm_str(d.get("goal", "")),
                members=_norm_str(d.get("members", "")),
                relation_to_mc=_norm_str(d.get("relation_to_mc", "")),
            )
        return cls()


@dataclass
class TerminologyItem:
    """术语统一卡（P2）：专有名词（译名/招式名/地名/称呼）的标准写法与禁用别名。
    style_taboos 是"禁用词"，术语卡是"统一写法"——同一专有名词全文只能一种写法。"""
    name: str = ""                       # 标准写法（正文统一用此）
    aliases: list[str] = field(default_factory=list)  # 别名/译名（禁止其他写法）
    definition: str = ""                 # 定义/说明

    def to_dict(self) -> dict:
        return {"name": self.name, "aliases": list(self.aliases),
                "definition": self.definition}

    @classmethod
    def from_dict(cls, d) -> "TerminologyItem":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")),
                aliases=[_norm_str(x) for x in (d.get("aliases") or [])],
                definition=_norm_str(d.get("definition", "")),
            )
        return cls()


@dataclass
class ItemCard:
    """物品/道具卡：重要道具的静态定义（来源/代价/限制/作用），防写成万能工具。
    只定义"静态属性"；物品的动态归属流水在 chapter_state 账本承接。"""
    name: str = ""
    origin: str = ""          # 来源/来历
    cost: str = ""            # 使用代价（每次使用的代价/消耗）
    restriction: str = ""     # 限制/触发条件（不能无限制使用，明确边界）
    effect: str = ""          # 作用/能力（明确边界，禁模糊万能表述）

    def to_dict(self) -> dict:
        return {"name": self.name, "origin": self.origin, "cost": self.cost,
                "restriction": self.restriction, "effect": self.effect}

    @classmethod
    def from_dict(cls, d) -> "ItemCard":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")), origin=_norm_str(d.get("origin", "")),
                cost=_norm_str(d.get("cost", "")), restriction=_norm_str(d.get("restriction", "")),
                effect=_norm_str(d.get("effect", "")),
            )
        return cls()


@dataclass
class PowerLevel:
    """单个境界/等级阶梯。"""
    name: str = ""            # 境界名（如 炼气/筑基/金丹）
    range: str = ""           # 数值区间或阶段描述（如"1-9 重"）
    requirement: str = ""     # 突破到本境界的条件
    capability: str = ""      # 本境界能力/战力上限（写死，防崩坏）

    def to_dict(self) -> dict:
        return {"name": self.name, "range": self.range,
                "requirement": self.requirement, "capability": self.capability}

    @classmethod
    def from_dict(cls, d) -> "PowerLevel":
        if isinstance(d, str):
            return cls(name=_norm_str(d))
        if isinstance(d, dict):
            return cls(
                name=_norm_str(d.get("name", "")), range=_norm_str(d.get("range", "")),
                requirement=_norm_str(d.get("requirement", "")),
                capability=_norm_str(d.get("capability", "")),
            )
        return cls()


@dataclass
class PowerLevelSystem:
    """等级/境界体系卡：境界阶梯 + 数值区间 + 主角当前境界，防长篇后期战力崩坏。
    仅升级流题材产出；非升级文保持 None。"""
    levels: list[PowerLevel] = field(default_factory=list)  # 境界阶梯（由低到高）
    protagonist_level: str = ""   # 主角当前境界
    progression_rule: str = ""    # 突破规则（如何升级/是否跳跃/瓶颈限制）

    def to_dict(self) -> dict:
        return {"levels": [l.to_dict() for l in self.levels],
                "protagonist_level": self.protagonist_level,
                "progression_rule": self.progression_rule}

    @classmethod
    def from_dict(cls, d) -> "PowerLevelSystem":
        if not isinstance(d, dict):
            return cls()
        return cls(
            levels=[PowerLevel.from_dict(x) for x in (d.get("levels") or [])
                    if isinstance(x, (dict, str))],
            protagonist_level=_norm_str(d.get("protagonist_level", "")),
            progression_rule=_norm_str(d.get("progression_rule", "")),
        )


@dataclass
class EndingCard:
    """结局卡：终局状态/主题落点/主角最终选择/主要人物归宿。
    全书末尾几章写手的终局锚点，防烂尾与自然收束失控。"""
    end_state: str = ""            # 终局世界状态（故事结束时世界处于什么状态）
    theme_landing: str = ""        # 主题落点（结局如何回归核心主题命题）
    protagonist_choice: str = ""   # 主角最终选择（终局的关键抉择）
    character_fates: list[str] = field(default_factory=list)  # 主要人物归宿（每人一句）
    tone: str = ""                 # 结局基调（悲/喜/开放式/遗憾）

    def to_dict(self) -> dict:
        return {"end_state": self.end_state, "theme_landing": self.theme_landing,
                "protagonist_choice": self.protagonist_choice,
                "character_fates": list(self.character_fates), "tone": self.tone}

    @classmethod
    def from_dict(cls, d) -> "EndingCard":
        if not isinstance(d, dict):
            return cls()
        return cls(
            end_state=_norm_str(d.get("end_state", "")),
            theme_landing=_norm_str(d.get("theme_landing", "")),
            protagonist_choice=_norm_str(d.get("protagonist_choice", "")),
            character_fates=[_norm_str(x) for x in (d.get("character_fates") or [])],
            tone=_norm_str(d.get("tone", "")),
        )


@dataclass
class World:
    """世界观 + 人物卡 + 伏笔清单 + 锁定设定（记忆系统的「设定库」）。

    多Agent协作分层新增底层锚点字段（theme_proposition / conflict_matrix /
    protagonist_arc / character_motives / goldfinger_rules / setting_taboos），
    foreshadowing 升级为结构化 ForeshadowItem；全部 from_dict 兼容旧数据。
    事前设计卡（REQ-事前设定卡）：enemies/locations/mechanism_baseline/clock/
    factions/style_taboos——写每章前注入，从源头防设定矛盾。
    """
    world_view: str = ""
    characters: list[Character] = field(default_factory=list)
    foreshadowing: list[ForeshadowItem] = field(default_factory=list)
    locked_settings: list[str] = field(default_factory=list)  # 核心设定锁定，AI不得违背
    mechanism_rules: str = ""  # 金手指机制卡：机制语义/数字结算/代价递增/终局，AI 不得违背且必须自洽
    world_reveals: list[WorldReveal] = field(default_factory=list)  # 世界观逐步揭示计划（跟随主角视角）
    # ---- 底层锚点（REQ 多Agent协作分层优化）----
    theme_proposition: str = ""                 # 核心主题命题（一句话内核）
    conflict_matrix: list[ConflictLayer] = field(default_factory=list)  # 三层冲突矩阵
    protagonist_arc: ArcCard | None = None      # 主角完整弧光卡
    character_motives: list[CharacterMotive] = field(default_factory=list)  # 配角动机卡
    goldfinger_rules: GoldfingerRules | None = None  # 金手指规则说明书（五要素）
    setting_taboos: list[str] = field(default_factory=list)  # 设定禁忌清单
    # ---- 事前设计卡（REQ-事前设定卡）----
    enemies: list[EnemyCard] = field(default_factory=list)        # 生物图鉴
    locations: list[LocationCard] = field(default_factory=list)  # 地点卡
    mechanism_baseline: MechanismBaseline | None = None           # 机制初始值（数字标准答案）
    clock: ClockCard | None = None                                # 时钟规则（静态）
    factions: list[FactionCard] = field(default_factory=list)    # 势力卡
    style_taboos: list[str] = field(default_factory=list)         # 语言风格禁忌（时代/术语禁用词）
    # ---- P1-3 物品卡 + 等级体系卡 ----
    items: list[ItemCard] = field(default_factory=list)           # 物品/道具卡（防万能工具）
    power_system: PowerLevelSystem | None = None                  # 等级/境界体系卡（防战力崩坏）
    # ---- P1-2 结局卡 ----
    ending_card: EndingCard | None = None                         # 结局卡（终局锚点，防烂尾）
    # ---- P2 术语统一卡 ----
    terminology: list[TerminologyItem] = field(default_factory=list)  # 专有名词统一表（防译名/招式名/地名多写法）
    # ---- P2 对白 Agent ----
    dialogue_guideline: str = ""   # 对话风格指南（对白 Agent 聚合人物口吻+节奏规则，注入正文生成）

    def to_dict(self) -> dict:
        return {
            "world_view": self.world_view,
            "characters": [c.to_dict() for c in self.characters],
            "foreshadowing": _list_to_dicts(self.foreshadowing, ForeshadowItem),
            "locked_settings": list(self.locked_settings),
            "mechanism_rules": self.mechanism_rules,
            "world_reveals": [r.to_dict() for r in self.world_reveals],
            "theme_proposition": self.theme_proposition,
            "conflict_matrix": _list_to_dicts(self.conflict_matrix, ConflictLayer),
            "protagonist_arc": self.protagonist_arc.to_dict() if self.protagonist_arc else None,
            "character_motives": _list_to_dicts(self.character_motives, CharacterMotive),
            "goldfinger_rules": self.goldfinger_rules.to_dict() if self.goldfinger_rules else None,
            "setting_taboos": list(self.setting_taboos),
            "enemies": [e.to_dict() for e in self.enemies],
            "locations": [l.to_dict() for l in self.locations],
            "mechanism_baseline": self.mechanism_baseline.to_dict() if self.mechanism_baseline else None,
            "clock": self.clock.to_dict() if self.clock else None,
            "factions": [f.to_dict() for f in self.factions],
            "style_taboos": list(self.style_taboos),
            "items": [i.to_dict() for i in self.items],
            "power_system": self.power_system.to_dict() if self.power_system else None,
            "ending_card": self.ending_card.to_dict() if self.ending_card else None,
            "terminology": [t.to_dict() for t in self.terminology],
            "dialogue_guideline": self.dialogue_guideline,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "World":
        raw_foreshadow = d.get("foreshadowing", []) or []
        foreshadowing = [ForeshadowItem.from_dict(x) for x in raw_foreshadow]
        locked_raw = d.get("locked_settings", []) or []
        locked = [_norm_str(x) for x in locked_raw]
        reveals_raw = d.get("world_reveals", []) or []
        reveals = [WorldReveal.from_dict(r) for r in reveals_raw if isinstance(r, dict)]
        pa = d.get("protagonist_arc")
        gf = d.get("goldfinger_rules")
        return cls(
            world_view=_norm_str(d.get("world_view", "")),
            characters=[Character.from_dict(c) for c in (d.get("characters", []) or [])],
            foreshadowing=foreshadowing,
            locked_settings=locked,
            mechanism_rules=_norm_str(d.get("mechanism_rules", "")),
            world_reveals=reveals,
            theme_proposition=_norm_str(d.get("theme_proposition", "")),
            conflict_matrix=[ConflictLayer.from_dict(c) for c in (d.get("conflict_matrix", []) or [])],
            protagonist_arc=ArcCard.from_dict(pa) if isinstance(pa, dict) else None,
            character_motives=[CharacterMotive.from_dict(m) for m in (d.get("character_motives", []) or [])],
            goldfinger_rules=GoldfingerRules.from_dict(gf) if isinstance(gf, dict) else None,
            setting_taboos=[_norm_str(x) for x in (d.get("setting_taboos", []) or [])],
            enemies=[EnemyCard.from_dict(e) for e in (d.get("enemies") or []) if isinstance(e, (dict, str))],
            locations=[LocationCard.from_dict(l) for l in (d.get("locations") or []) if isinstance(l, (dict, str))],
            mechanism_baseline=MechanismBaseline.from_dict(mb) if isinstance(mb := d.get("mechanism_baseline"), dict) else None,
            clock=ClockCard.from_dict(ck) if isinstance(ck := d.get("clock"), dict) else None,
            factions=[FactionCard.from_dict(f) for f in (d.get("factions") or []) if isinstance(f, (dict, str))],
            style_taboos=[_norm_str(x) for x in (d.get("style_taboos", []) or [])],
            items=[ItemCard.from_dict(i) for i in (d.get("items") or []) if isinstance(i, (dict, str))],
            power_system=PowerLevelSystem.from_dict(ps) if isinstance(ps := d.get("power_system"), dict) else None,
            ending_card=EndingCard.from_dict(ec) if isinstance(ec := d.get("ending_card"), dict) else None,
            terminology=[TerminologyItem.from_dict(t) for t in (d.get("terminology") or [])
                         if isinstance(t, (dict, str))],
            dialogue_guideline=d.get("dialogue_guideline", "") or "",
        )


@dataclass
class Chapter:
    """已生成的单章。"""
    no: int
    title: str = ""
    content: str = ""
    summary: str = ""  # 本章摘要，用于后续章节的记忆回填
    goal: str = ""     # 生成时的大纲目标快照（用于大纲变更检测；旧数据为空不误报）
    source: str = "ai" # 内容来源：ai=平台生成；user=用户手写（AI 流程不得覆盖，除非显式确认）

    def to_dict(self) -> dict:
        return {"no": self.no, "title": self.title, "content": self.content,
                "summary": self.summary, "goal": self.goal, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        return cls(
            no=int(d.get("no", 0)),
            title=d.get("title", ""),
            content=d.get("content", ""),
            summary=d.get("summary", ""),
            goal=d.get("goal", ""),
            source=d.get("source", "ai"),
        )


@dataclass
class ProjectState:
    """任务状态机：记录当前推进到哪个阶段，用于断点续传。"""
    phase: str = "outline"   # outline -> world -> writing -> done
    last_chapter: int = 0    # 已写到的章节号（writing 阶段用）
    total_chapters: int = 0
    archived: bool = False   # 用户归档标记
    error: str = ""          # 最近一次生成失败的错误信息（持久化，供前端红色提示）

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "last_chapter": self.last_chapter,
            "total_chapters": self.total_chapters,
            "archived": self.archived,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectState":
        return cls(
            phase=d.get("phase", "outline"),
            last_chapter=int(d.get("last_chapter", 0)),
            total_chapters=int(d.get("total_chapters", 0)),
            archived=bool(d.get("archived", False)),
            error=str(d.get("error", "")),
        )
