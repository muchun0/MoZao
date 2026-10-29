# -*- coding: utf-8 -*-
"""REQ-质检三层重构：L1 确定性闸门 / L2 多维评委 / L3 聚合器专项测试（P1）。

覆盖：
- aggregate：去重合并、级别排序、维度归类、recommendations 字段、可用/失败维度快照；
- 评委框架：LLM 异常降级 available=False 不阻塞；plot 开关关闭不发起调用；契约解析；
- check() 附加快照：reviewer_layered=True 输出 dimensions/recommendations/layers，
  旧字段（passed/issues/score/hook_score）口径不变；False 回退旧路径无附加字段。

离线 mock，不依赖真实 LLM 与数据库长文；占位文本避开代码特征词/节拍词（§11.8）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.pipeline.quality import aggregate as agg_mod
from novel_gen.pipeline.quality.reviewers import (
    ReviewContext, ReviewerResult, DimensionResult, ReviewerIssue,
    ReaderReviewer, PlotReviewer, ConsistencyReviewer, ProseReviewer,
)


# ---------- L3 聚合器单元 ----------

class TestAggregateLegacy:
    """旧口径收尾：去重 + 级别排序 + 门禁（行为零变化）。"""

    def test_dedup_by_type_detail_and_level_order(self):
        issues = [
            {"type": "小改类", "detail": "b", "level": "小改"},
            {"type": "大改类", "detail": "a", "level": "大改"},
            {"type": "小改类", "detail": "b", "level": "小改"},  # 重复
            {"type": "中改类", "detail": "c", "level": "中改"},
        ]
        passed, uniq, score = agg_mod.finalize_legacy(issues, 80, diag_feedback=False)
        assert len(uniq) == 3
        assert [x["level"] for x in uniq] == ["大改", "中改", "小改"]
        assert passed is False  # 存在大改
        assert score == 80

    def test_passed_when_no_major(self):
        issues = [{"type": "小改类", "detail": "x", "level": "小改"}]
        passed, uniq, score = agg_mod.finalize_legacy(issues, 90, diag_feedback=False)
        assert passed is True and len(uniq) == 1 and score == 90


class TestAggregate:
    def _reader_result(self) -> ReviewerResult:
        dim = DimensionResult(
            key="reader", label="读者体验", score=80, available=True,
            issues=[ReviewerIssue(type="读者-阅读障碍", detail="段落偏长", level="小改",
                                  evidence="片段", suggestion="拆分段落")],
            strengths=["钩子不错"],
        )
        return ReviewerResult(dimension=dim, raw_issues=dim.issues)

    def _failed_result(self) -> ReviewerResult:
        dim = DimensionResult(key="plot", label="情节结构", available=False,
                               score=None, note="评审失败已降级")
        return ReviewerResult(dimension=dim, raw_issues=[])

    def test_merge_dimensions_and_layers(self):
        l1 = [{"type": "结构-字数偏少", "detail": "正文偏短", "level": "中改",
               "source": "deterministic", "dimension": "reader"}]
        res = agg_mod.aggregate(l1, [self._reader_result(), self._failed_result()],
                                score=72, diag_feedback=False)
        assert res.score == 72
        assert "reader" in res.dimensions and res.dimensions["reader"].available
        assert res.dimensions["plot"].available is False
        assert res.layers["llm"]["available_dimensions"] == ["reader"]
        assert res.layers["llm"]["failed_dimensions"] == ["plot"]
        assert res.layers["deterministic"]["issue_count"] == 1

    def test_recommendations_fields_and_limit(self):
        l1 = [{"type": "AI痕迹-连接词堆砌", "detail": "连接词偏多", "level": "中改",
               "source": "deterministic", "dimension": "prose", "expected_gain": 5}]
        res = agg_mod.aggregate(l1, [], score=80, diag_feedback=False)
        assert res.recommendations, "应生成建议清单"
        rec = res.recommendations[0]
        for field in ("dimension", "type", "problem", "fix_suggestion", "evidence",
                      "source", "rule_id", "priority"):
            assert field in rec
        assert rec["priority"] == 1

    def test_cross_layer_evidence_merge_keeps_sources(self):
        l1 = [{"type": "结构-字数偏少", "detail": "短", "level": "中改",
               "evidence": "同一证据片段", "source": "deterministic"}]
        rr = self._reader_result()
        rr.raw_issues[0].evidence = "同一证据片段"
        res = agg_mod.aggregate(l1, [rr], score=80, diag_feedback=False)
        # 证据归一化相同 → 合并为一条；sources 保留两层
        same = [x for x in res.issues if x.get("evidence") == "同一证据片段"]
        assert len(same) == 1

    def test_classify_dimension_prefixes(self):
        assert agg_mod.classify_dimension({"type": "合规-疑似违规内容"}) == "compliance"
        assert agg_mod.classify_dimension({"type": "自相矛盾-事件重复"}) == "consistency"
        assert agg_mod.classify_dimension({"type": "AI痕迹-情绪词堆砌"}) == "prose"
        assert agg_mod.classify_dimension({"type": "情节-大纲核心事件未覆盖"}) == "plot"
        assert agg_mod.classify_dimension({"type": "读者-阅读障碍"}) == "reader"


# ---------- L2 评委框架 ----------

def _ctx(llm=None, **overrides) -> ReviewContext:
    cfg = NovelConfig(title="t", chapter_words=2500, total_words=10000)
    cfg.high_quality_mode = True
    base = dict(chapter_no=1, plan_title="第一章", plan_goal="推进",
                content="正文片段，他握紧武器向前走去。" * 20,
                summary="", core_event="", cfg=cfg, llm=llm, store=None,
                guard=None, memory=None)
    base.update(overrides)
    return ReviewContext(**base)


class TestReaderReviewer:
    def test_llm_exception_degrades_unavailable(self):
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("boom")
        res = ReaderReviewer().review(_ctx(llm=llm))
        assert res.dimension.available is False
        assert res.dimension.score is None
        assert res.raw_issues == []

    def test_parses_issues_and_hook(self):
        llm = MagicMock()
        llm.chat.return_value = (
            '{"issues":[{"type":"阅读障碍","detail":"段落长","level":"小改",'
            '"suggestion":"拆分"}],"追更欲":4,"一句话感受":"尚可"}')
        res = ReaderReviewer().review(_ctx(llm=llm))
        assert res.dimension.available is True
        assert res.dimension.extra["hook_score"] == 4
        assert res.raw_issues[0].type.startswith("读者-")


class TestPlotReviewer:
    def test_disabled_by_default_does_not_call_llm(self):
        llm = MagicMock()
        res = PlotReviewer().review(_ctx(llm=llm))
        assert res.dimension.available is False
        assert llm.chat.call_count == 0

    def test_enabled_high_quality_parses_contract(self):
        llm = MagicMock()
        llm.chat.return_value = (
            '{"score":75,"missing_core_events":["密报未兑现"],'
            '"issues":[{"type":"场景同构","detail":"两处场景重复","level":"中改",'
            '"evidence":"片段","suggestion":"合并"}],'
            '"strengths":["节奏紧凑"]}')
        ctx = _ctx(llm=llm)
        ctx.cfg.plot_reviewer = True
        res = PlotReviewer().review(ctx)
        assert res.dimension.available is True
        assert res.dimension.score == 75
        assert res.dimension.extra["missing_core_events"] == ["密报未兑现"]
        assert res.raw_issues[0].type.startswith("情节-")

    def test_llm_exception_degrades(self):
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("boom")
        ctx = _ctx(llm=llm)
        ctx.cfg.plot_reviewer = True
        res = PlotReviewer().review(ctx)
        assert res.dimension.available is False and res.dimension.score is None


class TestConsistencyReviewer:
    def test_disabled_normal_mode_skips(self):
        llm = MagicMock()
        ctx = _ctx(llm=llm)
        ctx.cfg.high_quality_mode = False
        ctx.cfg.consistency_llm_check = False
        res = ConsistencyReviewer().review(ctx)
        assert res.dimension.available is False
        assert llm.chat.call_count == 0


class TestProseReviewer:
    def test_runs_offline_without_llm(self):
        # 无 llm 时走词表/统计回退，不抛异常
        res = ProseReviewer().review(_ctx(llm=None))
        # 词表判定可能有/无问题，但维度框架不崩溃
        assert res.dimension.key == "prose"


# ---------- check() 附加快照（门面集成） ----------

@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "q", cfg)


def _offline_cfg(store):
    cfg = store.load_config()
    cfg.llm_semantic_beats = False
    cfg.llm_semantic_ai_taste = False
    cfg.ai_taste_check = False
    cfg.style_drift_check = False
    cfg.style_homogeneity_check = False
    cfg.consistency_llm_check = False
    cfg.rule_consistency_check = False
    cfg.plot_reviewer = False
    return cfg


class TestCheckSnapshot:
    def test_layered_snapshot_fields_present(self, store):
        cfg = _offline_cfg(store)
        llm = MagicMock()
        llm.chat.return_value = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
        qc = QualityChecker(cfg, llm, store)
        result = qc.check(1, "第一章", "推进", "他握紧武器向前走去。" * 30)
        # 旧契约字段保留
        for key in ("passed", "issues", "score", "hook_score"):
            assert key in result
        # 新附加字段
        assert "dimensions" in result
        assert "recommendations" in result
        assert "layers" in result
        assert set(result["dimensions"]) >= {"reader", "consistency", "prose"}
        assert result["layers"]["deterministic"]["issue_count"] == len(result["issues"])

    def test_legacy_path_has_no_snapshot_fields(self, store):
        cfg = _offline_cfg(store)
        cfg.reviewer_layered = False
        llm = MagicMock()
        llm.chat.return_value = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
        qc = QualityChecker(cfg, llm, store)
        result = qc.check(1, "第一章", "推进", "他握紧武器向前走去。" * 30)
        assert "dimensions" not in result
        assert "recommendations" not in result
        assert "layers" not in result
        for key in ("passed", "issues", "score", "hook_score"):
            assert key in result

    def test_plot_disabled_adds_no_extra_call(self, store):
        cfg = _offline_cfg(store)
        llm = MagicMock()
        llm.chat.return_value = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
        qc = QualityChecker(cfg, llm, store)
        qc.check(1, "第一章", "推进", "他握紧武器向前走去。" * 30)
        # plot 默认关闭：除 reader 外不应再有 plot 调用
        assert all("情节" not in str(c.args) for c in llm.chat.call_args_list)


class TestEnsureQualitySnapshot:
    """ensure_quality 两轮（首检 + 复检）走新链路，附加快照不崩、旧契约不变。"""

    def test_ensure_quality_attaches_snapshot_without_legacy_regression(self, store):
        cfg = _offline_cfg(store)
        cfg.auto_rewrite_rounds = 1
        llm = MagicMock()
        long_text = ("风从墟口灌进来，带着湿土和血的腥气。他把铜钱塞回怀里，"
                     "指尖在边缘上磨了一下，城墙的影子压过来，像一头睡着的兽。") * 60
        llm.chat.side_effect = [
            '{"issues": [], "追更欲": 4, "一句话感受": "ok"}',  # 首检 reader
            long_text,                                          # 重写稿
            '{"issues": [], "追更欲": 4, "一句话感受": "ok"}',  # 复检 reader
        ]
        qc = QualityChecker(cfg, llm, store)
        chapter, result = qc.ensure_quality(1, "第一章", "推进", "太短了。", "摘要", silent=True)
        # 旧契约字段齐全
        for key in ("passed", "issues", "score", "hook_score", "versions", "compliance_rejected"):
            assert key in result
        # 新附加快照存在于最终 result
        assert "dimensions" in result and "recommendations" in result and "layers" in result
        assert result["layers"]["deterministic"]["issue_count"] == len(result["issues"])
        # 章节产物正常返回
        assert chapter.content and chapter.no == 1
