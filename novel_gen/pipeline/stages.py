# -*- coding: utf-8 -*-
"""
各阶段生成器：大纲 / 人物世界观 / 逐章正文。

每个生成器职责单一：构造提示词 → 调 LLM → 解析/归一化 → 落盘。
"""
from __future__ import annotations

import re

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..research.schemas import ResearchReport
from ..store.project import ProjectStore
from ..store.schemas import Chapter, ChapterPlan, Outline, Volume, World
from .memory import Memory
from .style_anchor import extract_personal_anchor
from .beats import (
    _extract_beat_sequence, _beats_rewrite_detected, _beats_local_rewrite_detected,
    _beats_scene_isomorphic, _beat_cycle_issues,
)
from .templates import (
    chapter_prompt,
    outline_prompt,
    world_prompt,
    split_scenes_prompt,
    scene_prompt,
    scene_split_review_prompt,
    polish_chapter_prompt,
    verify_prompt,
    _pick_dynamic_module,
)

_CN_NUM = "一二三四五六七八九十"


def assign_volumes(outline: Outline, target: int) -> None:
    """按 volumes 的 start_ch~end_ch 区间，为无归属章节确定性回填 volume_no。

    规则：
    - 已手动设置（volume_no != 0）的章节保留原值，不覆盖；
    - 覆盖全书 [1, target]：区间内按卷归属；区间外（首卷前/卷间空档/末卷后）
      就近归入最近的分卷，保证不交叉、不留空档、无"未分卷"遗漏；
    - 无 volumes 时不处理（保持 0）。
    """
    vols = sorted((v for v in (outline.volumes or []) if v and v.no), key=lambda v: v.no)
    if not vols:
        return
    mapping: dict[int, int] = {}
    # 1) 区间内：重叠时取 no 较小者（setdefault）
    for v in vols:
        lo, hi = max(1, v.start_ch), min(target, v.end_ch)
        for c in range(lo, hi + 1):
            mapping.setdefault(c, v.no)
    # 2) 区间外补全，避免"未分卷"遗漏
    first_lo = max(1, vols[0].start_ch)
    for c in range(1, first_lo):            # 首卷 start 之前 → 归首卷
        mapping[c] = vols[0].no
    for i in range(len(vols) - 1):          # 卷间空档 → 归前一卷
        cur_end = min(target, vols[i].end_ch)
        nxt_start = max(1, vols[i + 1].start_ch)
        for c in range(cur_end + 1, nxt_start):
            mapping[c] = vols[i].no
    last_end = min(target, vols[-1].end_ch)
    for c in range(last_end + 1, target + 1):  # 末卷之后 → 归末卷
        mapping[c] = vols[-1].no
    # 3) 回填：只填未分配章节
    for ch in outline.chapters:
        if getattr(ch, "volume_no", 0) != 0:
            continue
        ch.volume_no = mapping.get(ch.no, 0)


def unique_chapter_titles(chapters: list) -> list:
    """章节标题唯一化（原地修改并返回）。

    - 空标题 / 占位式"第X章"（纯编号无内容）→ 直接用章节序号重命名（天然唯一）
    - 重名标题 → 后续同名追加（二）（三）…（超 10 次改用阿拉伯数字）
    """
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
        #: 角色底色（prompts/architect.md 等），为空则不拼接
        self.role_prompt = role_prompt

    def _sys(self, system: str) -> str:
        """把角色底色拼到阶段提示词之前；未配置时原样返回。"""
        return f"{self.role_prompt}\n\n{system}" if self.role_prompt else system

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
                print(f"  [大纲分批] 第 {start}—{end} 章 ...")
            system, user = outline_prompt(
                self.cfg,
                research.to_prompt_block(),
                batch=(start, end, prev_summary),
            )
            system = self._sys(system)
            data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
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
        outline = Outline(title=title, intro=intro, core_conflict=core_conflict, main_line=main_line, volumes=volumes, chapters=chapters)
        assign_volumes(outline, target)  # 按分卷区间确定性回填 volume_no
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
        unique_chapter_titles(normalized)  # 章节名全书唯一（重名自动加后缀）
        outline.chapters = normalized
        assign_volumes(outline, self.cfg.num_chapters)  # 按分卷区间确定性回填 volume_no
        return outline


