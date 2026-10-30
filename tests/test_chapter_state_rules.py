# -*- coding: utf-8 -*-
"""规则约束型状态账本测试（REQ-通用机制类型适配，M2/M5）。

覆盖：
1. active_rules_for_instance 作用域过滤（global/character/ability 常驻，instance 按副本，
   非 active 排除，空实例只留 global）；
2. _carry_rule_state 跨章累积：生效条款并集去重、规则事件追加去重、当前副本旧值兜底；
3. extract_chapter_state LLM 抽取规则槽位并承接旧状态；确定性降级路径同样承接；
4. format_chapter_state 输出当前副本与生效规则编号。
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
from novel_gen.store.schemas import Chapter, MechanismRule, World
from novel_gen.pipeline.chapter_state import (
    active_rules_for_instance,
    extract_chapter_state,
    format_chapter_state,
)


def _rule(rule_id: str, scope: str, status: str = "active") -> MechanismRule:
    return MechanismRule(rule_id=rule_id, scope=scope, statement=f"条款 {rule_id}",
                         condition="适用条件", consequence="违反后果", status=status)


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="规则怪谈测试", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "csr", cfg)


# ---------- active_rules_for_instance ----------

@pytest.mark.p1
def test_active_rules_scope_filtering():
    rules = [
        _rule("R1", "global"),
        _rule("R2", "character:林夏"),
        _rule("R3", "ability:读心"),
        _rule("R4", "instance:便利店"),
        _rule("R5", "instance:学校"),
        _rule("R6", "global", status="broken"),
    ]
    active = active_rules_for_instance(rules, "便利店")
    ids = [r.rule_id for r in active]
    assert ids == ["R1", "R2", "R3", "R4"]
    assert "R5" not in ids and "R6" not in ids


@pytest.mark.p1
def test_active_rules_empty_instance_only_global_and_persistent():
    rules = [_rule("R1", "global"), _rule("R2", "character:林夏"),
             _rule("R3", "instance:便利店")]
    active = active_rules_for_instance(rules, "")
    ids = [r.rule_id for r in active]
    assert ids == ["R1", "R2"]
    assert "R3" not in ids


# ---------- 跨章规则状态承接 ----------

@pytest.mark.p1
def test_carry_rule_state_union_and_event_dedup():
    old = {
        "chapter": 1,
        "current_instance": "便利店",
        "rules_active": ["R1", "R2"],
        "rule_events": [{"rule_id": "R1", "action": "broken",
                         "instance": "便利店", "detail": "违反禁语"}],
    }
    new = {"chapter": 2, "rules_active": ["R2", "R3"],
           "rule_events": [
               {"rule_id": "R1", "action": "broken", "instance": "便利店",
                "detail": "违反禁语"},  # 与旧事件重复 → 去重
               {"rule_id": "R3", "action": "discovered", "instance": "便利店",
                "detail": "纸条新公布"}],
           "numbers": ["剩余次数：2"]}
    from novel_gen.pipeline.chapter_state import _carry_rule_state
    _carry_rule_state(new, old)
    assert new["current_instance"] == "便利店"          # 旧副本兜底
    assert new["rules_active"] == ["R1", "R2", "R3"]    # 并集保序去重
    assert len(new["rule_events"]) == 2
    assert new["numbers"] == ["剩余次数：2"]             # 数字按章覆盖（既有行为不变）


@pytest.mark.p1
def test_extract_chapter_state_carries_old_rule_state(store):
    """LLM 抽取本章规则槽位；旧章生效条款与事件跨章承接。"""
    world = World.from_dict({
        "world_view": "规则怪谈", "mechanism_types": ["rule_set"],
        "mechanism_rules": "副本守则约束行为。",
    })
    store.save_world(world)
    old_state = {
        "chapter": 0, "current_instance": "便利店",
        "rules_active": ["R1"],
        "rule_events": [{"rule_id": "R1", "action": "broken",
                         "instance": "便利店", "detail": "违反禁语"}],
    }
    ch = Chapter(no=1, title="第一章", content="便利店内规则继续生效。" * 20, summary="s")
    llm = MagicMock()
    llm.chat_json.return_value = {
        "current_instance": "便利店",
        "rules_active": ["R1", "R2"],
        "rule_events": [{"rule_id": "R2", "action": "discovered",
                         "instance": "便利店", "detail": "纸条公布新规则"}],
    }
    st = extract_chapter_state(llm, store.load_config(), world, ch, 1, old_state)
    assert st["chapter"] == 1
    assert st["rules_active"] == ["R1", "R2"]
    assert len(st["rule_events"]) == 2
    assert st["current_instance"] == "便利店"


@pytest.mark.p1
def test_extract_chapter_state_deterministic_fallback_carries_rules(store):
    """LLM 失败降级确定性路径：旧规则状态仍被承接，不随降级丢失。"""
    world = World.from_dict({
        "world_view": "规则怪谈", "mechanism_types": ["rule_set"],
        "mechanism_rules": "副本守则约束行为。",
    })
    store.save_world(world)
    old_state = {"chapter": 1, "current_instance": "学校", "rules_active": ["R9"]}
    ch = Chapter(no=2, title="第二章", content="教室里的规则。" * 20, summary="s")
    llm = MagicMock()
    llm.chat_json.side_effect = RuntimeError("provider down")
    st = extract_chapter_state(llm, store.load_config(), world, ch, 2, old_state)
    assert st is not None
    assert st["current_instance"] == "学校"
    assert st["rules_active"] == ["R9"]


@pytest.mark.p1
def test_format_chapter_state_renders_instance_and_rules():
    text = format_chapter_state({
        "chapter": 3, "current_instance": "旧城区公寓",
        "rules_active": ["R1", "R3"],
    })
    assert "当前副本/场景：旧城区公寓" in text
    assert "当前生效规则编号：R1、R3" in text
