# -*- coding: utf-8 -*-
"""P1-4 BeatGate 正向供给专项测试（docs/REQ-门禁质检正向影响.md P1-4）。

覆盖：
- _beat_positive_hints：missing 拍 → 「可往哪个方向写」示范块（零 LLM 规则模板）、空/空串拍过滤
- _beat_positive_block：读上一章 beat_gate 的 positive_hints 注入；无上章/关闭 → 空串
- 开关 beat_positive_supply 默认开启
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="beat 正向测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _cw(store, cfg=None):
    cw = object.__new__(ChapterWriter)
    cw.cfg = cfg or store.load_config()
    cw.store = store
    return cw


def test_positive_hints_generates_directions(store):
    cw = _cw(store)
    hints = cw._beat_positive_hints(["主角抵达墟口", "发现蚀晶"])
    assert "上章未兑现拍" in hints
    assert "「主角抵达墟口」→ 可往：" in hints
    assert "「发现蚀晶」→ 可往：" in hints


def test_positive_hints_empty(store):
    cw = _cw(store)
    assert cw._beat_positive_hints([]) == ""
    assert cw._beat_positive_hints(None) == ""


def test_positive_hints_blank_filtered(store):
    cw = _cw(store)
    hints = cw._beat_positive_hints(["拍A", "  ", "拍B"])
    assert "拍A" in hints and "拍B" in hints
    assert "  " not in hints


def test_positive_block_reads_prev_chapter(store):
    store.save_beat_gate(1, {"missing": ["拍A"], "positive_hints": "上章示范块"})
    cw = _cw(store)
    assert cw._beat_positive_block(2) == "上章示范块"


def test_positive_block_no_prev_returns_blank(store):
    cw = _cw(store)
    assert cw._beat_positive_block(2) == ""


def test_positive_block_disabled(store):
    store.save_beat_gate(1, {"positive_hints": "示范"})
    cfg = NovelConfig(title="x")
    cfg.beat_positive_supply = False
    cw = _cw(store, cfg)
    assert cw._beat_positive_block(2) == ""


def test_beat_positive_supply_flag_default_true():
    assert NovelConfig(title="x").beat_positive_supply is True
