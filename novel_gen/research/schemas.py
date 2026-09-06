# -*- coding: utf-8 -*-
"""调研报告数据结构：调研阶段的产物，并作为后续大纲/人物/正文的上下文注入。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResearchReport:
    """一份「题材共性调研报告」，由调研引擎产出、持久化、注入下游。"""
    genre: str = ""
    summary: str = ""                     # 本题材共性综述
    trends: list[str] = field(default_factory=list)         # 当前流行方向
    plot_patterns: list[str] = field(default_factory=list)  # 高频情节套路
    styles: list[str] = field(default_factory=list)         # 可混合的文风元素
    pacing: str = ""                      # 节奏规律
    opening_strategy: str = ""            # 开篇策略
    avoid: list[str] = field(default_factory=list)          # 雷区/过时套路
    references: list[str] = field(default_factory=list)     # 参考作品/来源

    def to_dict(self) -> dict:
        return {
            "genre": self.genre,
            "summary": self.summary,
            "trends": list(self.trends),
            "plot_patterns": list(self.plot_patterns),
            "styles": list(self.styles),
            "pacing": self.pacing,
            "opening_strategy": self.opening_strategy,
            "avoid": list(self.avoid),
            "references": list(self.references),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResearchReport":
        def _list(v):
            return [str(x) for x in (v or []) if x]

        return cls(
            genre=str(d.get("genre", "")),
            summary=str(d.get("summary", "")),
            trends=_list(d.get("trends")),
            plot_patterns=_list(d.get("plot_patterns")),
            styles=_list(d.get("styles")),
            pacing=str(d.get("pacing", "")),
            opening_strategy=str(d.get("opening_strategy", "")),
            avoid=_list(d.get("avoid")),
            references=_list(d.get("references")),
        )

    def to_prompt_block(self) -> str:
        """转成给下游阶段的提示词注入块（大纲/人物/正文共用）。"""
        def _bullets(items: list[str], title: str) -> str:
            if not items:
                return ""
            body = "\n".join(f"- {x}" for x in items)
            return f"[{title}]\n{body}"

        parts = [
            f"【题材】{self.genre}",
            f"【共性综述】{self.summary}",
            _bullets(self.trends, "当前流行方向"),
            _bullets(self.plot_patterns, "高频情节套路"),
            _bullets(self.styles, "常见文风元素（可混合）"),
            f"【节奏规律】{self.pacing}",
            f"【开篇策略】{self.opening_strategy}",
            _bullets(self.avoid, "应避免的雷区"),
            _bullets(self.references, "参考作品/来源"),
        ]
        return "\n\n".join(p for p in parts if p)
