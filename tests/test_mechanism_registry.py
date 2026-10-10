# -*- coding: utf-8 -*-
"""P0-15/16/17/18 通用版测试（2026-10-10）。

覆盖：
- mechanism_field_registry：机制字段注册表（closed-world，从基线派生）
- unknown_mechanism_fields：注册表外字段违规（累计死亡次数类幻觉行）
- ledger_extract_numbers：注册字段末值抽取（含寸数字段）
- ledger_check：链式账本 + 事件驱动双向校验（无事件不变/回退、次数 0 边界、死亡递增）
- _core_event_missing / _validate_scene_plan core_event：大纲核心事件覆盖（60% 阈值）
- _summary_invented_words：摘要与正文脱节判定（2-gram 命中率）
"""
import pytest

from novel_gen.pipeline import chapter_state as cs
from novel_gen.pipeline import stages
from novel_gen.pipeline.quality import _core_event_missing

BASELINE = {
    "panel_format": "今日可死次数：3；距离蚀化：97%",
    "reset_rule": "每日子时重置今日可死次数为3；距离蚀化永久累计不重置",
    "scar_current": "已延伸三十七寸（从手腕到肘弯，距心脏约一寸）",
}


@pytest.fixture()
def registry():
    return cs.mechanism_field_registry(BASELINE)


class TestMechanismFieldRegistry:
    def test_fields_derived_from_baseline(self, registry):
        # 基线声明了哪些面板字段，注册表就含哪些（closed-world）
        assert "可死次数" in registry
        assert "蚀化" in registry
        assert "寸" in registry

    def test_direction_rules(self, registry):
        assert registry["可死次数"] == "decrease"
        assert registry["蚀化"] == "increase"
        assert registry["寸"] == "increase"

    def test_empty_baseline_no_registry(self):
        assert cs.mechanism_field_registry(None) == {}
        assert cs.mechanism_field_registry({}) == {}

    def test_field_core_strips_stopwords(self):
        assert cs._field_core("今日可死次数") == "可死次数"
        assert cs._field_core("距离蚀化") == "蚀化"
        assert cs._field_core("累计死亡次数") == "死亡次数"


class TestUnknownMechanismFields:
    def test_hallucinated_field_flagged(self, registry):
        # v9 ch3/ch6「累计死亡次数：39/37」幻觉行 → 注册表外字段违规
        out = cs.unknown_mechanism_fields("面板亮起：累计死亡次数：39，今日可死次数：2", registry)
        assert any("累计死亡次数" in s for s in out)

    def test_registered_fields_pass(self, registry):
        out = cs.unknown_mechanism_fields("面板亮起：今日可死次数：2；距离蚀化：98%", registry)
        assert out == []

    def test_plain_numbers_not_flagged(self, registry):
        out = cs.unknown_mechanism_fields("三只灰卒从墟口扑来，走出七步", registry)
        assert out == []

    def test_empty_registry_disables(self):
        assert cs.unknown_mechanism_fields("累计死亡次数：39", {}) == []


class TestLedgerExtractNumbers:
    def test_panel_last_values(self, registry):
        out = cs.ledger_extract_numbers(
            "面板：今日可死次数：3；距离蚀化：97%。今日可死次数：2；距离蚀化：98%", registry)
        assert out["可死次数"] == 2   # 末值
        assert out["蚀化"] == 98

    def test_inch_field(self, registry):
        out = cs.ledger_extract_numbers("蚀痕已延伸四十一寸，距心脏不到一寸", registry)
        assert out["寸"] == 41

    def test_cn_number(self):
        assert cs._cn_to_int("三十七") == 37
        assert cs._cn_to_int("四十一") == 41
        assert cs._cn_to_int("100") == 100

    def test_absent_fields_not_recorded(self, registry):
        out = cs.ledger_extract_numbers("只有正文没有面板", registry)
        assert out == {}


