# -*- coding: utf-8 -*-
"""P2-1 RepetitionDetector 正向替换供给专项测试（docs/REQ-门禁质检正向影响.md P2-1）。

覆盖：
- _repetition_direction_block：重复命中重写时，注入「新方向候选」
  （上一章未兑现承诺 / 本章场景设计推进方向），零 LLM 读 store
- 无素材 / 异常 → 空串（降级安全）
- 开关 repetition_direction 默认开启
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="重复供给测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _qc(store, cfg=None):
    qc = object.__new__(QualityChecker)
    qc.cfg = cfg or store.load_config()
    qc.store = store
    return qc


def test_direction_block_reads_unfulfilled(store):
    store.save_pending_commitments(1, {
        "missed": ["救回同伴", "揭开蚀晶来源"],
        "must_carry_to_next": ["追杀者的埋伏"],
        "committed": ["到达墟口"],
    })
    qc = _qc(store)
    blk = qc._repetition_direction_block(2)
    assert "新方向候选" in blk
    assert "未兑现承诺" in blk and "救回同伴" in blk
    assert "需带到下一章" in blk and "追杀者的埋伏" in blk
    assert "到达墟口" not in blk  # committed 不注入


def test_direction_block_reads_chapter_design(store):
    store.save_chapter_design({"version": 1, "chapters": [
        {"no": 2, "design": {"conflict": "与蚀卫正面冲突", "goal": "夺回墟口"}},
    ]})
    qc = _qc(store)
    blk = qc._repetition_direction_block(2)
    assert "本章可推进方向" in blk
    assert "与蚀卫正面冲突" in blk


def test_direction_block_no_material_blank(store):
    qc = _qc(store)
    assert qc._repetition_direction_block(2) == ""


def test_direction_block_blank_values_filtered(store):
    store.save_pending_commitments(1, {"missed": ["  ", ""], "must_carry_to_next": []})
    qc = _qc(store)
    assert qc._repetition_direction_block(2) == ""


def test_repetition_direction_flag_default_true():
    assert NovelConfig(title="x").repetition_direction is True
