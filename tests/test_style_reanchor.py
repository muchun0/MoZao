# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S2.2 周期轻量再锚定 专项测试。

覆盖：
- P1：周期命中章（chapter_no % style_reanchor_every == 0）注入滚动风格锚（book_memory goods）
- P1：非命中章不注入（空串，控成本）
- P1：style_reanchor_every=0 或 style_anchor_rolling=False 关闭时不注入
- P2：goods 空账本 → 空串（降级安全）

占位片段避开节拍词（§11.8）；不依赖真实 LLM。
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
from novel_gen.pipeline.stages import ChapterWriter


def _mk_cfg(every: int = 5, rolling: bool = True) -> NovelConfig:
    return NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                       chapter_words=2500, style_reanchor_every=every,
                       style_anchor_rolling=rolling)


@pytest.fixture
def writer(tmp_path):
    cfg = _mk_cfg()
    s = ProjectStore.create(tmp_path / "sc", cfg)
    # 造 book_memory goods：≥85 分高分片段（滚动风格源）
    bm = BookMemory(s, cfg)
    bm._ingest_good_samples(1, ["青石阶上霜痕未消，少年负剑而立，衣袂猎猎。"], 90)
    llm = MagicMock()
    memory = MagicMock()
    return ChapterWriter(cfg, llm, s, memory), s, cfg


class TestRollingStyleAnchor:
    @pytest.mark.p1
    def test_hit_chapter_injects_anchor(self, writer):
        """周期命中章（ch5，every=5）注入非空滚动风格锚。"""
        w, _, _ = writer
        blk = w._rolling_style_anchor(5)
        assert blk
        assert "青石阶" in blk   # goods 高分片段内容

    @pytest.mark.p1
    def test_non_hit_chapter_empty(self, writer):
        """非命中章（ch3）不注入（空串，控成本）。"""
        w, _, _ = writer
        assert w._rolling_style_anchor(3) == ""

    @pytest.mark.p1
    def test_every_zero_disabled(self, tmp_path):
        """style_reanchor_every=0 → 全部不注入。"""
        cfg = _mk_cfg(every=0)
        s = ProjectStore.create(tmp_path / "sc2", cfg)
        bm = BookMemory(s, cfg)
        bm._ingest_good_samples(1, ["青石阶上霜痕未消，少年负剑而立。"], 90)
        w = ChapterWriter(cfg, MagicMock(), s, MagicMock())
        assert w._rolling_style_anchor(5) == ""

    @pytest.mark.p1
    def test_rolling_off_disabled(self, tmp_path):
        """style_anchor_rolling=False → 不注入。"""
        cfg = _mk_cfg(rolling=False)
        s = ProjectStore.create(tmp_path / "sc3", cfg)
        bm = BookMemory(s, cfg)
        bm._ingest_good_samples(1, ["青石阶上霜痕未消，少年负剑而立。"], 90)
        w = ChapterWriter(cfg, MagicMock(), s, MagicMock())
        assert w._rolling_style_anchor(5) == ""

    @pytest.mark.p2
    def test_empty_goods_empty_anchor(self, tmp_path):
        """goods 空账本 → 空串（降级安全，不阻塞）。"""
        cfg = _mk_cfg()
        s = ProjectStore.create(tmp_path / "sc4", cfg)
        w = ChapterWriter(cfg, MagicMock(), s, MagicMock())
        assert w._rolling_style_anchor(5) == ""
