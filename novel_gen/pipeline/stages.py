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
        return Outline(title=title, intro=intro, core_conflict=core_conflict, main_line=main_line, volumes=volumes, chapters=chapters)

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
        try:
            outline = self.store.load_outline()
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

        # P1-1 人物行动表先行：正文生成前先规划本章人物行动（解析失败自动回退原流程）
        if getattr(self.cfg, "action_plan_first", True):
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


    def _generate_chapter_scenes(self, chapter_no, plan_title, plan_goal,
                                 memory_context, prev_tail, is_final,
                                 dynamic_module_used: bool = True):
        """场景级生成公共链路（P1-2）：拆场景 → 逐场景 → 润色。

        高质量模式（dynamic_module_used=True）额外注入动态模块与参数分层；
        普通模式只启用拆场景→逐场景→润色基础链路。拆场景失败自动回退整章生成。
        """
        import json
        from .templates import (
            split_scenes_prompt, scene_prompt, polish_chapter_prompt,
            _pick_dynamic_module,
        )

        # 1. 拆场景（低温 0.4）
        try:
            sys_s, user_s = split_scenes_prompt(
                self.cfg, chapter_no, plan_title, plan_goal, prev_tail=prev_tail
            )
            raw = self.llm.chat(sys_s, user_s, temperature=0.4)
            scenes = json.loads(_strip_json_block(raw)).get("scenes", [])
        except Exception:
            scenes = []

        if not scenes:
            # 拆场景失败，回退到普通整章生成
            from .templates import chapter_prompt
            system, user = chapter_prompt(
                self.cfg, chapter_no=chapter_no,
                chapter_title=plan_title, chapter_goal=plan_goal,
                research_block="", memory_context=memory_context,
                prev_tail=prev_tail, is_final=is_final,
            )
            system = self._sys(system)
            return self.llm.chat(system, user, temperature=0.85,
                                 frequency_penalty=0.3, presence_penalty=0.15)

        # 2. 逐场景生成（中温 0.7 + penalty）
        genre = getattr(self.cfg, "genre", "") or "default"
        dynamic = _pick_dynamic_module(chapter_no, genre) if dynamic_module_used else ""
        scene_texts = []
        running_tail = prev_tail
        for idx, sc in enumerate(scenes, start=1):
            sys_c, user_c = scene_prompt(
                self.cfg, chapter_no, plan_title, idx, sc,
                memory_context, running_tail, dynamic,
            )
            sys_c = self._sys(sys_c)
            try:
                piece = self.llm.chat(
                    sys_c, user_c,
                    temperature=0.7,
                    max_tokens=2000,
                    frequency_penalty=0.3,
                    presence_penalty=0.15,
                )
                piece = _strip_html_tags(piece)
                scene_texts.append(piece)
                # 下一场景的衔接用本场景结尾
                running_tail = piece[-120:] if piece else running_tail
            except Exception as e:
                print(f"    [场景] 第{idx}个场景生成失败: {e}")
                scene_texts.append("")

        chapter_text = "\n\n".join(t for t in scene_texts if t)

        # 3. 润色通读（中温 0.7）
        try:
            sys_p, user_p = polish_chapter_prompt(
                self.cfg, chapter_no, plan_title, chapter_text
            )
            polished = self.llm.chat(
                sys_p, user_p,
                temperature=0.7,
                max_tokens=4000,
                frequency_penalty=0.2,
                presence_penalty=0.1,
            )
            if polished and len(polished) > len(chapter_text) * 0.5:
                chapter_text = polished
        except Exception:
            pass

        # 4. 追加摘要（复用普通模式的摘要生成）
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
    """从模型输出中提取 JSON 对象（容忍 ```json 包裹）。"""
    import json
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t
