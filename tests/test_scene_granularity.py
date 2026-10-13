# -*- coding: utf-8 -*-
"""场景粒度优化测试（2026-10-13）：场景数由章节字数反推 + 单场景字数下限校验。

背景：用户实测《万墟之上》第1章（2500 字/章）被拆成 4 场景×~625 字，场景过小导致
无锚生成时模型靠"死亡-复活-面板循环注水"触发节拍重演拦截。调研结论：行业标准
单场景 800-2500 字、一章 1-3 个相关场景；2500 字/章应按字数反推 2 场景×1250 字。

覆盖：
- _scene_count_for_words：字数 → 目标场景数（2500→2 / 3000→3 / 4200→4 / 旧调用→4）
- _derive_events_from_moves(chapter_words=)：真实 ch1 5 拍@2500 → 2 事件（死亡-复活同事件）
- 旧行为兼容：不传 chapter_words → 与改造前一致（5 拍→4 事件）
- _validate_scene_plan 单场景 target_words 下限：4×625@2500（ch1 旧形态）→ 不合格；
  2×1250@2500 → 通过
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.stages import (
    _derive_events_from_moves,
    _scene_count_for_words,
    _validate_scene_plan,
)

# 真实《万墟之上》第1章 character_moves（已恢复的大纲原文，5 拍）
_CH1_MOVES = (
    "陆沉清晨出门前检查面板（3次），在墟口拾荒时遭遇灰卒，被咬喉致死→"
    "复活后面板显示2→利用灰卒进食同类的间隙用铁锤砸碎一只灰卒的核心→"
    "小九出现，弓背嘶吼，剩余灰卒逃窜→"
    "陆沉抱着小九回到铁匠铺，老陈头发现他衣服上的血洞但没追问，只是把药钱塞回他手里"
)


@pytest.mark.p1
def test_scene_count_for_words():
    """章节字数 → 目标场景数：1200 字/场景为基准，四舍五入后 clamp [1,4]；
    不传字数（<=0）→ 4（兼容旧行为）。"""
    assert _scene_count_for_words(2500) == 2
    assert _scene_count_for_words(2000) == 2
    assert _scene_count_for_words(3000) == 3
    assert _scene_count_for_words(4200) == 4
    assert _scene_count_for_words(1500) == 1
    assert _scene_count_for_words(0) == 4


@pytest.mark.p1
def test_derive_ch1_words_driven_two_events():
    """真实 ch1：5 拍 @2500 字 → 2 事件（场景粒度优化），死亡-复活同事件、骨架片段不丢。"""
    evs = _derive_events_from_moves(_CH1_MOVES, 2500)
    assert len(evs) == 2
    joined = "".join(e["desc"] for e in evs)
    for frag in ("被咬喉致死", "复活后面板显示2", "砸碎一只灰卒的核心", "小九出现", "老陈头"):
        assert frag in joined, f"骨架片段丢失: {frag}"
    for e in evs:
        if "被咬喉致死" in e["desc"]:
            assert "复活后面板显示2" in e["desc"], "死亡与复活被拆开（因果合并失效）"


@pytest.mark.p1
def test_derive_legacy_no_words_unchanged():
    """旧行为兼容：不传 chapter_words → 5 拍仍合并到 4 事件（与改造前一致）。"""
    evs = _derive_events_from_moves(_CH1_MOVES)
    assert len(evs) == 4
    assert evs[0]["desc"] == (
        "陆沉清晨出门前检查面板（3次），在墟口拾荒时遭遇灰卒，被咬喉致死→复活后面板显示2"
    )


@pytest.mark.p1
def test_derive_within_target_not_merged():
    """拍数 ≤ 目标场景数时不合并（2 拍 @2500 → 2 事件原样保留，无多余合并）。"""
    moves = "陆沉在墟口遭遇灰卒被咬死→复活后回铁匠铺"
    evs = _derive_events_from_moves(moves, 2500)
    assert len(evs) == 2
    assert "→" not in evs[0]["desc"] and "→" not in evs[1]["desc"]


@pytest.mark.p1
def test_validate_scene_plan_min_target_words():
    """单场景 target_words 下限：4×625 @2500（ch1 旧形态）= 拆得太碎 → 不合格；
    2×1250 = 通过（场景粒度优化）。"""
    ok, why = _validate_scene_plan([
        {"scene_event_ref": "事件1", "event": "墟口遇灰卒死亡复活", "target_words": 625},
        {"scene_event_ref": "事件2", "event": "砸核心", "target_words": 625},
        {"scene_event_ref": "事件3", "event": "小九驱散", "target_words": 625},
        {"scene_event_ref": "事件4", "event": "回铺", "target_words": 625},
    ], 2500)
    assert not ok and "下限" in why
    ok, why = _validate_scene_plan([
        {"scene_event_ref": "事件1", "event": "墟口遇灰卒死亡复活砸核心", "target_words": 1250},
        {"scene_event_ref": "事件2", "event": "小九驱散回铺", "target_words": 1250},
    ], 2500)
    assert ok
