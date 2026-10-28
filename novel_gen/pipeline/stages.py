# -*- coding: utf-8 -*-
"""
各阶段生成器：大纲 / 人物世界观 / 逐章正文。

每个生成器职责单一：构造提示词 → 调 LLM → 解析/归一化 → 落盘。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..llm.json_parse import parse_json_obj as _parse_json_obj
from ..utils.text import longest_common_substr_len as _lcs_len
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

# --- 大纲/世界观生成器已抽出到 outline_generators.py（§11.1 文件拆分）---
from .outline_generators import (
    OutlineGenerator, WorldGenerator,
    assign_volumes, unique_chapter_titles,
)

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
        #: REQ-reader-ledger：读者体验层叙事账本（读者认知/伏笔承诺/爽点配额）懒加载
        self._reader_ledger = None
        #: P0-8：最近一章场景累计机制数字基线（_generate_chapter_scenes 写入，
        #: write() 质检透传用；未走场景路径时为空 dict）
        self._scene_state_acc: dict = {}
        #: 用户级记忆库：用户级记忆注入块懒缓存（None=未读；关闭/失败 → "" 跳过）
        self._user_mem_loaded: str | None = None

    def _memory(self):
        """P0-7 懒加载小说级记忆；book_memory 开关关闭/初始化失败 → None（行为降级安全）。"""
        if self._book_memory is None and getattr(self.cfg, "book_memory", True):
            try:
                from .book_memory import BookMemory
                self._book_memory = BookMemory(self.store, self.cfg)
            except Exception:
                self._book_memory = None
        return self._book_memory

    def _reader_ledger_obj(self):
        """REQ-reader-ledger：懒加载读者体验层叙事账本；总开关关闭/失败 → None。

        方法名避开实例属性 self._reader_ledger（属性=缓存对象/关闭标记，不与方法同名）。"""
        if self._reader_ledger is None and getattr(self.cfg, "reader_ledger", True):
            try:
                from .reader_ledger import ReaderLedger
                self._reader_ledger = ReaderLedger(self.store, self.cfg)
            except Exception:
                logger.exception("读者认知账本初始化失败（降级跳过）")
                self._reader_ledger = False   # 初始化失败置 False，避免每章重复尝试
        return self._reader_ledger or None

    def _book_canon(self, max_chars: int = 800) -> str:
        """P0-7：设定账本注入块（新名词规范/永久纪律/机制基线），空则空串。"""
        mem = self._memory()
        if mem is None:
            return ""
        try:
            return mem.canon_block(max_chars=max_chars)
        except Exception:
            return ""

    def _book_pitfalls(self, top_n: int = 3, min_count: int = 2,
                       self_ch: int | None = None) -> str:
        """P0-7：本作历史错误（场景生成纪律）注入块——只注入已反复出现（≥2 次）
        的踩坑，避免低频问题过度束缚正常生成。self_ch：当前章号（P3 去污染——
        last_ch==当前章 的本轮错误降权到尾部，避免把本章刚犯的错当历史教训回灌）。
        空则空串。"""
        mem = self._memory()
        if mem is None:
            return ""
        try:
            return mem.pitfall_block(top_n=top_n, min_count=min_count,
                                     self_ch=self_ch)
        except Exception:
            return ""

    def _teardown_anchor(self) -> str:
        """REQ-双锚风格锚定：懒读取拆书库题材锚（webapp 在生成线程启动时写入 store）。
        首次读取后缓存；文件缺失/读取失败返回空串（调用方不注入，行为降级安全）。
        S2.1 双锚开关治理：题材锚开关 `teardown_style_anchor` 默认关，关时一律不注入
        （2026-10-14 朱雀归因实测为独立负面变量）。"""
        if not getattr(self.cfg, "teardown_style_anchor", False):
            return ""
        if self._anchor_loaded is None:
            self._anchor_loaded = self.store.load_teardown_style_anchor()
        return self._anchor_loaded

    def _personal_anchor(self) -> str:
        """REQ-双锚风格锚定：懒提取个人旧稿锚（用户人工编辑过的章节片段）。
        无人工编辑章返回空串（调用方不注入）；提取失败静默返回空串（不阻塞生成）。
        S2.1 双锚开关治理：个人旧稿锚开关 `personal_style_anchor` 默认关，关时一律不注入
        （个人锚无条件注入可能放大模板化表达，需显式开启）。"""
        if not getattr(self.cfg, "personal_style_anchor", False):
            return ""
        if self._personal_loaded is None:
            try:
                self._personal_loaded = extract_personal_anchor(self.store.list_chapters())
            except Exception:
                self._personal_loaded = ""
        return self._personal_loaded

    def _rolling_style_anchor(self, chapter_no: int) -> str:
        """REQ-超长篇攻坚 S2.2 周期轻量再锚定：周期命中章用 book_memory goods 最近高分片段
        作滚动风格锚注入 system 前段，治几十万字后文风/语感漂移。

        - 开关 style_reanchor_every=0 或 style_anchor_rolling=False → 空串（不注入）；
        - 非周期命中章（chapter_no % every != 0）→ 空串（控成本，多数章不注入）；
        - good_block 为空/读取失败 → 空串（降级安全，不阻塞生成）。
        """
        every = int(getattr(self.cfg, "style_reanchor_every", 0) or 0)
        if every <= 0 or not getattr(self.cfg, "style_anchor_rolling", True):
            return ""
        if int(chapter_no or 0) % every != 0:
            return ""
        try:
            from .book_memory import BookMemory
            mem = BookMemory(self.store, self.cfg)
            return mem.good_block(top_n=3, max_chars=900)
        except Exception:
            logger.warning("滚动风格锚获取失败（降级跳过，不阻塞生成）", exc_info=True)
            return ""

    def _fixed_style_anchor(self) -> str:
        """REQ-超长篇攻坚 S2.3 固定锚+滚动锚双轨：固定锚 = 风格原点（开头 1-2 章原文 +
        个人旧稿），永不替换，与滚动锚（最近高分，S2.2）双轨合并注入。

        - 开关 fixed_style_anchor 默认 False（双锚体系默认关防放大 AI 特征，开启才注入）；
        - 开时取第1/2章正文开头片段作全书风格原点（一次性采样、不随章节滚动）；
        - 空源/读取失败 → 空串（降级安全，不阻塞生成）。
        """
        if not getattr(self.cfg, "fixed_style_anchor", False):
            return ""
        parts: list[str] = []
        try:
            for c in (1, 2):
                ch = self.store.load_chapter(c)
                content = getattr(ch, "content", "") or ""
                if len(content.strip()) >= 200:
                    parts.append(f"- 开头第{c}章原文：{content[:300].strip()}")
            pa = self._personal_anchor()
            if pa:
                parts.append(f"- 个人旧稿锚：{pa}")
        except Exception:
            logger.warning("固定风格锚获取失败（降级跳过，不阻塞生成）", exc_info=True)
        return "\n".join(parts)

    def _far_outline_beats(self, chapter_no: int) -> list[str] | None:
        """REQ-超长篇攻坚 S3.6 远期大纲粗粒度：返回距当前 > outline_far_skip 的远期大纲
        节点名集合，供 beat_gate 从判定分母排除（方向约束、不逐拍兑现）。

        取大纲 key_nodes（全局关键节点，带 chapter_no）中 chapter_no 距当前超过窗口的
        节点名；无大纲/读取失败返回 None（调用方按"无远期节点"处理，行为兼容默认关）。
        """
        try:
            outline = self.store.load_outline()
            far_skip = max(0, int(getattr(self.cfg, "outline_far_skip", 30) or 30))
            names: list[str] = []
            for kn in getattr(outline, "key_nodes", None) or []:
                kch = int(getattr(kn, "chapter_no", 0) or 0)
                if kch <= 0 or int(chapter_no or 0) <= 0:
                    continue
                if abs(int(chapter_no) - kch) > far_skip:
                    nm = str(getattr(kn, "kind", "") or "").strip()
                    if nm:
                        names.append(nm)
                    desc = str(getattr(kn, "description", "") or "").strip()
                    if desc:
                        names.append(desc)
            return names or None
        except Exception:
            logger.warning("远期大纲节点获取失败（降级跳过，不阻塞生成）", exc_info=True)
            return None

    def _beat_fail_judge(self, chapter_no: int, content: str,
                         plan_moves: str, missing: list[str]) -> None:
        """REQ-超长篇攻坚 S3.4 fail 章 LLM 二级复核。

        仅 beat_gate=fail 且开关 beat_fail_judge（默认关）时调用。用 1 次 pointwise judge
        复核 beat_gate 判定的"未兑现拍"是否真的未兑现：judge 与生成端异模型族（用便宜模型，
        anti-length：明确提示"正文少、拍已兑现不算未兑现"）。judge 确认未兑现的清单
        才写入 verify_issues（作为 L3 定点重写触发信号）；误报（judge 认为已兑现）不记录。
        上线前须自建 20-50 章人工标注集验证一致率>80%。失败降级跳过，不阻塞生成。
        """
        try:
            body = content or ""
            miss = [str(m) for m in (missing or []) if str(m).strip()]
            if not miss or len(body.strip()) < 100:
                return
            sys_j = ("你是剧情执行复核。下面给出本章【计划拍】与【正文】，逐条判断计划拍是否"
                     "真的未兑现。注意 anti-length：即使正文较短，只要拍的关键动作/事件确实发生"
                     "即算已兑现，不要因正文少就判未兑现。只输出 JSON："
                     "{\"unfulfilled\": [\"拍1\",\"拍2\"], \"reason\": \"简短理由\"}。"
                     "只列入【确实未兑现】的拍。")
            user_j = (f"【计划拍】\n" + "；".join(miss)
                      + f"\n\n【正文（第{chapter_no}章）】\n" + body[:2500])
            raw = self.llm.chat(sys_j, user_j, temperature=0.2, max_tokens=400)
            data = _parse_json_obj(raw)
            unfulfilled = [str(u) for u in (data.get("unfulfilled") or []) if str(u).strip()]
            if unfulfilled:
                reason = str(data.get("reason", "") or "")
                self.store.append_verify_issues(chapter_no, [{
                    "type": "BeatGate-fail 复核（L3触发）",
                    "detail": f"judge 确认未兑现 {len(unfulfilled)} 拍：{'、'.join(unfulfilled)}。"
                              f"{reason}",
                    "level": "中改",
                }])
                self._trace_step(f"第{chapter_no}章fail复核", {"unfulfilled": unfulfilled})
        except Exception as e:
            logger.warning("fail 章 LLM 复核失败（降级跳过，不阻塞生成）: %s", e)

    def _user_memory_block(self) -> str:
        """用户级记忆库：跨书偏好/纪律/文风锚注入块（REQ-用户级记忆库，软约束）。

        懒读 store.user_memory_snapshot.json（webapp 在生成入口写入）；归属校验：
        快照 uid 必须为正整数（webapp 写入方保证 uid=项目 owner，防跨项目数据串用）。
        无快照/开关关闭/格式非法 → 空串（调用方不注入，行为降级安全）。
        """
        if self._user_mem_loaded is None:
            self._user_mem_loaded = self._build_user_memory_block()
        return self._user_mem_loaded

    def _build_user_memory_block(self) -> str:
        """组装用户级记忆注入块（确定性组装，零 LLM；异常降级空串）。"""
        if not getattr(self.cfg, "user_memory", True):
            return ""
        try:
            snap = self.store.load_user_memory()
        except Exception:
            return ""
        if not snap:
            return ""
        uid = snap.get("uid")
        if not isinstance(uid, int) or int(uid) <= 0:
            return ""
        parts: list[str] = []
        dis = snap.get("disciplines") or []
        if dis:
            lines = [
                f"- {d.get('rule', '')}（跨 {int(d.get('books', 1) or 1)} 本书反复出现，严禁再犯）"
                for d in dis if d.get("rule")
            ]
            if lines:
                parts.append("【用户跨书纪律（来自用户历史作品踩坑账本，自动继承——"
                             "同一本书也会犯的跨书高频错误，本作严禁重犯）】\n" + "\n".join(lines))
        anchors = snap.get("style_anchors") or []
        if anchors:
            samples = [f"- {a.get('text', '')}" for a in anchors if a.get("text")]
            if samples:
                parts.append("【用户个人文风锚（用户历史作品人工修订片段，"
                             "借鉴语感与行文习惯，严禁抄袭内容）】\n" + "\n".join(samples))
        return "\n\n".join(parts)

    def _chapter_design_block(self, chapter_no: int) -> str:
        """REQ-chapter-design 一期：本章设计卡注入块（正向创作指引·软约束）。

        懒读 store/chapter_design.json，命中本章则 render_block；开关关闭/无设计卡/
        未命中/异常 → 空串（不注入，行为降级安全）。注入位置仿 cliffhanger 块，
        拼进 memory_context，自动覆盖场景路径/整章路径/回退整章三条写章链路。
        """
        if not getattr(self.cfg, "chapter_design", True):
            return ""
        try:
            data = self.store.load_chapter_design()
            if not data:
                return ""
            for c in data.get("chapters", []):
                if int(c.get("no", -1) if c.get("no") is not None else -1) == chapter_no:
                    from .chapter_design import render_block
                    return render_block(c.get("design") or {})
        except Exception:
            logger.exception("章级设计卡渲染失败（降级不注入）")
        return ""

    def _pending_compensation_block(self, chapter_no: int) -> str:
        """REQ-超长篇生成攻坚 S3.1 + S3.3：上章漏兑现节点补偿块 + 连续漏兑现挂账镜头。

        L1：读上章承诺台账，有漏兑现节点则生成补偿块（S3.1，0 成本）；
        L2：扫描最近 3 章台账，连续 ≥2 章漏兑现的节点生成"挂账镜头"（S3.3，0 成本；
            下章为高潮/战斗等豁免章型时不强加）。
        无漏兑现 / 开关关 / 无台账 / 异常 → 空串（不注入，零 token）。拼进 memory_context，
        自动覆盖场景/整章/回退整章三条写章链路。
        """
        if not getattr(self.cfg, "pending_commitments", True):
            return ""
        parts: list[str] = []
        try:
            prev = self.store.prev_pending_commitments(chapter_no)
            if prev:
                from .pending_commitments import compensation_block
                cb = compensation_block(prev)
                if cb:
                    parts.append(cb)
        except Exception:
            logger.exception("上章承诺补偿块注入失败（降级不注入）")
        try:
            from .pending_commitments import l2_hangover_block
            lb = l2_hangover_block(self.store, chapter_no,
                                   current_chapter_type=self._next_chapter_exempt_type(chapter_no))
            if lb:
                parts.append(lb)
        except Exception:
            logger.exception("挂账镜头注入失败（降级不注入）")
        return "\n\n".join(parts) if parts else ""

    # P1-4 BeatGate 正向供给（REQ-门禁质检正向影响 P1-4）：missing 拍「不仅列漏了，
    # 还给往哪写」——零 LLM 规则模板，把上章未兑现拍转成下一章的推进方向示范。
    _BEAT_POSITIVE_DIRS = (
        "让主角主动采取行动去达成该目标，让行动成为情节推进的引擎",
        "安排一个障碍或对抗，把缺口转成张力与冲突",
        "通过关键对话或旁白自然引出该信息，避免信息硬塞",
        "让前文埋下的伏笔在此处呼应兑现，形成闭环",
    )

    def _beat_positive_hints(self, missing) -> str:
        """把 missing 拍清单转成「可往哪个方向写」的示范块（空 → 空串）。"""
        if not missing:
            return ""
        lines = []
        for i, m in enumerate(missing[:4]):
            m = str(m).strip()
            if not m:
                continue
            d = self._BEAT_POSITIVE_DIRS[i % len(self._BEAT_POSITIVE_DIRS)]
            lines.append(f"「{m}」→ 可往：{d}")
        if not lines:
            return ""
        return ("【上章未兑现拍的推进方向（本章生成时优先兑现，避免欠账）】\n"
                + "\n".join(lines))

    def _beat_positive_block(self, chapter_no: int) -> str:
        """读上一章 beat_gate 的 positive_hints，注入本章生成（空/关闭/异常 → 空串）。"""
        if not getattr(self.cfg, "beat_positive_supply", True):
            return ""
        try:
            _bg_all = self.store.load_beat_gate()
            _prev = _bg_all.get(str(int(chapter_no) - 1)) or {}
            hints = _prev.get("positive_hints") or ""
            return hints if isinstance(hints, str) else ""
        except Exception:
            logger.exception("beat 正向供给注入失败（降级跳过）")
            return ""

    def _inspector_block(self, chapter_no: int) -> str:
        """P1-5 场景体检建议闭环：读上一章体检沉淀的 bad/warn 建议，注入本章生成。
        空/关闭/异常 → 空串（降级安全，不阻塞写章）。"""
        if not getattr(self.cfg, "scene_inspector", True):
            return ""
        try:
            _sug = self.store.load_inspector_suggestions().get(str(int(chapter_no) - 1)) or []
            _sug = [str(s) for s in _sug if str(s).strip()]
            if not _sug:
                return ""
            lines = "\n".join(f"- {s}" for s in _sug[:4])
            return "【上一章场景体检的改进建议（本章写作时规避或落实）】\n" + lines
        except Exception:
            logger.exception("场景体检建议注入失败（降级跳过）")
            return ""

    def _next_chapter_exempt_type(self, chapter_no: int) -> str:
        """S3.3 L2 章型豁免：读大纲本章 tag，命中高潮/战斗/对话等 → 返回章型名（挂账镜头豁免）。

        大纲无该章 tag / 无大纲 → 返回空（不强豁免，仍按连续漏兑现注入）。
        """
        try:
            outline = self.store.load_outline()
            for p in (getattr(outline, "chapters", None) or []):
                if int(getattr(p, "no", 0) or 0) == int(chapter_no):
                    tag = str(getattr(p, "tag", "") or "")
                    for t in ("对话", "过渡", "情绪", "高潮", "战斗"):
                        if t in tag:
                            return t
        except Exception:
            pass
        return ""

    def _sys(self, system: str) -> str:
        persona = (getattr(self.cfg, "persona_override", "") or "").strip() or self.role_prompt
        return f"{persona}\n\n{system}" if persona else system

    def _volume_card_block(self, chapter_no: int, outline) -> str:
        """REQ-写作正向引导 5.5：分卷卡注入块（卷目标传导，软约束指引）。

        卷 → 章 目标传导：给写手"本卷要改变的局面 + 主要障碍 + 卷末高潮/证明 +
        本章在卷中的位置（卷首铺垫/卷中推进/卷末高潮）"——先定卷末高潮，再倒推
        本章该完成什么。卷末章（chapter_no == volume.end_ch）强制收束本卷目标并留
        跨卷钩子（legacy）。无卷卡/无 volumes 归属/异常 → 空串（不注入，降级安全）。
        """
        try:
            cards = getattr(outline, "volume_cards", None) or []
            vols = getattr(outline, "volumes", None) or []
            if not cards or not vols:
                return ""
            vol = None
            for v in vols:
                start = int(getattr(v, "start_ch", 0) or 0)
                end = int(getattr(v, "end_ch", 0) or 0)
                if start and start <= chapter_no <= (end or start):
                    vol = v
                    break
            if vol is None:
                return ""
            card = None
            vno = int(getattr(vol, "no", 0) or 0)
            for c in cards:
                if int(getattr(c, "volume_no", 0) or 0) == vno:
                    card = c
                    break
            if card is None:
                return ""
            start = int(getattr(vol, "start_ch", 0) or 0)
            end = int(getattr(vol, "end_ch", 0) or 0)
            span = max(end - start, 1)
            pos = "卷中推进"
            if chapter_no - start <= span * 0.33:
                pos = "卷首铺垫（本章负责埋本卷矛盾的引子/铺陈局面，不急于兑现卷末高潮）"
            elif chapter_no >= end or (end - start) * 0.9 <= chapter_no - start:
                pos = "卷末高潮（本章必须推动卷末高潮临近/收束本卷主要矛盾）"
            goal = str(getattr(card, "goal", "") or "").strip()
            obstacle = str(getattr(card, "obstacle", "") or "").strip()
            climax = str(getattr(card, "climax", "") or "").strip()
            legacy = str(getattr(card, "legacy", "") or "").strip()
            lines = [f"【本卷目标·本章位置（REQ-写作正向引导：本卷为第{vno}卷《{getattr(vol, 'name', '') or ''}》，"
                     f"第{start}-{end}章；先定卷末高潮，再倒推本章）】"]
            if goal:
                lines.append(f"- 本卷要改变的局面：{goal}")
            if obstacle:
                lines.append(f"- 本卷主要障碍：{obstacle}")
            if climax:
                lines.append(f"- 卷末高潮与如何证明改变：{climax}")
            lines.append(f"- 本章在卷中的位置：{pos}")
            if chapter_no >= end and legacy:
                lines.append(f"- 跨卷钩子（本章结尾必须留出并指向下一卷的压力）：{legacy}")
            elif chapter_no >= end:
                lines.append("- 跨卷钩子（本章为卷末章，结尾必须留下卷末遗留压力，串起下一卷）")
            return "\n".join(lines)
        except Exception:
            logger.exception("分卷卡注入失败（降级不注入）")
        return ""

    def _inject_memory_blocks(self, chapter_no: int, world, memory_context: str,
                               plan_title: str, plan_goal: str) -> tuple[str, str, str]:
        """写章前注入各类记忆/约束块到 memory_context，返回增强后的 (memory_context, plan_core_event, plan_moves)。

        集中了补偿块/时间线/章末钩子/章级设计卡/分卷卡/读者账本/good/反思/建议/beat正向/
        体检建议/用户锁定/行动表等 ~15 个注入点。每个注入点独立 try/except 降级，
        单块失败不阻塞整章生成。
        """
        # REQ S1.3 注入位置工程：到期必兑现的补偿块放 prompt 最前部
        try:
            _comp_block = self._pending_compensation_block(chapter_no)
            if _comp_block:
                memory_context = memory_context + "\n\n" + _comp_block if memory_context else _comp_block
        except Exception:
            logger.exception("上章承诺补偿块注入失败（降级跳过）")

        # 注入时间线提示 + 章末钩子 + 副本上下文
        plan_core_event = ""
        plan_moves = ""
        outline = None
        try:
            outline = self.store.load_outline()
            if outline and chapter_no - 1 < len(outline.chapters):
                plan_core_event = outline.chapters[chapter_no - 1].core_event or ""
                plan_moves = getattr(outline.chapters[chapter_no - 1], "character_moves", "") or ""
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
            if outline and outline.units:
                from .templates import unit_hint_for_chapter
                unit_hint = unit_hint_for_chapter(outline.units, chapter_no)
                if unit_hint:
                    memory_context = memory_context + "\n\n" + unit_hint if memory_context else unit_hint
        except Exception:
            pass

        # 章级设计卡块
        try:
            _design_block = self._chapter_design_block(chapter_no)
            if _design_block:
                memory_context = memory_context + "\n\n" + _design_block if memory_context else _design_block
        except Exception:
            logger.exception("章级设计卡注入失败（降级跳过）")

        # 分卷卡块
        try:
            if outline is not None:
                _vc_block = self._volume_card_block(chapter_no, outline)
                if _vc_block:
                    memory_context = memory_context + "\n\n" + _vc_block if memory_context else _vc_block
        except Exception:
            logger.exception("分卷卡注入失败（降级跳过）")

        # 读者认知账本
        try:
            _rl = self._reader_ledger_obj()
            if _rl is not None:
                _rl_block = _rl.inject_block(chapter_no)
                if _rl_block:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + _rl_block
        except Exception:
            logger.exception("读者认知账本注入失败（降级跳过，不阻塞写章）")

        # good 账本
        try:
            _gmem = self._memory()
            if _gmem is not None:
                _good = _gmem.good_block(top_n=3)
                if _good:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + _good
        except Exception:
            logger.exception("good 账本注入失败（降级跳过，不阻塞写章；但 good 质感引导缺失）")

        # 反思笔记
        try:
            _rmem = self._memory()
            if _rmem is not None:
                _ref = _rmem.reflection_block(chapter_no)
                if _ref:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + _ref
        except Exception:
            logger.exception("反思笔记注入失败（降级跳过，不阻塞写章；反思先验缺失）")

        # 建议指令库
        try:
            _smem = self._memory()
            if _smem is not None and getattr(self.cfg, "suggestion_lib", True):
                _sug = _smem.suggestion_block()
                if _sug:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + _sug
        except Exception:
            logger.exception("建议指令库注入失败（降级跳过，不阻塞写章；编辑建议未携带）")

        # beat 正向供给
        try:
            _bp = self._beat_positive_block(chapter_no)
            if _bp:
                memory_context = (memory_context + "\n\n" if memory_context else "") + _bp
        except Exception:
            logger.exception("beat 正向供给注入失败（降级跳过，不阻塞写章）")

        # 场景体检建议
        try:
            _insp = self._inspector_block(chapter_no)
            if _insp:
                memory_context = (memory_context + "\n\n" if memory_context else "") + _insp
        except Exception:
            logger.exception("场景体检建议注入失败（降级跳过，不阻塞写章）")

        # 用户锁定内容
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

        # 人物行动表（仅 action_plan_first=True 时独立调用）
        if getattr(self.cfg, "action_plan_first", False):
            try:
                action_plan = self._gen_action_plan(
                    chapter_no, plan_title, plan_goal, memory_context,
                )
                if action_plan:
                    memory_context = (memory_context + "\n\n" if memory_context else "") + \
                        "【本章人物行动表（写手必须逐条落实：人物言行与行动表一致，剧情不空转、不OOC）】\n" + action_plan
            except Exception as e:
                logger.warning(f"  [行动表] 跳过: {e}")

        return memory_context, plan_core_event, plan_moves

    def _write_body(
        self, chapter_no: int, plan_title: str, plan_goal: str,
        research: ResearchReport | None, is_final: bool, silent: bool,
    ) -> tuple[str, str, str, str, str]:
        """生成并清洗正文，返回 (content, summary, system, plan_core_event, plan_moves)。

        步骤：机制基线同步 + 场景计数重置 → 组装 memory_context → 注入记忆块 →
        场景级/整章生成 → 清洗 HTML → 摘要兜底/脱节重生成 → 高质量模式自动改稿+读者审校。
        """
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

        memory_context, plan_core_event, plan_moves = self._inject_memory_blocks(
            chapter_no, world, memory_context, plan_title, plan_goal,
        )

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
                conflict_names=_world_conflict_names(world),
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
                user_memory_block=self._user_memory_block(),
            )
            # P0-15/16 通用版（2026-10-10）：普通整章路径同样注入机制面板字段白名单
            _wl = self._panel_whitelist_block()
            if _wl:
                user = user + _wl
            system = self._sys(system)
            # REQ-超长篇攻坚 S2.2/S2.3：周期轻量再锚定（滚动锚）+ 固定锚双轨注入
            _anchor_parts = []
            _ra = self._rolling_style_anchor(chapter_no)
            if _ra:
                _anchor_parts.append(f"【本作滚动风格锚（参考最近高分片段质感，勿逐字复制）】\n{_ra}")
            _fa = self._fixed_style_anchor()
            if _fa:
                _anchor_parts.append(f"【本作固定风格锚（风格原点，参考语感勿复制）】\n{_fa}")
            if _anchor_parts:
                system = "\n\n".join(_anchor_parts) + "\n\n" + system
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
                        logger.info(f"  [摘要] 模型自带摘要与正文脱节（含正文未出现的 3 字词），"
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
                logger.warning(f'  [改稿] 跳过: {e}')
            # 读者视角审校（只记录不改正文；P0-10 注入本作记忆 + 三项专项检查）
            try:
                from .reviser import ReaderReviewer
                reader = ReaderReviewer(self.cfg, self.llm, self.store,
                                        role_prompt=self.reader_role_prompt,
                                        memory=self._memory())
                reader.review(chapter_no, plan_title, plan_goal, content)
            except Exception as e:
                logger.warning(f'  [读者审校] 跳过: {e}')
        return content, summary, system, plan_core_event, plan_moves

    def write(
        self,
        chapter_no: int,
        plan_title: str,
        plan_goal: str,
        research: ResearchReport | None,
        silent: bool = False,
        is_final: bool = False,
    ) -> Chapter:
        content, summary, system, plan_core_event, plan_moves = self._write_body(
            chapter_no, plan_title, plan_goal, research, is_final, silent,
        )

        # 模块4：ReaderAgent 内置结果自校验（保存前；可开关、异常降级不阻塞）
        guard_cfg = getattr(self.cfg, "agent_guard", None)
        qc_passed = True
        _qc: dict = {}  # quality_check 关闭/异常时兜底（P0-1 合规红线读它需安全）
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
                    # 整章回退时场景累积为空 → 回落上一章末权威值（chapter_ledger
                    # 含跨章继承），使校验与重写注入拿到与生成端同口径的权威基线，
                    # 治整章路径机制数字无基线可依（REQ 5.3 二期补盲）。
                    if not _bl and chapter_no > 1:
                        _mem_bl = self._memory()
                        if _mem_bl is not None:
                            try:
                                _ld = _mem_bl.chapter_ledger(chapter_no - 1) or {}
                                _bl = {k: int(v) for k, v in _ld.items()
                                       if v is not None}
                            except Exception:
                                _bl = {}
                    # 首章无上章 ledger → 回落机制初始值（与生成端同口径）
                    if not _bl and chapter_no <= 1:
                        _mem_bl0 = self._memory()
                        if _mem_bl0 is not None:
                            try:
                                from .chapter_state import (mechanism_field_registry as _mreg0,
                                                            _registry_initial as _rin0)
                                _canon0 = (_mem_bl0.load().get("canon") or {})
                                _bl0 = _canon0.get("mechanism_baseline") or {}
                                _bl = _rin0(_bl0, _mreg0(_bl0)) or {}
                            except Exception:
                                _bl = {}
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
                        _hook = _qc.get("hook_score")
                        _mem.record_taste(chapter_no, int(_qc.get("score", 0) or 0),
                                          _qc.get("issues") or [], hook_score=_hook)
                        # REQ-写作正向引导 5.6：高分章（≥85 且无大改）段落自动入
                        # good 账本（写得好 → 被学习 → 写得更好 闭环）。
                        # REQ-门禁质检正向影响 P0-4：hook_score 透传 → 分层入账
                        # （hook≥4 → demo 示范池）；P0-5：hook 高分 → 正向锚+streak。
                        _mem.record_good(chapter_no, content,
                                         int(_qc.get("score", 0) or 0),
                                         _qc.get("issues") or [], hook_score=_hook)
                        if getattr(self.cfg, "hook_positive_anchor", True):
                            _mem.record_hook_streak(_hook)
                        _mem.decay(chapter_no)
                except Exception:
                    pass
            except Exception as qe:
                if not silent:
                    logger.warning(f"  [自校验] 跳过: {qe}")

        # 方案4（落盘分流）：质检未通过且该章已有旧版 → 保留旧版正文，不覆盖
        # （坏章不得顶掉用户已有的可用版本；仅首次生成/无旧版时才落盘新稿）。
        # v11 实证（2026-10-10）：旧版必须健康才保留——v10 ch6 旧稿含「累计死亡
        # 次数：37」幻觉行（注册表外字段违规），v11 新稿质检未过时无脑回退旧版 =
        # 病稿复用。旧版健康检查（零 LLM：未知字段/重复节拍/链式账本），不健康
        # → 落盘新稿（ensure_quality 重写后的最优候选），宁要新稿 2 个问题不要
        # 旧版幻觉行。
        kept_previous = False
        # P1-2 StageGate 增强旁路：质检通过(无大改)但仍有 warn 级问题或追更欲未满时，
        # 可选触发一次 LLM 增强润色（钩子更强/爽点更密/伏笔呼应）。受 enhance_pass 开关 +
        # AgentGuard 成本账约束；增强稿复检不过/分数不降则保留原稿，失败静默降级留日志。
        if qc_passed and getattr(self.cfg, "enhance_pass", False) and isinstance(_qc, dict):
            try:
                _enh = self._enhance_pass(chapter_no, plan_title, plan_goal, content, summary,
                                          _qc, qc, baseline=_bl or None,
                                          core_event=plan_core_event or None)
                if _enh is not None:
                    content, summary = _enh
                    if not silent:
                        logger.info(f"  [增强旁路] 第{chapter_no}章质检通过后触发增强润色，已采纳增强稿")
            except Exception:
                logger.exception("增强润色旁路失败（降级跳过，保留原稿）")
        # P0-1 合规红线：最终稿含合规命中 → 该章整体拒绝落盘（除非有健康旧版可保留）。
        compliance_rejected = bool(_qc.get("compliance_rejected")) if isinstance(_qc, dict) else False
        if not qc_passed:
            prev = None
            try:
                prev = self.store.load_chapter(chapter_no)
            except Exception:
                prev = None
            _prev_ok = (prev is not None and getattr(prev, "content", "")
                        and self._chapter_healthy(prev.content, chapter_no))
            if compliance_rejected:
                # P0-1 合规红线：违规稿一律不落盘。有健康旧版 → 保留旧版；
                # 无健康旧版 → compliance_rejected 保持 True，整体跳过落盘。
                if _prev_ok:
                    content, summary = prev.content, prev.summary
                    kept_previous = True
                    if not silent:
                        logger.info(f"  [自校验] 第{chapter_no}章含合规违规（score={_qc.get('score', 0)}），"
                              f"保留上一版健康正文（{len(content)} 字），违规新稿不落盘")
                elif not silent:
                    logger.warning(f"  [自校验] 第{chapter_no}章含合规违规（score={_qc.get('score', 0)}）"
                          f"且无健康旧版，该章整体拒绝落盘（compliance_rejected）")
            elif _prev_ok:
                content, summary = prev.content, prev.summary
                kept_previous = True
                if not silent:
                    logger.warning(f"  [自校验] 第{chapter_no}章质检未通过（score={_qc.get('score', 0)}），"
                          f"保留上一版正文（{len(content)} 字），不覆盖旧章")
            else:
                kept_previous = False
                if prev is not None and not silent:
                    logger.warning(f"  [自校验] 第{chapter_no}章质检未通过（score={_qc.get('score', 0)}），"
                          f"上一版正文（{len(prev.content)} 字）健康检查未通过"
                          "（含注册表外字段/重复节拍/链式账本违规），"
                          f"改落盘本轮重写最优候选（{len(content)} 字）")

        # REQ-章节版本化 + 版本质量（每次生成都展示、每版带质检问题）：
        # ensure_quality 的 versions 日志 = 初始稿 + 每轮重写稿（各自带质检结果）。
        # 逐版落库：除最终稿外的候选版本以 promote=False 入库（不顶替当前选中，
        # 用户在版本弹窗可见/可选择/可对比），最终稿按方案4 决策决定是否成为 HEAD。
        # 保留旧版（kept_previous）时最终稿也以候选版本入库，不再丢弃重写产物。
        # P0-1 合规红线：compliance_rejected（无健康旧版）时整体跳过落库，不入版本库。
        try:
            _qc_versions = _qc.get("versions") if isinstance(_qc, dict) else None
        except Exception:
            _qc_versions = None
        if _qc_versions and not compliance_rejected:
            try:
                for _v in _qc_versions[:-1]:
                    _vch = Chapter(no=chapter_no, title=plan_title,
                                   content=_v.get("content", "") or "",
                                   summary=_v.get("summary", "") or "",
                                   goal=plan_goal)
                    self.store.save_chapter(
                        _vch,
                        source="rewrite" if _v.get("auto_revised") else "ai",
                        quality=_v.get("quality") if isinstance(_v.get("quality"), dict) else None,
                        promote=False,
                    )
                _final = _qc_versions[-1]
                _final_ch = Chapter(no=chapter_no, title=plan_title,
                                    content=_final.get("content", "") or content,
                                    summary=_final.get("summary", "") or summary,
                                    goal=plan_goal)
                self.store.save_chapter(
                    _final_ch,
                    source="rewrite" if _final.get("auto_revised") else "ai",
                    quality=_final.get("quality") if isinstance(_final.get("quality"), dict) else None,
                    promote=not kept_previous,
                )
            except Exception:
                logger.exception("质检版本落库失败: no=%d", chapter_no)

        chapter = Chapter(no=chapter_no, title=plan_title, content=content, summary=summary, goal=plan_goal)
        if not _qc_versions and not compliance_rejected:
            # 无版本日志（自校验异常跳过等兜底）：保持原行为，直接落盘最终内容
            self.store.save_chapter(chapter)
        self._write_post_gates(chapter_no, plan_title, content, summary,
                               plan_moves, plan_core_event, silent)
        return chapter

    def _write_post_gates(self, chapter_no, plan_title, content, summary,
                          plan_moves, plan_core_event, silent) -> None:
        """章末后处理门禁（正文落盘后）：Beat 覆盖度门禁 + 机制状态账本 + 场景落盘 + 摘要。

        与正文/质检解耦：全部为确定性/记录类副作用，失败逐块降级不阻塞。
        """
        # 三期 F1：Beat 覆盖度门禁（纯确定性、零 LLM）——正文落盘后自检对章计划拍的
        # 覆盖率，分级记录到 beat_gate.json（pass/warn/fail/skip）。检测+记录+提示，不独立
        # 重生成正文（重写统一走 quality.ensure_quality）；mock provider/关闭/无计划拍静默跳过。
        try:
            if getattr(self.cfg, "beat_gate", False):
                from .beat_gate import assess_chapter, detect_chapter_type
                # REQ S3.2：章型白名单（对话/过渡章不卡 fail）+ loose 匹配 + 节点分级
                _loose = bool(getattr(self.cfg, "beat_loose_match", True))
                _ctype = detect_chapter_type(content, plan_moves) \
                    if getattr(self.cfg, "beat_chapter_type", True) else "剧情"
                _priorities = None  # 大纲节点分级数据后续接入；未提供默认全 P1 向后兼容
                # REQ S3.6 远期大纲粗粒度：开启时，距当前 > outline_far_skip 的远期大纲节点
                # 从 beat_gate 判定分母排除（方向约束，不要求逐拍兑现；默认关不改变行为）
                _far = None
                if getattr(self.cfg, "outline_rolling_detail", False):
                    _far = self._far_outline_beats(chapter_no)
                _bg = assess_chapter(
                    content, plan_moves, plan_core_event,
                    min_coverage=float(getattr(self.cfg, "beat_gate_min_coverage", 0.6)),
                    warn_coverage=float(getattr(self.cfg, "beat_gate_warn_coverage", 0.4)),
                    chapter_type=_ctype, priorities=_priorities, loose=_loose,
                    far_beats=_far,
                )
                self.store.save_beat_gate(chapter_no, _bg)
                # P1-4 BeatGate 正向供给：missing 拍不仅列「漏了」，同时给「该拍可往
                # 哪个方向写」的示范指引（零 LLM 规则模板），并入 beat_gate 记录供下一章
                # 生成时注入兑现。受 beat_positive_supply 开关控制；失败静默不阻塞。
                if getattr(self.cfg, "beat_positive_supply", True):
                    try:
                        _miss = _bg.get("missing") or []
                        _bg["positive_hints"] = self._beat_positive_hints(_miss)
                        self.store.save_beat_gate(chapter_no, _bg)
                    except Exception:
                        logger.exception("beat 正向示范写入失败（降级跳过）")
                # REQ S3.4 fail 章 LLM 二级复核：仅 beat_gate=fail 且开关 beat_fail_judge
                # 开时，跑 1 次 pointwise judge 确认"未兑现清单"真实存在（防 beat_gate 误报），
                # 返回未兑现清单才进 L3 定点重写。默认关（启用待人工标注集验证一致率>80%）。
                if (getattr(self.cfg, "beat_fail_judge", False)
                        and _bg.get("verdict") == "fail"):
                    self._beat_fail_judge(chapter_no, content, plan_moves, _bg.get("missing") or [])
                # REQ S3.1：章末承诺台账（beat_gate 后确定性落账，供下章补偿块消费）
                if getattr(self.cfg, "pending_commitments", True):
                    from .pending_commitments import build_commitment_ledger
                    _ledger = build_commitment_ledger(_bg, chapter_no)
                    self.store.save_pending_commitments(chapter_no, _ledger)
        except Exception:
            logger.exception("[BeatGate] 覆盖评估失败（降级不阻塞）: ch=%d", chapter_no)
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
                # v11 实证（2026-10-10）：ch5 无面板 → erosion=None 断链 → 质检 2.8c
                # _base 回落起始 97 → ch6 蚀化差误算 101-97=4（把 ch3/ch4 死亡计入 ch6）
                # → ledger_check 报「4 次死亡」误伤。无面板=无事件=蚀化不变：缺失时
                # 继承上一章末（与 numbers 链式 chapter_ledger 同口径，账本永不破链）。
                if _eros is None and _prev_eros is not None:
                    _eros = int(_prev_eros)
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
            logger.info(f"  第{chapter_no}章《{plan_title}》完成（{len(content)} 字）{tail}")


    def _enhance_pass(self, chapter_no, plan_title, plan_goal, content, summary,
                      qc_result: dict, qc, baseline=None, core_event=None):
        """P1-2 StageGate 增强润色旁路（REQ-门禁质检正向影响 P1-2）。

        质检【通过】但仍有提升空间（存在 warn 级「中改」问题，或追更欲 hook<4）时，
        对正文做一次 LLM 增强润色（钩子更强/爽点更密/伏笔呼应），复检(check)通过且
        分数不降才采纳，否则返回 None 保留原稿（达标不是终点，而是「被推进」的绿灯）。

        - 受 enhance_pass 开关（默认关）+ AgentGuard 成本账约束（与重写共用 revise 场景）；
        - 失败/复检不过/长度不足 → None（§11.6 降级留日志，不阻塞、不回退原稿）。
        """
        issues = qc_result.get("issues") or []
        has_warn = any((it.get("level") or "") == "中改" for it in issues)
        hook = qc_result.get("hook_score")
        if not has_warn and (hook is None or int(hook) >= 4):
            return None  # 无增强空间
        focus = [f"{it.get('type', '')}:{(it.get('detail', '') or '')[:60]}"
                 for it in issues if (it.get("level") or "") == "中改"]
        if hook is not None and int(hook) < 4:
            focus.append("追更欲偏低：强化章末钩子与伏笔呼应")
        try:
            if self.guard is not None:
                self.guard.enter_scene("revise")
            raw = self.llm.chat(
                "你只输出增强润色后的完整章节正文，其余不输出。",
                "你是资深网文编辑。这篇章节质检【已通过】，但仍有提升空间。"
                "请在不推翻原结构、不改变剧情的前提下做增强润色："
                "强化章末钩子、加密爽点密度、加强伏笔呼应。"
                f"\n增强重点：{'；'.join(focus[:3])}"
                f"\n\n当前正文：\n{content}",
                temperature=0.5, max_tokens=4000)
        except Exception:
            logger.exception("增强润色 LLM 失败（降级跳过，保留原稿）")
            return None
        enhanced = (raw or "").strip()
        if not enhanced or len(enhanced) <= len(content) * 0.5:
            return None  # 增强无效（空/截断）
        # 复检：通过且分数不降才采纳
        try:
            nres = qc.check(chapter_no, plan_title, plan_goal, enhanced, summary,
                            baseline=baseline, core_event=core_event)
            if not nres.get("passed") or nres.get("score", 0) < int(qc_result.get("score", 0) or 0):
                return None
        except Exception:
            logger.exception("增强润色复检失败（降级跳过，保留原稿）")
            return None
        return enhanced, summary


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

    def _chapter_healthy(self, content: str, chapter_no: int) -> bool:
        """v11 实证新增：旧版健康检查（保留旧版前判定，零 LLM）。

        旧版带病（注册表外字段幻觉行/重复节拍/链式账本违规）时不得作为
        「可用版本」回退——否则病稿跨轮复用。任何书通用。
        """
        if not content:
            return False
        try:
            from .chapter_state import (mechanism_field_registry as _mreg_h,
                                        unknown_mechanism_fields as _unk_h,
                                        ledger_check as _lchk_h,
                                        _registry_initial as _rin_h,
                                        _EROSION_SEQ_RE as _eseq_h)
            _mem_h = self._memory()
            if _mem_h is None:
                return True  # 无记忆体系（未启用记忆）不判病，维持旧路径行为
            _canon_h = (_mem_h.load().get("canon") or {})
            _reg_h = _mreg_h(_canon_h.get("mechanism_baseline") or {})
            # 1) 注册表外字段（累计死亡次数类幻觉行）
            if _reg_h and _unk_h(content, _reg_h):
                return False
            # 2) 重复节拍（同事件循环重演）
            if _beat_cycle_issues(content):
                return False
            # 3) 链式账本（蚀化差口径，与质检 2.8c 一致）
            if _reg_h:
                _st_h = (_mem_h.load().get("state") or {})
                _prev_h = _mem_h.chapter_ledger(chapter_no - 1)
                _eros_h = max((int(_m.group(1)) for _m in _eseq_h.finditer(content or "")),
                              default=0)
                _base_h = 0
                try:
                    _pc = [c for c in _st_h.get("chapters", [])
                           if int(c.get("ch", 0)) < int(chapter_no)]
                    _pe = next((c.get("erosion") for c in reversed(_pc)
                                if c.get("erosion") is not None), None)
                    _base_h = int(_pe) if _pe is not None else \
                        int(_st_h.get("erosion_start", 0) or 0)
                except Exception:
                    pass
                _cumu_h = max(0, _eros_h - _base_h) if _base_h > 0 else 0
                _init_h = _rin_h(_canon_h.get("mechanism_baseline") or {}, _reg_h)
                if _lchk_h(content, _prev_h, _reg_h, _cumu_h, _init_h):
                    return False
        except Exception:
            return True
        return True

    def _panel_whitelist_block(self) -> str:
        """P0-15/16 通用版：机制面板字段白名单注入块（closed-world）。

        基线声明的字段才允许出现在正文面板，禁止累计死亡次数类自造标签；
        次数/剩余类字段为 0 时本章不得再安排死亡。从 memory canon 注册表派生，
        任何书通用；无记忆/无基线 → 空串。
        """
        try:
            _mem_w = self._memory()
            if _mem_w is not None:
                from .chapter_state import mechanism_field_registry as _mregw
                _canonw = (_mem_w.load().get("canon") or {})
                _regw = _mregw(_canonw.get("mechanism_baseline") or {})
                if _regw:
                    _blk = (
                        "\n\n【机制面板字段白名单（硬约束）】正文面板只允许出现以下字段："
                        f"{sorted(_regw)}；禁止出现基线未声明的面板字段（如「累计死亡次数」"
                        "「死亡总次数」等自造标签）；字段格式必须为「字段名：数字」（带分母"
                        "写法禁止）。"
                    )
                    if any(k.endswith("次数") or "剩余" in k for k in _regw):
                        _blk += (
                            "\n【边界规则】次数/剩余类字段为 0 时，本章不得再发生死亡结算"
                            "（次数用尽后死亡=违规）；如确有死亡，必须先声明字段为 1 以上。"
                        )
                    return _blk
        except Exception:
            pass
        return ""

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
        # REQ-写作正向引导 5.3 权威基线补盲（2026-10-15）：一期把「权威当前值·只抄
        # 不推」只接进了场景路径（scene_prompt），而本书 high_quality_mode 下拆场景
        # 频繁失败、主路径实为回退整章 → 整章路径缺权威基线，机制数字纪律靠 LLM 运气
        # （二期实证 ch3/ch4/ch6 跨章数字 3→1/37→1 漂移）。这里把权威当前值文本
        # 注入整章 prompt，与场景路径同口径。
        _pbt = ""
        try:
            from .chapter_state import panel_baseline_dict as _pbd
            _pb_base: dict = {}
            # 优先本章场景累积（本章已推进的权威值）
            if isinstance(panel_baseline, dict):
                _pb_base = _pbd((panel_baseline or {}).get("numbers") or [])
            # 整章回退时场景累积为空（无场景循环）→ 回落上一章末权威值（chapter_ledger
            # 含跨章继承），治 ch2「可死次数 1 vs 上章末 3」/ ch3「寸 3 vs 38」类
            # 跨章数字漂移——一期场景路径的基线起点正是上章末，整章路径必须同口径。
            if not _pb_base and chapter_no > 1:
                _mem_ld = self._memory()
                if _mem_ld is not None:
                    try:
                        _ld = _mem_ld.chapter_ledger(chapter_no - 1) or {}
                        _pb_base = {k: int(v) for k, v in _ld.items() if v is not None}
                    except Exception:
                        _pb_base = {}
            # 首章无上章 ledger → 回落机制初始值（注册表初始基线），让 ch1 写标准
            # 面板行，账本链才有权威起点（v12 实证 ch1 无面板 → ch2 自写蚀化 98）。
            if not _pb_base and chapter_no <= 1:
                _mem_ld0 = self._memory()
                if _mem_ld0 is not None:
                    try:
                        from .chapter_state import (mechanism_field_registry as _mreg0,
                                                    _registry_initial as _rin0)
                        _canon0 = (_mem_ld0.load().get("canon") or {})
                        _bl0 = _canon0.get("mechanism_baseline") or {}
                        _pb_base = _rin0(_bl0, _mreg0(_bl0)) or {}
                    except Exception:
                        _pb_base = {}
            if _pb_base:
                _pbt = "\n".join(f"- {k}：{v}" for k, v in _pb_base.items())
        except Exception:
            _pbt = ""
        system, user = chapter_prompt(
            self.cfg, chapter_no=chapter_no,
            chapter_title=plan_title, chapter_goal=plan_goal,
            research_block="", memory_context=_mem,
            prev_tail=prev_tail, is_final=is_final,
            chapter_core_event=plan_core_event,
            chapter_events=ev_block,
            teardown_anchor=self._teardown_anchor(),
            personal_anchor=self._personal_anchor(),
            user_memory_block=self._user_memory_block(),
            panel_baseline=_pbt,
        )
        system = self._sys(system)
        # P0-15/16 通用版（2026-10-10）：回退整章路径同样注入机制面板字段白名单
        # （closed-world：基线声明的字段才允许出现，禁止累计死亡次数类自造标签；
        # 次数/剩余类字段为 0 时本章不得再安排死亡）——v10 实证 ch3/ch6 幻觉行
        # 「累计死亡次数：39/37」出现在回退整章正文，正是该路径缺白名单预防。
        _whitelist_block = self._panel_whitelist_block()
        if _whitelist_block:
            user = user + _whitelist_block
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
            if _text:
                try:
                    from .chapter_state import (panel_direction_violations as _pdv,
                                                mechanism_field_registry as _mregf,
                                                unknown_mechanism_fields as _unkf)
                    # 2026-10-17 弱化：砍死亡信号与因果校验——被动方向检查（不回退/不回升）
                    # 保留；方向规则从设定账本派生（registry，支持加点/升级等新机制）；
                    # 数字结算准确性改由事后质检软提示兜底，生成端不再强制结算。
                    _regf = _mregf((self._memory().load().get("canon") or {})
                                   .get("mechanism_baseline") or {}) \
                        if self._memory() is not None else {}
                    if panel_baseline:
                        _viols = _pdv(_text, panel_baseline, _regf)
                    # P0-15/16 通用版：注册表外字段（累计死亡次数类）回退整章同样拦截
                    if _regf:
                        _viols = _viols + _unkf(_text, _regf)
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
                logger.warning(f"  [回退整章] 第{chapter_no}章生成后检出{_why}，重试第{_try}次")
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
        # 字数超长不做任何 LLM 级处理（用户决策 2026-10-15：单章超长不触发整章回退/
        # 精简重写——超长不等于写得差，重写浪费 token 且历轮越改越差）。超长仅作
        # 附注展示（见上方 _over 检查），交付由质检层按字数规则记录。
        return _text

    def _split_scenes(self, chapter_no, plan_title, plan_goal, prev_tail,
                      plan_core_event, plan_moves, conflict_names, silent):
        """拆场景段（从 _generate_chapter_scenes 抽出，§11.1 先拆函数）。

        产出 (scenes, events_list, fallback_events, action_plan_block)：
        - 拆场景（低温 0.4；顺带产出本章人物行动表，不独立调用）
        - REQ-scene-plan-align：事件清单由大纲 character_moves 确定性派生（硬骨架）
        - 场景事件引用展开 / 语义同构合并 / 结构校验 / 互斥校验 / 回退整章决策
        - 场景字数预算归一（确定性）
        """
        import json
        action_plan_block = ""
        fallback_events: list = []   # 拆场景失败但 events 可救回时，注入回退整章生成
        derived_events: list = []
        skeleton_arg = ""
        if getattr(self.cfg, "scene_plan_align", True) and plan_moves:
            derived_events = _derive_events_from_moves(
                plan_moves,
                int(getattr(self.cfg, "chapter_words", 0) or 0),
                names=conflict_names,
            )
            if derived_events:
                skeleton_arg = f"共 {len(derived_events)} 个事件，场景数必须等于事件数：\n" + \
                    "\n".join(f"{e['id']}：{e['desc']}" for e in derived_events)
        try:
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
            _ca_needs_fill = False
            try:
                split_data = json.loads(_strip_json_block(raw))
            except Exception:
                split_data = None
            if not isinstance(split_data, dict):
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
                        logger.warning("  [拆场景] JSON 解析失败，重试 1 次（温度 0.5）...")
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
                if not ca and _ca_needs_fill and scenes:
                    if not silent:
                        logger.warning("  [行动表] characters_action 截断救回失败，单字段补全调用...")
                    ca = self._fill_characters_action(
                        chapter_no, plan_title, plan_goal,
                        plan_core_event, fallback_events, prev_tail, silent=silent,
                    )
                    if not silent and ca:
                        logger.info(f"  [行动表] 补全成功，行动表注入 {len(ca)} 条（措辞层约束生效）")
                action_plan_block = _format_action_plan(ca or [])
                if not silent:
                    logger.info(f"  [拆场景] 规划通过：{len(scenes)} 个场景，"
                          f"{len(fallback_events)} 个事件")
            events_list = split_data.get("events") or [] if split_data else []
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
            if not derived_events and events_list and scenes and len(events_list) >= 2:
                _pairs = self._scene_split_review(
                    chapter_no, plan_title, plan_goal, events_list, scenes, silent
                )
                if _pairs:
                    _merged = _merge_duplicate_events(events_list, scenes, _pairs)
                    if _merged:
                        events_list, scenes = _merged
                        fallback_events = events_list
            ok_plan, why_bad = _validate_scene_plan(
                scenes, int(getattr(self.cfg, "chapter_words", 0) or 0),
                core_event=str(plan_core_event or ""),
            )
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
                sem_dup = _scene_events_semantically_overlap(events_list, conflict_names)
            if scenes and not ok_plan:
                if not silent:
                    logger.warning(f"  [拆场景] 场景规划校验不通过（{why_bad}），回退整章生成")
                scenes = []
            elif sem_dup:
                if not silent:
                    logger.warning("  [拆场景] 事件清单语义槽位重叠（同冲突对象+同动作=重复事件），"
                          "回退整章生成")
                scenes = []
            elif derived_events and scenes:
                refs = {str(sc.get("scene_event_ref") or "").strip() for sc in scenes if isinstance(sc, dict)}
                derived_ids = {str(e.get("id") or "").strip() for e in derived_events}
                if refs != derived_ids:
                    if not silent:
                        logger.warning(f"  [拆场景] 场景事件引用与派生事件骨架不一致"
                              f"（refs={sorted(refs)} vs derived={sorted(derived_ids)}），回退整章生成")
                    scenes = []
        except Exception:
            scenes = []

        if scenes:
            try:
                _normalize_scene_targets(
                    scenes, int(getattr(self.cfg, "chapter_words", 0) or 0))
            except Exception:
                pass
        return scenes, events_list, fallback_events, action_plan_block

    def _generate_chapter_scenes(self, chapter_no, plan_title, plan_goal,
                                 memory_context, prev_tail, is_final,
                                 dynamic_module_used: bool = True,
                                 plan_core_event: str = "",
                                 plan_moves: str = "",
                                 conflict_names: tuple[str, ...] = (),
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

        # 1. 拆场景（低温 0.4；含派生事件骨架/JSON 健壮化/结构校验/字数归一/回退决策）
        scenes, events_list, fallback_events, action_plan_block = self._split_scenes(
            chapter_no, plan_title, plan_goal, prev_tail,
            plan_core_event, plan_moves, conflict_names, silent,
        )

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
        # P3 去污染：传 self_ch=当前章——last_ch==本章 的本轮错误降权到尾部，避免把
        # 本章刚犯的错当历史教训回灌（第1章重写循环自我污染的根因之一）。
        book_pitfalls_text = self._book_pitfalls(self_ch=int(chapter_no))
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
            # 2026-10-15 REQ-写作正向引导 5.3：附结算提示——本场景事件层含死亡/复活
            # 信号时，明确「结算瞬间只显示结算前/后两个权威值，其余保持基线不变」，
            # 把 P0-8 期望值（次数-1/蚀化+1）前置到生成端，治跨章数字漂移。
            panel_baseline_text = ""
            _panel_base: dict = {}
            try:
                from .chapter_state import panel_baseline_dict as _pbd
                _panel_base = _pbd((scene_state_acc or {}).get("numbers") or [])
                if _panel_base:
                    panel_baseline_text = "\n".join(
                        f"- {k}：{v}" for k, v in _panel_base.items()
                    )
                    # 2026-10-17 弱化：移除依赖死亡信号的强"结算提示"——数字是背景参考，
                    # 不再由生成端强制结算展示；准确性改由事后质检软提示兜底。
            except Exception:
                _panel_base, panel_baseline_text = {}, ""
            # P0-15/16 通用版：机制字段白名单注入（closed-world）——基线声明的字段
            # 才允许出现在正文面板；禁止自造字段（累计死亡次数类）；次数类字段为 0
            # 时本章不得再安排死亡（基线未声明该边界行为）。
            _wl3 = self._panel_whitelist_block()
            if _wl3:
                panel_baseline_text += _wl3
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
                user_memory_block=self._user_memory_block(),
                state_anchor=state_anchor_text,
            )
            sys_c = self._sys(sys_c)
            # REQ-超长篇攻坚 S2.2/S2.3：周期轻量再锚定（滚动锚）+ 固定锚双轨注入
            _anchor_parts = []
            _ra = self._rolling_style_anchor(chapter_no)
            if _ra:
                _anchor_parts.append(f"【本作滚动风格锚（参考最近高分片段质感，勿逐字复制）】\n{_ra}")
            _fa = self._fixed_style_anchor()
            if _fa:
                _anchor_parts.append(f"【本作固定风格锚（风格原点，参考语感勿复制）】\n{_fa}")
            if _anchor_parts:
                sys_c = "\n\n".join(_anchor_parts) + "\n\n" + sys_c
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
                            logger.warning(f"  [场景] 第{idx}个场景与上一场景高度相似"
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
                            user_memory_block=self._user_memory_block(),
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
                    dup_ev = _scene_text_rewrites_event(piece, done_events, conflict_names)
                    if dup_ev:
                        if not silent:
                            logger.warning(f"  [场景] 第{idx}个场景复写了已发生事件"
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
                            user_memory_block=self._user_memory_block(),
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
                        if retry3 and not _scene_text_rewrites_event(retry3, done_events, conflict_names):
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
                            logger.warning(f"  [场景] 第{idx}个场景{_why}"
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
                            user_memory_block=self._user_memory_block(),
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
                            logger.warning(f"  [场景] 第{idx}个场景连续 2 次重试仍重演完整循环，回退整章生成")
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
                        # 2026-10-17 弱化：砍死亡信号与因果校验——只做被动方向检查
                        # （不回退/不回升，方向来自设定账本 registry）+ 字段白名单；
                        # 数字结算准确性由事后质检软提示兜底。
                        _viols = _pdv(piece, _panel_base, _reg)
                        # P0-15/16 通用版：注册表外机制字段违规（closed-world 白名单）
                        if _reg:
                            _viols = _viols + _unk(piece, _reg)
                    except Exception:
                        _viols = []
                    if _viols:
                        _panel_ok = False
                        for _try in (1, 2):
                            if not silent:
                                logger.warning(f"  [场景] 第{idx}个场景机制数字违反方向规则"
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
                                user_memory_block=self._user_memory_block(),
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
                            logger.warning(f"  [场景] 第{idx}个场景机制数字连续 2 次重试仍违反，"
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
                logger.warning(f"    [场景] 第{idx}个场景生成失败: {e}")
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
            logger.warning(f"  [行动表] 生成失败，回退原流程: {e}")
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
                        logger.info(f"  [行动表补全] 第{_try}次补全成功：注入 {len(ok)} 条行动（原解析截断救回）")
                    return ok
                if not silent:
                    logger.warning(f"  [行动表补全] 第{_try}次解析失败，重试 1 次...")
            if not silent:
                logger.warning("  [行动表补全] 两次均失败，本次不注入行动表（记录 trace 告警）")
            return []
        except Exception as e:
            if not silent:
                logger.warning(f"  [行动表补全] 补全调用异常：{e}")
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
                logger.info(f"  [拆场景] 评审发现 {len(out)} 组语义同构事件（"
                      + "; ".join(f"{i}≈{j}:{r[:20]}" for i, j, r in out) + "），代码合并去重")
            return out
        except Exception as e:
            if not silent:
                logger.warning(f"  [拆场景] 评审调用失败（降级不阻塞）: {e}")
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
                    logger.warning(f"  [场景] 第{idx}个场景超长（{len(piece)} 字），"
                          f"精简重写为 {len(out)} 字")
                return out
            return ""
        except Exception:
            return ""




# --- 场景事件/文本工具函数已抽出到 scene_event_utils.py（§11.1 文件拆分）---
from .scene_event_utils import (
    _format_action_plan, _world_conflict_names, _event_conflict,
    _scene_text_rewrites_event, _long_sentence_overlap_ratio,
    _norm_scene_event, _scene_event_ordinals, _scene_count_for_words,
    _derive_events_from_moves, _event_semantic_slots,
    _scene_events_semantically_overlap, _merge_duplicate_events,
    _expand_scene_event_refs, _normalize_scene_targets,
    _validate_scene_plan, _scene_events_overlap, _split_summary,
    _is_repetitive_text, _summary_invented_words,
    _strip_html_tags, _strip_json_block, _extract_json_field,
    _SCENE_EVENT_OVERLAP_RATIO, _SCENE_ADJACENT_REPEAT_RATIO,
    _SCENE_ADJACENT_SPAN_RATIO,
)
