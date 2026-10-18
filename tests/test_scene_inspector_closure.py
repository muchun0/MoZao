# -*- coding: utf-8 -*-
"""P1-5 scene_inspector 闭环专项测试（docs/REQ-门禁质检正向影响.md P1-5）。

覆盖：
- scene_inspector.collect_suggestions：从体检报告抽 bad/warn 维度的非空建议（带维度标签）
- stages._inspector_block：读上一章沉淀的体检建议注入下一章生成；空/关闭/异常 → 空串
- store.save/load_inspector_suggestions 按章读写
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
from novel_gen.pipeline.scene_inspector import collect_suggestions


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="体检闭环测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _cw(store, cfg=None):
    cw = object.__new__(ChapterWriter)
    cw.cfg = cfg or store.load_config()
    cw.store = store
    return cw


def test_collect_suggestions_filters_bad_warn():
    report = {
        "dimensions": [
            {"dim": "字数", "level": "warn", "suggestion": "内容偏短：可在冲突处补充动作。"},
            {"dim": "章末钩子", "level": "bad", "suggestion": "按大纲钩子在结尾收束。"},
            {"dim": "重复度", "level": "ok", "suggestion": ""},
            {"dim": "事件推进", "level": "ok", "suggestion": ""},
            {"dim": "承接", "level": "bad", "suggestion": ""},  # 空建议不抽
        ],
    }
    out = collect_suggestions(report)
    assert len(out) == 2
    assert out[0].startswith("字数：")
    assert out[1].startswith("章末钩子：")
    assert not any("承接" in s for s in out)


def test_collect_suggestions_empty():
    assert collect_suggestions({}) == []
    assert collect_suggestions({"dimensions": [{"dim": "x", "level": "ok", "suggestion": ""}]}) == []


def test_store_save_load_roundtrip(store):
    store.save_inspector_suggestions(1, ["建议A", "  ", "建议B"])
    data = store.load_inspector_suggestions()
    assert data.get("1") == ["建议A", "建议B"]  # 空串被过滤
    assert "1" in data


def test_inspector_block_reads_prev(store):
    store.save_inspector_suggestions(1, ["字数：内容偏短，补充动作细节。"])
    cw = _cw(store)
    blk = cw._inspector_block(2)
    assert "上一章场景体检" in blk
    assert "字数：内容偏短" in blk


def test_inspector_block_no_prev_blank(store):
    cw = _cw(store)
    assert cw._inspector_block(2) == ""


def test_inspector_block_disabled(store):
    store.save_inspector_suggestions(1, ["建议"])
    cfg = NovelConfig(title="x")
    cfg.scene_inspector = False
    cw = _cw(store, cfg)
    assert cw._inspector_block(2) == ""
