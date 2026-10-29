# -*- coding: utf-8 -*-
"""记忆注入按机制类型分派测试（REQ-通用机制类型适配，M2/M5）。

覆盖：
1. rule_set 书 → 注入规则条款卡，不注入能力规则说明书/机制初始值；
2. ability 书 → 注入能力规则说明书，不注入规则条款卡；
3. none/纯剧情书 → 三类机制卡块均不出现；
4. numeric_panel 书 → 注入机制初始值，不注入规则条款卡/能力说明书。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import World
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "mri", cfg)


def _ctx(store, world: World) -> str:
    store.save_world(world)
    return Memory(store, 5).build_context(world, up_to_chapter=1, chapter_goal="推进")


@pytest.mark.p1
def test_rule_set_injects_rule_cards_only(store):
    world = World.from_dict({
        "world_view": "深夜便利店规则怪谈。",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "副本规则约束主角行为边界。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "instance:便利店",
            "statement": "不得回应陌生顾客的搭话",
            "condition": "在便利店内", "consequence": "被顾客锁定",
            "change_policy": "fixed", "status": "active",
        }, {
            "rule_id": "R2", "scope": "global",
            "statement": "夜间不得离开公寓",
            "condition": "入夜后", "consequence": "违规即失败",
            "change_policy": "fixed", "status": "active",
        }],
    })
    ctx = _ctx(store, world)
    assert "规则条款卡" in ctx
    assert "不得回应陌生顾客的搭话" in ctx
    assert "夜间不得离开公寓" in ctx
    assert "能力规则说明书" not in ctx
    assert "机制初始值" not in ctx


@pytest.mark.p1
def test_ability_injects_ability_cards_only(store):
    world = World.from_dict({
        "world_view": "都市异能世界。",
        "mechanism_types": ["ability"],
        "mechanism_rules": "主角能听见物品残留的声音，边界清晰。",
        "goldfinger_rules": {
            "trigger": "手触碰物品", "upper_limit": "一次只能听一件物品",
            "growth_path": "逐步扩大可感知时长",
        },
    })
    ctx = _ctx(store, world)
    assert "能力规则说明书" in ctx
    assert "手触碰物品" in ctx
    assert "规则条款卡" not in ctx
    assert "机制初始值" not in ctx


@pytest.mark.p1
def test_none_book_injects_no_mechanism_cards(store):
    world = World.from_dict({
        "world_view": "纯剧情校园故事，无超自然机制。",
        "mechanism_types": ["none"],
    })
    ctx = _ctx(store, world)
    assert "规则条款卡" not in ctx
    assert "能力规则说明书" not in ctx
    assert "机制初始值" not in ctx


@pytest.mark.p1
def test_numeric_panel_injects_baseline_only(store):
    world = World.from_dict({
        "world_view": "数字面板生存世界。",
        "mechanism_types": ["numeric_panel"],
        "mechanism_rules": "剩余次数随结算递减，子时重置。",
        "mechanism_baseline": {
            "scar_start": "开局剩余三次",
            "scar_current": "剩余次数：3",
            "panel_format": "剩余次数：N/3",
            "reset_rule": "每日子时重置为 3",
        },
    })
    ctx = _ctx(store, world)
    assert "机制初始值" in ctx
    assert "剩余次数：N/3" in ctx
    assert "规则条款卡" not in ctx
    assert "能力规则说明书" not in ctx
