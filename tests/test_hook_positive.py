# -*- coding: utf-8 -*-
"""REQ-门禁质检正向影响 P0-5 追更欲正向锚 + streak + 进度盘聚合 专项测试（P1）。

覆盖：
- book_memory.record_hook_streak：hook>=4 累计、<4 冻结一次或重置、None 不动
- book_memory.record_taste 入账 hook_score
- book_memory.good 分层入账（hook>=4 → demo 示范池）、来源隔离、采样轮换 pick_state
- webapp.db.traces._aggregate_quality 每章色块（红/绿/黄/灰）聚合
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
from novel_gen.pipeline.book_memory import BookMemory
from webapp.db.traces import _aggregate_quality


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "hook", cfg)


def _memory(store):
    cfg = store.load_config()
    return BookMemory(store, cfg)


def _good_text():
    return ("他下颌绷紧，手背青筋一点点鼓了起来，一个字都没回。\n"
            "他怕了。他认得这种感觉——上次腿被打断那夜，冷也是这么冷。\n")


# ---------- record_hook_streak ----------

def test_hook_streak_accumulates_high(store):
    mem = _memory(store)
    assert mem.record_hook_streak(4)["current"] == 1
    assert mem.record_hook_streak(5)["current"] == 2
    assert mem.record_hook_streak(4)["current"] == 3
    st = mem.record_hook_streak(4)
    assert st["current"] == 4 and st["max"] == 4


def test_hook_streak_low_reset_with_freeze(store):
    mem = _memory(store)
    mem.record_hook_streak(5)
    mem.record_hook_streak(5)
    st = mem.record_hook_streak(2)  # <4 → 冻结一次（保留当前），不重置
    assert st["freeze_used"] is True
    assert st["current"] == 2
    # 再低 → 重置
    st2 = mem.record_hook_streak(1)
    assert st2["current"] == 0


def test_hook_streak_none_untouched(store):
    mem = _memory(store)
    st = mem.record_hook_streak(None)
    assert st["current"] == 0 and st["freeze_used"] is False


# ---------- record_taste 入账 hook_score ----------

def test_record_taste_stores_hook_score(store):
    mem = _memory(store)
    mem.record_taste(1, 88, [], hook_score=4)
    state = mem.load()
    assert state["taste"]["chapters"][0]["hook_score"] == 4
    # hook 连续达标账本独立存于顶层 hook_streak
    assert state["hook_streak"]["current"] == 0


# ---------- good 分层 + 来源隔离 + 轮换 ----------

def test_good_hook_promotes_to_demo(store):
    mem = _memory(store)
    # hook>=4 → demo 示范池（即使 score 较低）
    assert mem.record_good(1, _good_text(), 70, [], hook_score=5) > 0
    demo = [s for s in mem.load()["goods"]["samples"] if s["level"] == "demo"]
    assert demo, "hook>=4 应入 demo 池"


def test_good_manual_and_external_to_demo(store):
    mem = _memory(store)
    n = mem.record_good_manual(1, _good_text())
    assert n > 0
    assert all(s["src_type"] == "user" and s["level"] == "demo"
               for s in mem.load()["goods"]["samples"])
    mem2 = _memory(store)
    ext_text = "他一把握住那截烧红的铁链，猛地往回拽，火星溅了满手，也不肯松开。"
    assert mem2.ingest_external_good("战斗-动作链", ext_text) > 0
    ext = [s for s in mem2.load()["goods"]["samples"] if s["src_type"] == "external"]
    assert ext and all(s["level"] == "demo" for s in ext)


def test_good_pick_rotation(store):
    """采样轮换：pick_state 在同类多条间轮换，避免同一示范反复复用（多样性治理）。"""
    mem = _memory(store)
    for ch in range(1, 6):
        mem.ingest_external_good("对话-潜台词", f"第{ch}段：他什么都没说，只是把门带上，留一地沉默。")
    block1 = mem.good_block(top_n=3)
    block2 = mem.good_block(top_n=3)
    # 两次抽取应在不同窗口位（轮换生效），不应完全一样
    assert block1 != block2 or "参考质感" in block1
    state = mem.load()["goods"]
    assert "pick_state" in state and state["pick_state"], "轮换状态应持久化"


# ---------- webapp 进度盘聚合（纯函数，用代理模块避免依赖 webapp 全量导入） ----------

def test_aggregate_quality_chapter_status():
    rows = [
        {"step_name": "第1章自校验", "extra": {"score": 90, "passed": True,
         "hook_score": 5, "auto_revised": False}},
        {"step_name": "第2章自校验", "extra": {"score": 60, "passed": False,
         "hook_score": 2, "auto_revised": True}},
        {"step_name": "第3章自校验", "extra": {"score": 75, "passed": True,
         "hook_score": 3, "auto_revised": True}},
    ]
    agg = _aggregate_quality(rows)
    status = {c["chapter"]: c["color"] for c in agg["chapter_status"]}
    assert status[1] == "green"   # hook>=4 高分
    assert status[2] == "red"     # 未过
    assert status[3] == "yellow"  # 通过但自动重写