class WorldGenerator:
    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 role_prompt: str = ""):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.role_prompt = role_prompt

    def _sys(self, system: str) -> str:
        return f"{self.role_prompt}\n\n{system}" if self.role_prompt else system

    def run(self, outline: Outline, research: ResearchReport | None, silent: bool = False) -> World:
        system, user = world_prompt(self.cfg, outline, research.to_prompt_block() if research else "")
        system = self._sys(system)
        data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
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
        role_prompt: str = "",
        reviewer_role_prompt: str = "",
        reader_role_prompt: str = "",
        trace=None,
        guard=None,
    ):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.memory = memory
        self.role_prompt = role_prompt
        #: 审校角色底色，供 write() 内部的自动改稿使用
        self.reviewer_role_prompt = reviewer_role_prompt
        self.reader_role_prompt = reader_role_prompt
        #: 模块1/2：埋点与成本防护（由 NovelPipeline 注入）
        self.trace = trace
        self.guard = guard
        #: REQ-双锚风格锚定：拆书库题材锚懒读取缓存（None=未读）
        self._anchor_loaded: str | None = None
        #: REQ-双锚风格锚定：个人旧稿锚懒提取缓存（None=未读）
        self._personal_loaded: str | None = None
        #: P0-7 小说级记忆：懒加载 BookMemory（None=未初始化；关闭/失败 → None 跳过）
        self._book_memory = None
        #: P0-8：最近一章场景累计机制数字基线（_generate_chapter_scenes 写入，
        #: write() 质检透传用；未走场景路径时为空 dict）
        self._scene_state_acc: dict = {}

    def _memory(self):
        """P0-7 懒加载小说级记忆；book_memory 开关关闭/初始化失败 → None（行为降级安全）。"""
        if self._book_memory is None and getattr(self.cfg, "book_memory", True):
            try:
                from .book_memory import BookMemory
                self._book_memory = BookMemory(self.store, self.cfg)
            except Exception:
                self._book_memory = None
        return self._book_memory

    def _book_canon(self, max_chars: int = 800) -> str:
        """P0-7：设定账本注入块（新名词规范/永久纪律/机制基线），空则空串。"""
        mem = self._memory()
        if mem is None:
            return ""
        try:
            return mem.canon_block(max_chars=max_chars)
        except Exception:
            return ""

    def _book_pitfalls(self, top_n: int = 3, min_count: int = 2) -> str:
        """P0-7：本作历史错误（场景生成纪律）注入块——只注入已反复出现（≥2 次）
        的踩坑，避免低频问题过度束缚正常生成。空则空串。"""
        mem = self._memory()
        if mem is None:
            return ""
        try:
            return mem.pitfall_block(top_n=top_n, min_count=min_count)
        except Exception:
            return ""

    def _teardown_anchor(self) -> str:
        """REQ-双锚风格锚定：懒读取拆书库题材锚（webapp 在生成线程启动时写入 store）。
        首次读取后缓存；文件缺失/读取失败返回空串（调用方不注入，行为降级安全）。"""
        if self._anchor_loaded is None:
            self._anchor_loaded = self.store.load_teardown_style_anchor()
        return self._anchor_loaded

    def _personal_anchor(self) -> str:
        """REQ-双锚风格锚定：懒提取个人旧稿锚（用户人工编辑过的章节片段）。
        无人工编辑章返回空串（调用方不注入）；提取失败静默返回空串（不阻塞生成）。"""
        if self._personal_loaded is None:
            try:
                self._personal_loaded = extract_personal_anchor(self.store.list_chapters())
            except Exception:
                self._personal_loaded = ""
        return self._personal_loaded

    def _sys(self, system: str) -> str:
        return f"{self.role_prompt}\n\n{system}" if self.role_prompt else system

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
        # P0-8：每章开始——①机制基线同步进记忆设定账本（蚀痕位置等权威口径）；
        # ②场景计数按章重置（v6 第 4-7 章 review 超 25 次被 guard 拦停一致性校验的根因）
        try:
            _mem = self._memory()
            if _mem is not None:
                _mem.sync_baseline(world)
        except Exception:
            pass
        try:
            if self.guard is not None:
                self.guard.reset_scene_counts()
        except Exception:
            pass
        memory_context = self.memory.build_context(
            world, up_to_chapter=chapter_no, chapter_goal=plan_goal,
        )
        prev_tail = self._prev_tail(chapter_no)

        # 注入时间线提示
        plan_core_event = ""
        plan_moves = ""
        try:
            outline = self.store.load_outline()
            if outline and chapter_no - 1 < len(outline.chapters):
                plan_core_event = outline.chapters[chapter_no - 1].core_event or ""
                # REQ-scene-plan-align：人物动线（逐拍动作序列）注入拆场景规划层，
                # 事件清单从此确定性派生（唯一性最强的骨架不再被丢弃）
                plan_moves = getattr(outline.chapters[chapter_no - 1], "character_moves", "") or ""
                # P2 章末钩子：注入当前章章末钩子（writer 写章末须兑现，防流水账结尾）
                _cliff = getattr(outline.chapters[chapter_no - 1], "cliffhanger", "") or ""
                if _cliff:
                    _hook_block = "【本章章末钩子·本章结尾必须兑现此钩子，制造'想看下一章'的冲动】\n" + _cliff
                    memory_context = memory_context + "\n\n" + _hook_block if memory_context else _hook_block
            if outline and outline.timelines:
                from .templates import timeline_hint_for_chapter
                plan_tl = ""
                if chapter_no - 1 < len(outline.chapters):
                    plan_tl = outline.chapters[chapter_no - 1].timeline
                tl_hint = timeline_hint_for_chapter(outline.timelines, plan_tl)
                if tl_hint:
                    memory_context = memory_context + "\n\n" + tl_hint if memory_context else tl_hint
            # 注入副本上下文
            if outline and outline.units:
                from .templates import unit_hint_for_chapter
                unit_hint = unit_hint_for_chapter(outline.units, chapter_no)
                if unit_hint:
                    memory_context = memory_context + "\n\n" + unit_hint if memory_context else unit_hint
        except Exception:
            pass

        # 注入用户锁定的内容作为硬约束
        try:
            lock_path = self.store.dir / "lock.json"
            if lock_path.exists():
                import json as _json
                _lk = _json.loads(lock_path.read_text(encoding="utf-8"))
                _locked_bits = []
                if _lk.get("world_view") and world and world.world_view:
                    _locked_bits.append("【用户已锁定的世界观，必须严格遵守，不得违背】\n" + world.world_view)
                _locked_chars = (_lk.get("character") or {})
                if _locked_chars and world and world.characters:
                    for _c in world.characters:
                        if _locked_chars.get(getattr(_c, "name", "")):
                            _locked_bits.append(
                                f"【用户已锁定人物「{getattr(_c,'name','')}」，设定不得改动】\n"
                                f"性格:{getattr(_c,'personality','')} 欲望:{getattr(_c,'desire','')} "
                                f"说话方式:{getattr(_c,'speech_style','')}")
                if _locked_bits:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + \
                        "以下为用户锁定的不可违背设定：\n" + "\n".join(_locked_bits)
        except Exception:
            pass

        # P1-1 人物行动表先行：默认并入拆场景输出（REQ-token优化 P0-2，不再独立调用）；
        # 仅当 action_plan_first=True 时恢复独立调用（老项目/显式开启）。
        if getattr(self.cfg, "action_plan_first", False):
            try:
                action_plan = self._gen_action_plan(
                    chapter_no, plan_title, plan_goal, memory_context,
                )
                if action_plan:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + \
                        "【本章人物行动表（写手必须逐条落实：人物言行与行动表一致，剧情不空转、不OOC）】\n" + action_plan
            except Exception as e:
                print(f"  [行动表] 跳过: {e}")

        # 场景级生成：高质量模式始终启用；普通模式单章目标 ≥2000 字自动拆场景（P1-2）
        hq_mode = getattr(self.cfg, "high_quality_mode", False)
        scene_split = getattr(self.cfg, "scene_split", True)
        if hq_mode or (scene_split and self.cfg.chapter_words >= 2000):
            text = self._generate_chapter_scenes(
                chapter_no, plan_title, plan_goal,
                memory_context, prev_tail, is_final,
                dynamic_module_used=hq_mode,
                plan_core_event=plan_core_event,
                plan_moves=plan_moves,
                silent=silent,
            )
            system = ""
        else:
            system, user = chapter_prompt(
                self.cfg,
                chapter_no=chapter_no,
                chapter_title=plan_title,
                chapter_goal=plan_goal,
                research_block=research.to_prompt_block() if research else "",
                memory_context=memory_context,
                prev_tail=prev_tail,
                is_final=is_final,
                chapter_core_event=plan_core_event,
                teardown_anchor=self._teardown_anchor(),
                personal_anchor=self._personal_anchor(),
            )
            system = self._sys(system)
            text = self.llm.chat(system, user, temperature=0.85)
        # 清洗模型偶尔夹带的 HTML 标签（<p>、<br> 等），保证落盘正文是纯文本
        text = _strip_html_tags(text)
        content, summary = _split_summary(text)

        # 字数兜底：正文明显不足目标时，让模型扩写一次（防止整本低于目标字数）
        if not getattr(self.cfg, "high_quality_mode", False):
            content = self._ensure_length(content, plan_title, plan_goal, system)
        content = _strip_html_tags(content)  # 扩写结果同样可能夹带标签
        summary = _strip_html_tags(summary)

        if not summary:
            # 模型漏输出【摘要】时兜底：单独调用一次生成摘要，保证记忆库完整
            summary = self._gen_summary(chapter_no, plan_title, content)
        else:
            # P0-17 摘要一致性校验（2026-10-10，治 v9 ch6 摘要写大纲词「忘忧阁密报」
            # 而正文是不问阁）：模型自带的摘要若含 ≥2 个正文完全没有的 3 字连续词
            # （多为自创专有名词/大纲残留），判为摘要与正文脱节 → 基于正文重生成。
            if content and _summary_invented_words(summary, content) < 0.35:
                _regen_summary = self._gen_summary(chapter_no, plan_title, content)
                if _regen_summary:
                    if not silent:
                        print(f"  [摘要] 模型自带摘要与正文脱节（含正文未出现的 3 字词），"
                              f"基于正文重生成摘要")
                    summary = _regen_summary

        # 高质量模式：自动改稿（小改自动改，中改以上只记录）
        if getattr(self.cfg, 'high_quality_mode', False) and content:
            try:
                from .reviser import ChapterReviser
                reviser = ChapterReviser(self.cfg, self.llm, self.store,
                                         role_prompt=self.reviewer_role_prompt)
                content = reviser.revise(chapter_no, plan_title, plan_goal, content, silent=silent)
            except Exception as e:
                print(f'  [改稿] 跳过: {e}')
            # 读者视角审校（只记录不改正文；P0-10 注入本作记忆 + 三项专项检查）
            try:
                from .reviser import ReaderReviewer
                reader = ReaderReviewer(self.cfg, self.llm, self.store,
                                        role_prompt=self.reader_role_prompt,
                                        memory=self._memory())
                reader.review(chapter_no, plan_title, plan_goal, content)
            except Exception as e:
                print(f'  [读者审校] 跳过: {e}')

        # 模块4：ReaderAgent 内置结果自校验（保存前；可开关、异常降级不阻塞）
        guard_cfg = getattr(self.cfg, "agent_guard", None)
        qc_passed = True
        if guard_cfg is not None and guard_cfg.quality_check and content:
            try:
                from .quality import QualityChecker
                qc = QualityChecker(self.cfg, self.llm, self.store,
                                    trace=self.trace, guard=self.guard,
                                    memory=self._memory())
                # P0-5（2026-10-10）：把场景循环累计的机制数字基线透传给质检层——
                # 重写/改稿产物在 check 内叠加面板方向校验（蚀化回退/次数回升二次兜底）。
                # P0-8（2026-10-10）修复透传 bug：write() 的 locals() 拿不到
                # _generate_chapter_scenes 内部的 scene_state_acc（原实现 _bl 恒为空，
                # 质检层方向校验从未生效）；改为实例属性 self._scene_state_acc。
                _bl: dict = {}
                try:
                    from .chapter_state import panel_baseline_dict as _pbd
                    _bl = _pbd((self._scene_state_acc or {}).get("numbers") or [])
                except Exception:
                    _bl = {}
                chapter, _qc = qc.ensure_quality(
                    chapter_no, plan_title, plan_goal, content, summary,
                    silent=silent, baseline=_bl or None,
                    core_event=plan_core_event or None,
                )
                content, summary = chapter.content, chapter.summary
                qc_passed = bool(_qc.get("passed", True))
                # P0-7 小说级记忆：记录本章评分与问题分布（经验账本）+ 踩坑降权
                # （零 LLM，失败静默不阻塞）
                try:
                    _mem = self._memory()
                    if _mem is not None:
                        _mem.record_taste(chapter_no, int(_qc.get("score", 0) or 0),
                                          _qc.get("issues") or [])
                        _mem.decay(chapter_no)
                except Exception:
                    pass
            except Exception as qe:
                if not silent:
                    print(f"  [自校验] 跳过: {qe}")

        # 方案4（落盘分流）：质检未通过且该章已有旧版 → 保留旧版正文，不覆盖
        # （坏章不得顶掉用户已有的可用版本；仅首次生成/无旧版时才落盘新稿）。
        # 质检结果已由 ensure_quality 落 review.json（passed=false + issues），
        # 前端据 quality 字段显示红标并提示"已保留旧版"。
        kept_previous = False
        if not qc_passed:
            prev = None
            try:
                prev = self.store.load_chapter(chapter_no)
            except Exception:
                prev = None
            if prev is not None and getattr(prev, "content", ""):
                content, summary = prev.content, prev.summary
                kept_previous = True
                if not silent:
                    print(f"  [自校验] 第{chapter_no}章质检未通过（score={_qc.get('score', 0)}），"
                          f"保留上一版正文（{len(content)} 字），不覆盖旧章")

        chapter = Chapter(no=chapter_no, title=plan_title, content=content, summary=summary, goal=plan_goal)
        self.store.save_chapter(chapter)
        # P0-10 机制状态账本：本章末死亡次数/蚀化值确定性入记忆（零 LLM，失败静默）。
        # 治 v7 第 6 章蚀化跨章口径——代码累计入账后，下章拆场景/场景/重写 prompt
        # 注入「蚀化应 = 起始基线 + 累计死亡」确定性锚，LLM 不再自由发挥跨章数字。
        # P0-14（2026-10-10）根因修复：账本 deaths 改「本章末蚀化 - 上一章末蚀化」
        # 蚀化差反推——v8 实证旧版用 death_count 词面计数入账被污染（ch1 重复段落
        # 多计、ch4/ch7 隐式死亡漏计、ch5/ch6 自述面板误计）→ 累计虚高 → 锚正反馈
        # 放大。蚀化差只跟随正文面板链（面板正确性由质检 2.8c「本章死亡必+1、
        # 未死亡必不变」保证），账本与面板永远一致。
        try:
            _mem_st = self._memory()
            if _mem_st is not None:
                from .chapter_state import _EROSION_SEQ_RE as _eseq
                _eros = None
                _mv = [int(_m.group(1)) for _m in _eseq.finditer(content or "")]
                if _mv:
                    _eros = max(_mv)
                _prev_eros = None
                try:
                    _st0 = _mem_st.load().get("state") or {}
                    _prev_ch = [c for c in _st0.get("chapters", [])
                                if int(c.get("ch", 0)) < int(chapter_no)]
                    if _prev_ch:
                        _prev_eros = _prev_ch[-1].get("erosion")
                except Exception:
                    _prev_eros = None
                _deaths = 0
                if _eros is not None and _prev_eros is not None and _eros >= int(_prev_eros or 0):
                    _deaths = _eros - int(_prev_eros or 0)
                # P0-15/16 通用版：注册字段末值链式入账（ledger_extract_numbers 零 LLM
                # 抽取；本章未提及字段由 chapter_ledger 继承上一章末值，治 ch5 断链）
                _nums = {}
                try:
                    from .chapter_state import (mechanism_field_registry as _mreg2,
                                                ledger_extract_numbers as _lex)
                    _canon2 = (_mem_st.load().get("canon") or {})
                    _reg2 = _mreg2(_canon2.get("mechanism_baseline") or {})
                    if _reg2:
                        _nums = _lex(content or "", _reg2)
                except Exception:
                    _nums = {}
                _mem_st.record_chapter_state(chapter_no, deaths=_deaths, erosion=_eros,
                                             numbers=_nums)
        except Exception:
            pass
        # P2-4 场景落盘：拆场景结果 + 场景状态累积写入 store（供回看/编辑/复用；普通模式无 scenes 则跳过）
        try:
            _sc = locals().get("scenes")
            if _sc:
                _ss = locals().get("scene_state_acc") or {}
                self.store.save_scenes(chapter_no, _sc, _ss)
        except Exception:
            pass
        if summary:
            self.store.append_summary(chapter_no, summary)
        if not silent:
            tail = f"｜摘要：{summary}" if summary else "｜（未生成摘要）"
            print(f"  第{chapter_no}章《{plan_title}》完成（{len(content)} 字）{tail}")
        return chapter


    def _polish_blocks(self, sys_p: str, user_p: str, text: str) -> str:
        """润色分块（等效大纲 head+chunks 批量模式）：长文按段落切块逐块润色后拼接。

        一次 max_tokens=4000 写不完 6000 字全文必然截断（DeepSeek 单次输出上限 8192），
        分块后每块输出上限按块长自适应（中文 1 字 ≈ 1.5-2.2 token）。
        """
        if len(text) <= 2600:
            out = self.llm.chat(sys_p, user_p, temperature=0.7,
                                max_tokens=min(8192, max(4000, int(len(text) * 2.2))),
                                frequency_penalty=0.2, presence_penalty=0.1)
            return out or text
        paras = re.split(r"(\n\n)", text)
        blocks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for p in paras:
            if cur_len + len(p) > 2600 and cur:
                blocks.append("".join(cur))
                cur, cur_len = [], 0
            cur.append(p)
            cur_len += len(p)
        if cur:
            blocks.append("".join(cur))
        out: list[str] = []
        for i, blk in enumerate(blocks):
            head = (f"以下是全文第 {i + 1}/{len(blocks)} 段（全文 {len(text)} 字），"
                    "请润色这一段：统一语感、修正重复用词和句式、段落衔接自然；"
                    "不改情节、不增删内容。\n\n")
            res = self.llm.chat(sys_p, head + blk, temperature=0.7,
                                max_tokens=min(8192, max(4000, int(len(blk) * 2.2))),
                                frequency_penalty=0.2, presence_penalty=0.1)
            out.append(res if res else blk)
        return "\n\n".join(out)

    def _fallback_whole_chapter(self, chapter_no, plan_title, plan_goal,
                                memory_context, prev_tail, is_final,
                                plan_core_event, fallback_events, silent=False,
                                panel_baseline: dict | None = None) -> str:
        """拆场景失败 / 场景正文重演重试耗尽的整章回退路径。

        2026-10-01：events 已从拆场景输出救回时注入整章 prompt（拆场景失败不再丢事件）；
        2026-10-09：场景连续 2 次重试仍重演完整循环时同样回退整章——整章单次生成
        无场景间越权消费问题，重演由质检 2.12 情节级检测兜底。
        2026-10-10 P0-8：新增 panel_baseline——回退整章路径生成后同样做面板方向 +
        死亡-结算因果校验（v6 第 6 章蚀化 99→97 跨章回跳正是整章路径绕过基线校验的
        实证），违反 → 带错重试（数字修复回灌），堵"回退路径 = 数字失控通道"。
        """
        from .templates import chapter_prompt
        ev_block = ""
        if fallback_events:
            ev_block = "\n".join(
                f"事件{i + 1}（{e.get('id', '')}）：{e.get('desc', '')}"
                f"{'｜结束时机制状态：' + str(e.get('final_state', '')) if e.get('final_state') else ''}"
                for i, e in enumerate(fallback_events) if isinstance(e, dict) and e.get("desc")
            ) or ""
        # P0-7 小说级记忆：回退整章路径同样注入本作历史错误（重写措辞 + 数字回灌
        # 由 quality 层做；这里注入场景级纪律，防整章单次生成重演本书高频错误）
        _mem = memory_context
        _bmem = self._book_pitfalls(top_n=5, min_count=1)
        if _bmem:
            _mem = (_mem + "\n\n" if _mem else "") + _bmem
        system, user = chapter_prompt(
            self.cfg, chapter_no=chapter_no,
            chapter_title=plan_title, chapter_goal=plan_goal,
            research_block="", memory_context=_mem,
            prev_tail=prev_tail, is_final=is_final,
            chapter_core_event=plan_core_event,
            chapter_events=ev_block,
            teardown_anchor=self._teardown_anchor(),
            personal_anchor=self._personal_anchor(),
        )
        system = self._sys(system)
        # 2026-10-09 生成端兜底（REQ-scene-plan-align §7.3 措施3/5 生成端）：
        # 整章回退路径无场景级实时拦截（场景防线只在场景循环内），生成后立即做
        # 情节级重演（_beat_cycle_issues）+ 字数超限（>1.35×目标）+ P0-8 机制数字
        # 方向/因果校验——命中 → 重试至多 2 次（注入重复节拍证据 + 压缩字数目标 +
        # 数字修复回灌），仍命中 → 返回交给质检 2.12 兜底（见 §7.4）。
        _text = self.llm.chat(system, user, temperature=0.85,
                              frequency_penalty=0.3, presence_penalty=0.15) or ""
        for _try in (1, 2):
            _text = _strip_html_tags(_text)
            _issues = _beat_cycle_issues(_text)
            _over = len(_text) > max(self.cfg.chapter_words, 500) * 1.35
            _viols: list[str] = []
            if _text and panel_baseline:
                try:
                    from .chapter_state import (panel_direction_violations as _pdv,
                                                panel_causal_violations as _pcv,
                                                death_signals as _deathsig,
                                                book_death_signals as _bdeathsig)
                    _ev_text = " ".join(
                        str(e.get(k) or "") for e in (fallback_events or [])
                        for k in ("desc", "conflict", "final_state")
                    )
                    _sigs = self._memory().book_signals() if self._memory() is not None else []
                    # P0-14 修正：通用标准（词面）OR 个性化标准（本书机制词）——两套独立
                    _death = _deathsig(_text) or _bdeathsig(_text, _sigs) or \
                        _deathsig(_ev_text) or _bdeathsig(_ev_text, _sigs)
                    _viols = _pdv(_text, panel_baseline)
                    if _death:
                        _viols = _viols + _pcv(_text, panel_baseline, True)
                except Exception:
                    _viols = []
            # P0-14（2026-10-10）：字数超限不再触发整章回退重写——用户约束"如果生成的
            # 质量过关，就不需要整章回退重写"（v8 实证 ch5 3680/ch7 3682 字数超但质量
            # 好，回退纯浪费 token；历轮重写越改越差教训）。字数仅作附注展示。
            if not _issues and not _viols:
                break
            if not silent:
                _why = ("情节级重演：" + str(_issues[0]["detail"]) if _issues else "") + \
                       (f"；字数超限（{len(_text)} 字）" if _over else "") + \
                       (f"；机制数字违规（{_viols[0]}）" if _viols else "")
                print(f"  [回退整章] 第{chapter_no}章生成后检出{_why}，重试第{_try}次")
            _dedup_parts = []
            if _issues:
                _dedup_parts.append(
                    "1. 上文【本章事件序列】中的每个事件只允许完整发生一次；上一版把同一情节"
                    "（死亡-复活-对峙-包扎-离场等完整循环）演绎了两遍，检测到重复节拍："
                    f"『{_issues[0].get('detail', '')}』——本次必须删除重复版本，保留最完整的一个，"
                    "被删版本中不可替代的信息（关键对话/关键状态）合并进保留版本\n"
                    "2. 事件之间必须因果推进，后一事件从上一事件结局之后开始，禁止回到上一事件开头状态重演\n"
                    "3. 每个事件结束状态（面板/蚀痕/伤势）必须比上一事件结束时有增量变化\n"
                )
            if _over:
                _dedup_parts.append(
                    f"4. 正文控制在约 {self.cfg.chapter_words} 字（±15%），禁止超写注水——"
                    "宁精炼也禁止把同一情节写两遍凑字数\n"
                )
            if _viols:
                _viol_text = "\n".join(f"- {v}" for v in _viols[:4])
                _dedup_parts.append(
                    f"5. 机制数字（面板/蚀化等）必须按基线确定性结算，上一条正文违反：\n"
                    f"{_viol_text}\n——本次按上述修正值改，禁止再犯同类数字错误\n"
                )
            _dedup = ("\n\n【整章回退重试强制规则（上一条输出未通过检测，已作废）】\n"
                      + "\n".join(_dedup_parts)) if _dedup_parts else ""
            _text = self.llm.chat(system, user + _dedup, temperature=0.85,
                                  frequency_penalty=0.4, presence_penalty=0.2) or ""
        return _text

    def _generate_chapter_scenes(self, chapter_no, plan_title, plan_goal,
                                 memory_context, prev_tail, is_final,
                                 dynamic_module_used: bool = True,
                                 plan_core_event: str = "",
                                 plan_moves: str = "",
                                 silent: bool = False):
        """场景级生成公共链路（P1-2）：拆场景 → 逐场景 → 润色。

        高质量模式（dynamic_module_used=True）额外注入动态模块与参数分层；
        普通模式只启用拆场景→逐场景→润色基础链路。拆场景失败自动回退整章生成。

        REQ-scene-plan-align：plan_moves 非空且开关开启时，事件清单由大纲 character_moves
        确定性派生（_derive_events_from_moves），模型只做场景切分与细节补充——
        杜绝"每次生成事件清单不一样 + 同一冲突拆成多个版本"。
        """
        import json
        from .templates import (
            split_scenes_prompt, scene_prompt, polish_chapter_prompt,
            _pick_dynamic_module,
        )

        # 1. 拆场景（低温 0.4；REQ-token优化 P0-2：顺带产出本章人物行动表，不再独立调用）
        action_plan_block = ""
        fallback_events: list = []   # 拆场景失败但 events 可救回时，注入回退整章生成
        # REQ-scene-plan-align：从大纲 character_moves 确定性派生事件清单（硬骨架）。
        # 派生成功 → 模型无权增删/改写事件，只做补字段 + 拆场景；失败 → 回退原 LLM 规划路径。
        derived_events: list = []
        skeleton_arg = ""
        if getattr(self.cfg, "scene_plan_align", True) and plan_moves:
            derived_events = _derive_events_from_moves(plan_moves)
            if derived_events:
                # 骨架文本：编号 + desc 逐条列出，明示事件数与"场景数=事件数"约束
                skeleton_arg = f"共 {len(derived_events)} 个事件，场景数必须等于事件数：\n" + \
                    "\n".join(f"{e['id']}：{e['desc']}" for e in derived_events)
        try:
            # P0-10 拆场景层注入机制数字锚（蚀化当前应 = 起始基线 + 截至上一章累计死亡），
            # 规划事件 final_state 时模型据此写机制状态增量，不自造跨章口径
            _split_anchor = ""
            try:
                _msplit = self._memory()
                if _msplit is not None:
                    _split_anchor = _msplit.erosion_anchor(max(0, int(chapter_no) - 1))
            except Exception:
                _split_anchor = ""
            sys_s, user_s = split_scenes_prompt(
                self.cfg, chapter_no, plan_title, plan_goal,
                prev_tail=prev_tail, core_event=plan_core_event,
                moves=skeleton_arg,
                book_canon=self._book_canon() + (_split_anchor if _split_anchor else ""),
            )
            raw = self.llm.chat(sys_s, user_s, temperature=0.4)
            split_data = None
            # P0-4：仅当「整体 JSON 解析失败」（尾部截断证据）且救回 characters_action
            # 为空时才触发单字段补全；整体解析成功但 characters_action 为空数组 =
            # 模型真未规划（合法），不补全（避免无谓的额外 LLM 调用）。
            _ca_needs_fill = False
            try:
                split_data = json.loads(_strip_json_block(raw))
            except Exception:
                split_data = None
            if not isinstance(split_data, dict):
                # 2026-10-01 拆场景 JSON 健壮化：整体解析失败 → 字段级兜底
                # （events 在前、scenes 在中，长输出截断通常只丢 characters_action）。
                # 能救回 scenes 就继续走场景拆分；救不回就重试 1 次（温度升 0.5），
                # 仍失败才回退整章——回退前保留可解析的 events 供整章 prompt 注入
                # （防"拆场景失败 → 救人事件丢失/重复"整条事前防线失效）。
                scenes_fb = _extract_json_field(raw, "scenes")
                events_fb = _extract_json_field(raw, "events")
                if scenes_fb or events_fb:
                    split_data = {}
                    if scenes_fb:
                        split_data["scenes"] = scenes_fb
                    if events_fb:
                        split_data["events"] = events_fb
                    split_data["characters_action"] = _extract_json_field(raw, "characters_action")
                    if not split_data.get("characters_action"):
                        _ca_needs_fill = True
                else:
                    if not silent:
                        print("  [拆场景] JSON 解析失败，重试 1 次（温度 0.5）...")
                    raw2 = self.llm.chat(
                        sys_s,
                        user_s + "\n\n再次提醒：只输出一个合法的 JSON 对象，"
                        "不要输出任何代码块标记、注释、省略号或尾逗号。",
                        temperature=0.5,
                    )
                    try:
                        split_data = json.loads(_strip_json_block(raw2))
                    except Exception:
                        split_data = _extract_json_field(raw2, "scenes") and {
                            "scenes": _extract_json_field(raw2, "scenes"),
                            "events": _extract_json_field(raw2, "events"),
                            "characters_action": _extract_json_field(raw2, "characters_action"),
                        }
                        if not split_data:
                            split_data = None
                        elif not split_data.get("characters_action"):
                            _ca_needs_fill = True
            if isinstance(split_data, dict):
                fallback_events = split_data.get("events") or []
                scenes = split_data.get("scenes", [])
                ca = split_data.get("characters_action") or []
                # P0-4 characters_action 单字段补全（2026-10-09）：
                # 大 JSON 长输出截断只丢尾部 characters_action 时（_extract_json_field
                # 救回为空/损坏），单独让模型只输出行动表数组——措辞层约束真正生效。
                if not ca and _ca_needs_fill and scenes:
                    if not silent:
                        print("  [行动表] characters_action 截断救回失败，单字段补全调用...")
                    ca = self._fill_characters_action(
                        chapter_no, plan_title, plan_goal,
                        plan_core_event, fallback_events, prev_tail, silent=silent,
                    )
                    if not silent and ca:
                        print(f"  [行动表] 补全成功，行动表注入 {len(ca)} 条（措辞层约束生效）")
                action_plan_block = _format_action_plan(ca or [])
                if not silent:
                    print(f"  [拆场景] 规划通过：{len(scenes)} 个场景，"
                          f"{len(fallback_events)} 个事件")
            # 2026-09-30 拆场景设计重构（用户指出"一直在打补丁、根因在场景拆分设计"）：
            # 场景拆分 = 事件分配——LLM 先规划互斥事件清单 events，场景 scene_event_ref 逐字
            # 引用事件编号。这里把引用展开为事件原文（sc["event"]），互斥由「事件清单两两
            # 不同 + 引用唯一性 + 场景数/字数校验」结构化保证，不再依赖文本相似度猜重叠
            # （6316 字版"苏妄言被围"被拆成 5 个换措辞场景即旧校验盲区）。
            events_list = split_data.get("events") or [] if split_data else []
            # REQ-scene-plan-align：派生模式下事件清单以大纲 character_moves 为准（确定性），
            # desc/id 以骨架为准（模型无权增删/改写事件）；模型输出同 id 事件的结构化字段
            # （conflict/action/final_state，尤其机制数字增量）按 id 合并保留——
            # 骨架锁住"事件是什么"，模型只补"冲突对象/动作/机制状态增量"。
            # 回退整章注入的事件序列同样用合并后事件（防丢事件/换措辞重演）。
            if derived_events:
                model_by_id = {
                    str(e.get("id") or ""): e
                    for e in events_list if isinstance(e, dict) and e.get("id")
                }
                events_list = []
                for de in derived_events:
                    de = dict(de)
                    mo = model_by_id.get(str(de.get("id") or ""))
                    if mo:
                        for k in ("conflict", "action", "final_state"):
                            if mo.get(k):
                                de[k] = mo[k]
                    events_list.append(de)
                fallback_events = events_list
            _expand_scene_event_refs(scenes, events_list)
            # P0-10 拆场景评审（混合方案①，2026-10-10）：独立评审 agent 判事件
            # 语义同构 → 代码确定性合并。治 v7 ch5 药铺三重拼接规划层根因——
            # desc 相似度预筛（>0.55）对「查药粉/诊脉/探脉枕」三个不同原文不命中，
            # 需语义级判断；派生模式 desc 是骨架原文（合并会破坏 id 对应）→ 只告警。
            if not derived_events and events_list and scenes and len(events_list) >= 2:
                _pairs = self._scene_split_review(
                    chapter_no, plan_title, plan_goal, events_list, scenes, silent
                )
                if _pairs:
                    _merged = _merge_duplicate_events(events_list, scenes, _pairs)
                    if _merged:
                        events_list, scenes = _merged
                        fallback_events = events_list
            # P1-1 场景事件边界互斥 + 结构校验（场景数≤4、target_words 和∈±10%、
            # 事件引用唯一）+ 2026-10-01 事件语义槽位互斥（同冲突对象+同动作=重复，
            # 防"救苏妄言"拆成石殿版/墟口外版两场景）+ P0-17 大纲核心事件覆盖校验：
            # 不合格 = 同一事件拆成两场景（同事件两版本拼接源头），回退整章生成
            ok_plan, why_bad = _validate_scene_plan(
                scenes, int(getattr(self.cfg, "chapter_words", 0) or 0),
                core_event=str(plan_core_event or ""),
            )
            # REQ-scene-plan-align：派生模式下事件互斥改用 desc（骨架原文）相似度兜底——
            # 模型补充的 conflict/action 可能把同一人物交互的递进拍标成同动作（如"扫描/
            # 装傻"都标"试探"），槽位检查会误杀回退；desc 是骨架逐拍原文，互斥由"两拍
            # 原文明显不同"保证，相似度 >0.55 才视为重复（防大纲拍本身重复）。
            if derived_events:
                import difflib as _difflib
                _norms = [_norm_scene_event(e.get("desc") or "") for e in events_list]
                sem_dup = False
                for _i in range(len(_norms)):
                    for _j in range(_i + 1, len(_norms)):
                        if _norms[_i] and _norms[_j] and _difflib.SequenceMatcher(
                            None, _norms[_i], _norms[_j]
                        ).ratio() > _SCENE_EVENT_OVERLAP_RATIO:
                            sem_dup = True
                            break
                    if sem_dup:
                        break
            else:
                sem_dup = _scene_events_semantically_overlap(events_list)
            if scenes and not ok_plan:
                if not silent:
                    print(f"  [拆场景] 场景规划校验不通过（{why_bad}），回退整章生成")
                scenes = []
            elif sem_dup:
                if not silent:
                    print("  [拆场景] 事件清单语义槽位重叠（同冲突对象+同动作=重复事件），"
                          "回退整章生成")
                scenes = []
            elif derived_events and scenes:
                # REQ-scene-plan-align：派生模式下场景 scene_event_ref 必须恰好覆盖派生
                # 事件 id 集合（无遗漏/无重复/无新增）。模型自造 id、漏引用、重复引用
                # 均视为不遵守骨架 → 回退整章（整章路径仍注入派生事件序列兜底）。
                refs = {str(sc.get("scene_event_ref") or "").strip() for sc in scenes if isinstance(sc, dict)}
                derived_ids = {str(e.get("id") or "").strip() for e in derived_events}
                if refs != derived_ids:
                    if not silent:
                        print(f"  [拆场景] 场景事件引用与派生事件骨架不一致"
                              f"（refs={sorted(refs)} vs derived={sorted(derived_ids)}），回退整章生成")
                    scenes = []
        except Exception:
            scenes = []

        if not scenes:
            # 拆场景失败 / 场景正文重演重试耗尽，回退到普通整章生成（逻辑见下方方法）。
            # P0-8：整章回退路径补基线校验——self._scene_state_acc 此时仍是上一章章末
            # 数字（本章场景循环尚未写入），恰为"本章开始权威基线"（v6 第 6 章蚀化
            # 99→97 回跳即整章路径绕过基线校验的实证）。
            return self._fallback_whole_chapter(
                chapter_no, plan_title, plan_goal,
                memory_context, prev_tail, is_final,
                plan_core_event, fallback_events, silent,
                panel_baseline=self._scene_state_acc or None)

        # 2. 逐场景生成（中温 0.7 + penalty）
        genre = getattr(self.cfg, "genre", "") or "default"
        dynamic = _pick_dynamic_module(chapter_no, genre) if dynamic_module_used else ""
        scene_texts = []
        running_tail = prev_tail
        # 本章人物行动表（拆场景产出）拼入每个场景的记忆上下文
        mem_for_scene = memory_context
        if action_plan_block:
            mem_for_scene = (mem_for_scene + "\n\n" if mem_for_scene else "") + \
                "【本章人物行动表（全章总体动线：每个场景只落实与本场景唯一事件直接相关的行动，" \
                "禁止在多个场景重复落实同一行动/同一节拍，其余行动由后续场景落实）】\n" + action_plan_block
        # REQ-章内自洽与机制账本：场景间累积「已推进状态」（面板数字/人物生死/物品/时间），
        # 注入后续场景，防「三次死亡只减两次数字」「疤脸死而复生」等章内状态断裂。
        scene_state_acc: dict = {}
        scene_state_hint: str = ""
        # 2026-09-30（跨场景事件重复根因修复）：已推进状态只含数字/生死/物品，不含「事件」——
        # 场景2写完"救苏妄言"后场景3不知道事件已发生，把同一事件换个地点又写一遍
        # （《万墟之上》22:32 版空地版/坡地版双版本）。这里把本章已规划场景事件做成
        # 确定性事件清单，逐场景累积注入后续场景，零 LLM 成本。
        done_events: list[str] = []
        # P0-1 事件消费账本状态机（2026-10-09 生成端第三轮，对标 AgentWrite $STEP$ 指针制）：
        # 每个规划事件登记 {desc, consumed_scene_idx}；场景生成前把「已消费事件」与
        # 「后续事件预览」注入 prompt（已消费只许一句话回响、后续只许铺垫提及，
        # 禁提前消费完整流程）——治"跨场景分布型重演"（事件无 consumed 标记 +
        # 模型对已写事件仍有完整执行权，decisions.md 2026-10-09 §7.2 根因 A）。
        event_ledger: list[dict] = []
        # 2026-10-09 正文级节拍序列登记（REQ-scene-plan-align §7.3 措施3）：
        # done_events 只登记「规划事件」（sc.event），登记不了"场景正文实际越权消费"的
        # 后续事件；这里把每个场景正文的节拍序列（_extract_beat_sequence）登记为正文级
        # 指纹，后续场景与之比对——场景1越权写完事件2~4 后，场景2~4 直接感知并拦截。
        done_beat_seqs: list[list[str]] = []
        # P0-10 跨场景同构检测（治 v7 第 5 章药铺三重拼接）：历史场景序列全集。
        # 与 done_beat_seqs（供整序列/局部子序列 LCS，4 拍阈值）互补——单遍 3 拍
        # 核心节拍（诊脉→药包→离场）写 3 遍时 4 拍阈值全部漏检，本账本按
        # 「3 拍 + 非通用拍 ≥2」判同构场景。每章重置（跨章由事件账本/蚀化锚管）。
        scene_beat_ledger: list[list[str]] = []
        # 2026-10-09 生成端事前约束：已发生节拍序列文本（注入后续场景 prompt，
        # 让模型知道前面场景已写过哪些节拍组合，禁止再演）——事后比对升级为事前告知。
        done_beats_text: str = ""
        # REQ-token优化 P1-2：场景 prompt 分层注入——首场景注入全量记忆，后续场景只注入
        # 增量块（已推进状态独立参数 + 行动表），不重复全量 memory_context，降每章输入 token。
        scene_delta_on = getattr(self.cfg, "scene_delta_inject", True)
        # P0-7 小说级记忆：本作历史错误（已反复出现 ≥2 次的踩坑）注入场景 prompt，
        # 在生成点抑制已知高频错误（换措辞重演/数字回退等）；空账本 → 空串不注入。
        book_pitfalls_text = self._book_pitfalls()
        # P0-8 小说级记忆：机制基线注入块（蚀痕位置等权威口径；拆场景已注入 canon 全文，
        # 场景层只注入机制基线，避免 terms/disciplines 重复刷屏）
        book_canon_text = ""
        # P0-10 机制数字确定性锚：蚀化当前应 = 起始基线 + 截至上一章累计死亡（代码计算，
        # 治 v7 第 6 章蚀化跨章口径）。本章自身死亡由场景防线 panel_causal_violations 管。
        state_anchor_text = ""
        try:
            _mem = self._memory()
            if _mem is not None:
                book_canon_text = _mem.baseline_block()
                state_anchor_text = _mem.erosion_anchor(max(0, int(chapter_no) - 1))
        except Exception:
            book_canon_text, state_anchor_text = "", ""
        for idx, sc in enumerate(scenes, start=1):
            # REQ-scene-plan-align §7.3 措施1：事件进度硬约束——本章共 N 个事件/场景，
            # 本场景只允许推进本场景唯一事件，禁止提前写后续事件完整流程（防"越权消费"）
            event_progress = f"本章共 {len(scenes)} 个事件/场景，本场景为第 {idx}/{len(scenes)} 个。"
            if idx == 1 or not scene_delta_on:
                mem_for_this = mem_for_scene
            elif action_plan_block:
                mem_for_this = ("【前情记忆】已在第1个场景完整注入，本场景不再重复全文。\n"
                                "【本章人物行动表（仅参考：本场景只落实与『本场景唯一事件』直接相关的行动，"
                                "禁止跨场景重复落实同一行动/同一节拍，其余行动由后续场景落实）】\n" + action_plan_block)
            else:
                mem_for_this = "【前情记忆】已在第1个场景完整注入，本场景不再重复全文。"
            # P0-1 事件消费账本注入：已消费事件（只许回响）/ 后续事件预览（禁提前消费）
            consumed_events_text = "\n".join(
                f"- {e.get('desc','')}" for e in event_ledger if e.get("desc")
            )
            upcoming_events_text = "\n".join(
                f"- {str(s.get('event') or '').strip()}"
                for s in scenes[idx:] if str(s.get("event") or "").strip()
            )
            # P0-3 面板数字确定性闭环：注入本场景开始时的权威基线（上一场景结算值）
            panel_baseline_text = ""
            _panel_base: dict = {}
            try:
                from .chapter_state import panel_baseline_dict as _pbd
                _panel_base = _pbd((scene_state_acc or {}).get("numbers") or [])
                if _panel_base:
                    panel_baseline_text = "\n".join(
                        f"- {k}：{v}" for k, v in _panel_base.items()
                    )
            except Exception:
                _panel_base, panel_baseline_text = {}, ""
            # P0-15/16 通用版：机制字段白名单注入（closed-world）——基线声明的字段
            # 才允许出现在正文面板；禁止自造字段（累计死亡次数类）；次数类字段为 0
            # 时本章不得再安排死亡（基线未声明该边界行为）。
            try:
                _mem_r = self._memory()
                if _mem_r is not None:
                    from .chapter_state import mechanism_field_registry as _mreg3
                    _canon3 = (_mem_r.load().get("canon") or {})
                    _reg3 = _mreg3(_canon3.get("mechanism_baseline") or {})
                    if _reg3:
                        panel_baseline_text += (
                            "\n\n【机制面板字段白名单（硬约束）】正文面板只允许出现以下"
                            f"字段（{sorted(_reg3)}）；禁止出现基线未声明的面板字段（如"
                            "「累计死亡次数」「死亡总次数」等自造标签）；字段格式必须为"
                            "「字段名：数字」（带分母的「0/3」写法禁止）。"
                        )
                        if any(k for k in _reg3 if k.endswith("次数") or "剩余" in k):
                            panel_baseline_text += (
                                "\n【边界规则】上述次数/剩余类字段为 0 时，本章不得再发生"
                                "死亡结算事件（次数用尽后死亡=违规）；如本章确有死亡，必须"
                                "先声明字段为 1 以上。"
                            )
            except Exception:
                pass
            sys_c, user_c = scene_prompt(
                self.cfg, chapter_no, plan_title, idx, sc,
                mem_for_this, running_tail, dynamic,
                core_event=plan_core_event,
                scene_state_hint=scene_state_hint,
                teardown_anchor=self._teardown_anchor(),
                personal_anchor=self._personal_anchor(),
                event_progress=event_progress,
                done_beats=done_beats_text,
                consumed_events=consumed_events_text,
                upcoming_events=upcoming_events_text,
                panel_baseline=panel_baseline_text,
                book_pitfalls=book_pitfalls_text,
                book_canon=book_canon_text,
                state_anchor=state_anchor_text,
            )
            sys_c = self._sys(sys_c)
            try:
                # 场景输出上限按目标字数自适应：中文 1 字 ≈ 1.5-2.2 token，
                # 固定 2000 会在长场景中途截断（《万墟之上》6202 字版截断根因之一）
                _target_w = int(sc.get("target_words") or 1200)
                piece = self.llm.chat(
                    sys_c, user_c,
                    temperature=0.7,
                    max_tokens=max(4000, min(8192, int(_target_w * 2.2))),
                    frequency_penalty=0.3,
                    presence_penalty=0.15,
                )
                piece = _strip_html_tags(piece)
                # 2026-09-30 场景重演兜底（生成端）：场景 N 与上一场景全文高度相似
                # = 模型无视衔接把上一场景整段复刻（6316 字版"灰卒咬喉死"原样重复根因）。
                # 2026-10-01 升级：全文相似度对"局部复刻"漏检（4500 字版死亡复刻段约 200
                # 字嵌进场景 2、全文 ratio<0.55），叠加长段复刻率（与上一场景完全相同的
                # ≥15 字句子占比 >20%）→ 重试本场景 1 次（替换场景 prompt 强制从结局后续
                # 写），而非事后整章重写——拦截在拼接发生点，省整章重写 token。
                if idx > 1 and piece and scene_texts:
                    import difflib
                    prev_text = scene_texts[-1]
                    _ratio = 0.0
                    _span_ovlp = 0.0
                    if prev_text:
                        _ratio = difflib.SequenceMatcher(None, _norm_scene_event(prev_text),
                                                         _norm_scene_event(piece)).ratio()
                        _span_ovlp = _long_sentence_overlap_ratio(prev_text, piece)
                    if _ratio > _SCENE_ADJACENT_REPEAT_RATIO or _span_ovlp > _SCENE_ADJACENT_SPAN_RATIO:
                        if not silent:
                            print(f"  [场景] 第{idx}个场景与上一场景高度相似"
                                  f"（全文 {_ratio:.2f} / 长段复刻 {_span_ovlp:.2f}），"
                                  f"疑似重演上一场景，重试 1 次强制从上一场景结局后续写")
                        sys_c2, user_c2 = scene_prompt(
                            self.cfg, chapter_no, plan_title, idx, sc,
                            mem_for_this, running_tail, dynamic,
                            core_event=plan_core_event,
                            scene_state_hint=scene_state_hint,
                            force_new_progression=True,
                            done_beats=done_beats_text,
                            consumed_events=consumed_events_text,
                            upcoming_events=upcoming_events_text,
                            panel_baseline=panel_baseline_text,
                            book_pitfalls=book_pitfalls_text,
                            book_canon=book_canon_text,
                            state_anchor=state_anchor_text,
                        )
                        retry = self.llm.chat(
                            self._sys(sys_c2), user_c2,
                            temperature=0.7,
                            max_tokens=max(4000, min(8192, int(_target_w * 2.2))),
                            frequency_penalty=0.35,
                            presence_penalty=0.2,
                        )
                        retry = _strip_html_tags(retry)
                        _r2 = 0.0
                        _s2 = 0.0
                        if retry:
                            _r2 = difflib.SequenceMatcher(
                                None, _norm_scene_event(prev_text),
                                _norm_scene_event(retry)).ratio()
                            _s2 = _long_sentence_overlap_ratio(prev_text, retry)
                        if _s2 < _span_ovlp and _s2 <= _SCENE_ADJACENT_SPAN_RATIO:
                            piece = retry
                # 2026-10-01 跨场景同事件复写检测（治本，非打补丁）：
                # 语义互斥只校验拆场景 events 清单，拦不住"场景生成时模型把已发生事件
                # 换措辞又写一遍"（第三版坡顶救人/墟口外救人：conflict 措辞不同→
                # 文本相似度与事件清单双漏检）。对刚生成的场景正文做事件槽位提取
                # （冲突对象+救援动作），与全部已完成事件比对——命中=复写已发生事件，
                # 重试本场景（重试提示含被重写事件原文，禁止再次出现）。
                if piece and done_events:
                    dup_ev = _scene_text_rewrites_event(piece, done_events)
                    if dup_ev:
                        if not silent:
                            print(f"  [场景] 第{idx}个场景复写了已发生事件"
                                  f"（{dup_ev[:50]}），重试强制只写本场景唯一事件")
                        sys_c3, user_c3 = scene_prompt(
                            self.cfg, chapter_no, plan_title, idx, sc,
                            mem_for_this, running_tail, dynamic,
                            core_event=plan_core_event,
                            scene_state_hint=scene_state_hint,
                            force_new_progression=True,
                            forbid_event=dup_ev,
                            consumed_events=consumed_events_text,
                            upcoming_events=upcoming_events_text,
                            panel_baseline=panel_baseline_text,
                            book_pitfalls=book_pitfalls_text,
                            book_canon=book_canon_text,
                            state_anchor=state_anchor_text,
                        )
                        retry3 = self.llm.chat(
                            self._sys(sys_c3), user_c3,
                            temperature=0.7,
                            max_tokens=max(4000, min(8192, int(_target_w * 2.2))),
                            frequency_penalty=0.35,
                            presence_penalty=0.2,
                        )
                        retry3 = _strip_html_tags(retry3)
                        if retry3 and not _scene_text_rewrites_event(retry3, done_events):
                            piece = retry3
                # 2026-10-09 情节节拍序列重演检测（REQ-scene-plan-align §7.3 措施3/措施5）：
                # 换措辞"完整循环"重演（盘问→戳穿→失控→挡死→死亡→复活→面板→留牌→离场）
                # 文本相似度/救援动词防线全漏检（§7.2 根因 C/D/E），用节拍序列指纹比对；
                # 触发条件三重叠加：
                #  ① _beat_cycle_issues(piece)：本场景正文自身内部循环（同一节拍序列≥2次，
                #     堵"场景1内部写两遍"盲区——done_beat_seqs 为空时无比对对象）；
                #  ② _beats_rewrite_detected：整序列 LCS 重合（完整循环重演）；
                #  ③ _beats_local_rewrite_detected：局部子序列重合（重试稿压缩重演段/插序逃逸兜底）。
                # 命中 → 重试（forbid_event=本场景唯一事件，强制只推进本场景事件）；
                # 同一场景连续 2 次重试仍重演 → 整章回退（整章单次生成无场景间越权消费）。
                _inner_issue = _beat_cycle_issues(piece) if piece else None
                _seq = _extract_beat_sequence(piece) if piece else []
                _dup_beat = bool(
                    _seq and done_beat_seqs
                    and (_beats_rewrite_detected(_seq, done_beat_seqs)
                         or _beats_local_rewrite_detected(_seq, done_beat_seqs))
                )
                # P0-10：跨场景同构（治 v7 ch5 药铺三重拼接）——单遍 3 拍核心节拍
                # 换措辞写多遍时，整序列/局部子序列（4 拍阈值）漏检，按同构判据拦截
                _iso_scene = bool(
                    _seq and scene_beat_ledger
                    and _beats_scene_isomorphic(_seq, scene_beat_ledger)
                )
                if _inner_issue or _dup_beat or _iso_scene:
                    _beat_ok = False
                    for _try in (1, 2):
                        if not silent:
                            _why = "本场景内部循环重演" if _inner_issue else "与已发生场景节拍重合"
                            print(f"  [场景] 第{idx}个场景{_why}"
                                  f"（{'→'.join(_seq)}），疑似换措辞重演完整循环，"
                                  f"重试第{_try}次强制只写本场景唯一事件")
                        _ev_desc = str(sc.get("event") or "").strip() or "本场景唯一事件"
                        sys_c4, user_c4 = scene_prompt(
                            self.cfg, chapter_no, plan_title, idx, sc,
                            mem_for_this, running_tail, dynamic,
                            core_event=plan_core_event,
                            scene_state_hint=scene_state_hint,
                            force_new_progression=True,
                            forbid_event=_ev_desc,
                            event_progress=event_progress,
                            done_beats=done_beats_text,
                            consumed_events=consumed_events_text,
                            upcoming_events=upcoming_events_text,
                            panel_baseline=panel_baseline_text,
                            book_pitfalls=book_pitfalls_text,
                            book_canon=book_canon_text,
                            state_anchor=state_anchor_text,
                        )
                        retry4 = self.llm.chat(
                            self._sys(sys_c4), user_c4,
                            temperature=0.7,
                            max_tokens=max(4000, min(8192, int(_target_w * 2.2))),
                            frequency_penalty=0.4,
                            presence_penalty=0.25,
                        )
                        retry4 = _strip_html_tags(retry4)
                        _seq4 = _extract_beat_sequence(retry4) if retry4 else []
                        _inner4 = _beat_cycle_issues(retry4) if retry4 else None
                        _dup4 = bool(
                            _seq4 and done_beat_seqs
                            and (_beats_rewrite_detected(_seq4, done_beat_seqs)
                                 or _beats_local_rewrite_detected(_seq4, done_beat_seqs))
                        )
                        _iso4 = bool(
                            _seq4 and scene_beat_ledger
                            and _beats_scene_isomorphic(_seq4, scene_beat_ledger)
                        )
                        if retry4 and not _inner4 and not _dup4 and not _iso4:
                            piece = retry4
                            _beat_ok = True
                            break
                    if not _beat_ok:
                        if not silent:
                            print(f"  [场景] 第{idx}个场景连续 2 次重试仍重演完整循环，回退整章生成")
                        # P0-8：回退整章补基线校验——用本章场景累计数字（已推进场景的
                        # 结算值）做权威基线，整章正文数字必须从这些值继续单调推进
                        return self._fallback_whole_chapter(
                            chapter_no, plan_title, plan_goal,
                            memory_context, prev_tail, is_final,
                            plan_core_event, fallback_events, silent,
                            panel_baseline=scene_state_acc or None)
                # P0-3 面板数字确定性方向校验（2026-10-09 生成端第三轮，治面板账本崩坏）：
                # 正文中机制数字与基线比对（次数/剩余只许递减、蚀化/进度只许递增），
                # 违反 → 带错重试（重试 prompt 注入基线 + 违规描述），同一场景至多 2 次；
                # 仍违反用最后一次（不做整章回退——质检 2.x 仍会二次拦截）。
                # P0-15/16 通用版（2026-10-10）：叠加「机制字段注册表」未知字段检测——
                # 基线没声明的面板字段（如「累计死亡次数」）出现即违规，任何书通用。
                if piece and _panel_base:
                    try:
                        from .chapter_state import (panel_direction_violations as _pdv,
                                                    panel_causal_violations as _pcv,
                                                    death_signals as _deathsig,
                                                    book_death_signals as _bdeathsig,
                                                    mechanism_field_registry as _mreg,
                                                    unknown_mechanism_fields as _unk)
                        _reg = {}
                        try:
                            _mem0 = self._memory()
                            if _mem0 is not None:
                                _canon = (_mem0.load().get("canon") or {})
                                _reg = _mreg(_canon.get("mechanism_baseline") or {})
                        except Exception:
                            _reg = {}
                        # P0-8 死亡-结算因果校验（v6 实证：第 2 章死亡后次数 2→0 超扣、
                        # 第 3 章死亡后蚀化 98→98 未递增、第 7 章死亡后次数 1→1 死而不扣——
                        # 方向校验只报"回退/回升"，"未变化/超扣"不触发）。死亡信号 =
                        # 正文显式死亡表达 OR 本场景事件层信号（event/conflict/final_state，
                        # 覆盖正文隐式死亡"很冷。然后是黑"式）。
                        _ev_text = " ".join(
                            str(sc.get(k) or "") for k in ("event", "conflict", "final_state")
                        )
                        _sigs = self._memory().book_signals() if self._memory() is not None else []
                        # P0-14 修正：通用标准 OR 个性化标准（两套独立，不并入通用词表）
                        _death = _deathsig(piece) or _bdeathsig(piece, _sigs) or \
                            _deathsig(_ev_text) or _bdeathsig(_ev_text, _sigs)
                        _viols = _pdv(piece, _panel_base)
                        if _death:
                            _viols = _viols + _pcv(piece, _panel_base, True)
                        # P0-15/16 通用版：注册表外机制字段违规（closed-world 白名单）
                        if _reg:
                            _viols = _viols + _unk(piece, _reg)
                    except Exception:
                        _viols = []
                    if _viols:
                        _panel_ok = False
                        for _try in (1, 2):
                            if not silent:
                                print(f"  [场景] 第{idx}个场景机制数字违反方向规则"
                                      f"（{_viols[0]}），重试第{_try}次强制按基线推进")
                            _viol_text = "\n".join(f"- {v}" for v in _viols[:4])
                            sys_c5, user_c5 = scene_prompt(
                                self.cfg, chapter_no, plan_title, idx, sc,
                                mem_for_this, running_tail, dynamic,
                                core_event=plan_core_event,
                                scene_state_hint=scene_state_hint,
                                force_new_progression=True,
                                event_progress=event_progress,
                                done_beats=done_beats_text,
                                consumed_events=consumed_events_text,
                                upcoming_events=upcoming_events_text,
                                panel_baseline=panel_baseline_text,
                                panel_violations=_viol_text,
                                book_pitfalls=book_pitfalls_text,
                                book_canon=book_canon_text,
                                state_anchor=state_anchor_text,
                            )
                            retry5 = self.llm.chat(
                                self._sys(sys_c5), user_c5,
                                temperature=0.7,
                                max_tokens=max(4000, min(8192, int(_target_w * 2.2))),
                                frequency_penalty=0.4,
                                presence_penalty=0.25,
                            )
                            retry5 = _strip_html_tags(retry5)
                            _viol5 = []
                            if retry5:
                                try:
                                    _viol5 = _pdv(retry5, _panel_base)
                                    if not _viol5 and _death:
                                        _viol5 = _pcv(retry5, _panel_base, True)
                                except Exception:
                                    _viol5 = []
                            if retry5 and not _viol5:
                                piece = retry5
                                _panel_ok = True
                                break
                        if not _panel_ok and not silent:
                            print(f"  [场景] 第{idx}个场景机制数字连续 2 次重试仍违反，"
                                  f"采用最后稿（质检层二次拦截）")
                # P0-18 场景字数上限治理（2026-10-10，治 v9 ch4=5050/ch6=4310 超长与
                # 注水）：单场景正文超 target_words×1.5 → 精简重写 1 次（保留剧情与
                # 数字、删重复措辞/注水对话/无效描写，压到 target×1.2）；仍超保留
                # （不做整章回退——质量过关优先，字数不触发整章回退）。
                # 整句复读型注水（"正文"×N 同句重复）由节拍防线/质检 2.7 处理，
                # slim 只服务"剧情推进完整但字数超限"的真实超长场景。
                if (piece and not _is_repetitive_text(piece)
                        and len(piece) > max(600, int(sc.get("target_words") or 1200) * 1.5)):
                    _slim = self._slim_scene(piece, sc, chapter_no, plan_title, idx, silent)
                    if _slim and len(_slim) < len(piece):
                        piece = _slim
                        running_tail = piece[-120:] if piece else running_tail
                scene_texts.append(piece)
                # 下一场景的衔接用本场景结尾
                running_tail = piece[-120:] if piece else running_tail
                # 正文级节拍序列登记（措施3）：最终采用场景正文的节拍指纹，供后续场景比对
                _bs = _extract_beat_sequence(piece) if piece else []
                if _bs:
                    done_beat_seqs.append(_bs)
                    scene_beat_ledger.append(_bs)  # P0-10 跨场景同构账本
                    # 事前约束文本：最近若干场景的已发生节拍组合（注入后续场景 prompt 禁止再演）
                    _recent = done_beat_seqs[-3:]
                    done_beats_text = "\n".join(f"- {'→'.join(s)}" for s in _recent)
                # 跨场景事件清单：本场景规划事件（拆场景时定义）追加为"已发生事件"，
                # 拼入 scene_state_hint 注入后续场景——同一事件不得再次发生/重复描写。
                ev = str(sc.get("event") or "").strip()
                if ev and ev not in done_events:
                    done_events.append(ev)
                    ev_hint = ("\n【本章已发生事件（硬约束：以下事件已发生，后续场景禁止再次发生/"
                               "重复描写同一事件；同人物+同冲突+同结果=重复；如需呼应用一句话带过）】\n"
                               + "\n".join(f"{i}. {e}" for i, e in enumerate(done_events, 1)))
                    scene_state_hint = (scene_state_hint + ev_hint) if scene_state_hint else ev_hint
                # P0-1 事件消费账本登记：本场景事件标记 consumed（供后续场景「已消费事件」
                # 注入；desc 为空时用规划事件文本，避免账本缺环）
                if ev:
                    event_ledger.append({"desc": ev, "consumed_scene_idx": idx})
                    # P0-7 小说级记忆：跨章持久化事件消费（零 LLM，失败静默）
                    try:
                        _mem = self._memory()
                        if _mem is not None:
                            _mem.record_event_consumed(ev, chapter_no)
                    except Exception:
                        pass
                # 场景状态推进抽取（REQ-token优化 P0-1）：
                # scene_state_mode=auto → 高质量模式 LLM / 普通模式确定性面板正则；
                # llm → 全部 LLM；deterministic → 全部确定性（零 LLM）。
                if idx < len(scenes):
                    try:
                        from .chapter_state import (extract_scene_state, format_scene_state,
                                                    merge_scene_state, _deterministic_numbers)
                        mode = getattr(self.cfg, "scene_state_mode", "auto") or "auto"
                        hq = getattr(self.cfg, "high_quality_mode", False)
                        use_llm = (mode == "llm" or (mode == "auto" and hq)) and \
                            getattr(self.cfg, "chapter_state_llm", True)
                        if use_llm:
                            adv = extract_scene_state(self.llm, piece)
                        else:
                            nums = _deterministic_numbers(piece)
                            adv = {"numbers": nums} if nums else None
                        if adv:
                            scene_state_acc = merge_scene_state(scene_state_acc, adv)
                            scene_state_hint = format_scene_state(scene_state_acc)
                    except Exception:
                        pass
            except Exception as e:
                print(f"    [场景] 第{idx}个场景生成失败: {e}")
                scene_texts.append("")

        chapter_text = "\n\n".join(t for t in scene_texts if t)

        # 3. 润色通读（中温 0.7；REQ-token优化 P0-3：普通模式默认跳过，省整章输入+输出）
        hq_mode = getattr(self.cfg, "high_quality_mode", False)
        polish_on = hq_mode or not getattr(self.cfg, "polish_hq_only", True)
        if polish_on:
            try:
                sys_p, user_p = polish_chapter_prompt(
                    self.cfg, chapter_no, plan_title, chapter_text
                )
                # 润色分块（等效大纲 head+chunks 模式）：全文 >2600 字时按段落切块逐块润色，
                # 每块输出上限自适应——一次 4000 tokens 写不完 6000 字全文必然截断
                # （《万墟之上》21:27 版"正文未完整收尾"根因）
                polished = self._polish_blocks(sys_p, user_p, chapter_text)
                if polished and len(polished) > len(chapter_text) * 0.5:
                    chapter_text = polished
            except Exception:
                pass

        # 4. 追加摘要（复用普通模式的摘要生成）
        # 清理 LLM 偶发输出的尾部 Markdown 分隔线（---/***），避免章末残留无体例的分隔符
        chapter_text = re.sub(r"[\s\n]*(-{3,}|\*{3,}|_{3,})\s*$", "", chapter_text).rstrip()
        summary = self._gen_summary(chapter_no, plan_title, chapter_text)
        if summary:
            chapter_text = chapter_text + "\n\n【摘要】" + summary

        # P0-8：场景累计机制数字基线暴露给 write()（质检层透传，修复原 locals() 拿不到）
        self._scene_state_acc = scene_state_acc
        return chapter_text

    def _gen_action_plan(self, chapter_no, plan_title, plan_goal, memory_context) -> str:
        """P1-1 行动表先行：生成人物行动表 JSON，解析失败返回空（自动回退原流程）。

        P0-8（2026-10-10）：v6 实证 7 章行动表全部失败——`Expecting ',' delimiter:
        line 25/26 column 6 (char ~1000-1200)` = 输出在 ~1000-1200 字符处被截断
        （max_tokens=800 对中文约 400-500 字，行动表多人物时常超）。修复：max_tokens
        800→2000；解析失败增加数组级救回（_extract_json_field "characters"）+ 重试 1 次。
        """
        try:
            import json
            from .templates import action_plan_prompt
            sys_a, user_a = action_plan_prompt(
                self.cfg, chapter_no, plan_title, plan_goal, memory_context,
            )
            raw = self.llm.chat(sys_a, user_a, temperature=0.4, max_tokens=2000)
            data = None
            try:
                data = json.loads(_strip_json_block(raw))
            except Exception:
                # P0-8：长输出截断救回——characters 数组通常完整（截断多在尾部说明）
                _ca = _extract_json_field(raw, "characters")
                if _ca:
                    data = {"characters": _ca}
            if data is None:
                # P0-8：整体解析失败 → 重试 1 次（提高温度与上限）
                raw = self.llm.chat(
                    sys_a, user_a + "\n\n再次提醒：只输出一个合法的 JSON 对象，"
                    "不要输出任何代码块标记、注释、省略号或尾逗号。",
                    temperature=0.5, max_tokens=2000,
                )
                try:
                    data = json.loads(_strip_json_block(raw))
                except Exception:
                    data = _extract_json_field(raw, "characters") and {
                        "characters": _extract_json_field(raw, "characters")}
            chars = (data or {}).get("characters") or []
            lines = []
            for c in chars:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                actions = c.get("key_actions") or []
                if isinstance(actions, str):
                    actions = [actions]
                lines.append(
                    f"- {c['name']}：目标『{c.get('goal','')}』；关键行动：{'、'.join(str(a) for a in actions)}；"
                    f"情绪：{c.get('emotion','')}；对话意图：{c.get('dialogue_intent','')}"
                )
            return "\n".join(lines)
        except Exception as e:
            print(f"  [行动表] 生成失败，回退原流程: {e}")
            return ""

    def _fill_characters_action(self, chapter_no, plan_title, plan_goal,
                                core_event, events_list, prev_tail, silent=False) -> list:
        """P0-4 characters_action 单字段补全（2026-10-09 生成端第三轮）。

        拆场景大 JSON 长输出截断通常只丢尾部 characters_action（"Expecting ',' delimiter"
        实证：7 章行动表全部解析失败，措辞层约束从未生效）；这里让模型**只输出
        characters_action 数组**（小调用、低截断风险），成功后并入拆场景结果。
        最多重试 1 次，仍失败返回 []（不阻塞主流程，落 trace 告警）。
        """
        try:
            import json
            from .templates import characters_action_fill_prompt
            events_text = "\n".join(
                f"{e.get('id','')}：{e.get('desc','')}"
                for e in (events_list or []) if isinstance(e, dict) and e.get("desc")
            ) or "（无）"
            sys_f, user_f = characters_action_fill_prompt(
                self.cfg, chapter_no, plan_title, plan_goal,
                core_event=core_event, events_text=events_text, prev_tail=prev_tail,
            )
            for _try in (1, 2):
                raw = self.llm.chat(sys_f, user_f, temperature=0.3, max_tokens=600)
                try:
                    arr = json.loads(_strip_json_block(raw))
                except Exception:
                    arr = _extract_json_field(raw, "characters_action")
                if isinstance(arr, list) and arr:
                    ok = [c for c in arr if isinstance(c, dict) and c.get("name")]
                    # P0-5（2026-10-10）：补成功日志——此前只有失败日志，
                    # 无法区分"独立行动表调用失败回退"与"拆场景救回+单字段补全成功"。
                    if not silent:
                        print(f"  [行动表补全] 第{_try}次补全成功：注入 {len(ok)} 条行动（原解析截断救回）")
                    return ok
                if not silent:
                    print(f"  [行动表补全] 第{_try}次解析失败，重试 1 次...")
            if not silent:
                print("  [行动表补全] 两次均失败，本次不注入行动表（记录 trace 告警）")
            return []
        except Exception as e:
            if not silent:
                print(f"  [行动表补全] 补全调用异常：{e}")
            return []

    def _scene_split_review(self, chapter_no, plan_title, plan_goal,
                            events_list, scenes, silent=False) -> list[tuple[int, int, str]]:
        """P0-10 拆场景评审（混合方案①，2026-10-10）：独立评审 agent 判事件同构。

        治 v7 第 5 章药铺三重拼接的规划层根因——desc 相似度预筛（>0.55）对
        「查药粉/诊脉/探脉枕」三个不同原文不命中，需要语义级判断。评审只输出
        重复对清单（JSON），代码按清单确定性合并（_merge_duplicate_events），
        不重复调用模型重拆（省 token、可复现）。任何失败降级空列表（不阻塞）。
        """
        try:
            import json
            if not events_list or len(events_list) < 2:
                return []
            ev_text = "\n".join(
                f"{i}. {e.get('id','')}｜{e.get('desc','')}"
                for i, e in enumerate(events_list) if isinstance(e, dict)
            )
            sc_text = ""
            if scenes:
                sc_text = "\n".join(
                    f"- scene {sc.get('id','')}: ref={sc.get('scene_event_ref','')}"
                    for sc in scenes if isinstance(sc, dict)
                )
            sys_r, user_r = scene_split_review_prompt(
                self.cfg, chapter_no, plan_title, plan_goal, ev_text, sc_text
            )
            raw = self.llm.chat(sys_r, user_r, temperature=0.2, max_tokens=800)
            d = json.loads(_strip_json_block(raw))
            pairs = d.get("duplicate_pairs") or []
            out: list[tuple[int, int, str]] = []
            for p in pairs:
                try:
                    i, j = int(p["i"]), int(p["j"])
                except Exception:
                    continue
                if 0 <= i < len(events_list) and 0 <= j < len(events_list) and i != j:
                    out.append((i, j, str(p.get("reason") or "")))
            if out and not silent:
                print(f"  [拆场景] 评审发现 {len(out)} 组语义同构事件（"
                      + "; ".join(f"{i}≈{j}:{r[:20]}" for i, j, r in out) + "），代码合并去重")
            return out
        except Exception as e:
            if not silent:
                print(f"  [拆场景] 评审调用失败（降级不阻塞）: {e}")
            return []

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
        return prev.content.strip()[-300:]

    def _slim_scene(self, piece: str, sc: dict, chapter_no: int, plan_title: str,
                    idx: int, silent: bool = False) -> str:
        """P0-18 单场景精简重写（2026-10-10）：场景超 target_words×1.5 时调用。

        保留剧情主干/人物行为/机制数字/伏笔，删除重复措辞、注水对话、无效描写与
        自我复读，压缩到 target×1.2 左右；不改变情节走向、不新增情节。失败返回空串
        （调用方保留原稿）。
        """
        if not piece:
            return ""
        target = max(600, int(sc.get("target_words") or 1200) * 12 // 10)
        try:
            sys_p = ("你是小说精修编辑。对给定场景做「去水压缩」：只删除重复措辞、"
                     "注水对话轮、无效环境/心理描写、同一信息的多遍复述；保留全部剧情"
                     "主干、人物行为逻辑、机制面板数字、伏笔与钩子；不得改变情节走向、"
                     "不得新增情节、不得改机制数字。输出压缩后的正文。")
            user_p = (
                f"第{chapter_no}章《{plan_title}》第{idx}个场景，当前 {len(piece)} 字"
                f"（目标 ≤ {target} 字）：\n{piece}\n\n"
                "请压缩到目标字数内（如原文明显超长），保持事件推进与人物一致，"
                "直接输出压缩后正文。"
            )
            out = self.llm.chat(
                sys_p, user_p, temperature=0.5, max_tokens=max(3000, target * 2)
            )
            out = _strip_html_tags(out)
            if out and 0.5 * len(piece) <= len(out) <= len(piece) and len(out) <= target * 1.3:
                if not silent:
                    print(f"  [场景] 第{idx}个场景超长（{len(piece)} 字），"
                          f"精简重写为 {len(out)} 字")
                return out
            return ""
        except Exception:
            return ""


def _format_action_plan(chars: list) -> str:
    """把拆场景输出中的 characters_action 格式化为注入文本（空则返回空串）。

    REQ-token优化 P0-2：行动表由拆场景一次调用顺带产出，这里只做确定性格式化，
    零新增 LLM 调用；格式与原 _gen_action_plan 一致。
    """
    if not chars:
        return ""
    lines = []
    for c in chars:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        actions = c.get("key_actions") or []
        if isinstance(actions, str):
            actions = [actions]
        lines.append(
            f"- {c['name']}：目标『{c.get('goal', '')}』；关键行动：{'、'.join(str(a) for a in actions)}；"
            f"情绪：{c.get('emotion', '')}；对话意图：{c.get('dialogue_intent', '')}"
        )
    return "\n".join(lines)


#: 场景事件互斥校验（P1-1）：event 文本相似度阈值（归一化后 55% 相同即判重叠）
_SCENE_EVENT_OVERLAP_RATIO = 0.55
#: 事件次数标记正则：「第1/2/3…次」或「第一次/第二次…」，两事件次数不同视为不同事件
_SCENE_ORDINAL_PATTERN = r"第\s*[一二三1-3０-９\d]+\s*次"
#: 事件动作词归一化表（2026-10-01：事件互斥从文本相似度升级为语义槽位——
#: 同冲突对象+同动作=重复事件，即使地点/措辞不同（《万墟之上》4500 字版
#: "救苏妄言"石殿版/墟口外版：desc 相似度 <0.55 放行，但 conflict+action 相同）
_SCENE_ACTION_NORM = {
    "救": "救援", "救援": "救援", "营救": "救援", "搭救": "救援", "救出": "救援",
    "围": "围攻", "围攻": "围攻", "围住": "围攻", "围杀": "围攻", "包围": "围攻",
    "杀": "击杀", "击杀": "击杀", "杀死": "击杀", "斩杀": "击杀", "猎杀": "击杀",
    "逃": "逃离", "逃离": "逃离", "逃跑": "逃离", "撤": "撤离", "撤离": "撤离", "撤退": "撤离",
    "追": "追击", "追击": "追击", "追逐": "追击", "追捕": "追击",
    "挡": "抵挡", "抵挡": "抵挡", "防守": "抵挡", "阻挡": "抵挡",
    "偷袭": "偷袭", "刺杀": "刺杀", "对峙": "对峙", "埋伏": "埋伏", "侦查": "侦查", "探索": "探索",
}
#: 救援专属动词（2026-10-01 场景正文事件复写检测用）：仅"救/引开/砸/挡/扑/迎"这类
#: 明确救援行为才判定"该冲突事件被再次执行"；"冲/拽/拖"在逃离/撤离/移动场景也常见
#: （撤往补给站场景'冲到坡底拽住后领往外拖'即被误判），不放宽到通用动作
_SCENE_RESCUE_VERBS = ("救", "引开", "砸", "挡", "扑", "迎")


def _event_conflict(text: str) -> str:
    """提取事件槽位核心对象：优先被救/受害人物（苏妄言/学员/弟子/少年/少女），
    无人物才取敌人词（赤尉/灰卒/蚀兽/蚀卒）；只返回核心词（去量词）——
    敌人措辞（'三只灰卒' vs '三只赤尉伴生灰卒'）不参与精确比对，
    同一人物+同一救援动作即判复写（第四版救人岩台版/墟口外版即此类，敌人词不同双漏检）。"""
    m = re.search(r"(三头?|三只?|几只?|一头?|一只?|个)?(苏妄言|学员|弟子|少年|少女)", text or "")
    if m:
        return m.group(2)
    m = re.search(r"(三头?|三只?|几只?|一头?|一只?|个)?(赤尉|灰卒|蚀兽|蚀卒)", text or "")
    if m:
        return m.group(2)
    return ""


def _scene_text_rewrites_event(text: str, done_event_descs: list[str]) -> str:
    """2026-10-01 场景正文事件级复写检测（零 LLM、确定性）：
    语义互斥只校验拆场景 events 清单，拦不住"场景生成时模型把已发生事件换措辞又写一遍"
    （第三版坡顶救人/墟口外救人：conflict 措辞'三条赤尉'vs'巨型赤尉'，文本相似度与
    事件清单双漏检——场景 2 与场景 3 各自引用唯一事件，但场景 3 正文把'救苏妄言'
    完整重演）。规则：正文含救援行为动词（救/引开/冲/砸/挡/拽/拖/扑/迎）+ 冲突对象，
    且与某已完成事件的冲突对象+救援动作同现 → 该事件被再次完整执行，返回被复写事件原文。"""
    c = _event_conflict(text)
    if not c or not any(v in text for v in _SCENE_RESCUE_VERBS):
        return ""
    for ev in done_event_descs:
        ec = _event_conflict(ev)
        if not ec or not any(v in ev for v in _SCENE_RESCUE_VERBS):
            continue
        if c == ec or c in ec or ec in c:
            return ev
    return ""
#: 拆场景结构校验：场景数上限（2-4 个，超出=凑数拆场景的迹象，直接回退整章）
_SCENE_COUNT_MIN, _SCENE_COUNT_MAX = 2, 4
#: target_words 之和允许偏离 chapter_words 的幅度（±10%）
_SCENE_WORDS_TOLERANCE = 0.10
#: 相邻场景输出相似度阈值：场景 N 与场景 N-1 全文相似度超过该值 = 场景重演上一场景
#: （生成端把上一场景整段复刻，6316 字版"灰卒咬喉死"原样重复即此类），重试本场景
_SCENE_ADJACENT_REPEAT_RATIO = 0.55
#: 长段重叠率阈值（2026-10-01）：当前场景中与上一场景完全相同的连续句子（去空白后
#: ≥15 字）占总字数比例 >20% = 局部复刻（4500 字版"死亡复刻段"约 200 字嵌进场景 2，
#: 全文 ratio<0.55 漏检），重试本场景
_SCENE_ADJACENT_SPAN_RATIO = 0.20
#: 长段重叠判定：句长下限（去空白后字数）
_SCENE_ADJACENT_MIN_SENT = 15


def _long_sentence_overlap_ratio(prev: str, cur: str) -> float:
    """相邻场景"长段复刻率"（确定性、零 LLM）：
    cur 中与 prev 逐字相同的句子（去标点空白后 ≥15 字）的字数 / cur 总字数。
    4500 字版"死亡复刻段"整句整句复刻，此指标命中；全文 SequenceMatcher 只按
    字符级相似度算、局部复刻时 <0.55 漏检。
    """
    import re as _re
    norm = lambda s: "".join(ch for ch in s if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    prev_sents = {norm(s) for s in _re.split(r"[。！？\n]", prev or "") if len(norm(s)) >= _SCENE_ADJACENT_MIN_SENT}
    cur_sents = [norm(s) for s in _re.split(r"[。！？\n]", cur or "") if len(norm(s)) >= _SCENE_ADJACENT_MIN_SENT]
    if not cur_sents:
        return 0.0
    hit = sum(len(s) for s in cur_sents if s in prev_sents)
    total = sum(len(s) for s in cur_sents)
    return (hit / total) if total > 0 else 0.0


def _norm_scene_event(text: str) -> str:
    """归一化 event 文本：去标点空白、保留中文与字母数字（重叠判定基准）。"""
    return "".join(ch for ch in (text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _scene_event_ordinals(text: str) -> set[str]:
    """提取 event 文本中的事件次数标记（如「第一次」「第2次」）。

    同一动作不同次数（第一次死亡/第二次死亡）是不同事件，不是重叠。
    """
    import re
    return set(re.findall(_SCENE_ORDINAL_PATTERN, text))


# REQ-scene-plan-align：character_moves 因果粘连拍标记（后拍含以下词 → 并入前拍，
# 死亡-复活/昏迷-苏醒这类因果必须同事件，禁止拆成两个"独立事件"造成重演空间）
_CAUSAL_BEAT_RE = re.compile(r"复活|苏醒|醒来|回神|缓过神|回过神来|睁眼|醒转")


def _derive_events_from_moves(moves: str) -> list[dict]:
    """REQ-scene-plan-align：把大纲 character_moves（逐拍动作序列）确定性切分为事件清单。

    - 每拍 = 一个事件（id 按序编号、desc 逐字保留骨架原文，conflict/action 复用语义槽位提取）；
    - 拍数 >4 时合并到 ≤4：因果粘连拍（死亡-复活等）优先并入前拍，再合并最短拍；
    - 输入为空 / 切分后 <2 拍 / 合并后仍 <2 拍 → 返回 []（调用方回退原 LLM 规划路径）。

    确定性保证：纯字符串切分 + 固定合并规则，无 LLM 采样，同一输入恒得同一输出。
    """
    if not moves or not str(moves).strip():
        return []
    text = str(moves).strip()
    if "→" in text:
        beats = [b.strip() for b in text.split("→")]
    else:
        beats = [b.strip() for b in re.split(r"[。；;]", text) if b.strip()]
    beats = [b for b in beats if b]
    if len(beats) < 2:
        return []
    # 因果粘连一次性合并：含复活/苏醒标记的拍并入前拍（死亡-复活必须同事件）。
    # 合并产物仍含"复活"字样，但不再触发粘连检查（跳过产物），防连锁吞并整章。
    i = len(beats) - 1
    while i >= 1 and len(beats) > 4:
        if _CAUSAL_BEAT_RE.search(beats[i]):
            beats[i - 1] = f"{beats[i - 1]}→{beats[i]}"
            del beats[i]
            i -= 2  # 跳过合并产物（新文本含标记，不得再次触发）
        else:
            i -= 1
    # 仍 >4 → 合并最短拍到其前拍（保因果顺序）
    while len(beats) > 4:
        j = min(range(1, len(beats)), key=lambda k: len(beats[k]))
        beats[j - 1] = f"{beats[j - 1]}→{beats[j]}"
        del beats[j]
    if len(beats) < 2:
        return []
    events = []
    for i, b in enumerate(beats, start=1):
        c, a = _event_semantic_slots(b)
        events.append({"id": f"事件{i}", "desc": b, "conflict": c, "action": a, "final_state": ""})
    return events


def _event_semantic_slots(desc: str, conflict: str = "", action: str = "") -> tuple[str, str]:
    """提取事件的语义槽位（核心对象, 归一化动作）。

    2026-10-01 改为「被救/受害人物优先」：同一人物+同一动作即判重复（第四版
    '救苏妄言'岩台版/墟口外版：敌人措辞'三只灰卒'vs'三只赤尉伴生灰卒'，旧版按敌人词
    比对双漏检——事件清单互斥与场景正文复写检测全失效）。desc 无人物时才回退敌人词
    （赤尉/灰卒/蚀兽/蚀卒）；只取核心词（去量词）。动作归一化不变。
    """
    import re as _re
    conflict_v = ""
    action_v = (action or "").strip()
    if not action_v and desc:
        for w, norm in _SCENE_ACTION_NORM.items():
            if w in desc:
                action_v = norm
                break
    src = desc or ""
    m = _re.search(r"(三头?|三只?|几只?|一头?|一只?|个)?(苏妄言|学员|弟子|少年|少女)", src)
    if m:
        conflict_v = m.group(2)
    else:
        m = _re.search(r"(三头?|三只?|几只?|一头?|一只?|个)?(赤尉|灰卒|蚀兽|蚀卒)", src or conflict)
        if m:
            conflict_v = m.group(2)
    return conflict_v, action_v


def _scene_events_semantically_overlap(events: list[dict]) -> bool:
    """事件清单语义槽位互斥（零 LLM）：任意两事件的（冲突对象, 归一化动作）相同 =
    同一事件拆成两场景（救苏妄言石殿版/墟口外版），返回 True 由调用方回退整章。"""
    slots: list[tuple[str, str]] = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        desc = str(e.get("desc") or "").strip()
        c, a = _event_semantic_slots(desc, str(e.get("conflict") or ""), str(e.get("action") or ""))
        if not c and not a:
            continue
        slots.append((c, a))
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            ci, ai = slots[i]
            cj, aj = slots[j]
            if ai and aj and ai == aj and ci and cj and (ci == cj or ci in cj or cj in ci):
                return True
    return False


def _merge_duplicate_events(events_list: list, scenes: list,
                            pairs: list[tuple[int, int, str]]) -> tuple[list, list] | None:
    """P0-10 拆场景评审合并（确定性，零额外 LLM）：按评审重复对合并事件。

    - 保留 idx 小者的 desc/结构化字段，删除 idx 大者（换措辞版本）；
    - 引用被删 id 的场景一并删除（与保留场景语义同构，删除防正文再写一遍）；
    - 返回 (新 events_list, 新 scenes)；无有效合并返回 None（调用方不动作）。
    """
    if not events_list or not pairs:
        return None
    drop_idx: set[int] = set()
    for i, j, _r in pairs:
        if 0 <= i < len(events_list) and 0 <= j < len(events_list) and i != j:
            drop_idx.add(j)
    if not drop_idx:
        return None
    new_events = [e for idx, e in enumerate(events_list) if idx not in drop_idx]
    drop_ids = {str(events_list[idx].get("id") or "") for idx in drop_idx}
    new_scenes: list = []
    for sc in scenes or []:
        if not isinstance(sc, dict):
            continue
        ref = str(sc.get("scene_event_ref") or "").strip()
        if ref in drop_ids:
            continue  # 该场景对应被删事件 → 一并删除（同构版本）
        new_scenes.append(sc)
    if len(new_events) < 2 or not new_scenes:
        # 合并后不足以成章 → 不合并（走后续校验/回退兜底）
        return None
    return new_events, new_scenes


def _expand_scene_event_refs(scenes: list, events_list: list) -> None:
    """拆场景设计重构（2026-09-30）：场景 scene_event_ref 逐字引用事件清单编号，
    这里把引用展开为事件原文（sc["event"]）。事件互斥由「事件清单两两不同 + 引用唯一性」
    结构化保证，模型无法再靠换措辞把同一事件塞进两个场景。
    - 有 events_list + scene_event_ref → 展开引用（新格式）
    - 无 events_list 或引用缺失 → 保留场景自带 event 文本（旧格式，走相似度降级校验）
    """
    by_id: dict[str, dict] = {}
    for e in events_list or []:
        if isinstance(e, dict):
            eid = str(e.get("id") or "").strip()
            if eid:
                by_id[eid] = e
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        ref = str(sc.get("scene_event_ref") or "").strip()
        if ref and ref in by_id:
            e = by_id[ref]
            sc["event"] = str(e.get("desc") or "").strip()
            sc["scene_event_ref"] = ref
            # 2026-10-01：事件结构化字段（conflict/action/final_state）展开进场景，
            # scene_prompt 据此约束本场景唯一事件与结束状态增量（防蚀痕状态无增量矛盾）
            for k in ("conflict", "action", "final_state"):
                if e.get(k):
                    sc[k] = e[k]


def _validate_scene_plan(scenes: list, chapter_words: int = 0,
                         core_event: str = "") -> tuple[bool, str]:
    """拆场景结构校验（零 LLM 成本，2026-09-30 从文本相似度升级为结构化校验）：
    1. 场景数 ≤ 4（拆出 5-6 个场景 = 把同一事件拆多遍凑场景数，《万墟之上》6316 字版根因；
       单场景不视为失败——拆不出时按整章单场景写，交由后续字数/质量审计兜底）
    2. 多场景时 target_words 之和 ∈ [chapter_words ±10%]（拆场景字数规划失效迹象）
    3. 事件互斥：新格式按 scene_event_ref 引用唯一性（同一事件编号被 ≥2 场景引用=重叠）；
       旧格式（无 ref）降级按 event 文本归一化相似度（>0.55 判重叠）
    4. P0-17（2026-10-10）：大纲核心事件覆盖校验——core_event 分段后每段与事件清单
       并集做 2 字以上最长公共子串匹配，命中段占比 <60% = 拆场景丢失大纲核心事件
       （v9 ch6 实证：大纲「忘忧阁阁主密报」被替换为自创「不问阁」→ 覆盖不足应回退）
    返回 (是否通过, 失败原因)；不通过由调用方回退整章生成。
    """
    if not scenes:
        return True, ""
    if len(scenes) > _SCENE_COUNT_MAX:
        return False, f"场景数 {len(scenes)} 超出上限 4（疑似把同一事件拆多遍凑场景数）"
    if len(scenes) >= 2 and chapter_words > 0:
        total = sum(int(sc.get("target_words") or 0) for sc in scenes if isinstance(sc, dict))
        if total > 0 and abs(total - chapter_words) > chapter_words * _SCENE_WORDS_TOLERANCE:
            return False, f"场景 target_words 之和 {total} 偏离目标 {chapter_words} 超 ±10%"
    # P0-17：大纲核心事件覆盖校验（通用，任何书有 core_event 即校验）
    if core_event:
        segs = [s.strip() for s in re.split(r"[→，,。；;]", core_event)
                if len(s.strip()) >= 4]
        if segs:
            ev_all = " ".join(
                str(sc.get("event") or "") for sc in scenes if isinstance(sc, dict)
            )
            hit = sum(1 for s in segs if _lcs_len(s, ev_all) >= 2)
            if hit * 10 < len(segs) * 6:   # 命中段 ≥60% 才通过（2 段 1 命中=50% 判未覆盖）
                miss = [s for s in segs if _lcs_len(s, ev_all) < 2]
                return False, (f"拆场景未覆盖大纲核心事件（命中 {hit}/{len(segs)} 段，"
                               f"未覆盖段：{' / '.join(miss[:3])}）")
    # 事件互斥：优先引用唯一性，缺失 ref 降级文本相似度
    refs: list[str] = []
    texts: list[tuple[str, set[str]]] = []
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        ref = str(sc.get("scene_event_ref") or "").strip()
        if ref:
            refs.append(ref)
        ev = (sc.get("event") or "").strip()
        if ev:
            texts.append((_norm_scene_event(ev), _scene_event_ordinals(ev)))
    if refs:
        seen: set[str] = set()
        for r in refs:
            if r in seen:
                return False, f"事件编号 {r} 被多个场景引用（同一事件拆成两场景）"
            seen.add(r)
    import difflib
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, a_ord = texts[i]
            b, b_ord = texts[j]
            if not a or not b:
                continue
            # 次数标记互斥（第一次 vs 第二次…）→ 视为不同事件，跳过
            if a_ord and b_ord and not (a_ord & b_ord):
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() > _SCENE_EVENT_OVERLAP_RATIO:
                return False, "场景事件高度相似（同一事件换措辞拆成两场景）"
    return True, ""


def _lcs_len(a: str, b: str) -> int:
    """两文本最长公共子串长度（≥2 字视为「段被事件清单覆盖」；O(n*m) 小文本直算）。"""
    if not a or not b:
        return 0
    if len(a) > len(b):   # 短串作窗口，长串作母串
        a, b = b, a
    for w in range(len(a), 1, -1):
        for i in range(len(a) - w + 1):
            if a[i:i + w] in b:
                return w
    return 0


def _scene_events_overlap(scenes: list) -> bool:
    """兼容别名：仅事件互斥维度（旧测试/调用沿用）。"""
    ok, _ = _validate_scene_plan(scenes, chapter_words=0)
    return not ok


def _split_summary(text: str) -> tuple[str, str]:
    """按『【摘要】』标记拆分正文与摘要；未找到标记则整段视为正文。"""
    marker = "【摘要】"
    idx = text.rfind(marker)
    if idx > 0:
        content = text[:idx].strip()
        summary = text[idx + len(marker):].strip().replace("\n", " ")
        return content, summary
    return text.strip(), ""


def _is_repetitive_text(text: str) -> bool:
    """整句复读型注水检测：唯一 2-gram 占比 <15% = 同一句反复堆叠（测试构造
    "正文"×N / 模型无意义复读），此时 slim 无意义，交由节拍防线/质检处理。
    """
    if not text or len(text) < 200:
        return False
    grams = {text[i:i + 2] for i in range(max(0, len(text) - 1))}
    return len(grams) < len(text) * 0.15


def _summary_invented_words(summary: str, content: str) -> float:
    """P0-17 摘要一致性：摘要 2-gram 与正文 2-gram 的命中率。

    模型自带的摘要若大量使用正文完全没有的表述（多为自创专有名词/大纲残留，如
    v9 ch6 摘要「忘忧阁阁主密报」而正文是不问阁），命中率会显著低于正常摘要。
    返回命中率（0.0~1.0）；调用方以 <0.35 判「摘要与正文脱节 → 基于正文重生成」。
    """
    if not summary or not content:
        return 1.0
    if len(summary) < 8:
        return 1.0
    grams = {summary[i:i + 2] for i in range(max(0, len(summary) - 1))}
    if len(grams) < 4 or len(summary) < 8:   # 太短无统计意义（测试/占位摘要防误触发重生成）
        return 1.0
    body = {content[i:i + 2] for i in range(max(0, len(content) - 1))}
    if not body:
        return 1.0
    return sum(1 for g in grams if g in body) / len(grams)


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


def _strip_json_block(text: str) -> str:
    """从模型输出中提取 JSON 对象（容忍 ```json 包裹、前后赘述、截断、尾逗号）。

    2026-10-01 健壮化：拆场景输出三块（events+scenes+characters_action）较长，
    实测多次在结尾处 JSON 语法断裂（'Expecting value: line 46 col 17'）导致整条
    拆场景链路回退、事件清单丢失。这里先剥代码块，再截第一个 { 到最后一个 }，
    再修复尾逗号（',后紧跟 }/]' → 对应闭合符）。
    """
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    s = t.find("{")
    if s < 0:
        return ""
    # 截断场景：从最后一个 } 逐级向前回溯，取第一个可完整解析的闭合前缀
    # （拆场景长输出常在结尾断裂，scenes 元素未闭合时简单 rfind 会丢 ]
    # → 这里对每个 } 位置尝试解析，命中即返回）
    import json as _json
    closes = [m.end() - 1 for m in re.finditer(r"\}", t)]
    for e in closes[::-1]:
        if e <= s:
            break
        sub = t[s:e + 1]
        sub = re.sub(r",\s*([}\]])", r"\1", sub)
        try:
            _json.loads(sub)
            return sub
        except Exception:
            continue
    t = t[s:t.rfind("}") + 1] if t.rfind("}") > s else t[s:]
    return re.sub(r",\s*([}\]])", r"\1", t)


def _extract_json_field(raw: str, field: str) -> list:
    """整体 JSON 解析失败时，用正则从模型输出里单独提取某字段（events/scenes/
    characters_action）的数组字面量并解析。返回 [] 表示该字段无法恢复。

    拆场景输出长、易截断：events 在前（char 0~几百）、scenes 在中、characters_action
    在后——截断通常丢尾部，events/scenes 常可救回；events 可救回即可在回退整章时
    注入事件序列（防救人事件丢失/重复），scenes 可救回则继续走场景拆分。
    """
    import json as _json
    m = re.search(r'"%s"\s*:\s*(\[.*?\])\s*(?:,|$)' % field, raw, re.S)
    if not m:
        return []
    lit = m.group(1)
    lit = re.sub(r",\s*([}\]])", r"\1", lit)
    try:
        val = _json.loads(lit)
        return val if isinstance(val, list) else []
    except Exception:
        return []
