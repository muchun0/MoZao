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

    def to_dict(self) -> dict:
        return {
            "no": self.no, "title": self.title, "goal": self.goal, "timeline": self.timeline,
            "core_event": self.core_event, "conflict": self.conflict,
            "character_moves": self.character_moves, "foreshadowing": self.foreshadowing,
            "emotion": self.emotion, "transition": self.transition, "tag": self.tag,
            "volume_no": self.volume_no,
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
class World:
    """世界观 + 人物卡 + 伏笔清单 + 锁定设定（记忆系统的「设定库」）。"""
    world_view: str = ""
    characters: list[Character] = field(default_factory=list)
    foreshadowing: list[str] = field(default_factory=list)
    locked_settings: list[str] = field(default_factory=list)  # 核心设定锁定，AI不得违背

    def to_dict(self) -> dict:
        return {
            "world_view": self.world_view,
            "characters": [c.to_dict() for c in self.characters],
            "foreshadowing": list(self.foreshadowing),
            "locked_settings": list(self.locked_settings),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "World":
        def _norm_str(v):
            """把 LLM 可能返回的 dict/list 等非标量规范化为字符串。"""
            if isinstance(v, dict):
                return " ".join(f"{k}:{_norm_str(val)}" for k, val in v.items())
            if isinstance(v, list):
                return "；".join(_norm_str(x) for x in v)
            return str(v) if v is not None else ""

        raw_foreshadow = d.get("foreshadowing", []) or []
        foreshadowing = [_norm_str(x) for x in raw_foreshadow]
        locked_raw = d.get("locked_settings", []) or []
        locked = [_norm_str(x) for x in locked_raw]
        return cls(
            world_view=_norm_str(d.get("world_view", "")),
            characters=[Character.from_dict(c) for c in (d.get("characters", []) or [])],
            foreshadowing=foreshadowing,
            locked_settings=locked,
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
