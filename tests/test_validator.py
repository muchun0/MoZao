# -*- coding: utf-8 -*-
"""逻辑校验 Agent（LogicValidator）5 类专项校验 —— P1 用例。

覆盖（REQ-多Agent协作分层优化，阶段B）：
1. 设定一致性：金手指机制卡存在但五要素缺失 → 致命
2. 伏笔完整性：只埋不收 → 致命；回收早于埋点 → 致命；level 非法 → 一般
3. 主题锚点：主题命题/三层冲突/弧光/配角动机 缺失 → 严重/一般
4. 主线偏移：核心冲突/主线缺失 → 致命；分卷缺主要矛盾 → 严重
5. 重复情节：章节标题重复 → 严重；goal 同质化超阈值 → 严重
6. 分卷归属：未归属章节超阈值 → 严重
7. 问题分级工具：triage / has_fatal / sort_issues
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.issue_triage import (has_fatal, sort_issues, triage)
from novel_gen.pipeline.validator import LogicValidator
from novel_gen.store.schemas import Outline, World


@pytest.fixture
def validator():
    return LogicValidator()


@pytest.mark.p1
def test_goldfinger_missing_with_mechanism_is_minor(validator):
    """机制卡存在但缺金手指说明书 → 仅提示（机制可能非金手指型，不强造金手指）。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_rules": "机制语义：复活机制，能力定义与边界；结算规则：每死一次减一",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert not has_fatal(issues)
    assert any(i.scope == "goldfinger" and i.severity == "minor" for i in issues)


@pytest.mark.p1
def test_goldfinger_partial_missing_is_fatal(validator):
    """金手指存在但缺 下限/成长路径 等必填要素 → 致命；cost 可按能力类型缺省。"""
    w = World.from_dict({"goldfinger_rules": {"trigger": "濒死", "upper_limit": "u"}})
    issues = validator.validate_stage("bottom_anchors", w)
    assert has_fatal(issues)
    fatal = [i for i in issues if i.severity == "fatal"]
    assert any("缺失" in i.desc for i in fatal)
    assert any("lower_limit" in i.desc or "growth_path" in i.desc for i in fatal)


@pytest.mark.p1
def test_goldfinger_missing_cost_only_not_fatal(validator):
    """金手指五要素仅缺 cost（无代价能力）→ 不致命。"""
    w = World.from_dict({"goldfinger_rules": {
        "trigger": "濒死", "upper_limit": "u", "lower_limit": "l", "growth_path": "g"}})
    issues = validator.validate_stage("bottom_anchors", w)
    assert not has_fatal(issues)


@pytest.mark.p1
def test_foreshadow_buried_only_is_fatal(validator):
    w = World.from_dict({"foreshadowing": [{"name": "谜", "buried_ch": 3, "reveal_ch": None}]})
    issues = validator.validate_stage("bottom_anchors", w)
    assert has_fatal(issues)
    assert any(i.scope == "foreshadow" and "只埋不收" in i.desc for i in issues)


@pytest.mark.p1
def test_foreshadow_reveal_before_buried_is_fatal(validator):
    w = World.from_dict({"foreshadowing": [{"name": "谜", "buried_ch": 10, "reveal_ch": 3}]})
    issues = validator.validate_stage("bottom_anchors", w)
    assert has_fatal(issues)
    assert any(i.scope == "foreshadow" and "早于" in i.desc for i in issues)


@pytest.mark.p1
def test_foreshadow_illegal_level_is_minor(validator):
    w = World.from_dict({"foreshadowing": [{"name": "谜", "buried_ch": 1, "reveal_ch": 5, "level": "xx"}]})
    issues = validator.validate_stage("bottom_anchors", w)
    assert any(i.scope == "foreshadow" and i.severity == "minor" for i in issues)


@pytest.mark.p1
def test_theme_anchor_missing_is_major(validator):
    w = World.from_dict({"world_view": "w"})
    issues = validator.validate_stage("bottom_anchors", w)
    assert any(i.scope == "theme" and i.severity == "major" for i in issues)


@pytest.mark.p1
def test_mainline_missing_conflict_is_fatal(validator):
    o = Outline.from_dict({"volumes": [{"no": 1, "name": "起", "start_ch": 1, "end_ch": 10}]})
    issues = validator.validate_stage("skeleton", o)
    assert has_fatal(issues)
    assert any(i.scope == "mainline" and i.severity == "fatal" for i in issues)


@pytest.mark.p1
def test_duplicate_chapter_titles_is_major(validator):
    o = Outline.from_dict({
        "core_conflict": "c", "main_line": "m",
        "volumes": [{"no": 1, "name": "起", "start_ch": 1, "end_ch": 3, "main_conflict": "c"}],
        "chapters": [{"no": 1, "title": "第一章", "goal": "a", "volume_no": 1},
                     {"no": 2, "title": "第一章", "goal": "b", "volume_no": 1},
                     {"no": 3, "title": "第三章", "goal": "c", "volume_no": 1}],
    })
    issues = validator.validate_stage("skeleton", o)
    assert any(i.scope == "duplicate" and "标题重复" in i.desc for i in issues)


