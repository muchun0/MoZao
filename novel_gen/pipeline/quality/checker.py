# -*- coding: utf-8 -*-
"""
ReaderAgent 内置结果自校验（模块4）：输出前自动校验业务质量。

校验维度（基础规则集）：
1. 结构要求     —— 确定性：正文字数是否达标（严重不足触发重写）
2. 内容重复     —— 确定性：与最近 N 章正文的 n-gram 重叠率
3. 输出合规     —— 确定性：明显违规内容拦截（复用 novel_gen.safety.basic_content_check）
4. 读者体验     —— LLM：复用 reader_review_prompt（代入感/爽点/钩子/阅读障碍/主角主动性）
                + 人物卡对照（人物设定一致性、剧情逻辑）

策略：校验不通过 → 触发 1 次自动重写 → 重写后复检；
仍不通过 → 标记问题（review.json + trace step_type=quality_check）并返回当前内容，不阻塞生成。
任何异常降级跳过校验，绝不影响主流程。
"""
from __future__ import annotations

import json
import logging
import re
import time

from ...config import NovelConfig
from ...llm.base import LLMClient
from ...llm.json_parse import parse_json_obj
from ...store.project import ProjectStore
from ...store.schemas import Chapter
from ..templates import reader_review_prompt, _output_discipline, panel_anchor_check_prompt
from ..beats import _beat_cycle_issues
from ..trace import TraceRecorder
from ...utils.text import longest_common_substr_len as _lcs_len
from ..pitfall_reconcile import reconcile_pitfall_injection as _reconcile_injection
from ..pitfall_contract import contract_for, expected_params, CHANNEL_REWRITE
from . import constants as C
from .constants import (
    _MIN_WORDS_RATIO, _NGRAM, _DUP_RATIO, _MAX_RECENT,
    _SELF_NGRAM, _SELF_DUP_RATIO, _SALVAGE_MIN_LINE, _SALVAGE_MAX_LINE, _SALVAGE_SPREAD,
    _TERMINAL,
    _REPEAT_MIN_LEN, _REPEAT_LONG_LEN, _REPEAT_HARD_COUNT, _DIALOG_ALLOWED, _PANEL_KEYWORDS,
    _PANEL_REPEAT_SPAN, _PANEL_SPAN_LABEL, _PANEL_EVENT_ANCHORS, _PANEL_EVENT_RECALL,
    _MAX_REWRITE, _SALVAGE_MIN_KEEP, _CONSISTENCY_CHARS,
)
from .text_tools import (
    _seg_has_settle_event_regex, _llm_panel_seg_settle, _load_persona, _has_type,
    _split_summary, _core_event_missing,
    _iter_sentences, _norm_sentence, _is_panel_line, _iter_repeat_groups,
    _locate_problem_spans, _merge_spans, _align_to_paras,
)
from . import aggregate as _aggregate
from .reviewers import (
    ReviewContext, DimensionResult, ReviewerResult, ReviewerIssue,
    PlotReviewer,
)

logger = logging.getLogger("novel_gen.pipeline.quality")


