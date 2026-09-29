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
from .guard import AgentGuard, AgentGuardError
from .memory import Memory
from .stages import ChapterWriter, OutlineGenerator, WorldGenerator, _strip_html_tags
from .templates import (continue_outline_prompt, expand_chapter_prompt,
                        expand_outline_prompt, world_from_content_prompt,
                        verify_prompt, final_review_prompt)
from .trace import TraceRecorder
from .editor import EditorReviewer
from .orchestrator import Orchestrator
from .agents import ResearcherAgent, ArchitectAgent, WriterAgent, ReviewerAgent, StylistAgent
from webapp.logger import get_logger

logger = get_logger("pipeline")


def _parse_json_obj(text: str) -> dict:
    """容错解析 LLM 输出的 JSON 对象（去 ``` 围栏）；失败返回空 dict。"""
    try:
        import json
        t = (text or "").strip()
        if t.startswith("```"):
            lines = t.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            t = "\n".join(lines).strip()
        data = json.loads(t)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class NovelPipeline:
    def __init__(self, cfg: NovelConfig, store: ProjectStore, llm: LLMClient,
                 trace: TraceRecorder | None = None):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.trace = trace
        # 模块1/2：把全链路埋点与成本防护挂到 LLM 客户端——LLM 层模板方法
        # 自动覆盖所有调用（正文/大纲/审校/校验/重试），调用方零改动。
        if trace is not None:
            llm.recorder = trace
        # 成本防护只针对真实第三方成本：mock provider（离线测试/无 Key 冒烟）不挂 guard，
        # 避免本地假模型流程被阈值误拦，也保持离线测试行为与改造前一致。
        if str(cfg.llm.provider).lower() == "mock":
            from ..config import AgentGuardConfig
            self.guard = AgentGuard(AgentGuardConfig(enabled=False), recorder=trace)
        else:
            self.guard = AgentGuard(cfg.agent_guard, recorder=trace)
        llm.guard = self.guard
        self.memory = Memory(store, cfg.memory_window)
        # 架构总控（多Agent阶段门禁 / 冲突仲裁，REQ 多Agent协作分层优化）
        self.orchestrator = Orchestrator(cfg, store, llm, trace)
        # 多智能体角色（v2 架构）
        self.researcher = ResearcherAgent(cfg, llm, store)
        self.architect = ArchitectAgent(cfg, llm, store)
        self.writer = WriterAgent(cfg, llm, store, self.memory)
        self.reviewer = ReviewerAgent(cfg, llm, store)
        # 写作风格角色（风格师）：项目选了风格角色时承载其文风人设与挂载技能
        self.stylist = StylistAgent(cfg, llm, store)
        # 兼容旧引用（现有方法继续用）
        self.outline_gen = OutlineGenerator(cfg, llm, store)
        self.world_gen = WorldGenerator(cfg, llm, store)
        self.chapter_writer = ChapterWriter(cfg, llm, store, self.memory)
        self.editor = EditorReviewer(llm)
        # 模块4：把埋点/防护透传给章内自校验节点（WriterAgent 内部创建的 ChapterWriter）
        try:
            if hasattr(self.writer, "_writer"):
                self.writer._writer.trace = trace
                self.writer._writer.guard = self.guard
        except Exception:
            pass
        self.chapter_writer.trace = trace
        self.chapter_writer.guard = self.guard

    def _trace_step(self, name: str, extra: dict | None = None) -> None:
        """阶段业务语义埋点（agent_step）。"""
        if self.trace is not None:
            self.trace.record_agent_step(name, extra=extra)

    def _enter_scene(self, scene: str) -> None:
        if self.guard is not None:
            self.guard.enter_scene(scene)

    # ---------- 各阶段（幂等：已存在则直接返回） ----------
    def is_expand_mode(self) -> bool:
        """是否 AI 扩写模式（跳过调研，直接用用户提供的大纲/内容）。"""
        return self.cfg.mode == "expand"

    def run_research(self, silent: bool = False) -> ResearchReport | None:
        """题材调研阶段（委托 ResearcherAgent）。"""
        self._enter_scene("research")
        self._trace_step("调研开始")
        return self.researcher.run(silent=silent)

    def run_outline(self, silent: bool = False) -> Outline:
        self._enter_scene("outline")
        existing = self.store.load_outline()
        if existing is not None:
            if not silent:
                print(f"[大纲] 已存在，复用：{existing.title}（{len(existing.chapters)} 章）")
            return existing
        # 扩写模式：从用户提供的大纲/简要内容生成章节大纲，跳过调研
        if self.is_expand_mode():
            return self._run_expand_outline(silent=silent)
        def _produce():
            logger.info("阶段开始: 大纲 outline（架构师）")
            self._trace_step("大纲生成开始")
            research = self.run_research(silent=True)
            res = self.architect.run_outline(research, silent=silent)
            logger.info("阶段完成: 大纲 outline（%s，%d 章）", res.title, len(res.chapters))
            self._trace_step("大纲生成完成", {"chapters": len(res.chapters)})
            return res
        # 骨架阶段准出门禁（校验并记录；大纲重生成会级联清依赖，暂不自动打回）
        return self.orchestrator.run_stage("skeleton", _produce, invalidate=None)

    def _run_expand_outline(self, silent: bool = False) -> Outline:
        """扩写模式：把用户提供的大纲/简要内容拆解为结构化章节大纲。

        分批生成：先出总纲（书名/简介/冲突/主线/分卷），再按 8 章一批生成章节，
        避免一次性输出过长 JSON 被 max_tokens 截断（DeepSeek 上限 8192）。
        """
        if not self.cfg.expand_input.strip():
            raise RuntimeError("AI 扩写模式需要提供大纲或简要内容（expand_input 为空）")
        user_input = self.cfg.expand_input.strip()
        target = self.cfg.num_chapters
        batch_size = self.outline_gen._BATCH_SIZE
        logger.info("阶段开始: 扩写大纲（expand_input %d 字，目标 %d 章，分批 %d 章/批）",
                    len(user_input), target, batch_size)

        # 第一批：总纲（不含章节）
        head_system, head_user = expand_outline_prompt(self.cfg, user_input, batch=None)
        head = self.llm.chat_json(head_system, head_user, temperature=0.7, max_tokens=8192)
        title = head.get("title") or self.cfg.effective_title()
        intro = head.get("intro", "")
        core_conflict = head.get("core_conflict", "")
        main_line = head.get("main_line", "")
        from ..store.schemas import Volume
        volumes = [Volume.from_dict(v) for v in (head.get("volumes") or []) if isinstance(v, dict)]

        # 分批生成章节
        chapters: list[ChapterPlan] = []
        prev_summary = ""
        for start in range(1, target + 1, batch_size):
            end = min(start + batch_size - 1, target)
            if not silent:
                print(f"  [扩写大纲分批] 第 {start}—{end} 章 ...")
            sys_p, usr_p = expand_outline_prompt(
                self.cfg, user_input, batch=(start, end, prev_summary),
            )
            data = self.llm.chat_json(sys_p, usr_p, temperature=0.7, max_tokens=8192)
            batch_chapters = [ChapterPlan.from_dict(c) for c in data.get("chapters", [])]
            for i, c in enumerate(batch_chapters):
                c.no = start + i
            chapters.extend(batch_chapters)
            recent = batch_chapters[-3:]
            prev_summary = "；".join(
                f"第{c.no}章《{c.title}》：{c.goal}" for c in recent
            )

        outline = Outline(
            title=title, intro=intro, core_conflict=core_conflict,
            main_line=main_line, volumes=volumes, chapters=chapters,
        )
        outline = self.outline_gen._normalize(outline)
        self.store.save_outline(outline)
        if not silent:
            print(f"[扩写大纲完成] {outline.title}｜共 {len(outline.chapters)} 章")
        logger.info("阶段完成: 扩写大纲（%s，%d 章）", outline.title, len(outline.chapters))
        return outline

    def run_timeline(self, silent: bool = False) -> None:
        """大纲生成后，自动设计时间线结构。幂等：已有则跳过。"""
        outline = self.store.load_outline()
        if outline is None:
            return
        if outline.timelines:
            if not silent:
                print(f"[时间线] 已存在，复用：{len(outline.timelines)} 条")
            return
        try:
            from .templates import timeline_design_prompt
            from ..store.schemas import Timeline
            sys_t, user_t = timeline_design_prompt(
                self.cfg, self.cfg.genre, outline.title, outline.intro, len(outline.chapters)
            )
            raw = self.llm.chat(sys_t, user_t, temperature=0.4, max_tokens=1000)
            import json
            t = raw.strip()
            if t.startswith("```"):
                lines = t.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                t = "\n".join(lines).strip()
            data = json.loads(t)
            outline.timelines = [Timeline.from_dict(x) for x in data.get("timelines", [])]
            # 给每章标注时间线（简单轮询：主线占60%，支线穿插）
            if outline.timelines:
                main_name = outline.timelines[0].name
                for i, ch in enumerate(outline.chapters):
                    # 默认主线，每5章插一条支线
                    ch.timeline = main_name
                if len(outline.timelines) > 1:
                    side = outline.timelines[1]
                    for i, ch in enumerate(outline.chapters):
                        if (i + 1) % 4 == 0:  # 每4章插一章支线
                            ch.timeline = side.name
            self.store.save_outline(outline)
            if not silent:
                print(f"[时间线] 设计完成：{len(outline.timelines)} 条")
            logger.info("时间线设计完成：%d 条", len(outline.timelines))
        except Exception as e:
            logger.warning("时间线设计失败（不阻塞）: %s", e)

    def run_units(self, silent: bool = False) -> None:
        """时间线设计后，按3章一个副本拆分叙事单元。幂等：已有则跳过。"""
        outline = self.store.load_outline()
        if outline is None:
            return
        if outline.units:
            if not silent:
                print(f"[副本结构] 已存在，复用：{len(outline.units)} 个")
            return
        try:
            from .templates import unit_design_prompt
            from ..store.schemas import StoryUnit
            sys_u, user_u = unit_design_prompt(
                self.cfg, self.cfg.genre, outline.title, outline.intro,
                len(outline.chapters), outline.timelines
            )
            raw = self.llm.chat(sys_u, user_u, temperature=0.4, max_tokens=2000)
            import json
            t = raw.strip()
            if t.startswith("```"):
                lines = t.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                t = "\n".join(lines).strip()
            data = json.loads(t)
            outline.units = [StoryUnit.from_dict(x) for x in data.get("units", [])]
            self.store.save_outline(outline)
            if not silent:
                print(f"[副本结构] 设计完成：{len(outline.units)} 个副本")
            logger.info("副本结构设计完成：%d 个", len(outline.units))
        except Exception as e:
            logger.warning("副本结构设计失败（不阻塞）: %s", e)

    def run_world(self, silent: bool = False) -> World:
        """人物/世界观（底层锚点）阶段，经 StageGate 准出门禁（REQ 多Agent协作分层优化）。"""
        self._enter_scene("world")

        def _produce():
            existing = self.store.load_world()
            if existing is not None:
                if not silent:
                    print(f"[人物/世界观] 已存在，复用：{len(existing.characters)} 个人物，{len(existing.foreshadowing)} 条伏笔")
                return existing
            outline = self.run_outline(silent=True)
            self.run_timeline(silent=silent)
            self.run_units(silent=silent)
            research = self.store.load_research() or self.run_research(silent=True)
            logger.info("阶段开始: 人物/世界观 world（架构师）")
            self._trace_step("人物/世界观生成开始")
            result = self.architect.run_world(silent=silent)
            logger.info("阶段完成: 人物/世界观 world（%d 个人物）", len(result.characters))
            self._trace_step("人物/世界观生成完成", {"characters": len(result.characters)})
            return result

        def _invalidate():
            try:
                self.store.world_path().unlink(missing_ok=True)
                if not silent:
                    print("[StageGate] world 打回重生成（已清产物）")
            except Exception:
                pass
        # 底层锚点准出门禁：金手指五要素 / 伏笔闭环 / 主题锚点不合格自动重生成
        world = self.orchestrator.run_stage("bottom_anchors", _produce, invalidate=_invalidate)
        # REQ-token优化 P1-1：world 落盘后确定性构建分章设定卡索引（零 LLM，失败降级全量注入）
        self._build_setting_cards(world, silent=silent)
        return world

    def _build_setting_cards(self, world: World | None, silent: bool = False) -> None:
        """P1-1：world 落盘后确定性构建分章设定卡索引（零 LLM，Python 前置处理）。
        失败/关闭/已存在 → 降级或跳过，绝不阻塞主流程。"""
        try:
            if not getattr(self.cfg, "setting_cards", True):
                return
            if self.store.load_setting_cards() is not None:
                return
            if world is None:
                return
            outline = self.store.load_outline()
            from .setting_cards import build_setting_cards
            data = build_setting_cards(self.store, world, outline)
            self.store.save_setting_cards(data)
            if data and not silent:
                print(f"[设定卡索引] 已构建：{len(data.get('chapters', []))} 章")
        except Exception:
            logger.exception("分章设定卡构建失败（降级为全量注入）")

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
        self._enter_scene("review")
        self._trace_step("编辑质检开始")
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
        self._enter_scene("chapter")
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
        else:
            # REQ-token优化 P1-1：world 已存在但索引缺失时惰性构建（幂等，失败降级）
            self._build_setting_cards(self.store.load_world(), silent=silent)
        research = None
        if not self.is_expand_mode():
            research = self.store.load_research() or self.run_research(silent=True)
        logger.info("章节开始: 第 %d 章「%s」（写手）is_final=%s", no, plan.title, is_final)
        self._trace_step(f"第{no}章正文生成开始", {"title": plan.title, "is_final": is_final})
        try:
            chapter = self.writer.write_chapter(no, plan.title, plan.goal, research,
                                                silent=silent, is_final=is_final)
        except Exception:
            logger.exception("章节失败: 第 %d 章「%s」", no, plan.title)
            raise
        logger.info("章节完成: 第 %d 章「%s」（%d 字）", no, plan.title,
                    len(chapter.content) if chapter else 0)
        self._trace_step(f"第{no}章正文生成完成", {"words": len(chapter.content) if chapter else 0})

        return chapter

    def _update_character_states(self, no: int, silent: bool = False) -> None:
        """P0-2：每章正文落盘后更新人物动态状态快照（失败降级，不阻塞生成）。"""
        try:
            from .character_state import extract_character_states
            world = self.store.load_world()
            ch = self.store.load_chapter(no)
            if not world or not ch or not ch.content:
                return
            old = self.store.load_character_states()
            new = extract_character_states(self.llm, self.cfg, world, ch, no, old)
            self.store.save_character_states(new)
        except Exception:
            logger.exception("人物状态更新失败（降级跳过）: no=%d", no)

    def _update_chapter_state(self, no: int, silent: bool = False) -> None:
        """REQ-章内自洽与机制账本：每章落盘后抽取章末状态（机制数字/人物生死/
        物品/时间/事实），供下一章承接；失败降级，不阻塞生成。"""
        try:
            from .chapter_state import extract_chapter_state
            ch = self.store.load_chapter(no)
            if not ch or not ch.content:
                return
            world = self.store.load_world()
            state = extract_chapter_state(self.llm, self.cfg, world, ch, no,
                                          old_state=self.store.load_chapter_state())
            if state:
                self.store.save_chapter_state(state)
        except Exception:
            logger.exception("章末状态抽取失败（降级跳过）: no=%d", no)

    def _run_verify(self, chapter_no: int, silent: bool = False) -> None:
        """每 verify_every 章跑一次全局一致性校验（REQ-token优化 P1-5）。

        默认合并为 1 次调用（verify_combined_prompt 输出一致性/读者/编辑三类结果），
        输入不再把世界观与摘要重复喂 3 遍；verify_combined=False 时回退原 3 连调用。
        只写 issues 文件，不改已生成章节。
        """
        try:
            summaries = self.store.load_summaries()
            recent = [s for s in summaries if int(s.get("no", 0)) <= chapter_no][-3:]
            world = self.store.load_world()
            world_text = ""
            if world is not None:
                world_text = (world.world_view or "") + "\n" + "; ".join(
                    f"{c.name}({c.role})" for c in (world.characters or []) if c.name
                )
            ch = self.store.load_chapter(chapter_no)
            chapter_text = ch.content if (ch and ch.content) else ""
            if getattr(self.cfg, "verify_combined", True):
                from .templates import verify_combined_prompt
                sys_v, user_v = verify_combined_prompt(
                    self.cfg, chapter_no,
                    [s["summary"] for s in recent], world_text, chapter_text[:2000],
                )
                raw = self.llm.chat(sys_v, user_v, temperature=0.3, max_tokens=1600)
                data = _parse_json_obj(raw)
                issues = data.get("issues") or []
                if issues:
                    self.store.append_verify_issues(chapter_no, issues)
                    if not silent:
                        print(f"  [校验] 第{chapter_no}章后发现 {len(issues)} 个一致性问题，已记录供后续章节修正")
                    logger.info("一致性校验: 第%d章后发现 %d 个问题", chapter_no, len(issues))
                reader = data.get("reader") or {}
                r_issues = reader.get("issues") or []
                if reader.get("confused") or r_issues:
                    self.store.append_verify_issues(chapter_no, r_issues)
                    if not silent:
                        print(f"  [自检] 第{chapter_no}章后读者视角发现信息传递问题")
                editor = data.get("editor") or {}
                score = editor.get("score", 5)
                churn = editor.get("reader_churn_risk", "中")
                if not silent:
                    print(f"  [编辑审稿] 第{chapter_no}章评分: {score}/10 弃书风险: {churn}")
                logger.info("编辑审稿: 第%d章 score=%s churn=%s", chapter_no, score, churn)
                self._trace_step(f"第{chapter_no}章编辑审稿", {"score": score, "churn": churn})
                try:
                    _sc = int(score)
                except (TypeError, ValueError):
                    _sc = 5
                e_issues = editor.get("issues") or []
                if _sc < 5 or churn == "高":
                    if e_issues:
                        self.store.append_verify_issues(chapter_no, e_issues)
            else:
                # 回退：原 3 连调用（verify_combined=False）
                sys_v, user_v = verify_prompt(self.cfg, [s["summary"] for s in recent], world_text)
                raw = self.llm.chat(sys_v, user_v, temperature=0.3, max_tokens=800)
                data = _parse_json_obj(raw)
                issues = data.get("issues", [])
                if issues:
                    self.store.append_verify_issues(chapter_no, issues)
                    if not silent:
                        print(f"  [校验] 第{chapter_no}章后发现 {len(issues)} 个一致性问题，已记录供后续章节修正")
                    logger.info("一致性校验: 第%d章后发现 %d 个问题", chapter_no, len(issues))
                from .templates import blind_check_prompt
                sys_b, user_b = blind_check_prompt(
                    self.cfg, chapter_no,
                    [s["summary"] for s in recent],
                    world_text,
                )
                raw_b = self.llm.chat(sys_b, user_b, temperature=0.3, max_tokens=500)
                b_data = _parse_json_obj(raw_b)
                if b_data.get("confused") or b_data.get("issues"):
                    self.store.append_verify_issues(chapter_no, b_data.get("issues", []))
                    if not silent:
                        print(f"  [自检] 第{chapter_no}章后读者视角发现信息传递问题")
                from .templates import editor_review_text_prompt
                if ch and ch.content:
                    sys_e, user_e = editor_review_text_prompt(
                        self.cfg, chapter_no, ch.title, ch.content,
                        [s["summary"] for s in recent],
                    )
                    raw_e = self.llm.chat(sys_e, user_e, temperature=0.3, max_tokens=800)
                    e_data = _parse_json_obj(raw_e)
                    score = e_data.get("score", 5)
                    churn = e_data.get("reader_churn_risk", "中")
                    if not silent:
                        print(f"  [编辑审稿] 第{chapter_no}章评分: {score}/10 弃书风险: {churn}")
                    logger.info("编辑审稿: 第%d章 score=%d churn=%s", chapter_no, score, churn)
                    self._trace_step(f"第{chapter_no}章编辑审稿", {"score": score, "churn": churn})
                    if score < 5 or churn == "高":
                        e_issues = e_data.get("issues", [])
                        if e_issues:
                            self.store.append_verify_issues(chapter_no, e_issues)
        except Exception as e:
            logger.warning("一致性校验失败（不阻塞生成）: %s", e)


    def _run_final_review(self, silent: bool = False) -> None:
        """P2-2 全书终审：整本书写完后的终审编辑（final_review 开关或高质量模式开启时）。
        结果写 final_review.json；失败降级不阻塞导出与结算。"""
        try:
            final_on = getattr(self.cfg, "final_review", False) or getattr(self.cfg, "high_quality_mode", False)
            if not final_on:
                return
            outline = self.store.load_outline()
            chapters = self.store.list_chapters()
            if not outline or not chapters:
                return
            import time as _time
            summaries = self.store.load_summaries()
            by_no = {c.no: c.title for c in chapters}
            seen = {int(s.get("no", 0)) for s in summaries}
            lines = [f"第{s.get('no','')}章《{by_no.get(s.get('no'), '')}》：{s.get('summary','')}" for s in summaries]
            for c in chapters:
                if c.no not in seen:
                    lines.append(f"第{c.no}章《{c.title}》：")
            lines.sort(key=lambda x: int(x.split("章", 1)[0].replace("第", "")) if x.startswith("第") else 0)
            block = "\n".join(lines)
            world = self.store.load_world()
            world_text = ""
            if world is not None:
                world_text = (world.world_view or "") + "\n人物：" + "; ".join(
                    f"{c.name}（{c.role}）——{c.desc}" for c in (world.characters or []) if c.name
                )
            sys_f, user_f = final_review_prompt(self.cfg, outline, block, world_text)
            data = self.llm.chat_json(sys_f, user_f, temperature=0.3)
            issues = data.get("issues", []) if isinstance(data, dict) else []
            ok = bool(data.get("ok", not issues))
            self.store.save_final_review({
                "checked_at": _time.time(),
                "ok": ok,
                "issues": issues[:30],
                "summary": f"全书终审：发现 {len(issues)} 个整本层面问题（{'通过' if ok else '建议修复'}）",
            })
            if not silent:
                print(f"[全书终审] 完成：{len(issues)} 个整本层面问题（不阻塞导出）")
            self._trace_step("全书终审完成", {"issues": len(issues)})
        except Exception as e:
            logger.warning("全书终审失败（降级跳过，不阻塞导出）: %s", e)

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
        state.error = ""
        outline = self.store.load_outline()
        state.total_chapters = len(outline.chapters) if outline else state.total_chapters
        self.store.save_state(state)

    # ---------- 全流程 / 续跑 ----------
    def is_continue_mode(self) -> bool:
        """续写模式：用户已有分章正文，AI 从断点续写（content_type=chapters）。"""
        return self.cfg.content_type == "chapters"

    def is_expand_existing_mode(self) -> bool:
        """扩写已有章节模式：把用户已有章节扩写到目标字数（content_type=chapters_expand）。"""
        return self.cfg.content_type == "chapters_expand"

    def resume(self, silent: bool = False) -> None:
        """从当前进度继续：缺失产物即生成，已生成章节自动跳过。"""
        if not silent:
            self._print_plan()
        logger.info("流水线开始: 《%s》mode=%s content_type=%s total_words=%d",
                    self.cfg.effective_title(), self.cfg.mode, self.cfg.content_type, self.cfg.total_words)
        if self.is_continue_mode():
            self._resume_continue(silent=silent)
            return
        if self.is_expand_existing_mode():
            self.run_expand_existing(silent=silent)
            return

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
            # P0-2：每章正文落盘后更新人物动态状态快照（可开关，失败降级不阻塞）
            if getattr(self.cfg, "character_state_tracking", True):
                self._update_character_states(no, silent=silent)
            # REQ-章内自洽与机制账本：每章落盘后抽取章末状态供下一章承接（可开关，失败降级）
            if getattr(self.cfg, "chapter_state_tracking", True):
                self._update_chapter_state(no, silent=silent)
            # 高质量模式：每 verify_every 章跑一次全局一致性校验（REQ-token优化 P1-5，原每 3 章），结果只影响后续章节
            _v_every = max(1, int(getattr(self.cfg, "verify_every", 5) or 5))
            if getattr(self.cfg, 'high_quality_mode', False) and no % _v_every == 0 and no < total:
                self._run_verify(no, silent=silent)

        self._mark_state("done", last_chapter=total)
        self._write_design_reports(silent)
        self._run_final_review(silent=silent)
        if not silent:
            print(f"\n全部 {total} 章生成完毕。")
        logger.info("流水线完成: 《%s》共 %d 章", self.cfg.effective_title(), total)

    # ---------- 续写 / 扩写已有内容 ----------
    def _user_chapters(self) -> list[Chapter]:
        """已入库的用户手写章节（source=user）。"""
        return [c for c in self.store.list_chapters() if c.source == "user"]

    def _prepare_user_chapters(self, silent: bool = False) -> None:
        """为用户章节生成摘要并写入记忆库（续写衔接用）。幂等：已有摘要跳过。"""
        for ch in self._user_chapters():
            if ch.summary:
                continue
            summary = self.chapter_writer._gen_summary(ch.no, ch.title, ch.content)
            if summary:
                ch.summary = summary
                self.store.save_chapter(ch)
                self.store.append_summary(ch.no, summary)
        if not silent:
            print(f"[续写准备] 用户章节摘要：{len(self._user_chapters())} 章已就绪")

    def _world_from_content(self, silent: bool = False) -> World:
        """续写模式：基于用户正文提炼世界观/人物卡/伏笔（幂等：world.json 存在则复用）。"""
        existing = self.store.load_world()
        if existing is not None:
            if not silent:
                print(f"[设定提炼] 已存在，复用：{len(existing.characters)} 个人物")
            return existing
        block = "\n\n".join(
            f"第{c.no}章《{c.title}》\n{c.content[:1500]}" for c in self._user_chapters()
        )
        logger.info("阶段开始: 设定提炼 world_from_content")
        system, user = world_from_content_prompt(self.cfg, block)
        data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
        world = World.from_dict(data)
        if not world.world_view:
            world.world_view = f"{self.cfg.genre}题材原创世界观"
        self.store.save_world(world)
        if not silent:
            print(f"[设定提炼完成] {len(world.characters)} 个人物，{len(world.foreshadowing)} 条伏笔")
        logger.info("阶段完成: 设定提炼 world_from_content")
        return world

    def _continue_outline(self, silent: bool = False) -> Outline:
        """续写模式大纲：用户章（标题取自文件）+ AI 规划的后续章。幂等：大纲含续写规划则复用。"""
        existing = self.store.load_outline()
        if existing is not None and len(existing.chapters) > len(self._user_chapters()):
            if not silent:
                print(f"[续写大纲] 已存在，复用：{len(existing.chapters)} 章")
            return existing
        user_chs = self._user_chapters()
        user_block = "\n".join(
            f"第{c.no}章《{c.title}》：{c.content[:200]}" for c in user_chs
        )
        remain = max(1, self.cfg.num_chapters - len(user_chs))
        logger.info("阶段开始: 续写大纲 continue_outline（剩余 %d 章）", remain)
        system, user = continue_outline_prompt(self.cfg, user_block, remain)
        data = self.llm.chat_json(system, user, temperature=0.7, max_tokens=8192)
        plans = []
        for i, c in enumerate(data.get("chapters", []), start=1):
            title = (c.get("title") or "").strip() or f"第{len(user_chs) + i}章"
            goal = (c.get("goal") or "").strip() or "推进主线剧情，制造冲突并留下悬念。"
            plans.append(ChapterPlan(no=len(user_chs) + i, title=title, goal=goal))
        if not plans:
            raise RuntimeError("续写大纲未生成任何后续章节")
        # 用户章：标题取自文件，goal 留空（diff 不误报）
        for i, c in enumerate(user_chs, start=1):
            plans.insert(0, ChapterPlan(no=i, title=c.title or f"第{i}章", goal=""))
        outline = Outline(title=existing.title if existing else self.cfg.effective_title(),
                          intro=existing.intro if existing else "",
                          chapters=plans)
        outline = self.outline_gen._normalize(outline)
        self.store.save_outline(outline)
        if not silent:
            print(f"[续写大纲完成] 共 {len(outline.chapters)} 章（用户 {len(user_chs)} 章 + 续写 {remain} 章）")
        logger.info("阶段完成: 续写大纲（共 %d 章）", len(outline.chapters))
        return outline

    def _resume_continue(self, silent: bool = False) -> None:
        """续写流程：用户章摘要 → 设定提炼 → 续写大纲 → 从断点逐章续写。"""
        self._mark_state("outline")
        self._prepare_user_chapters(silent=silent)
        self._mark_state("world")
        self._world_from_content(silent=silent)
        self._mark_state("writing")
        outline = self._continue_outline(silent=silent)
        done, total = self.progress()
        for no in range(done + 1, total + 1):
            self.write_chapter(no, silent=silent)
            self._mark_state("writing", last_chapter=no)
        self._mark_state("done", last_chapter=total)
        self._write_design_reports(silent)
        self._run_final_review(silent=silent)
        if not silent:
            print(f"\n续写完成：用户 {len(self._user_chapters())} 章 + AI {total - len(self._user_chapters())} 章，共 {total} 章。")
        logger.info("续写流水线完成: 《%s》共 %d 章", self.cfg.effective_title(), total)

    def _write_design_reports(self, silent: bool = False) -> None:
        """成果整合（REQ 多Agent协作分层优化，阶段E）：全书完成后落盘 4 份设计文档。
        失败降级不阻塞导出与结算。问题来源：对当前产物跑一次底层锚点/骨架/节奏校验。"""
        try:
            from .report import write_reports
            world = self.store.load_world()
            outline = self.store.load_outline()
            if world is None or outline is None:
                return
            collected = []
            try:
                v = self.orchestrator.validator
                collected += v.validate_stage("bottom_anchors", world)
                collected += v.validate_stage("skeleton", outline)
                collected += v.validate_stage("pacing", outline)
            except Exception:
                collected = []
            paths = write_reports(self.store, world, outline, issues=collected,
                                  arbitrations=self.orchestrator.arbitrations)
            if paths and not silent:
                print(f"[成果整合] 已生成设计文档 {len(paths)} 份 → design/")
        except Exception:
            pass

    def _with_stylist(self, system: str) -> str:
        """正文生成统一叠加风格角色人设（项目选了风格角色时）。"""
        if self.stylist.is_active:
            persona = self.stylist.persona_prefix()
            if persona:
                return f"{persona}\n\n{system}"
        return system

    def run_expand_existing(self, silent: bool = False) -> None:
        """扩写已有章节：对用户章节（或 expand_targets 指定章节）扩写到单章目标字数。
        保留原剧情/对话/人设，只扩充细节；扩写后覆盖正文并重生成摘要。"""
        self._prepare_user_chapters(silent=silent)
        self._world_from_content(silent=silent)
        targets = [int(x) for x in (self.cfg.expand_targets or [])]
        user_chs = self._user_chapters()
        if not user_chs:
            raise RuntimeError("扩写模式需要用户已有章节（未在创建时写入）")
        if targets:
            chapters = [c for c in user_chs if c.no in targets]
        else:
            chapters = user_chs
        if not chapters:
            logger.warning("扩写范围为空: targets=%s", targets)
            return
        target_words = max(self.cfg.chapter_words, 500)
        for ch in chapters:
            if len(ch.content) >= target_words:
                if not silent:
                    print(f"  第{ch.no}章《{ch.title}》已达标（{len(ch.content)} 字），跳过")
                continue
            if not silent:
                print(f"  扩写第{ch.no}章《{ch.title}》（{len(ch.content)} 字 → {target_words} 字）...")
            logger.info("扩写章节: no=%d from=%d to=%d", ch.no, len(ch.content), target_words)
            try:
                system, user = expand_chapter_prompt(self.cfg, ch.no, ch.title, ch.content, target_words)
                system = self._with_stylist(system)
                text = self.llm.chat(system, user, temperature=0.8)
                text = _strip_html_tags(text)
                if len(text) > len(ch.content) + 50:
                    ch.content = text
                    new_summary = self.chapter_writer._gen_summary(ch.no, ch.title, ch.content)
                    if new_summary:
                        ch.summary = new_summary
                        self.store.append_summary(ch.no, new_summary)
                    self.store.save_chapter(ch)
                    if not silent:
                        print(f"  第{ch.no}章扩写完成（{len(ch.content)} 字）")
            except Exception:
                logger.exception("扩写失败: no=%d", ch.no)
        self._mark_state("done", last_chapter=len(self.store.list_chapters()))
        self._write_design_reports(silent)
        if not silent:
            print(f"\n扩写完成：共处理 {len(chapters)} 章。")
        logger.info("扩写完成: chapters=%d targets=%s", len(chapters), targets)

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
