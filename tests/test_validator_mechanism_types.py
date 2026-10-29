# -*- coding: utf-8 -*-
"""校验端类型分派测试（REQ-通用机制类型适配，M3）。

覆盖：
1. rule_set：条款四要素齐全 → 不报机制/金手指问题；不强制结算规则与代价终局；
2. rule_set：缺条款 / 条款要素不全 / 编号重复 / scope 非法 → 分级问题；
3. ability：trigger/upper_limit/growth_path 必填，lower_limit/cost 缺失仅 minor；
4. 非能力类型不要求 goldfinger_rules，也不出"缺金手指"提示；
5. none（显式无机制）跳过全部机制校验；
6. design_cards 的数字基线要求仅 numeric_panel 触发。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.issue_triage import has_fatal
from novel_gen.pipeline.validator import LogicValidator
from novel_gen.store.schemas import World


@pytest.fixture
def validator():
    return LogicValidator()


def _scopes(issues, scope: str) -> list:
    return [i for i in issues if i.scope == scope]


# ---------- rule_set（规则怪谈） ----------

@pytest.mark.p1
def test_rule_set_complete_ruleset_passes(validator):
    """规则约束型：条款齐全、机制总述有语义 → 无机制/规则/金手指致命问题。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：每个副本开局公布守则，副本内守则不可改写，违反即受惩罚。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "instance:便利店",
            "statement": "不得在 23:00 后进入冷藏区",
            "condition": "身处便利店副本期间",
            "consequence": "被冷藏区中的存在拖入冰柜",
            "change_policy": "fixed", "status": "active",
        }],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert not has_fatal(issues)
    assert _scopes(issues, "rule_set") == []
    assert _scopes(issues, "goldfinger") == []


@pytest.mark.p1
def test_rule_set_does_not_require_settlement_or_cost(validator):
    """规则约束型：机制总述不含结算/代价词 → 不报结算规则、代价终局缺失。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本守则约束行为边界，由副本环境强制执行。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "global",
            "statement": "不得回应陌生人的搭话",
            "condition": "进入副本后",
            "consequence": "被陌生人锁定为目标",
            "change_policy": "fixed", "status": "active",
        }],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    mech = _scopes(issues, "mechanism")
    assert mech == []


@pytest.mark.p1
def test_rule_set_empty_ruleset_is_fatal(validator):
    """声明 rule_set 但无结构化条款 → fatal。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本守则体系。",
        "mechanism_ruleset": [],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    rs = _scopes(issues, "rule_set")
    assert rs and rs[0].severity == "fatal"
    assert "mechanism_ruleset" in rs[0].desc


@pytest.mark.p1
def test_rule_set_incomplete_clause_is_fatal(validator):
    """条款缺 condition/consequence → fatal 并列出缺失字段。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本守则体系。",
        "mechanism_ruleset": [{
            "rule_id": "R2", "scope": "global",
            "statement": "不得照镜子", "condition": "", "consequence": "",
            "status": "active",
        }],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    rs = [i for i in _scopes(issues, "rule_set") if i.severity == "fatal"]
    assert rs and "condition" in rs[0].desc and "consequence" in rs[0].desc


@pytest.mark.p1
def test_rule_set_duplicate_rule_id_is_major(validator):
    """同一 rule_id 重复 → major。"""
    clause = {"scope": "global", "statement": "s", "condition": "c",
              "consequence": "x", "status": "active"}
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：守则体系。",
        "mechanism_ruleset": [
            {"rule_id": "R1", **clause}, {"rule_id": "R1", **clause},
        ],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    dup = [i for i in _scopes(issues, "rule_set") if i.severity == "major"]
    assert dup and "重复" in dup[0].desc


@pytest.mark.p1
def test_rule_set_bad_scope_normalized_to_global(validator):
    """非法 scope 在 schema 层兜底为 global，校验端不再报 scope 问题。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：守则体系。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "随便写的范围",
            "statement": "s", "condition": "c", "consequence": "x",
            "status": "active",
        }],
    })
    assert w.mechanism_ruleset[0].scope == "global"
    issues = validator.validate_stage("bottom_anchors", w)
    assert not any("scope" in i.desc for i in _scopes(issues, "rule_set"))


# ---------- ability（能力金手指） ----------

@pytest.mark.p1
def test_ability_required_fields_missing_is_fatal(validator):
    """ability：缺 growth_path → fatal；lower_limit/cost 缺失仅 minor。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["ability"],
        "mechanism_rules": "能力语义：主角能听见物品残留的声音。",
        "goldfinger_rules": {"trigger": "触碰物品", "upper_limit": "每日三次"},
    })
    issues = validator.validate_stage("bottom_anchors", w)
    gf_fatal = [i for i in _scopes(issues, "goldfinger") if i.severity == "fatal"]
    assert gf_fatal and "growth_path" in gf_fatal[0].desc
    gf_minor = [i for i in _scopes(issues, "goldfinger") if i.severity == "minor"]
    assert gf_minor and "lower_limit" in gf_minor[0].desc


@pytest.mark.p1
def test_ability_full_required_fields_no_fatal(validator):
    """ability：trigger/upper_limit/growth_path 齐全 → 无金手指致命。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["ability"],
        "mechanism_rules": "能力语义：主角能听见物品残留的声音。",
        "goldfinger_rules": {"trigger": "触碰物品", "upper_limit": "每日三次",
                             "growth_path": "随接触时长积累感知强度"},
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert not has_fatal(_scopes(issues, "goldfinger"))


@pytest.mark.p1
def test_non_ability_skips_goldfinger_check(validator):
    """rule_set 类型即使有机制总述、无 goldfinger_rules → 不出金手指问题。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本守则体系，约束行为。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "global", "statement": "s",
            "condition": "c", "consequence": "x", "status": "active",
        }],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert _scopes(issues, "goldfinger") == []


# ---------- none ----------

@pytest.mark.p1
def test_explicit_none_skips_mechanism_check(validator):
    """显式 none：即使 mechanism_rules 有文本，也不做机制完整性校验。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["none"],
        "mechanism_rules": "复活机制，无数字",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    assert _scopes(issues, "mechanism") == []
    assert _scopes(issues, "goldfinger") == []


# ---------- design_cards 类型门控 ----------

@pytest.mark.p1
def test_design_cards_baseline_required_only_for_numeric_panel(validator):
    """rule_set 类型机制总述含状态词，也不要求 mechanism_baseline。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本状态与守则体系。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "global", "statement": "s",
            "condition": "c", "consequence": "x", "status": "active",
        }],
    })
    issues = validator.validate_stage("bottom_anchors", w)
    dc = _scopes(issues, "design_cards")
    assert not any("mechanism_baseline" in i.desc for i in dc)


@pytest.mark.p1
def test_numeric_panel_missing_baseline_is_major(validator):
    """numeric_panel 缺数字基线 → design_cards major（存量行为保留）。"""
    w = World.from_dict({
        "world_view": "w",
        "mechanism_types": ["numeric_panel"],
        "mechanism_rules": "机制语义：剩余次数状态面板；结算规则：每死一次减一。",
    })
    issues = validator.validate_stage("bottom_anchors", w)
    dc = [i for i in _scopes(issues, "design_cards") if "mechanism_baseline" in i.desc]
    assert dc and dc[0].severity == "major"
