# -*- coding: utf-8 -*-
"""数据结构定义：大纲 / 人物世界观 / 章节 / 章节摘要 / 项目状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


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
    """大纲中的单章计划。"""
    no: int
    title: str = ""
    goal: str = ""  # 本章剧情目标/一句话剧情
    timeline: str = ""  # 属于哪条时间线

    def to_dict(self) -> dict:
        return {"no": self.no, "title": self.title, "goal": self.goal, "timeline": self.timeline}

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterPlan":
        return cls(no=int(d.get("no", 0)), title=d.get("title", ""), goal=d.get("goal", ""), timeline=d.get("timeline", ""))


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
    """整本书的大纲。"""
    title: str = ""
    intro: str = ""
    chapters: list[ChapterPlan] = field(default_factory=list)
    timelines: list[Timeline] = field(default_factory=list)
    units: list[StoryUnit] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "intro": self.intro,
            "chapters": [c.to_dict() for c in self.chapters],
            "timelines": [t.to_dict() for t in self.timelines],
            "units": [u.to_dict() for u in self.units],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Outline":
        return cls(
            title=d.get("title", ""),
            intro=d.get("intro", ""),
            chapters=[ChapterPlan.from_dict(c) for c in d.get("chapters", [])],
            timelines=[Timeline.from_dict(t) for t in d.get("timelines", [])],
            units=[StoryUnit.from_dict(u) for u in d.get("units", [])],
        )


@dataclass
class Character:
    name: str = ""
    role: str = ""   # 主角/配角/反派...
    desc: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "role": self.role, "desc": self.desc}

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        return cls(name=d.get("name", ""), role=d.get("role", ""), desc=d.get("desc", ""))


@dataclass
class World:
    """世界观 + 人物卡 + 伏笔清单（记忆系统的「设定库」）。"""
    world_view: str = ""
    characters: list[Character] = field(default_factory=list)
    foreshadowing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "world_view": self.world_view,
            "characters": [c.to_dict() for c in self.characters],
            "foreshadowing": list(self.foreshadowing),
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
        return cls(
            world_view=_norm_str(d.get("world_view", "")),
            characters=[Character.from_dict(c) for c in (d.get("characters", []) or [])],
            foreshadowing=foreshadowing,
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

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "last_chapter": self.last_chapter,
            "total_chapters": self.total_chapters,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectState":
        return cls(
            phase=d.get("phase", "outline"),
            last_chapter=int(d.get("last_chapter", 0)),
            total_chapters=int(d.get("total_chapters", 0)),
        )
