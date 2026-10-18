# -*- coding: utf-8 -*-
"""P1-1 Reflexion 反思笔记专项测试（docs/REQ-门禁质检正向影响.md P1-1）。

覆盖：
- 反思账本：record_reflection 入账 / 空文本不入账 / 容量上限（保留最近 N 条）
- 注入块：reflection_block 章节过滤（chapter<=当前）、组装格式、空账本返回空串
- 触发：QualityChecker._record_reflection 走 LLM → memory.record_reflection（正常 + 异常降级）
- 开关：reflexion_note=False 时关闭（由 config 默认 + 触发条件 guard）
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
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="反思测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _memory(store):
    return BookMemory(store, store.load_config())


def _qc(store, llm, memory=None):
    cfg = store.load_config()
    return QualityChecker(cfg, llm, store, trace=None, guard=None, memory=memory)


# ---------- 反思账本（book_memory 层）----------

def test_record_reflection_and_block(store):
    mem = _memory(store)
    assert mem.record_reflection(1, "错在哪：节奏拖沓；下次怎么写对：加快事件推进") is True
    blk = mem.reflection_block(2)
    assert "本作反思笔记" in blk
    assert "第1章教训" in blk
    assert "节奏拖沓" in blk
    # 空文本不入账
    assert mem.record_reflection(1, "   ") is False


def test_reflection_block_chapter_filter(store):
    mem = _memory(store)
    mem.record_reflection(3, "第3章教训A")
    mem.record_reflection(1, "第1章教训B")
    # chapter=2 只读到 chapter<=2 的（即第1章）
    blk = mem.reflection_block(2)
    assert "第1章教训" in blk
    assert "第3章教训A" not in blk
    # chapter=3 读到全部（默认最近 2 条）
    blk3 = mem.reflection_block(3)
    assert "第3章教训A" in blk3
    assert "第1章教训B" in blk3


def test_reflection_empty_returns_blank(store):
    mem = _memory(store)
    assert mem.reflection_block(1) == ""


def test_reflection_cap_keeps_recent(store):
    mem = _memory(store)
    for i in range(1, 20):
        mem.record_reflection(i, f"教训{i}")
    refs = mem.load().get("reflections") or []
    assert len(refs) == mem._REFLEXION_CAP == 8
    assert refs[-1]["chapter"] == 19  # 保留最近
    assert refs[0]["chapter"] == 12


# ---------- 触发（QualityChecker._record_reflection）----------

def test_record_reflection_uses_llm_and_memory(store):
    llm = MagicMock()
    llm.chat.return_value = "错在哪：机制数字方向写错；下次怎么写对：结算后回写账本"
    mem = MagicMock()
    qc = _qc(store, llm, memory=mem)
    qc._record_reflection(2, "正文片段", {
        "passed": False,
        "issues": [{"type": "机制数字方向违规", "detail": "蚀化值回退"}],
    })
    llm.chat.assert_called_once()
    mem.record_reflection.assert_called_once()
    args = mem.record_reflection.call_args[0]
    assert args[0] == 2
    assert "错在哪" in args[1]


def test_record_reflection_exception_silent(store):
    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("llm 挂了")
    mem = MagicMock()
    qc = _qc(store, llm, memory=mem)
    # 不抛异常、不阻塞（§11.6 降级留日志）
    qc._record_reflection(2, "正文", {"passed": False, "issues": []})
    mem.record_reflection.assert_not_called()


def test_reflexion_note_flag_default_true():
    cfg = NovelConfig(title="x")
    assert cfg.reflexion_note is True
