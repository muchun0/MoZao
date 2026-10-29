# -*- coding: utf-8 -*-
"""REQ-通用机制类型适配 · M1：类型清洗 / 兼容推断 / 规则条款 schema 往返 —— P1 用例。

覆盖：
1. normalize_types：枚举值、中文别名、顿号逗号串、去重、非法剔除、none 互斥
2. infer_types_from_world：旧书无显式类型时依据 baseline / ruleset / goldfinger 推断
3. resolve_types：显式声明优先，缺失走推断，旧 JSON 缺字段不报错
4. MechanismRule 四要素完整性、枚举字段兜底、World 序列化往返与旧数据兼容
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.mechanism_types import (
    MECHANISM_TYPES, has_type, infer_types_from_world, normalize_types, resolve_types,
)
from novel_gen.store.schemas import MechanismRule, World


@pytest.mark.p1
def test_normalize_enum_values_and_dedup():
    out = normalize_types(["rule_set", "rule_set", "ability", None, ""])
    assert out == ["rule_set", "ability"]


@pytest.mark.p1
def test_normalize_chinese_aliases_and_csv_string():
    out = normalize_types("规则怪谈， 能力； 等级")
    assert out == ["rule_set", "ability", "power_level"]


@pytest.mark.p1
def test_normalize_drops_unknown_and_none_is_exclusive():
    assert normalize_types(["飞天遁地"]) == []
    assert normalize_types(None) == []
    mixed = normalize_types(["none", "rule_set"])
    assert mixed == ["rule_set"]
    assert normalize_types("无机制") == ["none"]


@pytest.mark.p1
def test_has_type():
    types = ["numeric_panel", "rule_set"]
    assert has_type(types, "rule_set", "ability")
    assert not has_type(types, "contract", "temporal")


@pytest.mark.p1
def test_infer_numeric_panel_from_baseline():
    world = World.from_dict({
        "mechanism_baseline": {"panel_format": "旧痕 {scar}", "reset_rule": "副本结束重置"},
    })
    assert infer_types_from_world(world) == ["numeric_panel"]


@pytest.mark.p1
def test_infer_rule_set_from_ruleset():
    world = World.from_dict({
        "mechanism_ruleset": [{"rule_id": "r1", "scope": "global",
                               "statement": "禁止回头", "condition": "进入走廊",
                               "consequence": "迷失"}],
    })
    assert infer_types_from_world(world) == ["rule_set"]


@pytest.mark.p1
def test_infer_ability_from_goldfinger():
    world = World.from_dict({
        "goldfinger_rules": {"trigger": "接触物体", "upper_limit": "每日三次",
                             "growth_path": "升级读取深度"},
    })
    assert infer_types_from_world(world) == ["ability"]


@pytest.mark.p1
def test_infer_none_when_empty():
    world = World.from_dict({"world_view": "纯都市日常"})
    assert infer_types_from_world(world) == ["none"]


@pytest.mark.p1
def test_resolve_prefers_explicit_declaration():
    world = World.from_dict({
        "mechanism_types": ["rule_set"],
        "mechanism_baseline": {"panel_format": "x"},  # 显式声明优先，不被 baseline 覆盖
    })
    assert resolve_types(world) == ["rule_set"]


@pytest.mark.p1
def test_resolve_falls_back_to_inference():
    world = World.from_dict({
        "mechanism_ruleset": [{"rule_id": "r1", "scope": "instance:公寓",
                               "statement": "夜间不可开门", "condition": "22点后",
                               "consequence": "被拖走"}],
    })
    assert resolve_types(world) == ["rule_set"]


@pytest.mark.p1
def test_mechanism_rule_is_complete():
    full = MechanismRule(rule_id="r1", scope="global", statement="禁止开门",
                         condition="午夜后", consequence="门外存在进入房间")
    assert full.is_complete() is True
    assert MechanismRule(rule_id="r1").is_complete() is False


@pytest.mark.p1
def test_mechanism_rule_enum_fields_fallback():
    rule = MechanismRule.from_dict({
        "rule_id": "r1", "scope": "instance:公寓", "statement": "禁止开门",
        "condition": "午夜后", "consequence": "被替换",
        "change_policy": "随便谈", "status": "已破坏",
    })
    assert rule.change_policy == "fixed"   # 非法策略回退 fixed
    assert rule.status == "active"         # 非法状态回退 active
    assert rule.scope == "instance:公寓"


@pytest.mark.p1
def test_world_types_and_ruleset_roundtrip_old_json_compat():
    # 旧 JSON：无 mechanism_types / mechanism_ruleset 字段，解析不报错
    old = World.from_dict({"world_view": "旧书"})
    assert old.mechanism_types == []
    assert old.mechanism_ruleset == []

    world = World.from_dict({
        "mechanism_types": ["rule_set", "numeric_panel"],
        "mechanism_ruleset": [{
            "rule_id": "r1", "scope": "global", "statement": "禁止照镜子",
            "condition": "关灯后", "consequence": "看到不该看的",
            "change_policy": "fixed", "source": "首章墙上告示", "first_ch": 1,
            "status": "active",
        }],
    })
    # 往返一致
    world2 = World.from_dict(world.to_dict())
    assert world2.mechanism_types == ["rule_set", "numeric_panel"]
    assert len(world2.mechanism_ruleset) == 1
    rule = world2.mechanism_ruleset[0]
    assert rule.rule_id == "r1"
    assert rule.is_complete() is True
    assert rule.scope == "global"
    assert rule.first_ch == 1

    # 残留非 dict 条目被安全丢弃
    world3 = World.from_dict({"mechanism_ruleset": ["不是对象", {"rule_id": "ok"}]})
    assert len(world3.mechanism_ruleset) == 1
    assert world3.mechanism_ruleset[0].rule_id == "ok"


@pytest.mark.p1
def test_all_types_known_enum():
    # 防止新增类型忘记登记枚举 / 中文标签
    from novel_gen.pipeline.mechanism_types import TYPE_LABELS
    assert set(MECHANISM_TYPES) == set(TYPE_LABELS)