class TestLedgerCheck:
    def test_no_event_decrease_must_unchanged(self, registry):
        # ch2 次数 3→2 无死亡依据递减 → 违规
        out = cs.ledger_check("面板亮起：今日可死次数：2", {"可死次数": 3}, registry, 0)
        assert out

    def test_no_event_increase_no_regression(self, registry):
        # ch5 蚀痕 41→37 无事件回退 → 违规
        out = cs.ledger_check("蚀痕已延伸三十七寸", {"寸": 41}, registry, 0)
        assert any("寸" in s and "回退" in s for s in out)

    def test_no_event_increase_allow_legal_growth(self, registry):
        # 无事件但寸数合法正向增长（碎片/外力）→ 不违规
        out = cs.ledger_check("蚀痕已延伸三十九寸", {"寸": 38}, registry, 0)
        assert out == []

    def test_death_zero_boundary(self, registry):
        # ch4 次数 0 后仍死亡 → 边界违规
        out = cs.ledger_check("面板亮起：今日可死次数：0，他死了又活", {"可死次数": 0}, registry, 1)
        assert out

    def test_death_decrease_and_increase_ok(self, registry):
        out = cs.ledger_check(
            "面板亮起：今日可死次数：2；距离蚀化：98%", {"可死次数": 3, "蚀化": 97}, registry, 1)
        assert out == []

    def test_death_increase_not_enough(self, registry):
        # 死亡 1 次但蚀化未 +1 → 违规
        out = cs.ledger_check("面板亮起：距离蚀化：97%", {"蚀化": 97}, registry, 1)
        assert any("蚀化" in s for s in out)

    def test_prev_fallback_to_initial(self, registry):
        # prev 缺失 → 用基线初始值（可死次数 3、蚀化 97、寸 37）
        out = cs.ledger_check("面板亮起：今日可死次数：2", {}, registry, 0,
                              initial={"可死次数": 3, "蚀化": 97, "寸": 37})
        assert out


class TestCoreEventCoverage:
    def test_missing_core_event_flagged(self):
        # v9 ch6：大纲「忘忧阁阁主密报」未进正文（2 段仅 1 段命中=50% <60%）
        out = _core_event_missing(
            "陆沉走进不问阁换情报，林半夏跟在后面",
            "林半夏向忘忧阁阁主密报→阁主下令钥匙已现勿使其折")
        assert any("阁主下令" in s for s in out)

    def test_full_coverage_pass(self):
        out = _core_event_missing(
            "林半夏向忘忧阁阁主密报，阁主下令钥匙已现勿使其折",
            "林半夏向忘忧阁阁主密报→阁主下令钥匙已现勿使其折")
        assert out == []

    def test_66_percent_pass(self):
        out = _core_event_missing(
            "队伍跟随临时队下墟，遭遇灰卒袭击，陆沉死亡复活",
            "跟随临时队下墟→遭遇灰卒袭击→死而复生")
        assert out == []

    def test_scene_plan_core_event_gate(self):
        # 拆场景未覆盖大纲核心事件 → 回退整章
        ok, why = stages._validate_scene_plan([
            {"event": "陆沉去不问阁换情报", "target_words": 1400},
            {"event": "林半夏暗中跟随确认", "target_words": 1100},
        ], 2500, core_event="林半夏向忘忧阁阁主密报→阁主下令钥匙已现勿使其折")
        assert not ok
        assert "大纲核心事件" in why

    def test_scene_plan_core_event_pass(self):
        ok, why = stages._validate_scene_plan([
            {"event": "陆沉混入队伍下墟，遭灰卒袭击", "target_words": 1200},
            {"event": "陆沉死亡后复活，击杀灰卒", "target_words": 1300},
        ], 2500, core_event="跟随临时队下墟→遭遇灰卒袭击→死而复生")
        assert ok

    def test_scene_plan_no_core_event_unchanged(self):
        ok, why = stages._validate_scene_plan([
            {"event": "a事件", "target_words": 1200},
        ], 1200)
        assert ok


class TestSummaryConsistency:
    def test_summary_diverged_low_hit(self):
        # 摘要含正文完全没有的大纲残留（忘忧阁）→ 命中率低
        rate = stages._summary_invented_words(
            "林半夏向忘忧阁阁主密报，阁主下令钥匙已现勿使其折",
            "陆沉去不问阁换得情报，林半夏暗中跟随")
        assert rate < 0.35

    def test_summary_aligned_high_hit(self):
        rate = stages._summary_invented_words(
            "陆沉去不问阁换得情报，林半夏暗中跟随确认",
            "陆沉走进不问阁换情报，林半夏暗中跟在后面，确认贺枭去向")
        assert rate >= 0.35

    def test_empty_summary_safe(self):
        assert stages._summary_invented_words("", "正文") == 1.0
        assert stages._summary_invented_words("摘要", "") == 1.0
