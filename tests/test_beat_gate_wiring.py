# -*- coding: utf-8 -*-
"""三期 F1 接线冒烟：write() 落盘后 beat_gate.json 被正确写入（P1）。

验证新能力"接线到主链路"（AGENTS.md §11.2）：不是只建了 beat_gate 函数，
而是 ChapterWriter.write() 真的在生成后调用它并落盘 beat_gate.json。
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
from novel_gen.store.schemas import Outline, ChapterPlan
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store_with_outline(tmp_path):
    cfg = NovelConfig(title="冒烟书", chapter_words=2500, total_words=10000)
    store = ProjectStore.create(tmp_path / "sm", cfg)
    # 预置大纲：第1章有 2 拍 character_moves（beat 权威来源）
    outline = Outline(
        title="冒烟书", intro="末世求生",
        chapters=[ChapterPlan(
            no=1,
            title="第一章 墟口初醒", goal="主角在墟口拾荒",
            core_event="主角遭遇灰卒被咬死",
            character_moves="主角在墟口拾荒遭遇灰卒被咬死→复活后砸碎灰卒核心",
            transition="", emotion="紧张", cliffhanger="他发现面板数字不对",
        )],
    )
    store.save_outline(outline)
    return store


@pytest.mark.p1
def test_write_writes_beat_gate(store_with_outline):
    """write() 后 beat_gate.json 落盘且 verdict 非 skip（有计划拍时真接线）。"""
    store = store_with_outline
    cfg = store.load_config()
    cfg.scene_split = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    # 正文覆盖两拍（墟口拾荒遇灰卒被咬死 + 复活砸碎灰卒核心）
    body = ("陆沉在墟口拾荒时遭遇灰卒，被咬喉致死。随后他复活过来，"
            "拿起铁锤砸碎了灰卒的核心。") * 200 + "【摘要】陆沉墟口遇灰卒"
    llm.chat.return_value = body
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    ch = writer.write(1, "第一章 墟口初醒", "主角在墟口拾荒", None, silent=True)
    assert ch.content
    bg = store.load_beat_gate().get("1")
    assert bg is not None, "beat_gate.json 未写入（接线失败）"
    assert bg["verdict"] in ("pass", "warn", "fail")
    assert bg["coverage"] is not None
    # REQ S3.1：write() 后承诺台账同样落盘（接线到主链路）
    assert store.load_pending_commitments().get("1") is not None


@pytest.mark.p1
def test_pending_commitments_wiring_and_compensation(store_with_outline):
    """S3.1 接线：write() 落承诺台账；补偿块方法从上一章台账生成漏兑现补偿。"""
    store = store_with_outline
    cfg = store.load_config()
    cfg.scene_split = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    body = ("陆沉在墟口拾荒时遭遇灰卒，被咬喉致死。随后他复活过来，"
            "拿起铁锤砸碎了灰卒的核心。") * 60 + "【摘要】摘要"
    llm.chat.return_value = body
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    writer.write(1, "第一章", "目标", None, silent=True)
    ledger = store.load_pending_commitments().get("1")
    assert ledger is not None, "承诺台账未落盘（S3.1 接线失败）"
    assert "committed" in ledger and "must_carry_to_next" in ledger
    # 补偿块：手工造一条含漏兑现的上一章台账 → 第2章补偿块非空
    store.save_pending_commitments(1, dict(ledger, missed=["主角逃出灰雾"], must_carry_to_next=["主角逃出灰雾"]))
    comp = writer._pending_compensation_block(2)
    assert "上章未兑现节点补偿" in comp and "主角逃出灰雾" in comp


@pytest.mark.p1
def test_beat_gate_switch_off_no_file(store_with_outline):
    """beat_gate=False → 不写 beat_gate.json（开关生效）。"""
    store = store_with_outline
    cfg = store.load_config()
    cfg.scene_split = False
    cfg.agent_guard.quality_check = False
    cfg.beat_gate = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.return_value = "陆沉在墟口拾荒遭遇灰卒被咬死，复活砸碎核心。" * 200 + "【摘要】摘要"
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    writer.write(1, "第一章", "目标", None, silent=True)
    assert store.load_beat_gate() == {}
