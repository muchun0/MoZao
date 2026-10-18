# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S1.4 伏笔 stalled 状态 + 语义双路 专项测试。

覆盖：
- P1：埋后超 stall_after 章仍 planted → 标记 stalled
- P1：未超阈值 → 保持 planted
- P1：在回收窗口（due_soon / overdue）→ 不被 stalled 覆盖
- P1：stalled 注入块含"冷却"提醒
- P2：ledger_stalled=False → 不标 stalled
- P2：语义双路函数 mock llm 返回进度
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import ForeshadowItem, World
from novel_gen.pipeline.reader_ledger import ReaderLedger, semantic_foreshadow_check


def _mk_world(*fs_list):
    w = World()
    w.foreshadowing = list(fs_list)
    return w


def _fs(name, buried, reveal):
    f = ForeshadowItem(name=name, content=f"关于{name}的线索", buried_ch=buried,
                       reveal_ch=reveal, level="medium")
    return f


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=20000,
                      chapter_words=1600, foreshadow_lead=3)
    return ProjectStore.create(tmp_path / "sc", cfg)


class TestStalledState:
    @pytest.mark.p1
    def test_buried_long_no_progress_becomes_stalled(self, store):
        # 埋于第1章、计划第20章回收、当前第10章（超 stall_after=8 且不在回收窗口）→ stalled
        w = _mk_world(_fs("蚀晶钥匙", 1, 20))
        rl = ReaderLedger(store, store.load_config())
        items = rl._commitment_items(w, 10)
        assert items[0]["state"] == "stalled"

    @pytest.mark.p1
    def test_not_long_enough_stays_planted(self, store):
        # 埋于第1章、当前第5章（未超8）→ planted
        w = _mk_world(_fs("蚀晶钥匙", 1, 20))
        rl = ReaderLedger(store, store.load_config())
        items = rl._commitment_items(w, 5)
        assert items[0]["state"] == "planted"

    @pytest.mark.p1
    def test_due_window_not_stalled(self, store):
        # 在回收窗口（reveal_ch=12, lead=3, 当前11）→ due_soon，不被 stalled 覆盖
        w = _mk_world(_fs("蚀晶钥匙", 1, 12))
        rl = ReaderLedger(store, store.load_config())
        items = rl._commitment_items(w, 11)
        assert items[0]["state"] == "due_soon"

    @pytest.mark.p1
    def test_stalled_reminder_in_block(self, store):
        w = _mk_world(_fs("蚀晶钥匙", 1, 20))
        rl = ReaderLedger(store, store.load_config())
        blk = rl._foreshadow_block(w, None, 10)
        assert "蚀晶钥匙" in blk
        assert "冷却" in blk or "无实质推进" in blk

    @pytest.mark.p2
    def test_ledger_stalled_off(self, store):
        cfg = store.load_config()
        cfg.ledger_stalled = False
        w = _mk_world(_fs("蚀晶钥匙", 1, 20))
        rl = ReaderLedger(store, cfg)
        items = rl._commitment_items(w, 10)
        assert items[0]["state"] == "planted"


class TestSemanticCheck:
    @pytest.mark.p2
    def test_semantic_progress(self):
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.chat_json.return_value = {"semantic_progress": True,
                                      "reason": "正文用碎晶引路推进了钥匙线索"}
        r = semantic_foreshadow_check(llm, "蚀晶钥匙", "他把碎晶渣单独收起，一路被引向墟底。", 1, 20, 10)
        assert r["semantic_progress"] is True

    @pytest.mark.p2
    def test_semantic_not_progress(self):
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.chat_json.return_value = {"semantic_progress": False, "reason": "与钥匙无关"}
        r = semantic_foreshadow_check(llm, "蚀晶钥匙", "他在铁匠铺敲铁。", 1, 20, 10)
        assert r["semantic_progress"] is False

    @pytest.mark.p2
    def test_semantic_exception_downgrade(self):
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.chat_json.side_effect = Exception("llm fail")
        r = semantic_foreshadow_check(llm, "蚀晶钥匙", "正文", 1, 20, 10)
        assert r["semantic_progress"] is False