@pytest.mark.p1
def test_volume_unassigned_over_threshold_is_major(validator):
    o = Outline.from_dict({
        "core_conflict": "c", "main_line": "m",
        "volumes": [{"no": 1, "name": "起", "start_ch": 1, "end_ch": 3, "main_conflict": "c"}],
        "chapters": [{"no": 1, "goal": "a", "volume_no": 0},
                     {"no": 2, "goal": "b", "volume_no": 0},
                     {"no": 3, "goal": "c", "volume_no": 0}],
    })
    issues = validator.validate_stage("skeleton", o)
    assert any(i.scope == "volume" and i.severity == "major" for i in issues)


@pytest.mark.p1
def test_pacing_missing_volume_pacing_is_minor(validator):
    o = Outline.from_dict({"core_conflict": "c", "main_line": "m",
                           "volumes": [{"no": 1, "name": "起", "start_ch": 1, "end_ch": 5, "main_conflict": "c"}],
                           "chapters": [{"no": 1}]})
    issues = validator.validate_stage("pacing", o)
    assert any(i.scope == "pacing" and i.severity == "minor" for i in issues)


@pytest.mark.p1
def test_triage_and_sort():
    from novel_gen.pipeline.issue_triage import IssueItem
    a = IssueItem("minor", "x", "小")
    b = IssueItem("fatal", "x", "致命")
    c = IssueItem("major", "x", "重")
    tri = triage([a, b, c])
    assert tri["fatal"] == [b] and tri["major"] == [c] and tri["minor"] == [a]
    assert has_fatal([a, b]) and not has_fatal([a, c])
    assert sort_issues([a, b, c])[0] is b


@pytest.mark.p1
def test_validator_never_raises_on_bad_input(validator):
    # 校验异常自身不阻塞：传入非法对象返回 []（或仅结构项），不抛错
    issues = validator.validate_stage("bottom_anchors", object())
    assert isinstance(issues, list)
    issues = validator.validate_stage("unknown_stage", World())
    assert issues == []


# ---------- 机制卡三要素校验（REQ-章内自洽与机制账本，P1） ----------


@pytest.mark.p1
def test_mechanism_complete_passes(validator):
    """机制卡覆盖 语义/结算/代价终局 → 无机制类问题。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_rules": "机制语义：可死次数=今日剩余复活次数；"
                           "结算规则：每死一次减1，每日子时重置为3；"
                           "代价终局：次数用尽即死，蚀痕随累计死亡递增",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert not any(i.scope == "mechanism" for i in issues)


@pytest.mark.p1
def test_mechanism_missing_semantics_fatal(validator):
    """只写了名目、没有机制语义 → 致命。"""
    w = World.from_dict({"world_view": "w", "mechanism_rules": "复活机制，无数字"})
    issues = validator.validate_stage("bottom_anchors", w)
    mech = [i for i in issues if i.scope == "mechanism"]
    assert mech and mech[0].severity == "fatal"
    assert "机制语义" in mech[0].desc


@pytest.mark.p1
def test_mechanism_missing_settlement_fatal(validator):
    """有语义与代价但没有结算规则 → 致命（数字怎么变没交代）。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_rules": "机制语义：可死次数=今日剩余复活次数；代价终局：用尽即死",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    mech = [i for i in issues if i.scope == "mechanism"]
    assert mech and mech[0].severity == "fatal"
    assert "结算规则" in mech[0].desc


@pytest.mark.p1
def test_mechanism_no_cost_declared_passes(validator):
    """有语义与结算但未声明代价 → 不报机制问题（无代价机制合法）。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_rules": "机制语义：可死次数=今日剩余复活次数；结算规则：每死一次减1",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert not any(i.scope == "mechanism" for i in issues)


@pytest.mark.p1
def test_mechanism_declared_cost_missing_end_fatal(validator):
    """机制显式声明代价但缺终局/上限 → 致命。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_rules": "机制语义：可死次数=今日剩余复活次数；结算规则：每死一次减1；"
                           "代价：每次使用消耗寿命",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    mech = [i for i in issues if i.scope == "mechanism"]
    assert mech and mech[0].severity == "fatal"
    assert "代价与终局" in mech[0].desc


@pytest.mark.p1
def test_mechanism_concept_only_requires_semantics(validator):
    """纯概念型机制（无数字/状态/代价）→ 仅要求机制语义，不强制结算与代价。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_rules": "机制语义：他能听见死者未说完的话，只能听见死前最后一句话",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert not any(i.scope == "mechanism" for i in issues)


@pytest.mark.p1
def test_mechanism_empty_skipped(validator):
    """无金手指（空机制卡）→ 不报机制类问题（金手指五要素缺失另由 goldfinger 查）。"""
    w = World.from_dict({"world_view": "w"})
    issues = validator.validate_stage("bottom_anchors", w)
    assert not any(i.scope == "mechanism" for i in issues)
