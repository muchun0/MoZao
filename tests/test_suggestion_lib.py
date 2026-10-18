# -*- coding: utf-8 -*-
"""P1-3 EditorReviewer 建议指令库专项测试（docs/REQ-门禁质检正向影响.md P1-3）。

覆盖：
- record_suggestions 按手法分类沉淀（对话/战斗/情绪/环境/叙事）+ 去重 + 容量上限
- _classify_suggestion 关键词分类（含 fallback 叙事-节奏）
- suggestion_block 注入块组装（带类别标签、空库返回空串）
- 开关 suggestion_lib 默认开启
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.book_memory import BookMemory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="建议指令库测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _memory(store):
    return BookMemory(store, store.load_config())


def test_record_classify_and_block(store):
    mem = _memory(store)
    n = mem.record_suggestions([
        "加强对话的潜台词与言外之意",
        "战斗场景动作链写得更紧凑",
        "深化角色内心情绪的暴露与点破",
    ])
    assert n == 3
    lib = mem.load()["suggestion_lib"]
    assert "对话-潜台词" in lib
    assert "战斗-动作链" in lib
    assert "情绪-白描+点破" in lib
    blk = mem.suggestion_block()
    assert "本作编辑建议" in blk
    assert "[对话-潜台词]" in blk


def test_classify_fallback_to_narrative(store):
    mem = _memory(store)
    assert mem._classify_suggestion("推进节奏，让转折更快") == "叙事-节奏"
    assert mem._classify_suggestion("加强潜台词") == "对话-潜台词"
    assert mem._classify_suggestion("战斗场面更激烈") == "战斗-动作链"
    assert mem._classify_suggestion("环境氛围更压抑") == "环境-物象白描"


def test_dedup(store):
    mem = _memory(store)
    mem.record_suggestions(["加强章末钩子"])
    n = mem.record_suggestions(["加强章末钩子"])  # 重复
    assert n == 0
    lib = mem.load()["suggestion_lib"]
    total = sum(len(v) for v in lib.values())
    assert total == 1


def test_cap_per_category(store):
    mem = _memory(store)
    for i in range(30):
        mem.record_suggestions([f"推进节奏第{i}条"])
    lib = mem.load()["suggestion_lib"]
    assert len(lib.get("叙事-节奏") or []) == mem._SUGGEST_CAP == 20


def test_empty_and_blank(store):
    mem = _memory(store)
    assert mem.record_suggestions([]) == 0
    assert mem.record_suggestions(["  "]) == 0
    assert mem.suggestion_block() == ""


def test_suggestion_lib_flag_default_true():
    assert NovelConfig(title="x").suggestion_lib is True
