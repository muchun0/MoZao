# -*- coding: utf-8 -*-
"""
项目存储层：把一次「写书任务」落盘为一个目录，实现断点续传与成果沉淀。

目录结构：
    projects/<project_id>/
    ├── config.json      # 本次任务的配置快照
    ├── outline.json     # 大纲
    ├── world.json       # 世界观/人物卡/伏笔
    ├── state.json       # 状态机（phase / last_chapter / total_chapters）
    ├── memory.json      # 章节摘要列表（记忆系统持久化）
    └── chapters/        # 已生成的章节
        ├── 001.json
        └── ...

为什么按目录而非单文件：一个项目一个目录，天然支持「换配置续跑」「多本书并存」「导出独立文件」。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from ..config import LLMConfig, NovelConfig
from .schemas import Chapter, Outline, ProjectState, World


def _safe_name(name: str) -> str:
    """把标题清洗成合法的目录名片段。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return cleaned[:30] or "novel"


class ProjectStore:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self.chapters_dir = self.dir / "chapters"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.chapters_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 创建 / 打开 ----------
    @classmethod
    def create(cls, root: Path, cfg: NovelConfig) -> "ProjectStore":
        """创建新项目目录并写入配置快照。project_id = 时间戳_标题。"""
        project_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{_safe_name(cfg.effective_title())}"
        store = cls(Path(root) / project_id)
        store.save_config(cfg)
        store.save_state(ProjectState())  # 初始化状态机
        return store

    @classmethod
    def open(cls, project_dir: str | Path) -> "ProjectStore":
        return cls(Path(project_dir))

    @property
    def project_id(self) -> str:
        return self.dir.name

    # ---------- 配置 ----------
    def config_path(self) -> Path:
        return self.dir / "config.json"

    def save_config(self, cfg: NovelConfig) -> None:
        data = {
            "title": cfg.title,
            "genre": cfg.genre,
            "total_words": cfg.total_words,
            "chapter_words": cfg.chapter_words,
            "style": cfg.style,
            "premise": cfg.premise,
            "background": cfg.background,
            "hero": cfg.hero,
            "characters": list(cfg.characters),
            "plot_direction": cfg.plot_direction,
            "forbidden": list(cfg.forbidden),
            "editor_review": cfg.editor_review,
            "de_ai": cfg.de_ai,
            "anti_similarity": cfg.anti_similarity,
            "confirm": cfg.confirm,
            "memory_window": cfg.memory_window,
            "mode": cfg.mode,
            "expand_input": cfg.expand_input,
            "llm": {
                "provider": cfg.llm.provider,
                "model": cfg.llm.model,
                "base_url": cfg.llm.base_url,
                "temperature": cfg.llm.temperature,
                "max_tokens": cfg.llm.max_tokens,
                # 注意：不落盘 api_key，避免密钥明文留在项目目录
            },
        }
        self._write_json(self.config_path(), data)

    def load_config(self) -> NovelConfig:
        d = self._read_json(self.config_path(), {})
        llm_d = d.get("llm", {}) or {}
        return NovelConfig(
            title=d.get("title", ""),
            genre=d.get("genre", "玄幻"),
            total_words=int(d.get("total_words", 100_000)),
            chapter_words=int(d.get("chapter_words", 2500)),
            style=d.get("style", ""),
            premise=d.get("premise", ""),
            background=d.get("background", ""),
            hero=d.get("hero", ""),
            characters=[str(x) for x in (d.get("characters") or [])],
            plot_direction=d.get("plot_direction", ""),
            forbidden=[str(x) for x in (d.get("forbidden") or [])],
            editor_review=bool(d.get("editor_review", True)),
            de_ai=bool(d.get("de_ai", True)),
            anti_similarity=bool(d.get("anti_similarity", True)),
            confirm=bool(d.get("confirm", False)),
            memory_window=int(d.get("memory_window", 5)),
            mode=d.get("mode", "auto"),
            expand_input=d.get("expand_input", ""),
            llm=LLMConfig(
                provider=llm_d.get("provider", "deepseek"),
                model=llm_d.get("model", ""),
                api_key=llm_d.get("api_key", ""),
                base_url=llm_d.get("base_url", ""),
                temperature=float(llm_d.get("temperature", 0.8)),
                top_p=float(llm_d.get("top_p", 0.0)),
                max_tokens=int(llm_d.get("max_tokens", 4096)),
            ),
        )

    # ---------- 调研报告 ----------
    def research_path(self) -> Path:
        return self.dir / "research.json"

    def save_research(self, report) -> None:
        from ..research.schemas import ResearchReport
        self._write_json(self.research_path(), report.to_dict())

    def load_research(self):
        from ..research.schemas import ResearchReport
        d = self._read_json(self.research_path(), None)
        return ResearchReport.from_dict(d) if d else None

    # ---------- 大纲 ----------
    def outline_path(self) -> Path:
        return self.dir / "outline.json"

    def save_outline(self, outline: Outline) -> None:
        self._write_json(self.outline_path(), outline.to_dict())

    def load_outline(self) -> Optional[Outline]:
        d = self._read_json(self.outline_path(), None)
        return Outline.from_dict(d) if d else None

    # ---------- 编辑审阅结果 ----------
    def editor_review_path(self) -> Path:
        return self.dir / "editor_review.json"

    def save_editor_review(self, review_dict: dict) -> None:
        self._write_json(self.editor_review_path(), review_dict)

    def load_editor_review(self) -> Optional[dict]:
        return self._read_json(self.editor_review_path(), None)

    # ---------- 世界观 / 人物 ----------
    def world_path(self) -> Path:
        return self.dir / "world.json"

    def save_world(self, world: World) -> None:
        self._write_json(self.world_path(), world.to_dict())

    def load_world(self) -> Optional[World]:
        d = self._read_json(self.world_path(), None)
        return World.from_dict(d) if d else None

    # ---------- 章节 ----------
    def chapter_path(self, no: int) -> Path:
        return self.chapters_dir / f"{no:03d}.json"

    def save_chapter(self, chapter: Chapter) -> None:
        self._write_json(self.chapter_path(chapter.no), chapter.to_dict())

    def load_chapter(self, no: int) -> Optional[Chapter]:
        d = self._read_json(self.chapter_path(no), None)
        return Chapter.from_dict(d) if d else None

    def list_chapters(self) -> list[Chapter]:
        """返回已生成章节（按章节号升序）。"""
        items = []
        for p in sorted(self.chapters_dir.glob("*.json")):
            d = self._read_json(p, None)
            if d:
                items.append(Chapter.from_dict(d))
        items.sort(key=lambda c: c.no)
        return items

    # ---------- 状态机 ----------
    def state_path(self) -> Path:
        return self.dir / "state.json"

    def save_state(self, state: ProjectState) -> None:
        self._write_json(self.state_path(), state.to_dict())

    def load_state(self) -> ProjectState:
        d = self._read_json(self.state_path(), {})
        return ProjectState.from_dict(d)

    # ---------- 记忆（章节摘要） ----------
    def memory_path(self) -> Path:
        return self.dir / "memory.json"

    def load_summaries(self) -> list[dict]:
        """返回 [{no, summary}, ...]，按章节号升序。"""
        items = self._read_json(self.memory_path(), [])
        return sorted(items, key=lambda x: int(x.get("no", 0)))

    def append_summary(self, no: int, summary: str) -> None:
        summaries = self.load_summaries()
        summaries = [s for s in summaries if int(s.get("no", 0)) != no]  # 幂等：同章节覆盖
        summaries.append({"no": no, "summary": summary})
        self._write_json(self.memory_path(), sorted(summaries, key=lambda x: int(x.get("no", 0))))

    # ---------- 导出 TXT ----------
    def export_txt(self) -> Path:
        """把整本书拼成 UTF-8 txt 文件。
        成品主输出到工作目录下的 output/ 文件夹（用户取用），
        同时在项目目录保留一份副本（项目自包含，便于断点续跑）。
        返回 output/ 下的最终路径。
        """
        cfg = self.load_config()
        outline = self.load_outline()
        chapters = self.list_chapters()
        if not chapters:
            raise RuntimeError("没有已生成的章节，无法导出")

        lines: list[str] = []
        lines.append(cfg.effective_title())
        lines.append("")
        lines.append("作者：AI 自动生成")
        lines.append(f"题材：{cfg.genre}")
        lines.append("")
        if outline and outline.intro:
            lines.append("【简介】")
            lines.append(outline.intro)
            lines.append("")
        lines.append("【目录】")
        for ch in chapters:
            lines.append(f"第{ch.no}章 {ch.title}".rstrip())
        lines.append("")
        lines.append("=" * 40)
        lines.append("")
        for ch in chapters:
            lines.append(f"第{ch.no}章 {ch.title}".rstrip())
            lines.append("")
            lines.append(ch.content.strip())
            lines.append("")
            lines.append("=" * 40)
            lines.append("")

        content = "\n".join(lines)
        filename = f"{_safe_name(cfg.effective_title())}.txt"

        # 项目目录保留副本（自包含）
        (self.dir / filename).write_text(content, encoding="utf-8")

        # 全局 output/ 作为主输出目录
        output_dir = self.dir.parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_out = output_dir / filename
        final_out.write_text(content, encoding="utf-8")
        return final_out

    # ---------- 工具 ----------
    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
