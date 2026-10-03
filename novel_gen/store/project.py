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
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from ..config import AgentGuardConfig, LLMConfig, NovelConfig, SearchConfig
from ..utils.paths import get_output_dir
from .schemas import Chapter, Outline, ProjectState, World


def _safe_name(name: str) -> str:
    """把标题清洗成合法的 ASCII 目录名片段（避免项目 ID 含中文导致 URL 编码问题）。
    只保留 ASCII 字母数字；无可保留字符时用标题 hash 兜底。"""
    cleaned = re.sub(r'[^A-Za-z0-9]+', '', name.strip())
    cleaned = cleaned[:20]
    if not cleaned:
        import hashlib
        cleaned = hashlib.md5(name.strip().encode("utf-8")).hexdigest()[:8]
    return cleaned or "novel"


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
        # P1 修复：加短 uuid 后缀，避免同一秒创建同名书时目录冲突
        project_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{_safe_name(cfg.effective_title())}_{uuid.uuid4().hex[:6]}"
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
            "confirm": cfg.confirm,
            "mode": cfg.mode,
            "expand_input": cfg.expand_input,
            "content_type": cfg.content_type,
            "expand_targets": list(cfg.expand_targets),
            "with_research": getattr(cfg, "with_research", False),
            "user_words": getattr(cfg, "_user_words", 0),
            "custom_prompt": getattr(cfg, "custom_prompt", ""),
            "skill_prompt_append": getattr(cfg, "skill_prompt_append", ""),
            "style_role_id": getattr(cfg, "style_role_id", 0),
            "style_role_prompt": getattr(cfg, "style_role_prompt", ""),
            "style_role_reference": getattr(cfg, "style_role_reference", ""),
            "agent_guard": {
                "enabled": bool(getattr(cfg.agent_guard, "enabled", True)),
                "max_loops": int(getattr(cfg.agent_guard, "max_loops", 80)),
                "max_tokens": int(getattr(cfg.agent_guard, "max_tokens", 2_000_000)),
                "input_check": bool(getattr(cfg.agent_guard, "input_check", True)),
                "output_check": bool(getattr(cfg.agent_guard, "output_check", True)),
                "quality_check": bool(getattr(cfg.agent_guard, "quality_check", True)),
                "scene_limits": dict(getattr(cfg.agent_guard, "scene_limits", {}) or {}),
            },
            "search": {
                "provider": cfg.search.provider,
                "api_url": cfg.search.api_url,
                "count": cfg.search.count,
                # api_key 不落盘，读环境变量
            },
            "llm": {
                "provider": cfg.llm.provider,
                "model": cfg.llm.model,
                "base_url": cfg.llm.base_url,
                "temperature": cfg.llm.temperature,
                "top_p": cfg.llm.top_p,
                "max_tokens": cfg.llm.max_tokens,
                # 注意：不落盘 api_key，避免密钥明文留在项目目录
            },
        }
        # 功能开关（FEATURE_FLAGS 注册表驱动：新增开关自动落盘，无需手改此处；
        # guard 子项在 agent_guard 段，不进顶层）
        from ..config import FEATURE_FLAGS
        for _f in FEATURE_FLAGS:
            if _f.module == "guard":
                continue
            data[_f.key] = getattr(cfg, _f.key)
        self._write_json(self.config_path(), data)

    def load_config(self) -> NovelConfig:
        d = self._read_json(self.config_path(), {})
        llm_d = d.get("llm", {}) or {}
        kwargs: dict = {
            "title": d.get("title", ""),
            "genre": d.get("genre", "玄幻"),
            "total_words": int(d.get("total_words", 100_000)),
            "chapter_words": int(d.get("chapter_words", 2500)),
            "style": d.get("style", ""),
            "premise": d.get("premise", ""),
            "background": d.get("background", ""),
            "hero": d.get("hero", ""),
            "characters": [str(x) for x in (d.get("characters") or [])],
            "plot_direction": d.get("plot_direction", ""),
            "forbidden": [str(x) for x in (d.get("forbidden") or [])],
            "confirm": bool(d.get("confirm", False)),
            "mode": d.get("mode", "auto"),
            "expand_input": d.get("expand_input", ""),
            "content_type": d.get("content_type", ""),
            "expand_targets": [int(x) for x in (d.get("expand_targets") or [])],
            "with_research": bool(d.get("with_research", False)),
            "custom_prompt": d.get("custom_prompt", ""),
            "skill_prompt_append": d.get("skill_prompt_append", ""),
            "style_role_id": int(d.get("style_role_id", 0) or 0),
            "style_role_prompt": d.get("style_role_prompt", ""),
            "style_role_reference": d.get("style_role_reference", ""),
        }
        # 功能开关（FEATURE_FLAGS 注册表驱动：旧项目缺字段自动用注册表默认值）
        from ..config import FEATURE_FLAGS, _coerce_feature
        for _f in FEATURE_FLAGS:
            if _f.module == "guard":
                continue
            kwargs[_f.key] = _coerce_feature(d.get(_f.key), _f.default)
        kwargs["agent_guard"] = AgentGuardConfig(
            enabled=bool((d.get("agent_guard") or {}).get("enabled", True)),
            max_loops=int((d.get("agent_guard") or {}).get("max_loops", 80)),
            max_tokens=int((d.get("agent_guard") or {}).get("max_tokens", 2_000_000)),
            input_check=bool((d.get("agent_guard") or {}).get("input_check", True)),
            output_check=bool((d.get("agent_guard") or {}).get("output_check", True)),
            quality_check=bool((d.get("agent_guard") or {}).get("quality_check", True)),
            scene_limits=dict((d.get("agent_guard") or {}).get("scene_limits", {}) or {}),
        )
        kwargs["search"] = SearchConfig(
            provider=(d.get("search") or {}).get("provider", ""),
            api_key="",
            api_url=(d.get("search") or {}).get("api_url", ""),
            count=int((d.get("search") or {}).get("count", 5)),
        )
        kwargs["llm"] = LLMConfig(
            provider=llm_d.get("provider", "deepseek"),
            model=llm_d.get("model", ""),
            api_key=llm_d.get("api_key", ""),
            base_url=llm_d.get("base_url", ""),
            temperature=float(llm_d.get("temperature", 0.8)),
            top_p=float(llm_d.get("top_p", 0.0)),
            max_tokens=int(llm_d.get("max_tokens", 4096)),
        )
        return NovelConfig(**kwargs)

    # ---------- 用户手写内容 ----------
    def user_content_path(self) -> Path:
        return self.dir / "user_content.txt"

    def save_user_content(self, text: str) -> None:
        self.user_content_path().write_text(text or "", encoding="utf-8")

    def set_user_words(self, words: int) -> None:
        """记录用户手写内容原字数（创建时统计），用于按 AI 实际生成字数结算。"""
        cfg = self.load_config()
        cfg._user_words = int(words)
        self.save_config(cfg)

    def user_words(self) -> int:
        d = self._read_json(self.config_path(), {})
        return int(d.get("user_words", 0) or 0)

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

    # ---------- 拆书库回流素材（P0-3） ----------
    # 由 webapp 侧在调研前把同题材爆款拆书条目格式化为纯文本写入，
    # ResearchEngine 读取并并入调研素材（保持 novel_gen 不依赖 webapp 的分层）。
    def teardown_material_path(self) -> Path:
        return self.dir / "teardown_material.txt"

    def save_teardown_material(self, text: str) -> None:
        (self.dir / "teardown_material.txt").write_text(text or "", encoding="utf-8")

    def load_teardown_material(self) -> str:
        p = self.dir / "teardown_material.txt"
        try:
            return p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except Exception:
            return ""

    # ---------- 大纲 ----------
    def outline_path(self) -> Path:
        return self.dir / "outline.json"

    def save_outline(self, outline: Outline) -> None:
        self._write_json(self.outline_path(), outline.to_dict())
        # 自动快照：每次保存备份到 snapshots/，保留最近20个版本
        try:
            import time, json, glob, os
            snap_dir = self.dir / "outline_snapshots"
            snap_dir.mkdir(exist_ok=True)
            snap_file = snap_dir / f"snap_{int(time.time())}.json"
            snap_file.write_text(json.dumps(outline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            snaps = sorted(glob.glob(str(snap_dir / "snap_*.json")))
            for old_f in snaps[:-20]:
                os.remove(old_f)
        except Exception:
            pass



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

    # ---------- 分章设定卡索引（REQ-token优化 P1-1）----------
    def setting_cards_path(self) -> Path:
        return self.dir / "setting_cards.json"

    def save_setting_cards(self, data: dict) -> None:
        if data:
            self._write_json(self.setting_cards_path(), data)

    def load_setting_cards(self) -> Optional[dict]:
        return self._read_json(self.setting_cards_path(), None)

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

    # ---------- 轻量统计（列表页缓存，避免每次全量读章节文本） ----------
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    def load_or_refresh_meta(self) -> dict:
        """返回 {generated_words, chapters_done, chapters_total}，带缓存失效检测。

        meta.json 比 config/outline/所有章节文件都新时直接复用缓存（零文件内容读取）；
        否则全量重算并原子写回。章节被编辑/新增/删除都会使 meta 过期。
        """
        meta = self.meta_path()
        stale = not meta.exists()
        if not stale:
            meta_mt = meta.stat().st_mtime
            for p in (self.config_path(), self.outline_path()):
                if p.exists() and p.stat().st_mtime > meta_mt:
                    stale = True
                    break
            if not stale:
                try:
                    for e in os.scandir(self.chapters_dir):
                        if e.stat().st_mtime > meta_mt:
                            stale = True
                            break
                except OSError:
                    stale = True
        if not stale:
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                if all(k in data for k in ("generated_words", "chapters_done", "chapters_total")):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
            stale = True
        # 全量重算（首次或已过期）
        cfg = self.load_config()
        outline = self.load_outline()
        chapters = self.list_chapters()
        data = {
            "generated_words": sum(len(c.content or "") for c in chapters),
            "chapters_done": len(chapters),
            "chapters_total": len(outline.chapters) if outline and outline.chapters else cfg.num_chapters,
            "updated_at": time.time(),
        }
        try:
            self._write_json(meta, data)
        except OSError:
            pass
        return data

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

    # ---------- 一致性校验（高质量模式） ----------
    def verify_issues_path(self) -> Path:
        return self.dir / "verify_issues.json"
    def load_verify_issues(self) -> list[dict]:
        """返回 [{chapter, problem, suggestion}, ...]，按章节号升序。"""
        items = self._read_json(self.verify_issues_path(), [])
        return sorted(items, key=lambda x: int(x.get("chapter", 0)))

    def save_verify_issues(self, issues: list[dict]) -> None:
        self._write_json(self.verify_issues_path(), issues)

    def append_verify_issues(self, chapter: int, new_issues: list[dict]) -> None:
        """追加本次校验发现的问题（不覆盖历史）。"""
        all_issues = self.load_verify_issues()
        for it in new_issues:
            it["chapter"] = chapter
            all_issues.append(it)
        self.save_verify_issues(all_issues)

    # ---------- 人物动态状态快照（P0-2） ----------
    def character_states_path(self) -> Path:
        return self.dir / "character_states.json"

    def load_character_states(self) -> dict:
        """返回 {人物名: {last_seen, location, body, emotion, relations, new_info, goal}, ...}。"""
        d = self._read_json(self.character_states_path(), {})
        return d if isinstance(d, dict) else {}

    def save_character_states(self, states: dict) -> None:
        """整体覆盖保存（每章更新后写入最新快照）。"""
        self._write_json(self.character_states_path(), states)

    # ---------- 章内/章间状态账本（REQ-章内自洽与机制账本） ----------
    def chapter_state_path(self) -> Path:
        return self.dir / "chapter_state.json"

    def load_chapter_state(self) -> Optional[dict]:
        """返回 {chapter, numbers, characters, items, time, facts}；无账本返回 None。"""
        d = self._read_json(self.chapter_state_path(), None)
        return d if isinstance(d, dict) and d.get("chapter") else None

    def save_chapter_state(self, state: dict) -> None:
        """整体覆盖保存（每章落盘后写入最新章末状态）。"""
        self._write_json(self.chapter_state_path(), state)

    # ---------- 揭示执行跟踪（REQ-揭示执行跟踪） ----------
    def reveal_status_path(self) -> Path:
        return self.dir / "reveal_status.json"

    def load_reveal_status(self) -> dict:
        """返回 {reveal索引: {from_ch, content, hit_ch[]}}；未跟踪过返回 {}。"""
        d = self._read_json(self.reveal_status_path(), {})
        return d if isinstance(d, dict) else {}

    def save_reveal_status(self, status: dict) -> None:
        """整体覆盖保存（每章检测后写入最新命中情况）。"""
        self._write_json(self.reveal_status_path(), status)

    # ---------- 全书终审（P2-2） ----------
    def final_review_path(self) -> Path:
        return self.dir / "final_review.json"

    def load_final_review(self) -> Optional[dict]:
        """返回 {checked_at, ok, issues, summary}；未终审过返回 None。"""
        return self._read_json(self.final_review_path(), None)

    def save_final_review(self, review: dict) -> None:
        self._write_json(self.final_review_path(), review)

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

        # 全局 output/ 作为主输出目录（数据根目录由 DATA_DIR 配置）
        output_dir = get_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        final_out = output_dir / filename
        final_out.write_text(content, encoding="utf-8")
        return final_out

    # ---------- 工具 ----------
    @staticmethod
    def _write_json(path: Path, data) -> None:
        """原子写：先写临时文件再 os.replace，避免进程崩溃留下半截 JSON。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
