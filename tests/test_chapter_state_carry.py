# -*- coding: utf-8 -*-
"""章末账本 B+A 定稿回归：每章抽取（B）+ 已发生事实跨章累积（A）。

背景：`state_extract_frequency` 默认 5 时，非抽取章的确定性路径无面板数字就返回
`None`，`chapter_state.json` 长期停在首章 → 承接块永远是第 1 章、时间线与状态视图
全部失真。2026-11-16 用户定稿：频率改 1（每章抽），facts 跨章累积。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig, feature_default
from novel_gen.pipeline.chapter_state import (MAX_CHAPTER_FACTS, _carry_facts,
                                              _should_llm_extract,
                                              extract_chapter_state)
from novel_gen.store.schemas import Chapter


def _cfg() -> NovelConfig:
    return NovelConfig(title="账本承接", total_words=8000, genre="玄幻")


@pytest.mark.p1
def test_feature_default_is_every_chapter():
    assert feature_default("state_extract_frequency") == 1
    cfg = _cfg()
    assert int(cfg.state_extract_frequency) == 1
    assert _should_llm_extract(2, cfg) is True
    assert _should_llm_extract(5, cfg) is True


@pytest.mark.p1
def test_facts_accumulate_in_time_order_with_dedupe():
    old = {"chapter": 1, "facts": ["第1章的事", "第1章的第二件事"]}
    state = {"chapter": 2, "facts": ["第1章的第二件事", "第2章的事"]}
    _carry_facts(state, old)
    assert state["facts"] == ["第1章的事", "第1章的第二件事", "第2章的事"]


@pytest.mark.p1
def test_facts_capped_keeps_newest():
    old = {"facts": [f"旧事实{i}" for i in range(MAX_CHAPTER_FACTS)]}
    state = {"facts": ["本章新事实"]}
    _carry_facts(state, old)
    assert len(state["facts"]) == MAX_CHAPTER_FACTS
    assert state["facts"][-1] == "本章新事实"
    assert "旧事实0" not in state["facts"]


@pytest.mark.p1
def test_deterministic_fallback_advances_with_old_facts():
    """非抽取/抽取失败章：无面板数字也必须写盘，章号前进且 facts 承接。"""
    cfg = _cfg()
    cfg.chapter_state_llm = False
    chapter = Chapter(no=3, title="第3章", content="这一章没有任何机制数字面板的正文。")
    state = extract_chapter_state(None, cfg, None, chapter, 3,
                                  old_state={"chapter": 1, "facts": ["第1章的事"]})
    assert state is not None
    assert state["chapter"] == 3
    assert state["facts"] == ["第1章的事"]


@pytest.mark.p1
def test_deterministic_fallback_still_empty_without_anything():
    """旧账本也没有 facts 时保持原语义：返回 None，不写空壳账本。"""
    cfg = _cfg()
    cfg.chapter_state_llm = False
    chapter = Chapter(no=4, title="第4章", content="没有面板数字，也没有旧事实可承接。")
    assert extract_chapter_state(None, cfg, None, chapter, 4, old_state={"chapter": 1}) is None
