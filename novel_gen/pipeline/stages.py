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
from .templates import (
    chapter_prompt,
    outline_prompt,
    world_prompt,
    split_scenes_prompt,
    scene_prompt,
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
        memory_context = self.memory.build_context(
            world, up_to_chapter=chapter_no, chapter_goal=plan_goal,
        )
        prev_tail = self._prev_tail(chapter_no)

        # 注入时间线提示
        plan_core_event = ""
        try:
            outline = self.store.load_outline()
            if outline and chapter_no - 1 < len(outline.chapters):
                plan_core_event = outline.chapters[chapter_no - 1].core_event or ""
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

        # 高质量模式：自动改稿（小改自动改，中改以上只记录）
        if getattr(self.cfg, 'high_quality_mode', False) and content:
            try:
                from .reviser import ChapterReviser
                reviser = ChapterReviser(self.cfg, self.llm, self.store,
                                         role_prompt=self.reviewer_role_prompt)
                content = reviser.revise(chapter_no, plan_title, plan_goal, content, silent=silent)
            except Exception as e:
                print(f'  [改稿] 跳过: {e}')
            # 读者视角审校（只记录不改正文）
            try:
                from .reviser import ReaderReviewer
                reader = ReaderReviewer(self.cfg, self.llm, self.store,
                                        role_prompt=self.reader_role_prompt)
                reader.review(chapter_no, plan_title, plan_goal, content)
            except Exception as e:
                print(f'  [读者审校] 跳过: {e}')

        # 模块4：ReaderAgent 内置结果自校验（保存前；可开关、异常降级不阻塞）
        guard_cfg = getattr(self.cfg, "agent_guard", None)
        if guard_cfg is not None and guard_cfg.quality_check and content:
            try:
                from .quality import QualityChecker
                qc = QualityChecker(self.cfg, self.llm, self.store,
                                    trace=self.trace, guard=self.guard)
                chapter, _qc = qc.ensure_quality(
                    chapter_no, plan_title, plan_goal, content, summary, silent=silent
                )
                content, summary = chapter.content, chapter.summary
            except Exception as qe:
                if not silent:
                    print(f"  [自校验] 跳过: {qe}")

        chapter = Chapter(no=chapter_no, title=plan_title, content=content, summary=summary, goal=plan_goal)
        self.store.save_chapter(chapter)
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

    def _generate_chapter_scenes(self, chapter_no, plan_title, plan_goal,
                                 memory_context, prev_tail, is_final,
                                 dynamic_module_used: bool = True,
                                 plan_core_event: str = "",
                                 silent: bool = False):
        """场景级生成公共链路（P1-2）：拆场景 → 逐场景 → 润色。

        高质量模式（dynamic_module_used=True）额外注入动态模块与参数分层；
        普通模式只启用拆场景→逐场景→润色基础链路。拆场景失败自动回退整章生成。
        """
        import json
        from .templates import (
            split_scenes_prompt, scene_prompt, polish_chapter_prompt,
            _pick_dynamic_module,
        )

        # 1. 拆场景（低温 0.4；REQ-token优化 P0-2：顺带产出本章人物行动表，不再独立调用）
        action_plan_block = ""
        fallback_events: list = []   # 拆场景失败但 events 可救回时，注入回退整章生成
        try:
            sys_s, user_s = split_scenes_prompt(
                self.cfg, chapter_no, plan_title, plan_goal,
                prev_tail=prev_tail, core_event=plan_core_event,
            )
            raw = self.llm.chat(sys_s, user_s, temperature=0.4)
            split_data = None
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
            if isinstance(split_data, dict):
                fallback_events = split_data.get("events") or []
                scenes = split_data.get("scenes", [])
                action_plan_block = _format_action_plan(split_data.get("characters_action") or [])
            # 2026-09-30 拆场景设计重构（用户指出"一直在打补丁、根因在场景拆分设计"）：
            # 场景拆分 = 事件分配——LLM 先规划互斥事件清单 events，场景 scene_event_ref 逐字
            # 引用事件编号。这里把引用展开为事件原文（sc["event"]），互斥由「事件清单两两
            # 不同 + 引用唯一性 + 场景数/字数校验」结构化保证，不再依赖文本相似度猜重叠
            # （6316 字版"苏妄言被围"被拆成 5 个换措辞场景即旧校验盲区）。
            events_list = split_data.get("events") or [] if split_data else []
            _expand_scene_event_refs(scenes, events_list)
            # P1-1 场景事件边界互斥 + 结构校验（场景数≤4、target_words 和∈±10%、
            # 事件引用唯一）+ 2026-10-01 事件语义槽位互斥（同冲突对象+同动作=重复，
            # 防"救苏妄言"拆成石殿版/墟口外版两场景）：
            # 不合格 = 同一事件拆成两场景（同事件两版本拼接源头），回退整章生成
            ok_plan, why_bad = _validate_scene_plan(scenes, int(getattr(self.cfg, "chapter_words", 0) or 0))
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
        except Exception:
            scenes = []

        if not scenes:
            # 拆场景失败，回退到普通整章生成。2026-10-01：若 events 已从拆场景输出
            # 救回（拆场景 JSON 截断常见），注入整章 prompt——拆场景失败不再丢事件
            # （否则"救苏妄言"等核心事件被整章生成遗漏，或模型自发挥写重复）。
            # 同时注入"每事件只写一遍"纪律，整章生成也受事件序列约束（事前规避）。
            from .templates import chapter_prompt
            ev_block = ""
            if fallback_events:
                ev_block = "\n".join(
                    f"事件{i + 1}（{e.get('id', '')}）：{e.get('desc', '')}"
                    f"{'｜结束时机制状态：' + str(e.get('final_state', '')) if e.get('final_state') else ''}"
                    for i, e in enumerate(fallback_events) if isinstance(e, dict) and e.get("desc")
                ) or ""
            system, user = chapter_prompt(
                self.cfg, chapter_no=chapter_no,
                chapter_title=plan_title, chapter_goal=plan_goal,
                research_block="", memory_context=memory_context,
                prev_tail=prev_tail, is_final=is_final,
                chapter_core_event=plan_core_event,
                chapter_events=ev_block,
            )
            system = self._sys(system)
            return self.llm.chat(system, user, temperature=0.85,
                                 frequency_penalty=0.3, presence_penalty=0.15)

        # 2. 逐场景生成（中温 0.7 + penalty）
        genre = getattr(self.cfg, "genre", "") or "default"
        dynamic = _pick_dynamic_module(chapter_no, genre) if dynamic_module_used else ""
        scene_texts = []
        running_tail = prev_tail
        # 本章人物行动表（拆场景产出）拼入每个场景的记忆上下文
        mem_for_scene = memory_context
        if action_plan_block:
            mem_for_scene = (mem_for_scene + "\n\n" if mem_for_scene else "") + \
                "【本章人物行动表（写手必须逐条落实：人物言行与行动表一致，剧情不空转、不OOC）】\n" + action_plan_block
        # REQ-章内自洽与机制账本：场景间累积「已推进状态」（面板数字/人物生死/物品/时间），
        # 注入后续场景，防「三次死亡只减两次数字」「疤脸死而复生」等章内状态断裂。
        scene_state_acc: dict = {}
        scene_state_hint: str = ""
        # 2026-09-30（跨场景事件重复根因修复）：已推进状态只含数字/生死/物品，不含「事件」——
        # 场景2写完"救苏妄言"后场景3不知道事件已发生，把同一事件换个地点又写一遍
        # （《万墟之上》22:32 版空地版/坡地版双版本）。这里把本章已规划场景事件做成
        # 确定性事件清单，逐场景累积注入后续场景，零 LLM 成本。
        done_events: list[str] = []
        # REQ-token优化 P1-2：场景 prompt 分层注入——首场景注入全量记忆，后续场景只注入
        # 增量块（已推进状态独立参数 + 行动表），不重复全量 memory_context，降每章输入 token。
        scene_delta_on = getattr(self.cfg, "scene_delta_inject", True)
        for idx, sc in enumerate(scenes, start=1):
            if idx == 1 or not scene_delta_on:
                mem_for_this = mem_for_scene
            elif action_plan_block:
                mem_for_this = ("【前情记忆】已在第1个场景完整注入，本场景不再重复全文。\n"
                                "【本章人物行动表（写手必须逐条落实：人物言行与行动表一致）】\n" + action_plan_block)
            else:
                mem_for_this = "【前情记忆】已在第1个场景完整注入，本场景不再重复全文。"
            sys_c, user_c = scene_prompt(
                self.cfg, chapter_no, plan_title, idx, sc,
                mem_for_this, running_tail, dynamic,
                core_event=plan_core_event,
                scene_state_hint=scene_state_hint,
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
                scene_texts.append(piece)
                # 下一场景的衔接用本场景结尾
                running_tail = piece[-120:] if piece else running_tail
                # 跨场景事件清单：本场景规划事件（拆场景时定义）追加为"已发生事件"，
                # 拼入 scene_state_hint 注入后续场景——同一事件不得再次发生/重复描写。
                ev = str(sc.get("event") or "").strip()
                if ev and ev not in done_events:
                    done_events.append(ev)
                    ev_hint = ("\n【本章已发生事件（硬约束：以下事件已发生，后续场景禁止再次发生/"
                               "重复描写同一事件；同人物+同冲突+同结果=重复；如需呼应用一句话带过）】\n"
                               + "\n".join(f"{i}. {e}" for i, e in enumerate(done_events, 1)))
                    scene_state_hint = (scene_state_hint + ev_hint) if scene_state_hint else ev_hint
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

        return chapter_text

    def _gen_action_plan(self, chapter_no, plan_title, plan_goal, memory_context) -> str:
        """P1-1 行动表先行：生成人物行动表 JSON，解析失败返回空（自动回退原流程）。"""
        try:
            import json
            from .templates import action_plan_prompt
            sys_a, user_a = action_plan_prompt(
                self.cfg, chapter_no, plan_title, plan_goal, memory_context,
            )
            raw = self.llm.chat(sys_a, user_a, temperature=0.4, max_tokens=800)
            data = json.loads(_strip_json_block(raw))
            chars = data.get("characters") or []
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


def _event_semantic_slots(desc: str, conflict: str = "", action: str = "") -> tuple[str, str]:
    """提取事件的语义槽位（冲突对象, 归一化动作）。

    2026-10-01 事件互斥升级：文本相似度挡不住"同冲突对象+同动作、换地点/换措辞"
    的重复（4500 字版"救苏妄言"石殿版/墟口外版即此类）。事件 desc 或结构化
    conflict/action 字段任一来源，归一化后两事件（conflict, action）相同 = 重复事件。
    """
    import re as _re
    conflict_v = (conflict or "").strip()
    action_v = (action or "").strip()
    if not conflict_v and desc:
        # 从 desc 兜底取冲突对象：常见敌人/对象名词（赤尉/灰卒/蚀兽/学员/苏妄言 等）
        m = _re.search(r"(三头?|三只?|几只?|一头?|一只?|一头?|个)?(赤尉|灰卒|蚀兽|蚀卒|学员|弟子|苏妄言|少年|少女|光盾)", desc)
        if m:
            conflict_v = m.group(0)
    if not action_v and desc:
        for w, norm in _SCENE_ACTION_NORM.items():
            if w in desc:
                action_v = norm
                break
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


def _validate_scene_plan(scenes: list, chapter_words: int = 0) -> tuple[bool, str]:
    """拆场景结构校验（零 LLM 成本，2026-09-30 从文本相似度升级为结构化校验）：
    1. 场景数 ≤ 4（拆出 5-6 个场景 = 把同一事件拆多遍凑场景数，《万墟之上》6316 字版根因；
       单场景不视为失败——拆不出时按整章单场景写，交由后续字数/质量审计兜底）
    2. 多场景时 target_words 之和 ∈ [chapter_words ±10%]（拆场景字数规划失效迹象）
    3. 事件互斥：新格式按 scene_event_ref 引用唯一性（同一事件编号被 ≥2 场景引用=重叠）；
       旧格式（无 ref）降级按 event 文本归一化相似度（>0.55 判重叠）
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
