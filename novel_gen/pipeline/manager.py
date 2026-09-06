# -*- coding: utf-8 -*-
"""
流水线编排器：把调研 → 大纲 → 人物世界观 → 逐章正文 → 导出 串成一条可断点续跑的流水线。

断点续传设计（产物驱动）：
    以「产物文件是否存在」为唯一权威来源判断进度——
    没有 research.json 就跑调研，没有 outline.json 就跑大纲，
    没有 world.json 就跑人物，已生成章节之后接着写。
    这样即使手动删了某个文件也能正确续跑，state.json 仅作展示参考。
"""
from __future__ import annotations

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..research.research import ResearchEngine
from ..research.schemas import ResearchReport
from ..store.project import ProjectStore
from ..store.schemas import Chapter, ChapterPlan, Outline, ProjectState, World
from .memory import Memory
from .stages import ChapterWriter, OutlineGenerator, WorldGenerator
from .editor import EditorReviewer
from .templates import expand_outline_prompt
from webapp.logger import get_logger

logger = get_logger("pipeline")


class NovelPipeline:
    def __init__(self, cfg: NovelConfig, store: ProjectStore, llm: LLMClient):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.memory = Memory(store, cfg.memory_window)
        self.outline_gen = OutlineGenerator(cfg, llm, store)
        self.world_gen = WorldGenerator(cfg, llm, store)
        self.chapter_writer = ChapterWriter(cfg, llm, store, self.memory)
        self.editor = EditorReviewer(llm)

    # ---------- 各阶段（幂等：已存在则直接返回） ----------
    def is_expand_mode(self) -> bool:
        """是否 AI 扩写模式（跳过调研，直接用用户提供的大纲/内容）。"""
        return self.cfg.mode == "expand"

    def run_research(self, silent: bool = False) -> ResearchReport | None:
        # 扩写模式：跳过联网调研（用户已给出内容，无需共性调研）
        if self.is_expand_mode():
            return None
        existing = self.store.load_research()
        if existing is not None:
            if not silent:
                print(f"[调研] 已存在，复用：{existing.summary}")
            return existing
        logger.info("阶段开始: 调研 research")
        result = ResearchEngine(self.cfg, self.llm, self.store).run(silent=silent)
        logger.info("阶段完成: 调研 research（摘要 %s）", (result.summary or "")[:50] if result else "None")
        return result

    def run_outline(self, silent: bool = False) -> Outline:
        existing = self.store.load_outline()
        if existing is not None:
            if not silent:
                print(f"[大纲] 已存在，复用：{existing.title}（{len(existing.chapters)} 章）")
            return existing
        # 扩写模式：从用户提供的大纲/简要内容生成章节大纲，跳过调研
        if self.is_expand_mode():
            return self._run_expand_outline(silent=silent)
        logger.info("阶段开始: 大纲 outline")
        research = self.run_research(silent=True)
        result = self.outline_gen.run(research, silent=silent)
        logger.info("阶段完成: 大纲 outline（%s，%d 章）", result.title, len(result.chapters))
        return result

    def _run_expand_outline(self, silent: bool = False) -> Outline:
        """扩写模式：把用户提供的大纲/简要内容拆解为结构化章节大纲。"""
        if not self.cfg.expand_input.strip():
            raise RuntimeError("AI 扩写模式需要提供大纲或简要内容（expand_input 为空）")
        system, user = expand_outline_prompt(self.cfg, self.cfg.expand_input.strip())
        logger.info("阶段开始: 扩写大纲（expand_input %d 字）", len(self.cfg.expand_input.strip()))
        data = self.llm.chat_json(system, user, temperature=0.7)
        outline = Outline.from_dict(data)
        outline.title = outline.title or self.cfg.effective_title()
        outline = self.outline_gen._normalize(outline)
        self.store.save_outline(outline)
        if not silent:
            print(f"[扩写大纲完成] {outline.title}｜共 {len(outline.chapters)} 章")
        logger.info("阶段完成: 扩写大纲（%s，%d 章）", outline.title, len(outline.chapters))
        return outline

    def run_world(self, silent: bool = False) -> World:
        existing = self.store.load_world()
        if existing is not None:
            if not silent:
                print(f"[人物/世界观] 已存在，复用：{len(existing.characters)} 个人物")
            return existing
        outline = self.run_outline(silent=True)
        research = self.store.load_research() or self.run_research(silent=True)
        logger.info("阶段开始: 人物/世界观 world")
        result = self.world_gen.run(outline, research, silent=silent)
        logger.info("阶段完成: 人物/世界观 world（%d 个人物）", len(result.characters))
        return result

    def run_editor_review(self, silent: bool = False) -> None:
        """大纲生成后执行编辑视角质检；不通过则自动修正一次。幂等：已审阅则跳过。"""
        # 扩写模式：用户已给大纲，跳过编辑质检（避免改动用户既定内容）
        if not self.cfg.editor_review or self.is_expand_mode():
            return
        if self.store.load_editor_review() is not None:
            if not silent:
                print("[编辑质检] 已审阅，跳过")
            return
        outline = self.store.load_outline()
        if outline is None:
            return
        research = self.store.load_research()
        research_block = research.to_prompt_block() if research else ""
        if not silent:
            print("[编辑质检] 正在审阅大纲...")
        logger.info("阶段开始: 编辑质检 editor_review")
        review = self.editor.review(self.cfg, outline, research_block)
        self.store.save_editor_review(review.to_dict())
        if not silent:
            verdict = "通过" if review.passed else "需修正"
            print(f"[编辑质检] {verdict}｜评分 {review.score}/10｜问题 {len(review.issues)} 条")
            for action in review.priority_actions[:3]:
                print(f"  → {action}")
        logger.info("阶段完成: 编辑质检（%s，评分 %.1f，问题 %d 条）",
                    "通过" if review.passed else "需修正", review.score, len(review.issues))
        if not review.passed:
            if not silent:
                print("[编辑质检] 正在自动修正大纲...")
            logger.info("编辑质检未通过，自动修正大纲")
            revised = self.editor.revise(self.cfg, outline, review, research_block)
            if revised is not outline:  # 修正成功
                self.store.save_outline(revised)
                if not silent:
                    print(f"[编辑质检] 大纲已修正：{revised.title}（{len(revised.chapters)} 章）")
                logger.info("大纲修正完成（%s，%d 章）", revised.title, len(revised.chapters))

    def write_chapter(self, no: int, silent: bool = False) -> Chapter:
        outline = self.store.load_outline()
        if outline is None or no > len(outline.chapters):
            raise RuntimeError(f"大纲不存在或章节号越界（{no}/{len(outline.chapters) if outline else 0}）")
        plan = outline.chapters[no - 1]
        # 是否为全书最后一章（按大纲实际章节数判断，而非配置目标数——
        # 模型可能只生成了 N-1 章，最后一章也必须是完整结局）
        is_final = no == len(outline.chapters)
        # 确保人物/世界观已生成，正文才有设定库可注入
        if self.store.load_world() is None:
            self.run_world(silent=silent)
        research = None
        if not self.is_expand_mode():
            research = self.store.load_research() or self.run_research(silent=True)
        logger.info("章节开始: 第 %d 章「%s」（is_final=%s）", no, plan.title, is_final)
        try:
            chapter = self.chapter_writer.write(no, plan.title, plan.goal, research,
                                                silent=silent, is_final=is_final)
        except Exception:
            logger.exception("章节失败: 第 %d 章「%s」", no, plan.title)
            raise
        logger.info("章节完成: 第 %d 章「%s」（%d 字）", no, plan.title,
                    len(chapter.content) if chapter else 0)
        return chapter

    # ---------- 进度查询 ----------
    def progress(self) -> tuple[int, int]:
        """返回 (已写章节数, 总章节数)。"""
        outline = self.store.load_outline()
        total = len(outline.chapters) if outline else 0
        done = len(self.store.list_chapters())
        return done, total

    def _mark_state(self, phase: str, last_chapter: int = 0) -> None:
        state = self.store.load_state()
        state.phase = phase
        state.last_chapter = last_chapter
        outline = self.store.load_outline()
        state.total_chapters = len(outline.chapters) if outline else state.total_chapters
        self.store.save_state(state)

    # ---------- 全流程 / 续跑 ----------
    def resume(self, silent: bool = False) -> None:
        """从当前进度继续：缺失产物即生成，已生成章节自动跳过。"""
        if not silent:
            self._print_plan()
        logger.info("流水线开始: 《%s》mode=%s total_words=%d",
                    self.cfg.effective_title(), self.cfg.mode, self.cfg.total_words)

        research = self.run_research(silent=silent)
        self._mark_state("outline")
        outline = self.run_outline(silent=silent)
        self._mark_state("editor_review")
        self.run_editor_review(silent=silent)
        self._mark_state("world")
        self.run_world(silent=silent)
        self._mark_state("writing")

        done, total = self.progress()
        for no in range(done + 1, total + 1):
            self.write_chapter(no, silent=silent)
            self._mark_state("writing", last_chapter=no)

        self._mark_state("done", last_chapter=total)
        if not silent:
            print(f"\n全部 {total} 章生成完毕。")
        logger.info("流水线完成: 《%s》共 %d 章", self.cfg.effective_title(), total)

    def run_all(self, silent: bool = False) -> Path | None:
        """一键全流程（等价于从零 resume），结束后导出 txt。返回导出文件路径。"""
        self.resume(silent=silent)
        return self.export(silent=silent)

    def export(self, silent: bool = False):
        from pathlib import Path
        out = self.store.export_txt()
        if not silent:
            print(f"[导出] {out}")
        logger.info("导出完成: %s", out)
        return Path(out)

    def _print_plan(self) -> None:
        cfg = self.cfg
        print("=" * 56)
        print(f"《{cfg.effective_title()}》生成计划")
        print(f"  题材：{cfg.genre}｜目标 {cfg.total_words} 字 / 约 {cfg.num_chapters} 章"
              f"（每章约 {cfg.chapter_words} 字）")
        print(f"  模型：{cfg.llm.provider} / {cfg.llm.model}")
        flow = "AI 扩写（用用户提供的大纲/内容直接扩写正文）" if self.is_expand_mode() \
            else "调研 → 大纲 → 编辑质检 → 人物世界观 → 逐章正文 → 导出 txt"
        print(f"  流程：{flow}")
        print("=" * 56)
