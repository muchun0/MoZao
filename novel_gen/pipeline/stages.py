# -*- coding: utf-8 -*-
"""
各阶段生成器：大纲 / 人物世界观 / 逐章正文。

每个生成器职责单一：构造提示词 → 调 LLM → 解析/归一化 → 落盘。
"""
from __future__ import annotations

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..research.schemas import ResearchReport
from ..store.project import ProjectStore
from ..store.schemas import Chapter, ChapterPlan, Outline, World
from .memory import Memory
from .templates import chapter_prompt, outline_prompt, world_prompt


class OutlineGenerator:
    """大纲生成器。

    章节数超过 _BATCH_SIZE 时自动分批生成（超长篇专用）：
    每批约 _BATCH_SIZE 章，批间用「前情提要」衔接，最后合并为完整大纲。
    """

    _BATCH_SIZE = 25

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore):
        self.cfg = cfg
        self.llm = llm
        self.store = store

    def run(self, research: ResearchReport | None, silent: bool = False) -> Outline:
        target = self.cfg.num_chapters
        research_block = research.to_prompt_block() if research else ""
        if target > self._BATCH_SIZE:
            outline = self._run_batched(research, target, silent=silent)
        else:
            system, user = outline_prompt(self.cfg, research_block)
            data = self.llm.chat_json(system, user, temperature=0.7)
            outline = Outline.from_dict(data)
        outline.title = outline.title or self.cfg.effective_title()
        outline = self._normalize(outline)
        self.store.save_outline(outline)
        if not silent:
            print(f"[大纲完成] {outline.title}｜共 {len(outline.chapters)} 章")
            print(f"简介：{outline.intro}\n")
        return outline

    def _run_batched(
        self, research: ResearchReport, target: int, silent: bool = False
    ) -> Outline:
        """分批生成完整大纲：先规划全书 title/intro，再逐批生成章节区间。"""
        # 第一批同时产出书名与简介（用完整无章节的输出引导）
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
        )
        head = self.llm.chat_json(head_system, head_user, temperature=0.7)

        title = head.get("title") or self.cfg.effective_title()
        intro = head.get("intro", "")

        chapters: list[ChapterPlan] = []
        prev_summary = ""
        for start in range(1, target + 1, self._BATCH_SIZE):
            end = min(start + self._BATCH_SIZE - 1, target)
            if not silent:
                print(f"  [大纲分批] 第 {start}—{end} 章 ...")
            system, user = outline_prompt(
                self.cfg,
                research.to_prompt_block(),
                batch=(start, end, prev_summary),
            )
            data = self.llm.chat_json(system, user, temperature=0.7)
            batch_chapters = [ChapterPlan.from_dict(c) for c in data.get("chapters", [])]
            # 校验并修复序号（模型可能漂移，强制从 start 连续编号）
            for i, c in enumerate(batch_chapters):
                c.no = start + i
            chapters.extend(batch_chapters)
            # 为下一批生成前情提要（最近几章的 goal 浓缩）
            recent = batch_chapters[-3:]
            prev_summary = "；".join(
                f"第{c.no}章《{c.title}》：{c.goal}" for c in recent
            )
        return Outline(title=title, intro=intro, chapters=chapters)

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
            # 模型未给足章节：超长篇分批生成场景下可能因某批缺失而不足，
            # 打印提示让上层知晓（保留已生成部分，避免静默丢失）。
            print(f"  [提示] 大纲仅 {len(chapters)} 章，少于目标 {target} 章（不补足，按实际推进）")
        elif not chapters:
            print("  [提示] 大纲未生成任何章节。")
        # 重新编号，并补默认标题/目标
        normalized = []
        for i, c in enumerate(chapters, start=1):
            c.no = i
            if not c.title:
                c.title = f"第{i}章"
            if not c.goal:
                c.goal = f"推进主线剧情，制造冲突并留下悬念。"
            normalized.append(c)
        outline.chapters = normalized
        return outline


class WorldGenerator:
    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore):
        self.cfg = cfg
        self.llm = llm
        self.store = store

    def run(self, outline: Outline, research: ResearchReport | None, silent: bool = False) -> World:
        system, user = world_prompt(self.cfg, outline, research.to_prompt_block() if research else "")
        data = self.llm.chat_json(system, user, temperature=0.7)
        world = World.from_dict(data)
        if not world.world_view:
            world.world_view = f"{self.cfg.genre}题材原创世界观"
        self.store.save_world(world)
        if not silent:
            print(f"[人物/世界观完成] {len(world.characters)} 个人物，{len(world.foreshadowing)} 条伏笔\n")
        return world


