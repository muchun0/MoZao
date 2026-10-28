# -*- coding: utf-8 -*-
"""scene_event_utils 通用语义合并器 _dedup_events_by_pairs 用例（P1）。

REQ-场景重演修复-事件清单去重：按语义重复对清单确定性合并事件（丢弃后者）。
重复对来源不限（_scene_split_review 的 LLM 语义评审、人工给定等），故本合并器
为通用工具；覆盖合并/多对/无重复/空单/越界非法/去重后过少回退信号。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.scene_event_utils import _dedup_events_by_pairs


def _ev(desc, conflict="", action=""):
    return {"id": "?", "desc": desc, "conflict": conflict,
            "action": action, "final_state": ""}


# ---------- 合并命中 ----------

@pytest.mark.p1
def test_merge_drops_later_keeps_earlier():
    """重复对 (0,1) → 丢弃后者（事件1），保留较早事件0，返回丢弃数 1。"""
    events = [
        _ev("沈砚当夜在祭坛烧格目", "沈砚", ""),
        _ev("次日清晨沈砚又在祭坛烧格目", "沈砚", ""),
        _ev("老更夫沿河巡查", "老更夫", ""),
    ]
    pairs = [(0, 1, "同事件重复：当夜/次日烧格目")]
    new, dropped = _dedup_events_by_pairs(events, pairs)
    assert dropped == 1
    assert len(new) == 2
    assert new[0]["desc"] == "沈砚当夜在祭坛烧格目"
    assert "次日清晨" not in [e["desc"] for e in new]


@pytest.mark.p1
def test_merge_multiple_pairs():
    """多对重复 → 全部丢弃后者，保留未重复事件。"""
    events = [
        _ev("沈砚探香樟林", "沈砚", ""),
        _ev("沈砚又探香樟林", "沈砚", ""),
        _ev("老更夫巡河", "老更夫", ""),
        _ev("老更夫再巡河", "老更夫", ""),
    ]
    pairs = [(0, 1, "a"), (2, 3, "b")]
    new, dropped = _dedup_events_by_pairs(events, pairs)
    assert dropped == 2 and len(new) == 2


# ---------- 不合并 / 边界 ----------

@pytest.mark.p1
def test_no_duplicates_unchanged():
    """无重复对 → 原样返回，丢弃数 0。"""
    events = [_ev("沈砚探林", "沈砚", ""), _ev("老更夫巡河", "老更夫", "")]
    new, dropped = _dedup_events_by_pairs(events, [])
    assert dropped == 0 and new == events


@pytest.mark.p1
def test_empty_or_single_unchanged():
    """空 / 单事件 → 原样返回，丢弃数 0。"""
    assert _dedup_events_by_pairs([], [(0, 1)]) == ([], 0)
    one = [_ev("沈砚独自打铁")]
    new, dropped = _dedup_events_by_pairs(one, [(0, 1)])
    assert new == one and dropped == 0


@pytest.mark.p1
def test_invalid_or_out_of_range_pair_ignored():
    """越界 / 非法 pair（含 i==j、下标超界）→ 忽略，不崩溃不合并。"""
    events = [_ev("沈砚探林", "沈砚", ""), _ev("老更夫巡河", "老更夫", "")]
    pairs = [(0, 0), (1, 5), (5, 1), ("x", "y"), None]
    new, dropped = _dedup_events_by_pairs(events, pairs)
    assert dropped == 0 and len(new) == 2


@pytest.mark.p1
def test_merge_after_dedup_too_few_returns_empty_signal():
    """去重后事件过少（<2）→ 返回空清单 + 丢弃数（回退信号，由调用方走安全路径）。"""
    events = [
        _ev("沈砚当夜烧格目", "沈砚", ""),
        _ev("次日沈砚又烧格目", "沈砚", ""),
    ]
    pairs = [(0, 1, "重复")]
    new, dropped = _dedup_events_by_pairs(events, pairs)
    assert dropped == 1
    assert new == []          # 回退信号：不去重成单事件残稿
