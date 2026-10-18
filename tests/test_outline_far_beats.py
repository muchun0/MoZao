# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S3.6 远期大纲粗粒度 专项测试。

覆盖：
- P1：far_beats 中的远期节点不计入 beat_gate 分母（方向约束不 fail）
- P1：全为 far_beats 时 verdict=skip_far（不卡 fail）
- P1：far_beats 为空/None → 行为与默认一致（向后兼容）
- P2：outline_rolling_detail 默认关，_far_outline_beats 不改变默认行为
- P2：_far_outline_beats 返回距当前>outline_far_skip 的 KeyNode 描述/kind
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import KeyNode, Outline
from novel_gen.pipeline.beat_gate import compute_beat_coverage, assess_chapter


class TestFarBeatsExclusion:
    @pytest.mark.p1
    def test_far_beats_excluded_from_denominator(self):
        # 计划拍 2 个：1 个远期 → 排除后分母只算非远期拍（远期节点不参与 fail 判定）
        res = compute_beat_coverage(
            "主角启程，踏出城门，向废墟走去。",
            "主角启程；远期之战的伏笔铺垫",
            chapter_type="剧情",
            far_beats=["远期之战的伏笔铺垫"],
        )
        beats = [b["beat"] for b in res["planned"]]
        assert "远期之战的伏笔铺垫" not in beats
        assert "主角启程" in beats

    @pytest.mark.p1
    def test_all_far_beats_skip_far(self):
        res = compute_beat_coverage(
            "主角在集市闲逛。",
            "二十章后的终极对决；三十章后的神域降临",
            chapter_type="剧情",
            far_beats=["二十章后的终极对决", "三十章后的神域降临"],
        )
        assert res["verdict"] == "skip_far"
        assert res["coverage"] is None

    @pytest.mark.p1
    def test_no_far_beats_backward_compat(self):
        # 不传 far_beats → 默认行为（此 moves 未覆盖 → fail）
        res = compute_beat_coverage(
            "主角在集市闲逛。",
            "主角启程迎战强敌；解开残卷之谜",
            chapter_type="剧情",
        )
        assert res["verdict"] == "fail"

    @pytest.mark.p2
    def test_assess_chapter_passthrough(self):
        res = assess_chapter(
            "主角启程，踏出城门，向废墟走去。",
            "主角启程；远期之战的伏笔铺垫",
            chapter_type="剧情",
            far_beats=["远期之战的伏笔铺垫"],
        )
        beats = [b["beat"] for b in res["planned"]]
        assert "远期之战的伏笔铺垫" not in beats
        assert "主角启程" in beats


class TestFarOutlineBeats:
    @pytest.fixture
    def writer(self, tmp_path):
        cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                          chapter_words=1600, outline_far_skip=30)
        store = ProjectStore.create(tmp_path / "sc", cfg)
        from novel_gen.pipeline.stages import ChapterWriter
        from unittest.mock import MagicMock
        w = ChapterWriter(cfg, MagicMock(), store, MagicMock())
        return w, store

    @pytest.mark.p2
    def test_returns_far_keynodes(self, writer):
        w, store = writer
        o = Outline()
        o.key_nodes = [
            KeyNode(kind="激励事件", chapter_no=3, description="主角踏上征途"),
            KeyNode(kind="终极高潮", chapter_no=80, description="神域降临决战"),
        ]
        store.save_outline(o)
        far = w._far_outline_beats(40)
        assert far and "终极高潮" in far and "神域降临决战" in far

    @pytest.mark.p2
    def test_no_far_returns_none(self, writer):
        w, store = writer
        o = Outline()
        o.key_nodes = [KeyNode(kind="激励事件", chapter_no=35, description="近章节点")]
        store.save_outline(o)
        assert w._far_outline_beats(40) is None
