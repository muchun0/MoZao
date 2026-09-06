# -*- coding: utf-8 -*-
"""
记忆系统：解决长篇小说「前后矛盾」的核心组件。

两层记忆：
1. 设定库（长期）：世界观 + 人物卡 + 伏笔清单，全书记忆，永不遗忘
2. 章节摘要（短期）：已写章节的一句话摘要，只回填最近 N 章，控制上下文成本

生成当前章时，把「设定库 + 最近 N 章摘要」拼进提示词，让模型基于既定事实续写。
"""
from __future__ import annotations

from ..store.project import ProjectStore
from ..store.schemas import World


class Memory:
    def __init__(self, store: ProjectStore, memory_window: int = 5):
        self.store = store
        self.memory_window = max(0, memory_window)

    def build_context(self, world: World | None, up_to_chapter: int) -> str:
        """为第 up_to_chapter 章构建记忆上下文（该章之前的剧情）。"""
        parts: list[str] = []

        if world is not None:
            w_lines = []
            if world.world_view:
                w_lines.append(f"世界观：{world.world_view}")
            if world.characters:
                chars = "；".join(
                    f"{c.name}（{c.role}）——{c.desc}" for c in world.characters if c.name
                )
                if chars:
                    w_lines.append(f"人物卡：{chars}")
            if world.foreshadowing:
                def _fs(x):
                    if isinstance(x, dict):
                        return " ".join(str(v) for v in x.values())
                    return str(x)
                w_lines.append("已埋伏笔：" + "；".join(_fs(x) for x in world.foreshadowing))
            if w_lines:
                parts.append("[世界设定·全书记忆]\n" + "\n".join(w_lines))

        summaries = self.store.load_summaries()
        recent = [s for s in summaries if int(s.get("no", 0)) < up_to_chapter]
        recent = recent[-self.memory_window:] if self.memory_window else recent
        if recent:
            lines = [f"第{s['no']}章：{s['summary']}" for s in recent]
            parts.append(
                f"[近期剧情·最近 {len(recent)} 章摘要]\n" + "\n".join(lines)
            )

        if not parts:
            return ""
        return "\n\n".join(parts)