class QualityChecker:
    """章级质量校验器（确定性规则 + 读者视角 LLM 校验）。"""

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 trace: TraceRecorder | None = None, guard=None, memory=None):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.trace = trace
        self.guard = guard
        # P0-7 小说级记忆：None/关闭时全部记忆行为安全跳过（老调用方不受影响）
        self.memory = memory

    # ---------- 对外入口 ----------
    def _record_pitfalls(self, chapter_no: int, issues: list[dict]) -> None:
        """P0-7：把质检出问题写入小说级踩坑账本（零额外 LLM，失败静默）。

        每轮 check（初始稿/抢救后/每轮重写复检）都记录——重写后仍未消除的问题
        同样入账（说明该类错误重写修不动，是高频踩坑）。同一章同 pattern 只计
        一次（record_pitfall 内部处理），跨章真实累加。
        """
        if self.memory is None or not getattr(self.cfg, "book_memory", True):
            return
        try:
            self.memory.record_pitfall(chapter_no, issues)
            self.memory.escalate()
        except Exception:
            pass

    def _record_reflection(self, chapter_no: int, content: str, result: dict) -> None:
        """P1-1 Reflexion：让 LLM 用一句话反思「错在哪、下次怎么写对」，存入 book_memory。

        只在重写 >=2 轮仍未通过时触发（问题顽固才值得反思）；一次 LLM 调用走 revise
        场景成本账（受 guard 约束）。失败静默降级并留日志，不阻塞重写主流程（§11.6）。
        """
        try:
            issues = result.get("issues") or []
            issue_text = "; ".join(
                f"{it.get('type','')}:{(it.get('detail','') or '')[:60]}"
                for it in issues[:3])
            prompt = (
                "你是资深网文编辑。本章质检未通过且已自动重写两轮仍未解决，"
                "需要沉淀一条可复用的写作教训。请用一句中文（≤60字）写反思笔记，"
                "格式必须为：错在哪：……；下次怎么写对：……。不要引号、不要序号、不要分点。"
                f"\n本章片段开头：{content[:200]}"
                + (f"\n待解决问题：{issue_text}" if issue_text else "")
            )
            if self.guard is not None:
                self.guard.enter_scene("revise")
            raw = self.llm.chat(
                "你只输出一句中文反思笔记，其余不输出。",
                prompt, temperature=0.3, max_tokens=200)
            note = (raw or "").strip()
            if note and self.memory is not None:
                self.memory.record_reflection(chapter_no, note)
        except Exception:
            logger.warning("Reflexion 反思入账失败（降级跳过，不阻塞重写）", exc_info=True)

    def ensure_quality(self, chapter_no: int, plan_title: str, plan_goal: str,
                       content: str, summary: str, silent: bool = False,
                       baseline: dict | None = None,
                       core_event: str = "") -> tuple[Chapter, dict]:
        """校验 → 不通过则按 auto_rewrite_rounds 自动重写（默认 0=不自动重写，
        一次操作只产 1 个版本；通过即停）→ 复检 → 返回 (最终 Chapter, 校验结果)。

        baseline：机制数字基线（panel_baseline_dict(numbers)），供质检层叠加
        面板方向校验（P0-5：重写产物/改稿产物在质检层二次兜底蚀化回退/次数回升）。
        core_event：P0-17 本章大纲核心事件（叠加正文覆盖校验，见 check）。
        P0-5 兜底：重写无效/超轮仍未消除问题时，回退到循环内「问题最少、分数最高」
        的最优版本，不再把重写后的坏稿原样落盘（第四轮第 5 章：重写 2 无效 break
        后坏稿直接落盘的缺口）。

        版本日志（REQ-章节版本化扩展）：返回 result["versions"]——初始稿 + 每轮
        重写稿的 {round, content, summary, auto_revised, quality(精简)} 列表，
        供 stages.write 逐版落库（每次生成的版本都展示、每版带质检问题）。
        """
        from ..compliance import has_compliance
        versions: list[dict] = []

        def _push(content: str, smry: str, res: dict, round_no: int,
                  auto: bool) -> None:
            if versions and versions[-1]["content"] == content:
                return  # 内容未变不重复记录（重写无效/抢救无变化）
            issues = res.get("issues") or []
            versions.append({
                "round": round_no,
                "content": content,
                "summary": smry,
                "auto_revised": bool(auto),
                "quality": {
                    "passed": bool(res.get("passed")),
                    "score": int(res.get("score", 0)),
                    "issue_count": len(issues),
                    "issues": [
                        {"level": it.get("level", ""), "type": it.get("type", ""),
                         "detail": (it.get("detail") or "")[:120]}
                        for it in issues
                    ],
                },
            })

        result = self.check(chapter_no, plan_title, plan_goal, content, summary,
                            baseline=baseline, core_event=core_event)
        issues = result["issues"]
        rewritten_occurred = False
        self._record_pitfalls(chapter_no, issues)
        _push(content, summary, result, 0, False)

        # 事后自愈①：章内多稿拼接 → 确定性单稿抢救（不依赖 LLM，避免重写再次堆稿）
        if _has_type(issues, "章内大量自重复"):
            salvaged = self._salvage_single_draft(content)
            if salvaged and salvaged != content and len(salvaged) > len(content) * _SALVAGE_MIN_KEEP:
                content = salvaged
                nc, ns = _split_summary(content)
                if nc:
                    content = nc
                if ns:
                    summary = ns
                rewritten_occurred = True
                if not silent:
                    logger.info(f"  [自校验] 第{chapter_no}章检测到多稿拼接，已抢救为单一草稿（{len(content)} 字）")
                result = self.check(chapter_no, plan_title, plan_goal, content, summary,
                                    baseline=baseline, core_event=core_event)
                issues = result["issues"]
                self._record_pitfalls(chapter_no, issues)
                # 确定性抢救是"修复异常输出"而非"新增版本"：清空初始异常稿、只保留抢救稿，
                # 避免异常多稿拼接也落库、按字数重复计费（一次操作只产 1 个版本）。
                versions.clear()
                _push(content, summary, result, 0, True)

        # P0-5 最优版本账本：初始稿作为基准，重写轮内择优（问题数升序、分数降序）
        best_content, best_summary, best_issues, best_result = content, summary, issues, result
        best_rewritten = rewritten_occurred

        # 不通过 → 自动重写（最多 auto_rewrite_rounds 轮；默认 0=不自动重写：一次操作
        # 只产 1 个版本、只扣 1 份钱，重写已收费；需要自动多稿时由后台调高）。
        # 每轮重写后复检，通过即停。
        rewrite_rounds = 0
        max_rewrite = int(getattr(self.cfg, "auto_rewrite_rounds", _MAX_REWRITE))
        while not result["passed"] and rewrite_rounds < max_rewrite:
            rewrite_rounds += 1
            if not silent:
                logger.warning(
                    f"  [自校验] 第{chapter_no}章未通过（{len(issues)} 个问题），"
                    f"触发第 {rewrite_rounds}/{max_rewrite} 次自动重写...")
            if self.guard is not None:
                self.guard.enter_scene("revise")
            rewritten = self._rewrite_once(chapter_no, plan_title, plan_goal, content, issues,
                                           baseline=baseline)
            if not rewritten or len(rewritten) <= len(content) * 0.5:
                if not silent:
                    logger.warning("  [自校验] 重写结果无效（为空或长度不足），停止重写")
                break
            content = rewritten
            nc, ns = _split_summary(content)
            if nc:
                content = nc
            if ns:
                summary = ns
            rewritten_occurred = True
            # 复检
            result = self.check(chapter_no, plan_title, plan_goal, content, summary,
                                baseline=baseline, core_event=core_event)
            issues = result["issues"]
            self._record_pitfalls(chapter_no, issues)
            if not silent:
                logger.info(f"  [自校验] 第 {rewrite_rounds} 次重写后复检："
                      f"{'通过' if result['passed'] else f'仍有 {len(issues)} 个问题'}")
            _push(content, summary, result, rewrite_rounds, True)
            # P0-5 择优：问题更少，或问题相同但分数更高 → 更新最优版本。
            # P0-1 合规红线：合规稿一律不参与择优（避免把违规稿当"最优版"回退/落盘）。
            if (not has_compliance(issues)) and (
                len(issues) < len(best_issues)
                or (len(issues) == len(best_issues)
                    and result.get("score", 0) > best_result.get("score", 0))
            ):
                best_content, best_summary, best_issues, best_result = content, summary, issues, result
                best_rewritten = rewritten_occurred

        # P0-5 兜底：循环结束仍未通过（超轮/重写无效 break）且已有优于当前的最优版本
        # → 回退最优版本（坏稿不得顶掉相对较好的版本；首次生成无旧版时尤其关键）。
        # P0-1 合规红线：最优版本若含合规问题也不回退（合规稿不作为落盘回退目标）。
        if not result["passed"] and best_content is not None and (
            not has_compliance(best_issues)
        ) and (
            len(best_issues) < len(issues)
            or (len(best_issues) == len(issues)
                and best_result.get("score", 0) > result.get("score", 0))
        ):
            content, summary, issues, result = best_content, best_summary, best_issues, best_result
            rewritten_occurred = best_rewritten or rewritten_occurred
            if not silent:
                logger.warning(f"  [自校验] 第{chapter_no}章重写未消除问题，回退到最优版本"
                      f"（{len(issues)} 个问题，{len(content)} 字）")

        # P1-1 Reflexion：重写 >=2 轮仍未通过 → LLM 写一句反思入账（下次生成携带为先验）。
        # 受 reflexion_note 开关 + AgentGuard 成本账约束；失败静默降级不阻塞（§11.6 留日志）。
        if (getattr(self.cfg, "reflexion_note", True) and self.memory is not None
                and rewrite_rounds >= 2 and not result["passed"]):
            self._record_reflection(chapter_no, content, result)

        # 结果落库（review.json 兼容既有格式，P2-4：记录实际是否触发重写）
        self._save_review(chapter_no, issues, result, auto_revised=rewritten_occurred)
        # 结果入 trace（模块1 快照，P2-4：extra 带 score/issue_count/auto_revised 供报表聚合）
        if self.trace is not None:
            self.trace.record_quality(chapter_no, result, issues, auto_revised=rewritten_occurred)

        # 版本日志收尾：最终采用版本（best 回退后 content 已换，确保最后一版=最终稿）
        _push(content, summary, result, rewrite_rounds, rewritten_occurred)
        result["versions"] = versions
        # P0-1 合规红线：最终稿若仍含合规命中 → 标记拒绝（stages 据此不落盘、
        # 不入版本库）；普通质量问题不含此标记。
        result["compliance_rejected"] = bool(
            has_compliance(result.get("issues") or [])
        )
        # P2-2 SPC 控制图数据采集：每章落一次通过/重写轮数/合规命中指标，
        # 供管理端画控制图检测「质量骤降」信号。失败静默（降级不阻塞写章）。
        try:
            self.store.save_quality_metric(chapter_no, {
                "passed_once": bool(rewrite_rounds == 0 and result.get("passed")),
                "rewrite_rounds": int(rewrite_rounds),
                "compliance_hit": bool(result["compliance_rejected"]),
            })
        except Exception:
            pass
        return Chapter(no=chapter_no, title=plan_title, content=content,
                       summary=summary, goal=plan_goal), result

    # ---------- 校验 ----------
    def check(self, chapter_no: int, plan_title: str, plan_goal: str,
              content: str, summary: str = "",
              baseline: dict | None = None,
              core_event: str = "") -> dict:
        """执行全部校验维度，返回 {"passed": bool, "issues": [...], "score": int}。

        baseline：机制数字基线（panel_baseline_dict(numbers)，P0-5 从场景循环透传）；
        非空时叠加面板方向校验（质检层二次兜底蚀化回退/次数回升，与场景级防线4 同基线）。
        core_event：P0-17（2026-10-10）本章大纲核心事件（非空时叠加正文覆盖校验：
        正文必须覆盖核心事件 ≥60% 分段，否则大改——治 v9 ch6 大纲「忘忧阁密报」
        未进正文）。
        """
        issues: list[dict] = []
        score = 100

        # 1. 结构要求（确定性）
        target = max(self.cfg.chapter_words, 1)
        _it, _d = self._check_structure(content, target)
        issues.extend(_it); score += _d

        # 2. 内容重复（确定性，与最近 N 章对比）+ 2.4 章内多稿自重复
        _it, _d = self._check_repetition(chapter_no, content)
        issues.extend(_it); score += _d

        # 2.7 章内同句复读（确定性，REQ-章内自洽与机制账本）
        _it, _d = self._check_repeat_sentence(content)
        issues.extend(_it); score += _d

        # 2.8 机制数字结算校验（确定性）
        _it, _d = self._check_panel_progress(content)
        issues.extend(_it); score += _d

        # 2.8b 面板数字方向校验（P0-5）
        _it, _d = self._check_panel_direction(content, baseline)
        issues.extend(_it); score += _d

        # 2.8c 蚀化跨章累计 + 链式账本校验（REQ-机制数字软提示与通用化）
        _it, _d = self._check_mechanism_ledger(chapter_no, content)
        issues.extend(_it); score += _d

        # 2.8d 大纲核心事件正文覆盖校验（P0-17）
        _it, _d = self._check_core_event(content, core_event)
        issues.extend(_it); score += _d

        # 2.9 对话轮重复（确定性兜底）
        _it, _d = self._check_dialogue_loop(content)
        issues.extend(_it); score += _d

        # 2.12 情节级重演检测（确定性，2026-10-09）
        _it, _d = self._check_beat_cycle(content)
        issues.extend(_it); score += _d

        # 2.10 字数审计（确定性·仅提示）
        _it, _d = self._check_length_audit(content)
        issues.extend(_it); score += _d

        # 2.11 摘要一致性（确定性兜底）
        _it, _d = self._check_summary_consistency(content, summary)
        issues.extend(_it); score += _d

        # 2.6 结尾完整性（确定性）
        _it, _d = self._check_ending(content)
        issues.extend(_it); score += _d

        # 2.5 AI 痕迹 + 文风漂移 + 同质度检测（确定性，各子项独立开关）
        ai_style_issues, ai_style_delta = self._check_ai_and_style(content, chapter_no)
        issues.extend(ai_style_issues)
        score += ai_style_delta

        # 3. 输出合规（模块3 基础合规校验）
        _it, _d = self._check_compliance(content)
        issues.extend(_it); score += _d

        # 4. 读者体验（LLM：代入感/爽点/钩子/主动性 + 人物一致性）
        llm_issues, hook_score = self._reader_llm_check(chapter_no, plan_title, plan_goal, content)
        for it in llm_issues:
            issues.append(it)
        if hook_score is not None:
            if hook_score <= 2:
                issues.append({
                    "type": "读者-追更欲过低",
                    "detail": f"追更欲评分 {hook_score}/5，低于 3 分阈值",
                    "level": "大改" if hook_score <= 1 else "中改",
                })
                score -= 20

        # 4.5 章内前后矛盾自检（LLM，REQ-章内自洽与机制账本·扩展）
        _it, _d = self._check_consistency(chapter_no, content)
        issues.extend(_it); score += _d

        # 4.6 规则条款一致性（LLM，REQ-通用机制类型适配；仅 rule_set 类型启用）
        _it, _d = self._check_rule_consistency(chapter_no, content)
        issues.extend(_it); score += _d

        # L2 新增评委：plot 情节结构（唯一新增 LLM 调用，开关 + 普通模式降频；
        # reader/consistency/prose 复用上方已发起的现有调用，不重复计费，TECH §3.2）。
        plot_result: ReviewerResult | None = None
        layered = bool(getattr(self.cfg, "reviewer_layered", True))
        if layered:
            plot_result = self._run_plot_reviewer(
                chapter_no, plan_title, plan_goal, content, core_event)
            if plot_result and plot_result.dimension.available:
                for ri in plot_result.raw_issues:
                    issues.append(ri.to_dict())

        # 去重 + 排序 + 诊断回喂
        passed, uniq, score = self._finalize_issues(issues, score)

        result = {
            "passed": passed,
            "issues": uniq,
            "score": max(0, min(100, score)),
            "hook_score": hook_score,   # P0-5 追更欲（1-5/None），供 stages 透传入账
        }

        # L3 附加快照（D-1：不接管旧 score/门禁，仅附加 dimensions/recommendations/layers）
        if layered:
            result.update(self._build_layered_snapshot(uniq, score, hook_score, plot_result))
        return result

    def _run_plot_reviewer(self, chapter_no: int, plan_title: str, plan_goal: str,
                           content: str, core_event: str) -> ReviewerResult | None:
        """调用 plot 情节评委；无 llm / 开关关闭 / 降频跳过时返回 None（不发起调用）。"""
        if self.llm is None:
            return None
        ctx = ReviewContext(
            chapter_no=chapter_no, plan_title=plan_title, plan_goal=plan_goal,
            content=content, core_event=core_event, cfg=self.cfg, llm=self.llm,
            store=self.store, guard=self.guard, memory=self.memory,
        )
        try:
            return PlotReviewer().review(ctx)
        except Exception:  # noqa: BLE001 —— 评委失败已在基类降级，此处兜底不阻塞
            logger.exception("[quality] plot 评委调度失败（降级跳过，不阻塞主链路）")
            return None

    def _build_layered_snapshot(self, uniq: list[dict], score: int,
                                hook_score: int | None,
                                plot_result: ReviewerResult | None) -> dict:
        """L3 附加快照：把现有 issue 归类到四维度，组装 dimensions / recommendations / layers。

        旧 `score` / `passed` / issues 口径不变；维度分仅作附加信号（D-1）。
        reader/consistency/prose 维度由现有 issue 归类映射，plot 来自新评委结果。
        """
        reviewer_results: list[ReviewerResult] = []
        if plot_result is not None:
            reviewer_results.append(plot_result)

        # 现有 issue 按维度归类，构造 reader/consistency/prose 占位维度快照（available=True，
        # 不重复发起 LLM；维度分本期留空，仅承载问题清单与亮点位）。
        grouped: dict[str, list[dict]] = {"reader": [], "plot": [],
                                          "consistency": [], "prose": []}
        for it in uniq:
            dim = _aggregate.classify_dimension(it)
            grouped.setdefault(dim, []).append(it)

        for key, label in (("reader", "读者体验"), ("consistency", "逻辑一致性"),
                           ("prose", "文风自然度")):
            dim_issues = [ReviewerIssue(
                type=str(x.get("type", "")), detail=str(x.get("detail", "")),
                level=str(x.get("level", "小改")),
                fix_suggestion=str(x.get("fix_suggestion") or ""),
                evidence=str(x.get("evidence", "") or ""),
            ) for x in grouped.get(key, [])]
            reviewer_results.append(ReviewerResult(
                dimension=DimensionResult(key=key, label=label, score=None,
                                          available=True, issues=dim_issues),
                raw_issues=dim_issues))

        agg = _aggregate.aggregate(
            l1_issues=[], reviewer_results=reviewer_results, score=score,
            diag_feedback=False, hook_score=hook_score,
        )
        # recommendations 面向全部合并后 issue；layers 统计确定性/评委来源
        agg.recommendations = _aggregate._build_recommendations(uniq)
        agg.layers["deterministic"]["issue_count"] = len(uniq)
        return {
            "dimensions": {k: _aggregate.dimension_to_dict(v) for k, v in agg.dimensions.items()},
            "recommendations": agg.recommendations,
            "layers": agg.layers,
        }

    def _check_structure(self, content: str, target: int) -> tuple[list, int]:
        """1. 结构要求（确定性）：字数严重不足（<30%）大改、不足 60% 中改。"""
        words = len(content or "")
        if words < target * _MIN_WORDS_RATIO:
            return ([{
                "type": "结构-字数严重不足",
                "detail": f"本章仅 {words} 字，不足目标 {target} 字的 30%",
                "level": "大改",
            }], -30)
        if words < target * 0.6:
            return ([{
                "type": "结构-字数偏少",
                "detail": f"本章 {words} 字，低于目标 {target} 字的 60%",
                "level": "中改",
            }], -10)
        return [], 0

    def _check_repetition(self, chapter_no: int, content: str) -> tuple[list, int]:
        """2/2.4 内容重复（与最近 N 章对比）+ 章内多稿自重复（草稿拼接补盲）。"""
        issues, score = [], 0
        dup = self._dup_issues(chapter_no, content)
        if dup:
            issues.extend(dup)
            score -= 20
        for it in self._self_dup_issues(content):
            issues.append(it)
            score -= 30
        return issues, score

    def _check_repeat_sentence(self, content: str) -> tuple[list, int]:
        """2.7 章内同句复读：同一句/同一动作整句原样复读，大改级触发自动重写。"""
        issues, score = [], 0
        for it in self._repeat_sentence_issues(content):
            issues.append(it)
            score -= 15
        return issues, score

    def _check_panel_progress(self, content: str) -> tuple[list, int]:
        """2.8 机制数字结算校验：同标签同数值跨段复现 = 结算未推进（软扣分）。

        仅 numeric_panel 类型启用；规则/能力/契约等非数字类型不做面板数字判定。
        REQ-词表换LLM 批次二：事件锚点判定优先 LLM 语义（开关 llm_semantic_panel_anchor
        默认开），失败/关闭回退 _PANEL_EVENT_ANCHORS 锚点正则。
        """
        try:
            world = self.store.load_world()
            if world is not None:
                from ..mechanism_types import has_type, resolve_types
                if not has_type(resolve_types(world), "numeric_panel"):
                    return [], 0
        except Exception:
            pass
        issues, score = [], 0
        use_llm_panel = bool(getattr(self.cfg, "llm_semantic_panel_anchor", True))
        for it in self._panel_progress_issues(content, llm=self.llm, use_llm=use_llm_panel):
            issues.append(it)
            score -= 5
        return issues, score

    def _check_panel_direction(self, content: str, baseline: dict | None) -> tuple[list, int]:
        """2.8b 面板数字方向校验（P0-5）：与场景级防线4 同基线，质检层二次兜底。"""
        if not baseline:
            return [], 0
        try:
            from ..chapter_state import (panel_direction_violations as _pdv,
                                        mechanism_field_registry as _mregq)
            _regq = {}
            if self.memory is not None:
                _regq = _mregq((self.memory.load().get("canon") or {})
                               .get("mechanism_baseline") or {})
            issues, score = [], 0
            for it in _pdv(content, baseline, _regq):
                issues.append({
                    "type": "写作-机制数字方向违规",
                    "detail": it,
                    "level": "小改",
                })
                score -= 5
            return issues, score
        except Exception:
            return [], 0

    def _ledger_cumulative(self, chapter_no: int, content: str, _reg: dict,
                           _canon: dict) -> int:
        """2.8c 机制死亡/递减量反推：increase 字段链差 + decrease 递减量取最大（双源抗漏）。"""
        from ..chapter_state import (ledger_extract_numbers as _lex3,
                                    _registry_initial as _rin)
        _cur = _lex3(content or "", _reg)
        _prev_ledger = self.memory.chapter_ledger(chapter_no - 1)
        _init = _rin(_canon.get("mechanism_baseline") or {}, _reg)
        _cumu = 0
        for _c, _r in _reg.items():
            if _r == "increase" and _c in _cur:
                _pv = _prev_ledger.get(_c)
                if _pv is None:
                    _pv = (_init or {}).get(_c)
                if _pv is not None:
                    _cumu = max(_cumu, max(0, int(_cur[_c]) - int(_pv)))
        _dec3 = 0
        for _c3, _r3 in _reg.items():
            if _r3 == "decrease" and _c3 in _cur:
                _pv3 = _prev_ledger.get(_c3)
                if _pv3 is None:
                    _pv3 = (_init or {}).get(_c3)
                if _pv3 is not None and int(_cur[_c3]) < int(_pv3):
                    _dec3 += int(_pv3) - int(_cur[_c3])
        return max(_cumu, _dec3)

    def _check_mechanism_ledger(self, chapter_no: int, content: str) -> tuple[list, int]:
        """2.8c 链式机制账本（REQ-机制数字软提示与通用化）：注册表从设定账本派生，
        字段白名单违规与链式方向违规一律软扣分（小改/不阻塞）。"""
        if self.memory is None:
            return [], 0
        try:
            from ..chapter_state import (mechanism_field_registry as _mreg2,
                                        unknown_mechanism_fields as _unk2,
                                        ledger_check as _lchk,
                                        _registry_initial as _rin)
            _canon = (self.memory.load().get("canon") or {})
            _reg = _mreg2(_canon.get("mechanism_baseline") or {})
            if not _reg:
                return [], 0
            issues, score = [], 0
            for _it in _unk2(content, _reg):
                issues.append({
                    "type": "写作-机制字段白名单违规",
                    "detail": _it,
                    "level": "小改",
                })
                score -= 5
            _cumu = self._ledger_cumulative(chapter_no, content, _reg, _canon)
            _prev_ledger = self.memory.chapter_ledger(chapter_no - 1)
            _init = _rin(_canon.get("mechanism_baseline") or {}, _reg)
            for _it in _lchk(content, _prev_ledger, _reg, _cumu, _init):
                issues.append({
                    "type": "写作-机制数字方向违规",
                    "detail": _it,
                    "level": "小改",
                })
                score -= 5
            return issues, score
        except Exception:
            return [], 0

    def _check_core_event(self, content: str, core_event: str) -> tuple[list, int]:
        """2.8d 大纲核心事件正文覆盖校验（P0-17）：核心事件分段命中 <60% = 大改。"""
        if not core_event:
            return [], 0
        try:
            _miss = _core_event_missing(content, core_event)
            if _miss:
                return ([{
                    "type": "情节-大纲核心事件未覆盖",
                    "detail": (f"本章正文未覆盖大纲核心事件段（命中不足 60%）："
                               f"{' / '.join(_miss[:3])}——核心事件必须写进正文，"
                               "不得替换为自创事件"),
                    "level": "大改",
                }], -30)
            return [], 0
        except Exception:
            return [], 0

    def _check_dialogue_loop(self, content: str) -> tuple[list, int]:
        """2.9 对话轮重复（确定性兜底）：同一轮问答序列原样重复 ≥2 次 = 循环拼接。"""
        issues, score = [], 0
        for it in self._dialogue_loop_issues(content):
            issues.append(it)
            score -= 30
        return issues, score

    def _check_beat_cycle(self, content: str) -> tuple[list, int]:
        """2.12 情节级重演检测：完整节拍序列非重叠 ≥2 次 = 重演。

        REQ-词表换LLM 批次一：节拍序列优先 LLM 语义抽取，失败自动回退词表；
        开关 llm_semantic_beats（默认开）置 False 时纯词表。
        """
        issues, score = [], 0
        use_llm_beats = bool(getattr(self.cfg, "llm_semantic_beats", True))
        for it in _beat_cycle_issues(content, llm=self.llm, use_llm=use_llm_beats):
            issues.append(it)
            score -= 30
        return issues, score

    def _check_length_audit(self, content: str) -> tuple[list, int]:
        """2.10 字数审计（确定性·仅提示）：远超目标 → 提示不扣分（2026-10-17 起）。"""
        return self._length_audit_issues(content), 0

    def _check_summary_consistency(self, content: str, summary: str) -> tuple[list, int]:
        """2.11 摘要一致性（确定性兜底）：摘要面板分必须与正文最终结算一致。"""
        if not summary:
            return [], 0
        issues, score = [], 0
        for it in self._summary_consistency_issues(content, summary):
            issues.append(it)
            score -= 5
        return issues, score

    def _check_ending(self, content: str) -> tuple[list, int]:
        """2.6 结尾完整性（确定性）：正文未以句末标点收尾 → 疑似截断。"""
        issues, score = [], 0
        for it in self._ending_issues(content):
            issues.append(it)
            score -= 30
        return issues, score

    def _check_compliance(self, content: str) -> tuple[list, int]:
        """3. 输出合规（模块3 基础合规校验；结构化 rule_category/rule_phrase）。"""
        if not getattr(self.cfg.agent_guard, "output_check", True):
            return [], 0
        from ...safety import basic_content_check_structured as _bcs
        bad = _bcs(content)
        if not bad:
            return [], 0
        _cats = sorted({b.get("rule_category", "") for b in bad if b.get("rule_category")})
        return ([{
            "type": "合规-疑似违规内容",
            "detail": "；".join(f"「{b.get('phrase', '')}」" for b in bad),
            "level": "大改",
            "rule_category": "、".join(_cats),
            "rule_phrase": "；".join(b.get("phrase", "") for b in bad),
        }], -40)

    def _check_consistency(self, chapter_no: int, content: str) -> tuple[list, int]:
        """4.5 章内前后矛盾自检（LLM，REQ-章内自洽）：确定性+读者视角都漏的硬矛盾。
        高质量模式每章开启；普通模式按 consistency_llm_every 降频。"""
        _hq = getattr(self.cfg, "high_quality_mode", False)
        _every = max(1, int(getattr(self.cfg, "consistency_llm_every", 3) or 3))
        if not (_hq or (getattr(self.cfg, "consistency_llm_check", False) and chapter_no % _every == 1)):
            return [], 0
        issues, score = [], 0
        for it in self._consistency_llm_check(chapter_no, content):
            issues.append(it)
            score -= 25
        return issues, score

    #: 规则一致性 code → (问题级别, 扣分)；rule_conflict 与章内矛盾同级大改
    _RULE_CODE_POLICY = {
        "rule_conflict": ("大改", -25),
        "rule_added": ("中改", -10),
        "rule_scope": ("中改", -10),
    }

    def _check_rule_consistency(self, chapter_no: int, content: str) -> tuple[list[dict], int]:
        """4.6 规则条款一致性 LLM judge（REQ-通用机制类型适配）。

        启用条件（全部满足才调用）：开关开启；world 类型集合含 rule_set；ruleset 非空；
        高质量模式每章执行，普通模式按 rule_consistency_every 降频。仅对当前副本生效
        的条款（global + 当前实例 + 角色/能力作用域）做对照；抽取/LLM/解析失败一律
        返回空列表并 warning 留痕，不阻塞主流程。
        """
        if not content:
            return [], 0
        try:
            if not getattr(self.cfg, "rule_consistency_check", True):
                return [], 0
            world = self.store.load_world()
            if world is None:
                return [], 0
            from ..mechanism_types import has_type, resolve_types
            if not has_type(resolve_types(world), "rule_set"):
                return [], 0
            ruleset = getattr(world, "mechanism_ruleset", None) or []
            if not ruleset:
                return [], 0
            hq = getattr(self.cfg, "high_quality_mode", False)
            every = max(1, int(getattr(self.cfg, "rule_consistency_every", 1) or 1))
            if not (hq or chapter_no % every == 1):
                return [], 0

            from ..chapter_state import active_rules_for_instance
            state = self.store.load_chapter_state() or {}
            instance = str(state.get("current_instance") or "").strip()
            active = active_rules_for_instance(ruleset, instance)
            if not active:
                return [], 0

            rule_lines = []
            for rule in active:
                rule_lines.append(
                    f"[{getattr(rule, 'rule_id', '')}] scope={getattr(rule, 'scope', 'global')} "
                    f"policy={getattr(rule, 'change_policy', 'fixed')}；"
                    f"规则：{getattr(rule, 'statement', '')}；"
                    f"条件：{getattr(rule, 'condition', '')}；"
                    f"违反后果：{getattr(rule, 'consequence', '')}"
                )
            persona = _load_persona("rule_consistency")
            sys = (persona + "\n\n" if persona else "") + (
                "你是规则条款一致性裁判，只输出规定 JSON，不附加解释。")
            user = (
                f"当前副本/场景：{instance or '全局场景'}\n\n"
                "【当前生效规则清单】\n" + "\n".join(rule_lines) + "\n\n"
                f"【第{chapter_no}章正文】\n{content[:_CONSISTENCY_CHARS]}\n\n"
                "请对照规则清单检查，输出 violations JSON。"
            )
            raw = self.llm.chat(sys, user, temperature=0.2, max_tokens=800)
            data = parse_json_obj(raw)
            issues: list[dict] = []
            score = 0
            for item in (data.get("violations") or []):
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).strip()
                level, delta = self._RULE_CODE_POLICY.get(code, ("中改", -10))
                detail = str(item.get("detail", "")).strip()
                if not detail:
                    continue
                issues.append({
                    "type": f"规则一致性-{code}",
                    "detail": detail,
                    "level": level,
                    "fix_suggestion": str(item.get("suggestion") or "").strip(),
                })
                score += delta
            return issues, score
        except Exception as exc:
            logger.warning("[quality] 规则一致性校验降级跳过（ch=%s）：%s", chapter_no, exc)
            return [], 0

    def _finalize_issues(self, issues: list[dict], score: int) -> tuple[bool, list[dict], int]:
        """去重 + 排序（大改在前）+ 诊断回喂，返回 (passed, uniq, score)。"""
        # 去重 + 排序（大改在前）
        seen, uniq = set(), []
        for it in issues:
            k = (it.get("type"), it.get("detail"))
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        uniq.sort(key=lambda x: 0 if x.get("level") == "大改" else (1 if x.get("level") == "中改" else 2))

        passed = not any(x.get("level") == "大改" for x in uniq)

        # P0-3 质检诊断回喂（REQ-门禁质检正向影响）：确定性维度补 fix_suggestion /
        # expected_gain（"怎么改 + 预计提升"）；LLM 维度已带 suggestion 的不覆盖；
        # diag_feedback 关闭时不补（回退旧格式）。
        if getattr(self.cfg, "diag_feedback", True):
            try:
                from ..diagnosis import suggest_for as _sf
                for _it in uniq:
                    if _it.get("fix_suggestion"):
                        continue
                    _fb = _sf(_it)
                    if _fb:
                        _it["fix_suggestion"] = _fb.get("fix_suggestion", "")
                        if _fb.get("expected_gain"):
                            _it["expected_gain"] = _fb.get("expected_gain")
            except Exception:
                pass
        return passed, uniq, score

    def _check_ai_and_style(self, content: str, chapter_no: int) -> tuple[list[dict], int]:
        """AI 痕迹 + 文风漂移 + 同质度检测（确定性，各子项独立开关）。

        返回 (issues, score_delta)；score_delta 为负数表示扣分。
        """
        issues: list[dict] = []
        delta = 0
        # AI 痕迹量化检测（P1-4，确定性规则，只扣分不单独阻断）
        if getattr(self.cfg, "ai_taste_check", True):
            try:
                from ..ai_taste import ai_taste_issues
                taste_issues = ai_taste_issues(
                    content,
                    threshold=int(getattr(self.cfg, "ai_taste_threshold", 60) or 60),
                    llm=self.llm,
                    use_llm=bool(getattr(self.cfg, "llm_semantic_ai_taste", True)),
                )
                if taste_issues:
                    issues.extend(taste_issues)
                    delta -= 20
            except Exception:
                pass
        # 文风漂移 L1 确定性检测（REQ-超长篇攻坚 S2.4，每章必跑 ≈0 token）。
        if getattr(self.cfg, "style_drift_check", True):
            try:
                from ..ai_taste import drift_score, style_drift_metrics
                d = drift_score(content)
                if d < int(getattr(self.cfg, "style_drift_threshold", 60) or 60):
                    m = style_drift_metrics(content)
                    issues.append({
                        "type": "文风漂移-L1（句长模板化）",
                        "detail": (
                            f"漂移分 {d}/100；句长波动系数 {m.get('sentence_cv')}（过低=句长趋同"
                            f"模板化）、词汇多样性 TTR {m.get('ttr')}、填充词占比 "
                            f"{m.get('filler_ratio')}。单章信号，建议与前期章基线对比，"
                            f"跨章持续低分需再锚定"),
                        "level": "小改",
                    })
                    delta -= 5
            except Exception:
                logger.exception("文风漂移 L1 检测失败（降级跳过）")
        # P0-4 手法/句式层同质度（REQ-门禁质检正向影响）：跨章趋同软提示。
        if getattr(self.cfg, "style_homogeneity_check", True):
            try:
                for _homog_it in self._style_homogeneity_issues(chapter_no, content):
                    issues.append(_homog_it)
            except Exception:
                logger.exception("同质度检测失败（降级跳过）")
        return issues, delta

    # ---------- 确定性规则实现 ----------
    def _dup_issues(self, chapter_no: int, content: str) -> list[dict]:
        if not content:
            return []
        grams = {content[i:i + _NGRAM] for i in range(0, len(content) - _NGRAM + 1)}
        if not grams:
            return []
        issues = []
        for no in range(max(1, chapter_no - _MAX_RECENT), chapter_no):
            prev = self.store.load_chapter(no)
            if not prev or not prev.content:
                continue
            pgrams = {prev.content[i:i + _NGRAM] for i in range(0, len(prev.content) - _NGRAM + 1)}
            if not pgrams:
                continue
            ratio = len(grams & pgrams) / len(pgrams)
            if ratio > _DUP_RATIO:
                issues.append({
                    "type": "结构-与前文重复",
                    "detail": f"与第{no}章重叠率 {ratio:.0%}，疑似大段复用",
                    "level": "中改",
                })
        return issues

    @staticmethod
    def _draft_markers(content, min_count=2, min_line=_SALVAGE_MIN_LINE,
                       max_line=_SALVAGE_MAX_LINE, spread=_SALVAGE_SPREAD) -> dict:
        """检测「同一章多稿首尾拼接」的分界标记：跨大跨度重复出现的独特短行。

        返回 {行文本: [字符偏移,...]}；无拼接返回空 dict。
        只统计「短促收尾/面板行」（长度在 min_line~max_line 之间）：多稿拼接时
        每篇草稿都会自带同一句短收尾（如"剩余次数：2。"），而长行重复多为
        正常叙事复用（如第2章一条 40 字长行重复 2 次是正常内容），不能当标记。
        """
        if not content or len(content) < min_line * 3:
            return {}
        lines = content.split("\n")
        occ: dict[str, list[int]] = {}
        pos = 0
        for ln in lines:
            s = ln.strip()
            # 面板数字行不作为「草稿分界标记」：同值复现属于"数字结算未推进"
            # （_panel_progress_issues 专检），不应误判为多稿拼接而拦腰截断
            if (min_line <= len(s) <= max_line
                    and not _is_panel_line(_norm_sentence(s))):
                occ.setdefault(s, []).append(pos)
            pos += len(ln) + 1
        return {s: ps for s, ps in occ.items()
                if len(ps) >= min_count and ps[-1] - ps[0] >= spread}

    def _self_dup_issues(self, content: str) -> list[dict]:
        """章内自重复（确定性）：同一章正文内部出现大段重复 → 疑似多稿拼接。

        这是对「与前文重复」检查的补盲：后者只比对前几章，看不见本章内部的
        多段草稿彼此重复。两个互补信号，任一命中即视为拼接：
        A. 草稿分界标记：同一短促收尾/面板行（8~16字）跨大跨度重复 ≥3 次
           （正常单章几乎不会，多稿拼接必然多篇共用同一句短收尾）；
        B. n-gram 覆盖：被「出现≥2次的 n-gram」覆盖的字符占比过高
           （草稿之间大段内容相同但措辞略有差异，或草稿数少<3 时用）。
        """
        if not content or len(content) < _SELF_NGRAM * 3:
            return []
        # 信号 A：草稿分界标记（短收尾行重复 ≥3 次）
        if self._draft_markers(content, min_count=3):
            return [{
                "type": "结构-章内大量自重复（疑似多稿拼接）",
                "detail": "正文中出现跨大跨度重复的短促收尾句（疑似同一章多段草稿首尾拼接）；"
                          "应只保留单一完整版本",
                "level": "大改",
            }]
        # 信号 B：n-gram 覆盖比例
        grams: dict[str, list[int]] = {}
        for i in range(len(content) - _SELF_NGRAM + 1):
            grams.setdefault(content[i:i + _SELF_NGRAM], []).append(i)
        covered = [False] * len(content)
        for poss in grams.values():
            if len(poss) >= 2:
                for i in poss:
                    for j in range(i, min(i + _SELF_NGRAM, len(content))):
                        covered[j] = True
        ratio = sum(covered) / len(content)
        if ratio > _SELF_DUP_RATIO:
            return [{
                "type": "结构-章内大量自重复（疑似多稿拼接）",
                "detail": (f"正文 {len(content)} 字中约 {ratio:.0%} 由重复片段构成，"
                           "疑似同一章多段草稿被首尾拼接；应只保留单一完整版本"),
                "level": "大改",
            }]
        return []

    def _repeat_sentence_issues(self, content: str) -> list[dict]:
        """章内同句复读（确定性，REQ-章内自洽与机制账本）。

        归一化句子后统计：叙述句（引号外）出现 ≥3 次、或叙述长句 ≥12 字出现 2 次
        → 大改；叙述短句（8~11 字）出现 2 次 → 中改；对话句（口头禅）≥3 次 → 大改、
        2 次放行。
        聚合为每条类型一条 issue（附最多 3 个例句），避免刷屏。

        P1-2 起数字面板行（含次数/进度/剩余/累积/状态+数字）不再纳入复读统计——面板行
        重复由 _panel_progress_issues 专检（区分"结算未推进"大改/"冗余显示"中改），
        复读再判大改会造成双重大改、误触发重写（《万墟之上》22:32 版实测）。
        """
        if not content or len(content) < 100:
            return []
        groups: dict[str, dict[str, list[str]]] = {"叙述": {}, "对话": {}}
        for sentence, in_quote in _iter_sentences(content):
            norm = _norm_sentence(sentence)
            if not norm or len(norm) < _REPEAT_MIN_LEN:
                continue
            if _is_panel_line(norm):
                continue  # 面板行归 _panel_progress_issues 专检
            bucket = "对话" if in_quote else "叙述"
            groups[bucket].setdefault(norm, []).append(sentence.strip())

        hard: list[str] = []
        soft: list[str] = []
        for bucket, norm, sentences in _iter_repeat_groups(groups):
            cnt = len(sentences)
            if cnt >= _REPEAT_HARD_COUNT:
                hard.append(f"[{bucket}×{cnt}] {sentences[0]}")
            elif bucket == "叙述" and cnt == 2:
                if len(norm) >= _REPEAT_LONG_LEN:
                    hard.append(f"[叙述×2] {sentences[0]}")
                else:
                    soft.append(f"[叙述×2] {sentences[0]}")
            # 对话 ×2 = 口头禅（writer.md 允许 ≤2 次），不报

        issues: list[dict] = []
        if hard:
            issues.append({
                "type": "写作-同句复读",
                "detail": "同一句/同一动作整句原样复读（" + "；".join(hard[:3]) + "……）；"
                          "同一母题不得原样复读，重复必须有递进",
                "level": "大改",
            })
        if soft:
            issues.append({
                "type": "写作-同句复读（轻）",
                "detail": "叙述短句原样出现 2 次（" + "；".join(soft[:3]) + "）；建议改写为不同表述",
                "level": "中改",
            })
        return issues

    def _panel_progress_issues(self, content: str, llm=None, use_llm: bool = True) -> list[dict]:
        """机制数字结算校验（确定性）：同一面板数字（同标签同数值）跨段复现 = 结算未推进。

        如「剩余次数：2/3」在两次死亡后仍显示「2/3」——数字未随事件递减。
        与 _repeat_sentence_issues 的区别：这里按「标签+数值」对齐（不要求整句相同），
        且要求两次出现间隔超过 _PANEL_REPEAT_SPAN（过滤"数字跳了一下"式紧邻强调）。
        正常递减（3/3→2/3→1/3）不误报。

        事件锚点（2026-09-30 修复）：两次同值之间若出现死亡/复活/刷新等结算事件词
        → 真"跨结算点同值"→ 大改；若无事件词（如复活后确认、赶路途中再看一眼、
        章末备注栏变化时数字本就不变）→ 属同一结算值的冗余重复显示 → 降级中改
        （《万墟之上》21:52 版"1/3"三次显示被判大改属误报，本规则修复）。
        """
        if not content or len(content) < _PANEL_REPEAT_SPAN * 2:
            return []
        # 后缀只保留通用机制词（次数/进度/剩余/累积/状态），书级字段名（如《万墟之上》
        # 的「蚀化/蚀痕」）由设定账本派生的字段注册表在 direction/ledger 校验层兜底
        # （本函数是冗余层，不预置单书机制词）。
        pattern = re.compile(
            r"([\u4e00-\u9fff]{0,12}?(?:次数|进度|剩余|累积|状态))\s*[：:]\s*"
            r"([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
        )
        hits: dict[tuple[str, str], list[int]] = {}
        for m in pattern.finditer(content):
            label = (m.group(1).strip() or "")[-_PANEL_SPAN_LABEL:]
            num = (m.group(2) or "").replace(" ", "")
            if not label or not num:
                continue
            hits.setdefault((label, num), []).append(m.start())
        major: list[str] = []   # 跨结算点同值（真未推进）
        minor: list[str] = []   # 无事件冗余重复显示
        for (label, num), poss in hits.items():
            if len(poss) < 2:
                continue
            for a, b in zip(poss, poss[1:]):
                gap = b - a
                if gap <= _PANEL_REPEAT_SPAN:
                    continue
                seg = content[a:b]
                # 事件锚点判定（REQ-词表换LLM 批次二）：LLM 语义判定优先；
                # llm 缺失/开关关/调用失败/输出非结构化 → 回退锚点+回忆正则兜底（行为零变化）
                event: bool | None = None
                if llm is not None and use_llm:
                    try:
                        event = _llm_panel_seg_settle(seg, llm)
                    except Exception as exc:
                        logger.warning(
                            "panel seg settle LLM 判定失败，回退词表锚点正则: %s: %s",
                            type(exc).__name__, exc)
                        event = None
                    else:
                        if event is None:
                            logger.warning("panel seg settle LLM 输出非结构化，回退词表锚点正则")
                if event is None:
                    event = _seg_has_settle_event_regex(seg)
                if event:
                    major.append(f"{label}：{num}")
                else:
                    minor.append(f"{label}：{num}")
        out: list[dict] = []
        if major:
            out.append({
                "type": "写作-机制数字未结算推进",
                "detail": "面板数字「" + "；".join(major[:3]) + "」在结算事件后仍未变化，"
                          "数值未随事件结算推进（如次数未递减）；建议按机制卡随事件结算，"
                          "避免同值跨结算点复现（改稿建议，不强制重写）",
                "level": "小改",
            })
        if minor:
            out.append({
                "type": "写作-面板数字重复显示（冗余）",
                "detail": "面板数字「" + "；".join(minor[:3]) + "」在无新事件时重复显示，"
                          "稀释面板的紧张感；建议面板数字只在结算/刷新瞬间出现一次，"
                          "后续确认状态改用叙述（如'他看了一眼面板，还剩一次'）"
                          "（改稿建议，不强制重写）",
                "level": "小改",
            })
        return out

    def _ending_issues(self, content: str) -> list[dict]:
        """结尾完整性（确定性）：正文未以句末标点收尾 → 疑似在句子中途被截断。"""
        if not content:
            return []
        last = content.rstrip()[-1]
        if last not in _TERMINAL:
            return [{
                "type": "结构-正文未完整收尾（疑似截断）",
                "detail": "本章正文未以句末标点结束，疑似在句子中途被截断；应补完结尾",
                "level": "大改",
            }]
        return []

    def _dialogue_loop_issues(self, content: str) -> list[dict]:
        """场景内对话轮重复（确定性兜底，防'同一轮问答写多遍'循环拼接）。

        抓相邻台词对（连续两句台词组成的问答序列）重复出现：同一轮对话若写成
        「你死了？→你看错了→我看见数字了」多遍（《万墟之上》21:27 版三遍循环），
        相邻台词对必然 ≥2 次重复。区别于 _repeat_sentence_issues 的单句统计：
        这里按"两句台词序列"对齐，命中即大改（场景级循环拼接）。
        """
        if not content or len(content) < 300:
            return []
        lines = [s.strip() for s, in_quote in _iter_sentences(content) if in_quote]
        norms = [_norm_sentence(s) for s in lines if _norm_sentence(s)]
        pairs: dict[tuple[str, str], int] = {}
        for a, b in zip(norms, norms[1:]):
            if a and b:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1
        bad: list[str] = []
        for (a, b), cnt in pairs.items():
            if cnt >= 2 and len(a) >= 3 and len(b) >= 3:
                bad.append(f"「{a}→{b}」×{cnt}")
        if not bad:
            return []
        return [{
            "type": "写作-对话轮重复（循环拼接）",
            "detail": "同一轮问答/对话序列原样重复（" + "；".join(bad[:3]) + "）；"
                      "同一场景的同一轮对话只允许出现一次，重复=循环拼接，必须重写合并",
            "level": "大改",
        }]

    def _length_audit_issues(self, content: str) -> list[dict]:
        """字数审计（确定性·仅提示，不判大改、不扣分）：正文远超章节目标字数。

        2026-09-30 方向修正（用户确认）：超长 ≠ 质量失败，纯字数不触发整章重写
        （重写 3 轮全文 + 复检 = 数万 token 浪费，内容质量正常的超长正文应保留）。
        2026-10-17 再修正（用户确认）：超长连小改也不扣分——只要剧情与质量正常，
        字数超出不应成为减分项，不能单纯以字数作为判断；仍保留一条提示（小改级、
        不扣分）供模型后续收敛，若同时存在质量大改，重写 prompt 已含字数区间约束，
        重写时自然压缩。
        剥离【摘要】后统计正文；>1.35 倍目标 → 小改提示（不扣分）。
        """
        target = int(getattr(self.cfg, "chapter_words", 0) or 0)
        if target <= 0 or not content:
            return []
        import re as _re
        body = _re.split(r"【摘要】", content)[0].strip()
        if not body:
            return []
        n = len(body)
        if n > target * 1.35:
            return [{
                "type": "写作-字数超出目标",
                "detail": f"正文 {n} 字，超过本章目标字数 {target} 的 1.35 倍；"
                          f"仅提示收敛，不扣分、不重写（超长只要剧情与质量正常即保留）",
                "level": "小改",
            }]
        return []

    def _summary_consistency_issues(self, content: str, summary: str) -> list[dict]:
        """摘要一致性（确定性兜底）：摘要提到的机制数字分数必须与正文最终结算一致。

        实测版：正文死亡两次、最终结算 1/3，摘要却写"面板从3/3结算为2/3"——摘要未随
        正文更新（只提取了第一次死亡）。正文无机制分数或摘要无分数 → 不报（不误伤无
        机制数字的章节）。只匹配通用「次数：N/M」格式（书级标签如「可死次数」由
        「次数」分支覆盖，不预置单书词）。
        """
        if not content or not summary:
            return []
        import re as _re
        panel = _re.findall(r"(?:剩余次数|次数)\s*[:：]\s*(\d+)\s*[／/]\s*(\d+)", content)
        if not panel:
            return []
        final_body = "/".join(panel[-1])
        sum_nums = _re.findall(r"(\d+)\s*[／/]\s*(\d+)", summary)
        if not sum_nums:
            return []
        final_sum = "/".join(sum_nums[-1])
        if final_sum == final_body:
            return []
        return [{
            "type": "写作-摘要与正文最终状态不符",
            "detail": f"摘要提到「{final_sum}」，而正文最终结算为「{final_body}」——"
                      f"摘要未随正文更新，建议覆盖本章最终结算后的机制状态（次数递减到哪）"
                      "（改稿建议，不强制重写）",
            "level": "小改",
        }]

    def _salvage_single_draft(self, content: str) -> str:
        """同章多稿首尾拼接 → 确定性抢救出最完整的单一草稿；无拼接则原样返回。

        用「草稿分界标记」（重复独特长句）切候选段，取「最长且以句末标点收尾」的一段。
        纯确定性实现，不依赖 LLM，避免重写时再次堆稿。
        """
        marks = self._draft_markers(content)
        if not marks:
            return content
        # 选「出现次数最多、行最短」的重复句作分界标记：短而独立的收尾句
        # （如面板行"剩余次数：2。"）比长开头句更贴近"草稿收尾"，避免切出碎片。
        marker = max(marks, key=lambda s: (len(marks[s]), -len(s)))
        cuts = sorted(marks[marker])
        segs, prev = [], 0
        for c in cuts:
            nl = content.find("\n", c)
            end = nl if nl != -1 else len(content)
            segs.append(content[prev:end])
            prev = end + 1
        segs.append(content[prev:])  # 最后一稿（通常为新起笔的截断稿）

        def _score(s: str) -> int:
            t = s.strip()
            if not t:
                return -1
            return len(t) + (100000 if t[-1] in _TERMINAL else 0)

        best = max(segs, key=_score).strip()
        # 去掉开头的悬空残留行（上一稿收尾的短行，如"明天呢？"）：只去
        # ≤4字 或 ≤12字且以问号/省略号结尾的短行，避免误删真实起句（如"他走得很快。"）
        bl = best.split("\n")
        while len(bl) > 1 and bl[0].strip():
            s = bl[0].strip()
            if len(s) <= 4 or (len(s) <= 12 and s[-1] in "？…"):
                bl = bl[1:]
            else:
                break
        best = "\n".join(bl).strip()
        return best

    def _content_check(self, content: str) -> list[str]:
        from ...safety import basic_content_check
        return basic_content_check(content)

    def _style_homogeneity_issues(self, chapter_no: int, content: str) -> list[dict]:
        """P0-4 手法/句式层同质度（REQ-门禁质检正向影响 §7.6）：跨章风格趋同软提示。

        读最近 3 章正文尾部（含本章），用 ai_taste 指标（句长波动系数 CV / 词汇
        多样性 TTR）量化；近 3 章 CV 持续偏低且彼此接近（pstdev 小）、TTR 偏低
        → 跨章趋同模板化信号（可能来自 good 片段过度复用）。level=小改、不扣分
        （软提示，不阻断流程；供作者/后续降权参考）。
        读取失败/章数不足/文本过短 → 空列表（降级，不阻塞）。
        """
        if chapter_no <= 2 or not content:
            return []
        try:
            from ..ai_taste import style_drift_metrics as _sdm
            rows: list[dict] = []
            for ch in range(chapter_no - 2, chapter_no + 1):
                if ch == chapter_no:
                    txt = content
                else:
                    try:
                        _c = self.store.load_chapter(ch)
                        txt = getattr(_c, "content", "") or ""
                    except Exception:
                        txt = ""
                if len(txt) < 150:
                    return []
                rows.append(_sdm(txt[-600:]))
            if len(rows) < 3:
                return []
            import statistics
            cvs = [float(r.get("sentence_cv") or 0) for r in rows]
            ttrs = [float(r.get("ttr") or 0) for r in rows]
            # 趋同判定（宽松阈值，宁漏报不误伤）：3 章句长 CV 都偏低且彼此接近 + TTR 偏低
            if (all(c < 0.6 for c in cvs) and statistics.pstdev(cvs) < 0.10
                    and all(t < 0.6 for t in ttrs)):
                return [{
                    "type": "文风同质化-跨章趋同（句式/词汇模板化）",
                    "detail": (
                        f"最近 3 章句长波动系数 CV={[round(c, 2) for c in cvs]}、"
                        f"词汇多样性 TTR={[round(t, 2) for t in ttrs]} 持续偏低且稳定，"
                        "提示跨章风格趋同（可能来自正向片段过度复用）。建议本章引入 "
                        "1-2 个新的写法特征或场景处理，避免套路重复。"),
                    "level": "小改",
                }]
            return []
        except Exception:
            return []

    # ---------- LLM 校验（读者视角 + 人物一致性） ----------
    def _reader_llm_check(self, chapter_no: int, plan_title: str, plan_goal: str,
                          content: str) -> tuple[list[dict], int | None]:
        if self.guard is not None:
            self.guard.enter_scene("review")
        try:
            world = self.store.load_world()
            char_block = ""
            if world and world.characters:
                char_block = "；".join(
                    f"{c.name}({c.role or ''}：{(c.desc or '')[:60]})"
                    for c in world.characters[:12] if getattr(c, "name", None)
                )
            system, user = reader_review_prompt(
                self.cfg, chapter_no, plan_title, plan_goal, content
            )
            if char_block:
                user = (
                    user.replace("【本章正文】",
                                 "【人物卡】\n" + char_block + "\n\n【本章正文】")
                    + "\n额外校验：正文中的人物言行是否符合【人物卡】设定？有无性格/身份/称呼与人物卡冲突？"
                    "（冲突则加入 issues，type='人物一致性'）"
                )
            raw = self.llm.chat(system, user, temperature=0.3, max_tokens=1200)
            data = parse_json_obj(raw)
            issues = [{
                "pos": it.get("pos", ""),
                "type": f"读者-{it.get('type', '')}",
                "detail": it.get("detail", ""),
                "level": it.get("level", "小改"),
                "fix_suggestion": str(it.get("suggestion") or "").strip(),  # P0-3 诊断回喂
            } for it in (data.get("issues") or []) if isinstance(it, dict)]
            hook = data.get("追更欲")
            hook_score = int(hook) if isinstance(hook, (int, float)) and 1 <= int(hook) <= 5 else None
            return issues, hook_score
        except Exception:
            return [], None

    # ---------- 章内前后矛盾自检（LLM） ----------
    def _consistency_llm_check(self, chapter_no: int, content: str) -> list[dict]:
        """通读全章找硬矛盾：①主角身体状态（伤口位置/知觉范围/服饰损坏）前后不一；
        ②同一类生物/配角的行为模式前后不一；③方位/指代错误（人在A地却描述B地）；
        ④同一意象的比喻出现两个版本；⑤同一事件出现两个版本（同人物+同冲突+同结果，
        换地点/换描写重写一遍，如"救同伴引开追兵"后又写坡地版同过程）。
        只报硬矛盾，报了即大改。"""
        if not content:
            return []
        try:
            persona_r = _load_persona("reviewer")
            sys = (
                (persona_r + "\n\n" if persona_r else "")
                + "你是严谨的小说文字编辑，通读本章后只找「前后自相矛盾的硬伤」，不评文笔、不评节奏。\n"
                "只输出 JSON：{\"contradictions\":[{\"type\":\"身体状态/行为模式/方位指代/比喻/事件重复\","
                "\"detail\":\"具体矛盾：前一处…后一处…\",\"suggestion\":\"统一为…的具体改法\"}]}。\n"
                "判定标准：两处描写不能同时为真才算矛盾；比喻措辞不同但喻体相同不算；"
                "随剧情合理变化的伤口/状态不算矛盾；没有矛盾就输出空数组。\n"
                "特别注意⑤同一事件重复：同一人物面对同一冲突对象、实际做出同一件事（救/围/杀/逃/挡等行为动作）"
                "且结果相同，即使换了地点或改了细节，也算'同一事件两版本'（如先写'主角冲下去救同伴引开追兵'"
                "后又写一遍'坡地上同伴又被围、主角再次冲下去救'）——报 type=事件重复，"
                "detail 必须引用两处【实际发生的行为原句】，写明共同的人物/冲突/结果。\n"
                "【事件重复排除条款·必须遵守】以下情况一律【不得】判为事件重复：\n"
                "a) 同一冲突对象下，人物的'内心决定/犹豫/退缩念头'（如'决定绕走'、'想不救'、'打算撤退'）"
                "与后续'实际行动'（如'冲出去救人'）属于'抉择→行动'的因果弧光，两者是先后承接关系、"
                "可以同时为真，不是同一事件的两个版本；\n"
                "b) 未执行的念头、计划、心理活动（未实际发生的行为）不算'事件发生'；"
                "只有实际发生的动作才构成事件；\n"
                "c) 若无法引用两处【实际发生行为】的原句，不得输出事件重复。"
            )
            user = (
                f"第{chapter_no}章正文：\n{content[:_CONSISTENCY_CHARS]}\n\n"
                "请输出上述 JSON。"
            )
            raw = self.llm.chat(sys, user, temperature=0.2, max_tokens=800)
            data = parse_json_obj(raw)
            out = []
            for it in (data.get("contradictions") or []):
                if not isinstance(it, dict):
                    continue
                detail = str(it.get("detail", "")).strip()
                if not detail:
                    continue
                out.append({
                    "type": f"自相矛盾-{it.get('type', '')}",
                    "detail": detail,
                    "level": "大改",
                    "fix_suggestion": str(it.get("suggestion") or "").strip(),  # P0-3 诊断回喂
                })
            return out
        except Exception:
            return []

    # ---------- 自动重写（1 次；REQ-token优化 P1-4：定点化，不整章重出） ----------
    def _rewrite_once(self, chapter_no: int, plan_title: str, plan_goal: str,
                      content: str, issues: list[dict],
                      baseline: dict | None = None) -> str:
        """优先只重写问题段落（省整章重出 token 60~75%）；无定位点/跨度过大 → 整章重写。

        REQ-门禁质检正向影响 P0-1：合规红线命中（compliance_clean_rewrite 开）时
        **跳过定点重写**，强制 `_rewrite_whole(skip_original=True)` 删旧稿整章重写——
        违规旧稿的其余段落不得被定点重写保留拼回。
        """
        try:
            if getattr(self.cfg, "compliance_clean_rewrite", True):
                from ..compliance import has_compliance
                if has_compliance(issues):
                    return self._rewrite_whole(
                        chapter_no, plan_title, plan_goal, content, issues,
                        baseline=baseline, skip_original=True,
                    )
            if getattr(self.cfg, "targeted_rewrite", True):
                spans = _locate_problem_spans(content, issues)
                if spans:
                    spans = _align_to_paras(content, spans)
                    # 合并后仍为单一窗口且覆盖 <60% 正文 → 定点重写
                    if len(spans) == 1 and (spans[0][1] - spans[0][0]) < len(content) * 0.6:
                        rewritten = self._rewrite_span(
                            chapter_no, plan_title, plan_goal, content, spans[0], issues,
                            baseline=baseline,
                        )
                        if rewritten and rewritten != content:
                            return rewritten
            return self._rewrite_whole(chapter_no, plan_title, plan_goal, content, issues,
                                       baseline=baseline)
        except Exception:
            return ""

    def _memory_block(self, issues: list[dict], chapter_no: int | None = None) -> str:
        """P0-7：重写时注入本作历史错误（踩坑 Top-N）+ 数字修复回灌；空则返回空串。

        只对重写路径生效（for_rewrite=True 措辞：删除重复版本）；场景生成用
        stages 侧注入的 pitfall_block（min_count=2 纪律类）。
        REQ-写作正向引导 5.6：叠加 good 账本（本作高分章好片段——重写不只
        「避免什么」，还要「按什么质感写」，参考质感非内容来源）。
        chapter_no：当前重写章号（P3 去污染——last_ch==本章 的本轮错误降权到
        尾部，避免把本章刚犯的错当历史教训回灌）。
        """
        if self.memory is None or not getattr(self.cfg, "book_memory", True):
            return ""
        try:
            top_n = int(getattr(self.cfg, "book_memory_top_n", 5) or 5)
            pb = self.memory.pitfall_block(top_n=top_n, for_rewrite=True,
                                           self_ch=chapter_no)
            # P0-8（2026-10-10）：数字 issue 挑选收紧为 type 精确匹配——原条件含
            # "面板" in detail，会把「情节-同事件循环重演」（detail 含"面板"字样）误挑
            # 进 numeric_backfill（v6 实测两条纪律 rule 错配的同源 bug）。
            num_issues = [
                str(it.get("detail") or "") for it in (issues or [])
                if "机制数字" in (str(it.get("type") or ""))
            ]
            nb = self.memory.numeric_backfill(num_issues)
            gb = self.memory.good_block(top_n=2)
            return "\n\n".join(x for x in (pb, nb, gb) if x)
        except Exception:
            return ""

    def _diagnose_rewrite(self, content: str) -> list[dict]:
        """P0-10 重写前诊断（混合方案②，2026-10-10）：重写前独立审稿判重复结构。

        治 v4 教训（重写 1 把拼接写得更严重：第四轮第 5 章 2 个问题→4 个问题）——
        原链路「重写 → 复检」让重写 agent 在信息真空里自由发挥，自校验查不出
        同构场景/拼接。本 agent 在重写**前**独立诊断，输出「删除哪段、保留哪段」，
        重写 prompt 按诊断执行。任何失败降级空列表（不阻塞）。
        """
        try:
            import json
            from ..templates import rewrite_diagnose_prompt
            sys_d, user_d = rewrite_diagnose_prompt(content or "")
            raw = self.llm.chat(sys_d, user_d, temperature=0.2, max_tokens=1200)
            t = raw.strip()
            if t.startswith("```"):
                lines = t.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                t = "\n".join(lines).strip()
            d = json.loads(t)
            out = []
            for f in d.get("findings") or []:
                try:
                    s, e = int(f["start"]), int(f["end"])
                except Exception:
                    continue
                if 0 <= s < e <= len(content or ""):
                    out.append({
                        "type": str(f.get("type") or ""),
                        "start": s, "end": e,
                        "action": str(f.get("action") or "删除"),
                        "reason": str(f.get("reason") or "")[:80],
                    })
            return out
        except Exception:
            return []

    @staticmethod
    def _diag_block(findings: list[dict]) -> str:
        """重写前诊断注入块：按诊断清单逐条「删除哪段、保留哪段」确定性指令。"""
        if not findings:
            return ""
        lines = []
        for f in findings[:6]:
            s, e = f.get("start", 0), f.get("end", 0)
            t = f.get("type", "")
            reason = f.get("reason", "")
            if "拼接" in t or "重复" in t:
                lines.append(f"- 删除正文 [{s}:{e}] 的重复版本（{reason}）；"
                             "只保留最完整的一遍，被删版本的不可替代信息合并进保留版本")
            elif "对话" in t or "循环" in t:
                lines.append(f"- 删除正文 [{s}:{e}] 的重复对话轮（{reason}）；"
                             "同一组问答/对峙只保留一次")
            else:
                lines.append(f"- 按诊断修正 [{s}:{e}]（{reason}）")
        return ("【重写前诊断（独立审稿，先于重写执行：正文存在以下重复结构，"
                "重写必须按此删除后再连贯成篇）】\n" + "\n".join(lines))

    def _rewrite_whole(self, chapter_no: int, plan_title: str, plan_goal: str,
                       content: str, issues: list[dict],
                       baseline: dict | None = None,
                       skip_original: bool = False) -> str:
        """整章重写（原行为，无定位信息/定点失败时兜底）。

        skip_original（REQ-门禁质检正向影响 P0-1）：合规红线命中时 True——
        不携带【当前正文】（违规旧稿不可作为重写参照），注入"整章全新重写"声明 +
        首次合规教育块（P0-2），模型不得复用/复述原稿任何段落或措辞。
        P0-3：issue_block 用带建议的诊断（fix_suggestion/expected_gain）。

        注入设定上下文（世界观/机制卡/人物卡/章末账本）——重写不得在"信息真空"
        中进行，否则模型不知道机制结算规则，数字与矛盾修不动（实测复检仍 5 条）。
        REQ-写作正向引导 5.3：重写同样注入「权威当前值·只抄不推」（一期只接了
        场景路径，重写路径缺权威基线 → 数字违规重写后仍违规，二期实证）。
        """
        try:
            target = max(self.cfg.chapter_words, 500)
            if getattr(self.cfg, "diag_feedback", True):
                try:
                    from ..diagnosis import build_issue_block as _bib
                    issue_block = _bib(issues, max_n=8)
                except Exception:
                    issue_block = "\n".join(
                        f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:8]
                    )
            else:
                issue_block = "\n".join(
                    f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:8]
                )
            # 2026-10-01：问题含"事件重复/两版本"（矛盾自检事件重复、同句复读整段重复等）
            # 时，重写必须显式执行"每事件只写一遍"——质检问题清单只有位置描述，
            # 不注入事件序列纪律，模型会把重复版本换个写法又保留（4500 字版重写 3 轮
            # 修不掉救人二版/死亡复刻即此类）。
            has_event_dup = any(
                "事件重复" in (it.get("type") or "")
                or "两版本" in (it.get("detail") or "")
                or "整段" in (it.get("detail") or "")
                or "原样复刻" in (it.get("detail") or "")
                # P0-5（2026-10-10）：补 2.12 情节级重演的判定词——此前重演 issue
                # （type=情节-同事件循环重演，detail 含"多版本/换措辞循环"）不匹配
                # 上述关键词，dedup_block 不触发，重写无"每事件只写一遍"约束，
                # 重写 1 反而把拼接写得更严重（第四轮第 5 章 2 个问题→4 个问题）。
                or "循环重演" in (it.get("type") or "")
                or "多版本" in (it.get("detail") or "")
                or "换措辞" in (it.get("detail") or "")
                or "重演" in (it.get("type") or "")
                for it in issues
            )
            dedup_block = (
                "\n本章存在重复事件/两版本拼接，重写必须执行：\n"
                "6. 本章每个事件（救援、逃离、被围、对峙等）只允许完整发生一次：\n"
                "   删除重复的事件版本，保留最完整的一个；同一事件不得以任何形式再次出现"
                "（包括换地点、换措辞、换细节重写一遍）；被删除版本中不可替代的信息"
                "（如关键对话、关键状态）合并进保留版本\n"
                "7. 事件之间必须因果推进：后一事件从上一事件的结局之后开始，"
                "禁止回到上一事件开头状态重演（连'伏看-犹豫-起手式'都禁止重复）\n"
                "8. 每个事件的结束状态（机制数字/伤势/状态）必须比上一事件结束时有增量变化，"
                "禁止两事件结束时间一状态\n\n"
            ) if has_event_dup else ""
            # P2-1 正向替换供给：重复类问题命中时，除「每事件只写一遍」禁令外，
            # 注入「新方向候选」（未兑现承诺/伏笔/场景多样化），让重写知道「往哪走」。
            direction_block = (
                self._repetition_direction_block(chapter_no)
                if has_event_dup and getattr(self.cfg, "repetition_direction", True)
                else ""
            )
            mem_block = self._memory_block(issues, chapter_no)
            # REQ-写作正向引导 5.3：权威当前值注入重写（与生成路径同口径——重写
            # 必须按账本权威值修数字，禁止模型自由发挥）
            _blk_text = ""
            try:
                from ..templates import _panel_baseline_block as _pbb
                if baseline:
                    _blk_text = _pbb("\n".join(f"- {k}：{v}" for k, v in baseline.items()))
            except Exception:
                _blk_text = ""
            # P0-10 重写前诊断（混合方案②）：独立审稿判多稿拼接/对话循环 →
            # 注入"删除哪段、保留哪段"确定性指令（治 v4 重写越改越差）
            _diag = self._diagnose_rewrite(content)
            _diag_text = self._diag_block(_diag) if _diag else ""
            ctx = self._context_block(chapter_no)
            prompt = (
                f"你是资深网文写手。以下是第{chapter_no}章《{plan_title}》的正文，"
                f"质检发现如下问题，请重写本章正文。\n"
                f"【本章目标】{plan_goal}\n"
                + (f"\n{ctx}\n" if ctx else "")
                + (f"\n{mem_block}\n" if mem_block else "")
                + (f"\n{_blk_text}\n" if _blk_text else "")
                + (f"\n{_diag_text}\n" if _diag_text else "")
                + f"【质检问题】\n{issue_block}\n\n"
                f"重写要求：\n"
                f"1. 保留原有剧情走向、人物设定与关键对话，不推翻前文既定事实\n"
                f"2. 针对性修复上述每个问题（补足字数/去除重复/修正人物一致性/提升读者体验）\n"
                f"3. 正文（不含摘要）控制在约 {target} 字（±15% 以内）：不足则补足，"
                f"超出则精炼删减；禁止超写注水——超长输出会被截断丢弃\n"
                f"4. 同一角色本章外貌/服装/代称全局唯一（不得同一角色两套描述两版本）\n"
                f"5. 输出格式：先输出正文，正文结束后另起一行输出【摘要】加 1-2 句剧情摘要；"
                f"摘要必须写本章最终结算后的机制状态（如'剩余次数减至 2/3'）\n"
                + dedup_block
                + direction_block
                + "\n【硬性要求：重写即整体替换】\n"
                  "只输出一个单一完整连贯的新版正文。严禁保留原文段落再追加新写法"
                  "（多稿拼接）；严禁把同一场景/事件换措辞、换地点、换细节写多遍后"
                  "拼在一起。如需修改某处内容，直接在该处替换，禁止在文末或他处"
                  "复写另一平行版本。输出必须是前后连贯的一整篇，不是多段草稿的合集。\n"
                + self._clean_rewrite_block(chapter_no, issues, content, skip_original)
            )
            _persona = _load_persona("writer")
            # REQ-踩坑账本注入对账 - 第 3 步（P1 重写路径 whole）：prompt 拼装完成、chat 之前
            if getattr(self.cfg, "pitfall_reconcile", True):
                _rew_patterns = []
                if self.memory is not None and getattr(self.cfg, "book_memory", True):
                    try:
                        _rew_top_n = int(getattr(self.cfg, "book_memory_top_n", 5) or 5)
                        _, _rew_patterns = self.memory.pitfall_injection(
                            top_n=_rew_top_n, for_rewrite=True, self_ch=chapter_no)
                    except Exception:
                        _rew_patterns = []
                _rew_contract = contract_for("rewrite_whole", self.cfg, chapter_no)
                _recon_prompt = ((_persona + "\n\n") if _persona else "") + "\n" + prompt
                _reconcile_injection(
                    expected=list(_rew_patterns),
                    params=dict(expected_params(_rew_contract)),
                    prompt=_recon_prompt,
                    path_id="rewrite_whole",
                    chapter_no=chapter_no,
                    cfg=self.cfg,
                    store=self.store,
                    project_id=getattr(self.store, "project_id", "") or "",
                    inject_channel=CHANNEL_REWRITE,
                    trace=self.trace,
                )
            text = self.llm.chat(
                (_persona + "\n\n" if _persona else "")
                + "你是资深网络小说写手，擅长按质检意见重写章节。\n\n"
                + _output_discipline()
                + "\n重写后必须只输出【一个】完整连贯的正文版本，并以句末标点完整收尾。",
                prompt, temperature=0.8, max_tokens=8000,
            )
            return (text or "").strip()
        except Exception:
            return ""

    def _repetition_direction_block(self, chapter_no: int) -> str:
        """P2-1 正向替换供给：重复类问题命中重写时，注入「新方向候选」。

        从 store 读未兑现承诺（pending_commitments 上一章 missed/must_carry）、
        本章场景设计（chapter_design 的推进方向），组装「不要重复旧情节，
        往这些未兑现/未推进的方向写」块。零 LLM，失败静默 → 空串。
        """
        parts: list[str] = []
        try:
            _pc = self.store.load_pending_commitments()
            _prev = _pc.get(str(int(chapter_no) - 1)) or {}
            for _key, _label in (("missed", "未兑现承诺"), ("must_carry_to_next", "需带到下一章")):
                _vals = _prev.get(_key) or []
                _vals = [str(v) for v in _vals if str(v).strip()]
                if _vals:
                    parts.append(f"{_label}：{'、'.join(_vals[:3])}")
        except Exception:
            pass
        try:
            _cd = self.store.load_chapter_design()
            if isinstance(_cd, dict):
                for _ch in (_cd.get("chapters") or []):
                    if not isinstance(_ch, dict) or int(_ch.get("no") or 0) != int(chapter_no):
                        continue
                    _d = _ch.get("design") or {}
                    for _f in ("conflict", "goal"):
                        _v = _d.get(_f)
                        if _v:
                            parts.append(f"本章可推进方向：{_v}")
                            break
                    break
        except Exception:
            pass
        if not parts:
            return ""
        body = "\n".join(f"- {p}" for p in parts[:4])
        return ("【重写的新方向候选（不要重复旧情节，往这些未兑现/未推进的方向写）】\n"
                + body + "\n")

    def _clean_rewrite_block(self, chapter_no: int, issues: list[dict],
                             content: str, skip_original: bool) -> str:
        """重写 prompt 尾部注入块（REQ-门禁质检正向影响 P0-1/P0-2）。

        skip_original=False（普通质量问题）→ 原行为，注入【当前正文】作重写参照。
        skip_original=True（P0-1 合规红线）→ 不注入原稿，注入：
          - 整章全新重写声明（不得复用/复述原稿任何段落或措辞）；
          - 首次合规教育块（P0-2：compliance_education 开且本书该 rule_category
            首次命中才注入；重复命中只重写不重复教育，靠 book_memory 持久化）。
        """
        if not skip_original:
            return f"【当前正文】\n{content}"
        from ..compliance import compliance_categories, compliance_education_block
        cats = compliance_categories(issues)
        head = (
            "\n【本稿含违规内容·整章全新重写】本稿因命中合规红线被整体废弃。\n"
            "请基于【本章目标】与设定上下文全新写一章，剧情走向可保留核心冲突，"
            "但不得复用、复述、改写原稿任何段落、措辞或句式；违规类型见【质检问题】。\n"
        )
        edu = ""
        if getattr(self.cfg, "compliance_education", True) and cats:
            try:
                if self.memory is not None:
                    new_cats = self.memory.mark_compliance_educated(cats)
                    if new_cats:
                        edu = compliance_education_block(new_cats)
            except Exception:
                pass
        return head + (f"\n{edu}\n" if edu else "")

    def _rewrite_span(self, chapter_no: int, plan_title: str, plan_goal: str,
                      content: str, span: tuple[int, int], issues: list[dict],
                      baseline: dict | None = None) -> str:
        """只重写问题窗口：上下文 + 问题段 → LLM 输出替换段 → Python 拼回（零结构破坏）。"""
        try:
            s, e = span
            ctx_before = content[max(0, s - 200):s]
            ctx_after = content[e:min(len(content), e + 200)]
            problem_seg = content[s:e]
            if getattr(self.cfg, "diag_feedback", True):
                try:
                    from ..diagnosis import build_issue_block as _bib
                    issue_block = _bib(issues, max_n=5)
                except Exception:
                    issue_block = "\n".join(
                        f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:5]
                    )
            else:
                issue_block = "\n".join(
                    f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:5]
                )
            ctx = self._context_block(chapter_no)
            mem_block = self._memory_block(issues, chapter_no)
            # REQ-写作正向引导 5.3：权威当前值注入定点重写（数字违规段修复必须按权威值）
            _blk_text = ""
            try:
                from ..templates import _panel_baseline_block as _pbb
                if baseline:
                    _blk_text = _pbb("\n".join(f"- {k}：{v}" for k, v in baseline.items()))
            except Exception:
                _blk_text = ""
            prompt = (
                f"你是资深网文写手。以下是第{chapter_no}章《{plan_title}》的一个段落，"
                f"质检发现如下问题，请只重写这个段落（不要改动上下文，不要输出其他内容）。\n"
                f"【本章目标】{plan_goal}\n"
                + (f"\n{ctx}\n" if ctx else "")
                + (f"\n{mem_block}\n" if mem_block else "")
                + (f"\n{_blk_text}\n" if _blk_text else "")
                + f"【质检问题】\n{issue_block}\n\n"
                f"【段落前文（保持衔接，不要重复）】\n{ctx_before}\n\n"
                f"【需要重写的段落】\n{problem_seg}\n\n"
                f"【段落后文（保持衔接，不要提前写）】\n{ctx_after}\n\n"
                f"重写要求：\n"
                f"1. 保留剧情走向、人物设定与关键信息，不推翻前文既定事实\n"
                f"2. 针对性修复上述问题（去重复/修正矛盾/补足内容）；机制数字必须按机制卡随事件结算\n"
                f"3. 只输出替换后的这一个段落，不要解释，不要输出【摘要】\n"
                f"4. 以句末标点完整收尾"
            )
            # REQ-踩坑账本注入对账 - 第 3 步（P1 重写路径 span）：prompt 拼装完成、chat 之前
            if getattr(self.cfg, "pitfall_reconcile", True):
                _rew_patterns = []
                if self.memory is not None and getattr(self.cfg, "book_memory", True):
                    try:
                        _rew_top_n = int(getattr(self.cfg, "book_memory_top_n", 5) or 5)
                        _, _rew_patterns = self.memory.pitfall_injection(
                            top_n=_rew_top_n, for_rewrite=True, self_ch=chapter_no)
                    except Exception:
                        _rew_patterns = []
                _rew_contract = contract_for("rewrite_span", self.cfg, chapter_no)
                _recon_prompt = "你是资深网络小说写手，擅长按质检意见局部修改章节段落。\n" + prompt
                _reconcile_injection(
                    expected=list(_rew_patterns),
                    params=dict(expected_params(_rew_contract)),
                    prompt=_recon_prompt,
                    path_id="rewrite_span",
                    chapter_no=chapter_no,
                    cfg=self.cfg,
                    store=self.store,
                    project_id=getattr(self.store, "project_id", "") or "",
                    inject_channel=CHANNEL_REWRITE,
                    trace=self.trace,
                )
            text = (self.llm.chat(
                "你是资深网络小说写手，擅长按质检意见局部修改章节段落。",
                prompt, temperature=0.8, max_tokens=4000,
            ) or "").strip()
            if not text:
                return ""
            return content[:s] + text + content[e:]
        except Exception:
            return ""

    def _context_block(self, chapter_no: int, max_chars: int = 2500) -> str:
        """重写时的设定上下文（防"真空重写"）：世界观/机制卡/人物卡/章末账本。

        根因（《万墟之上》第1章单章重写实测）：_rewrite_whole/_rewrite_span 原
        只给【本章目标】+【质检问题】+【当前正文】，模型不知道机制结算规则
        （如"每死一次减 1"），重写后数字与矛盾修不动，复检仍是同样的 5 条。
        """
        parts: list[str] = []
        types: list[str] = []
        instance = ""
        try:
            world = self.store.load_world()
            if world:
                from ..mechanism_types import has_type, resolve_types
                types = resolve_types(world)
                wv = getattr(world, "world_view", None)
                if wv:
                    parts.append("【世界设定（正文必须遵守）】" + str(wv)[:800])
                mr = getattr(world, "mechanism_rules", None)
                if mr:
                    parts.append("【机制体系卡（正文必须遵守）】" + str(mr)[:1000])
                # 数字基线仅 numeric_panel 类型注入；规则型走条款清单块
                if has_type(types, "numeric_panel"):
                    mb = getattr(world, "mechanism_baseline", None)
                    if mb:
                        parts.append("【机制初始值/面板标准答案（正文数字的唯一来源）】" + str(mb)[:600])
                if has_type(types, "rule_set"):
                    st0 = self.store.load_chapter_state() or {}
                    instance = str(st0.get("current_instance") or "").strip()
                    from ..chapter_state import active_rules_for_instance
                    active = active_rules_for_instance(
                        getattr(world, "mechanism_ruleset", None) or [], instance)
                    if active:
                        rule_lines = [
                            f"[{getattr(r, 'rule_id', '')}] {getattr(r, 'statement', '')}"
                            for r in active if getattr(r, "statement", "")
                        ]
                        if rule_lines:
                            parts.append("【当前生效规则条款（不得改写/不得私增/不得串场）】"
                                         + "；".join(rule_lines)[:800])
                chars = getattr(world, "characters", None) or []
                if chars:
                    blk = "；".join(
                        f"{getattr(c, 'name', '')}（{getattr(c, 'role', '')}："
                        f"{(getattr(c, 'desc', '') or '')[:50]}）"
                        for c in chars[:8] if getattr(c, "name", None)
                    )
                    if blk:
                        parts.append("【人物卡（人物言行/外貌必须一致）】" + blk)
        except Exception:
            pass
        try:
            st = self.store.load_chapter_state()
            if st:
                st_ch = int(st.get("chapter") or 0)
                # 账本注入仅限「章号 < 当前章」的既有前文状态；账本章号 >= 当前章
                # 说明是本章旧稿/旧版本的账本，不得当"上章承接"喂给模型——
                # 否则重写第 1 章会拿到旧稿的「2/3」当标准答案，数字回跳修不动
                if st_ch and st_ch < chapter_no:
                    nums = st.get("numbers") or []
                    if nums:
                        parts.append("【上一章机制数字（本章必须承接结算，不得回退）】"
                                     + "；".join(str(x) for x in nums))
        except Exception:
            pass
        joined = "\n".join(parts)
        return joined[:max_chars]

    # ---------- 落库与工具 ----------
    def _save_review(self, chapter_no: int, issues: list[dict], result: dict,
                      auto_revised: bool = False) -> None:
        """与既有 review.json 格式兼容：该章记录合并校验问题。"""
        try:
            path = self.store.dir / "review.json"
            existing = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    existing = []
            existing = [r for r in existing if int(r.get("chapter", 0)) != chapter_no]
            existing.append({
                "chapter": chapter_no,
                "issues": issues,
                "auto_revised": bool(auto_revised),
                "quality_check": {
                    "passed": bool(result.get("passed")),
                    "score": int(result.get("score", 0)),
                    "hook_score": result.get("hook_score"),   # P0-5 追更欲（进度盘绿色分支）
                    "checked_at": time.time(),
                },
            })
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

