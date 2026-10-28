# -*- coding: utf-8 -*-
"""大纲与世界观生成器（从 stages.py 抽出，§11.1 文件拆分）。

本模块承载：
- OutlineGenerator：全书大纲生成（支持超长篇分批）
- WorldGenerator：人物/世界观生成
- assign_volumes / unique_chapter_titles：大纲后处理工具

与 ChapterWriter（逐章正文生成）解耦——大纲/世界观是前期阶段，
正文生成是后期阶段，拆文件降低单次认知负载。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..research.schemas import ResearchReport
from ..store.project import ProjectStore
from ..store.schemas import ChapterPlan, Outline, Volume, World
from .templates import outline_prompt, world_prompt

_CN_NUM = "一二三四五六七八九十"


def assign_volumes(outline: Outline, target: int) -> None:
    """按 volumes 的 start_ch~end_ch 区间，为无归属章节确定性回填 volume_no。"""
    vols = sorted((v for v in (outline.volumes or []) if v and v.no), key=lambda v: v.no)
    if not vols:
        return
    mapping: dict[int, int] = {}
    for v in vols:
        lo, hi = max(1, v.start_ch), min(target, v.end_ch)
        for c in range(lo, hi + 1):
            mapping.setdefault(c, v.no)
    first_lo = max(1, vols[0].start_ch)
    for c in range(1, first_lo):
        mapping[c] = vols[0].no
    for i in range(len(vols) - 1):
        cur_end = min(target, vols[i].end_ch)
        nxt_start = max(1, vols[i + 1].start_ch)
        for c in range(cur_end + 1, nxt_start):
            mapping[c] = vols[i].no
    last_end = min(target, vols[-1].end_ch)
    for c in range(last_end + 1, target + 1):
        mapping[c] = vols[-1].no
    for ch in outline.chapters:
        if getattr(ch, "volume_no", 0) != 0:
            continue
        ch.volume_no = mapping.get(ch.no, 0)


def unique_chapter_titles(chapters: list) -> list:
    """章节标题唯一化（原地修改并返回）。"""
    seen: dict[str, int] = {}
    for i, c in enumerate(chapters, start=1):
        t = (c.title or "").strip()
        if not t or re.fullmatch(r"第\s*[0-9一-龥]+\s*章", t):
            t = f"第{i}章"
        if t in seen:
            seen[t] += 1
            n = seen[t]
            suffix = f"（{_CN_NUM[n - 1]}）" if n <= 10 else f"（{n}）"
            t = f"{t}{suffix}"
        else:
            seen[t] = 1
        c.title = t
    return chapters


class OutlineGenerator:
    """大纲生成器。

    章节数超过 _BATCH_SIZE 时自动分批生成（超长篇专用）：
    每批约 _BATCH_SIZE 章，批间用「前情提要」衔接，最后合并为完整大纲。
    """

    _BATCH_SIZE = 8

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 role_prompt: str = ""):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.role_prompt = role_prompt

    def _sys(self, system: str) -> str:
        """把角色人设拼到阶段提示词之前。"""
        persona = (getattr(self.cfg, "persona_override", "") or "").strip() or self.role_prompt
        return f"{persona}\n\n{system}" if persona else system

    def run(self, research: ResearchReport | None, silent: bool = False) -> Outline:
        target = self.cfg.num_chapters
        research_block = research.to_prompt_block() if research else ""
        if target > self._BATCH_SIZE:
            outline = self._run_batched(research, target, silent=silent)
        else:
            system, user = outline_prompt(self.cfg, research_block)
            system = self._sys(system)
            data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
            outline = Outline.from_dict(data)
        outline.title = outline.title or self.cfg.effective_title()
        outline = self._normalize(outline)
        self.store.save_outline(outline)
        if not silent:
            logger.info(f"[大纲完成] {outline.title}｜共 {len(outline.chapters)} 章")
            logger.info(f"简介：{outline.intro}\n")
        return outline

    def _run_batched(
        self, research: ResearchReport, target: int, silent: bool = False
    ) -> Outline:
        """分批生成完整大纲：先规划全书 title/intro，再逐批生成章节区间。"""
        sys0, usr0 = outline_prompt(self.cfg, research.to_prompt_block())
        head_system = (
            "你是一位经验丰富的网络小说主编兼金牌写手。你的任务是为一本超长篇网络小说"
            "确定最终书名与作品简介（不生成章节，章节后续分批完成）。"
            "用户的强约束（背景/主角/预设人物/避雷）必须严格遵循；剧情走向为参考方向。"
        )
        if self.cfg.anti_similarity:
            from .templates import _anti_similarity_system
            head_system += "\n\n" + _anti_similarity_system("outline")
        hard = self._head_blocks()
        head_user = (
            f"【目标】创作一本 {self.cfg.genre} 题材小说"
            f"（全书约 {self.cfg.total_words} 字，共 {target} 章，每章约 {self.cfg.chapter_words} 字）\n"
            f"【核心创意/简介】{self.cfg.premise or '（未提供，按题材常规发挥）'}\n"
            f"【文风要求】{self.cfg.style or '（未指定）'}\n\n"
            f"{hard}\n\n"
            f"【题材共性调研报告】\n{research.to_prompt_block()}\n\n"
            f"请输出 JSON：\n"
            f"- title：最终书名（如可用则沿用给定书名）\n"
            f"- intro：作品简介（3-5 句，含卖点与情绪钩子）\n"
            f"- core_conflict：全书核心冲突（一句话）\n"
            f"- main_line：全书主线脉络（3-5 句）\n"
            f"- volumes：分卷数组，将全书按剧情阶段分为3-5卷，每卷字段：no, name(卷名), intro(本卷核心主题), main_conflict(本卷主要矛盾), character_growth(本卷人物成长), climax(本卷高潮), start_ch(起始章号), end_ch(结束章号)\n"
        )
        head_system = self._sys(head_system)
        head = self.llm.chat_json(head_system, head_user, temperature=0.7, max_tokens=8192)

        title = head.get("title") or self.cfg.effective_title()
        intro = head.get("intro", "")
        core_conflict = head.get("core_conflict", "")
        main_line = head.get("main_line", "")
        volumes = [Volume.from_dict(v) for v in (head.get("volumes") or []) if isinstance(v, dict)]

        chapters: list[ChapterPlan] = []
        prev_summary = ""
        for start in range(1, target + 1, self._BATCH_SIZE):
            end = min(start + self._BATCH_SIZE - 1, target)
            if not silent:
                logger.info(f"  [大纲分批] 第 {start}—{end} 章 ...")
            system, user = outline_prompt(
                self.cfg,
                research.to_prompt_block(),
                batch=(start, end, prev_summary),
            )
            system = self._sys(system)
            data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
            batch_chapters = [ChapterPlan.from_dict(c) for c in data.get("chapters", [])]
            for i, c in enumerate(batch_chapters):
                c.no = start + i
            chapters.extend(batch_chapters)
            recent = batch_chapters[-3:]
            prev_summary = "；".join(
                f"第{c.no}章《{c.title}》：{c.goal}" for c in recent
            )
        outline = Outline(title=title, intro=intro, core_conflict=core_conflict,
                          main_line=main_line, volumes=volumes, chapters=chapters)
        assign_volumes(outline, target)
        return outline

    def _head_blocks(self) -> str:
        """复用强约束/参考方向区块文本。"""
        from .templates import _hard_constraints, _soft_direction
        hard = _hard_constraints(self.cfg)
        soft = _soft_direction(self.cfg)
        return f"{hard}\n\n{soft}".strip()

    def _normalize(self, outline: Outline) -> Outline:
        """章节数归一化：截断超出的、提示不足的，并强制重新编号。"""
        target = self.cfg.num_chapters
        chapters = outline.chapters
        if len(chapters) > target:
            chapters = chapters[:target]
        elif chapters and len(chapters) < target:
            logger.info(f"  [提示] 大纲仅 {len(chapters)} 章，少于目标 {target} 章（不补足，按实际推进）")
        elif not chapters:
            logger.info("  [提示] 大纲未生成任何章节。")
        normalized = []
        for i, c in enumerate(chapters, start=1):
            c.no = i
            if not c.title:
                c.title = f"第{i}章"
            if not c.goal:
                c.goal = f"推进主线剧情，制造冲突并留下悬念。"
            if not c.core_event:
                c.core_event = c.goal
            if not c.conflict:
                c.conflict = "主角面临新的阻碍，需要做出选择。"
            if not c.character_moves:
                c.character_moves = "主角行动并应对局势变化。"
            if not c.foreshadowing:
                c.foreshadowing = "无"
            if not c.emotion:
                c.emotion = "悬念推进"
            if not c.transition:
                c.transition = "自然衔接前章，引出下章。"
            if not c.tag:
                c.tag = "过渡"
            normalized.append(c)
        unique_chapter_titles(normalized)
        outline.chapters = normalized
        assign_volumes(outline, self.cfg.num_chapters)
        return outline


class WorldGenerator:
    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 role_prompt: str = ""):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.role_prompt = role_prompt

    def _sys(self, system: str) -> str:
        persona = (getattr(self.cfg, "persona_override", "") or "").strip() or self.role_prompt
        return f"{persona}\n\n{system}" if persona else system

    def run(self, outline: Outline, research: ResearchReport | None, silent: bool = False) -> World:
        system, user = world_prompt(self.cfg, outline, research.to_prompt_block() if research else "")
        system = self._sys(system)
        data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
        world = World.from_dict(data)
        if not world.world_view:
            world.world_view = f"{self.cfg.genre}题材原创世界观"
        self.store.save_world(world)
        if not silent:
            logger.info(f"[人物/世界观完成] {len(world.characters)} 个人物，{len(world.foreshadowing)} 条伏笔\n")
        return world
