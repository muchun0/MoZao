# -*- coding: utf-8 -*-
"""机制账本回归：`cumulative_value` 与 `record_mechanism_state`。

回归对象：提交 5c611da（机制数字软提示与通用化）删除了 `_EROSION_SEQ_RE`，
但 `chapter_ledger.record_mechanism_state` 与 `stages._reject_invalid_version`
仍 import 该符号 → 每章「机制状态账本入账失败（降级跳过）」ImportError，机制数字
从未入账（真实 30 章样本生成时暴露，devtest/master 静默失效）。
本用例锁死：累积值按注册表方向提取、账本入账不再抛异常、跨章 deaths 按差值反推。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.chapter_ledger import record_mechanism_state
from novel_gen.pipeline.chapter_state import cumulative_value, mechanism_field_registry
from novel_gen.store.project import ProjectStore


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=1500, total_words=6000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _memory_with_baseline(store):
    mem = BookMemory(store, store.load_config())
    data = mem.load()
    data.setdefault("canon", {})["mechanism_baseline"] = {
        "面板格式": "蚀化：98（累计递增、不重置），可死次数：3（用尽即重置）"}
    mem.save()
    return mem


def test_cumulative_value_takes_max_increase_field():
    reg = {"蚀化": "increase", "可死次数": "decrease"}
    text = "他抬头看面板，蚀化：98，随后一道黑影压来，蚀化：99。可死次数：2。"
    assert cumulative_value(text, reg) == 99


def test_cumulative_value_none_when_no_increase_field():
    assert cumulative_value("可死次数：2", {"可死次数": "decrease"}) is None
    assert cumulative_value("", {"蚀化": "increase"}) is None
    assert cumulative_value("蚀化：99", {}) is None


def test_record_mechanism_state_writes_numbers_without_import_error(store):
    mem = _memory_with_baseline(store)
    reg = mechanism_field_registry(mem.load()["canon"]["mechanism_baseline"])
    assert "蚀化" in reg and reg["蚀化"] == "increase"

    ok = record_mechanism_state(mem, 1, "面板浮起，蚀化：98，可死次数：3。")
    assert ok is True
    ch = mem.load()["state"]["chapters"][-1]
    assert ch["ch"] == 1
    assert ch["erosion"] == 98
    assert ch["nums"].get("蚀化") == 98
    assert ch["nums"].get("可死次数") == 3


def test_record_mechanism_state_deaths_is_erosion_delta(store):
    mem = _memory_with_baseline(store)
    record_mechanism_state(mem, 1, "蚀化：98，可死次数：2。")
    record_mechanism_state(mem, 2, "蚀化：99，可死次数：1。")
    ch2 = [c for c in mem.load()["state"]["chapters"] if c["ch"] == 2][0]
    assert ch2["erosion"] == 99
    assert ch2["deaths"] == 1
    assert ch2["nums"].get("可死次数") == 1


def test_record_mechanism_state_inherits_when_panel_absent(store):
    mem = _memory_with_baseline(store)
    record_mechanism_state(mem, 1, "蚀化：98，可死次数：3。")
    record_mechanism_state(mem, 2, "这一章没有面板行，只有风。")
    ch2 = [c for c in mem.load()["state"]["chapters"] if c["ch"] == 2][0]
    assert ch2["erosion"] == 98      # 继承上一章末，不断链
    assert ch2["deaths"] == 0

