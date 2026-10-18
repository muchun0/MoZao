# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S3.3 三级闭环-L2 挂账镜头 专项测试。

覆盖：
- P1：连续 ≥2 章漏兑现同一节点 → 生成挂账镜头块
- P1：仅 1 章漏兑现 → 空串（不升级）
- P1：命中豁免章型（对话/高潮/战斗等）→ 空串（防误伤）
- P2：无台账 / 空数据 → 空串（降级安全）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.pending_commitments import (
    l2_hangover_block, compensation_block, build_commitment_ledger,
)


def _ledger(chapter_no: int, missed: list[str], verdict: str = "warn"):
    return {"chapter_no": chapter_no, "verdict": verdict, "coverage": 0.3,
            "committed": [], "missed": missed, "planted": [],
            "must_carry_to_next": missed}


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "sc", cfg)


class TestL2HangoverBlock:
    @pytest.mark.p1
    def test_consecutive_miss_triggers(self, store):
        """节点在 ch3/ch4 连续漏兑现 → ch5 生成挂账镜头。"""
        store.save_pending_commitments(3, _ledger(3, ["主角突破"]))
        store.save_pending_commitments(4, _ledger(4, ["主角突破"]))
        blk = l2_hangover_block(store, 5)
        assert blk
        assert "挂账镜头" in blk
        assert "主角突破" in blk

    @pytest.mark.p1
    def test_single_miss_no_escalate(self, store):
        """仅 1 章漏兑现 → 空串（不升级）。"""
        store.save_pending_commitments(4, _ledger(4, ["主角突破"]))
        assert l2_hangover_block(store, 5) == ""

    @pytest.mark.p1
    def test_exempt_chapter_type_empty(self, store):
        """下章为对话/高潮等豁免章型 → 空串（防误伤）。"""
        store.save_pending_commitments(3, _ledger(3, ["主角突破"]))
        store.save_pending_commitments(4, _ledger(4, ["主角突破"]))
        for t in ("对话", "过渡", "情绪", "高潮", "战斗"):
            assert l2_hangover_block(store, 5, current_chapter_type=t) == ""

    @pytest.mark.p2
    def test_no_data_empty(self, store):
        """无台账 → 空串。"""
        assert l2_hangover_block(store, 5) == ""

    @pytest.mark.p1
    def test_compensation_block_l1_still_works(self, store):
        """L1 补偿块（S3.1）仍独立生效。"""
        prev = _ledger(4, ["主角突破"])
        cb = compensation_block(prev)
        assert cb and "上章未兑现节点补偿" in cb


class TestBuildLedger:
    @pytest.mark.p2
    def test_ledger_from_bg(self):
        """build_commitment_ledger 兼容 beat_gate 结果（covered/missing 映射）。"""
        bg = {"verdict": "warn", "coverage": 0.5,
              "planned": [{"beat": "节点A", "covered": True},
                          {"beat": "节点B", "covered": False}]}
        led = build_commitment_ledger(bg, 3)
        assert led["committed"] == ["节点A"]
        assert led["missed"] == ["节点B"]
        assert led["must_carry_to_next"] == ["节点B"]