class ChapterWriter:
    def __init__(
        self,
        cfg: NovelConfig,
        llm: LLMClient,
        store: ProjectStore,
        memory: Memory,
    ):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.memory = memory

    def write(
        self,
        chapter_no: int,
        plan_title: str,
        plan_goal: str,
        research: ResearchReport | None,
        silent: bool = False,
        is_final: bool = False,
    ) -> Chapter:
        world = self.store.load_world()
        memory_context = self.memory.build_context(world, up_to_chapter=chapter_no)
        prev_tail = self._prev_tail(chapter_no)

        system, user = chapter_prompt(
            self.cfg,
            chapter_no=chapter_no,
            chapter_title=plan_title,
            chapter_goal=plan_goal,
            research_block=research.to_prompt_block() if research else "",
            memory_context=memory_context,
            prev_tail=prev_tail,
            is_final=is_final,
        )
        text = self.llm.chat(system, user, temperature=0.85)
        # 清洗模型偶尔夹带的 HTML 标签（<p>、<br> 等），保证落盘正文是纯文本
        text = _strip_html_tags(text)
        content, summary = _split_summary(text)

        # 字数兜底：正文明显不足目标时，让模型扩写一次（防止整本低于目标字数）
        content = self._ensure_length(content, plan_title, plan_goal, system)
        content = _strip_html_tags(content)  # 扩写结果同样可能夹带标签
        summary = _strip_html_tags(summary)

        if not summary:
            # 模型漏输出【摘要】时兜底：单独调用一次生成摘要，保证记忆库完整
            summary = self._gen_summary(chapter_no, plan_title, content)

        chapter = Chapter(no=chapter_no, title=plan_title, content=content, summary=summary)
        self.store.save_chapter(chapter)
        if summary:
            self.store.append_summary(chapter_no, summary)
        if not silent:
            tail = f"｜摘要：{summary}" if summary else "｜（未生成摘要）"
            print(f"  第{chapter_no}章《{plan_title}》完成（{len(content)} 字）{tail}")
        return chapter

    def _ensure_length(self, content: str, plan_title: str, plan_goal: str, base_system: str) -> str:
        """字数兜底：正文不足目标 90% 时，让模型扩写一次到目标字数。

        扩写原则：保留既有剧情与人设，只增加场景细节、对话交锋、动作过程与心理挣扎，
        不改变情节走向、不提前揭伏笔、不注水重复。扩写失败或仍不足则原样返回（不阻塞流程）。
        """
        target = self.cfg.chapter_words
        if not content or len(content) >= target * 0.9:
            return content
        try:
            prompt = (
                f"以下是本章已有正文（{len(content)} 字），但本章目标字数为 {target} 字，需要扩写到该规模。\n"
                f"【本章目标】{plan_goal}\n"
                f"扩写要求：\n"
                f"1. 保留全部既有剧情、人物言行与对话，不得改变情节走向、不得删减已有内容\n"
                f"2. 在原文基础上扩充：增加场景细节、对话交锋、动作过程、心理挣扎与氛围铺垫\n"
                f"3. 新增内容必须贴合本章剧情与人物设定，不得注水、不得重复啰嗦、不得提前揭穿后续伏笔\n"
                f"4. 扩写后正文（不含摘要）应达到约 {target} 字\n\n"
                f"【已有正文】\n{content}\n\n"
                f"请输出扩写后的完整正文（要包含原有内容并扩充到目标字数），不要输出摘要。"
            )
            expanded = self.llm.chat(base_system, prompt, temperature=0.85, max_tokens=8000)
            expanded_content, _ = _split_summary(expanded)
            if expanded_content and len(expanded_content) > len(content):
                return expanded_content.strip()
        except Exception:
            pass
        return content

    def _gen_summary(self, no: int, title: str, content: str) -> str:
        """正文缺失摘要时的兜底：用小成本调用生成 1-2 句摘要。"""
        try:
            return self.llm.chat(
                "你是小说助理，为给定章节生成精炼剧情摘要。只输出摘要本身，不要任何前缀。",
                f"第{no}章《{title}》正文：\n{content[:1200]}\n\n"
                f"请用 1-2 句话概括本章剧情（含关键事件、人物动向、留下的钩子），只输出摘要。",
                temperature=0.3,
                max_tokens=200,
            ).strip()
        except Exception:
            return ""

    def _prev_tail(self, chapter_no: int) -> str:
        """取上一章结尾约 120 字作为衔接提示，减少章节断裂感。"""
        if chapter_no <= 1:
            return ""
        prev = self.store.load_chapter(chapter_no - 1)
        if not prev or not prev.content:
            return ""
        return prev.content.strip()[-120:]


def _split_summary(text: str) -> tuple[str, str]:
    """按『【摘要】』标记拆分正文与摘要；未找到标记则整段视为正文。"""
    marker = "【摘要】"
    idx = text.rfind(marker)
    if idx > 0:
        content = text[:idx].strip()
        summary = text[idx + len(marker):].strip().replace("\n", " ")
        return content, summary
    return text.strip(), ""


def _strip_html_tags(text: str) -> str:
    """剥离模型偶尔夹带的 HTML 标签，返回纯文本。

    处理规则：
    - <p> / </p> / <br> / <br/> 等块级/换行标签 → 替换为换行符（保留段落结构）
    - 其余 HTML 标签 → 直接删除
    保持正文可读性，避免落盘与导出 txt 时出现 <p> 之类字面字符。
    """
    if not text:
        return ""
    import re
    # 块级/换行标签先转成换行
    t = re.sub(r"(?i)</?(?:p|br|div|section|h[1-6])\s*/?>", "\n", text)
    # 剩余任何标签直接删除
    t = re.sub(r"<[^>]+>", "", t)
    # 压缩多余空行
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
