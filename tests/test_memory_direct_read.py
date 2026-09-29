# -*- coding: utf-8 -*-
"""P0-1 前文直读测试（P1）。

覆盖：默认开启近章正文直读、只读最近 N 章、长度截断、关闭开关行为与旧版一致、
配置字段 save/load 持久化。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    s = ProjectStore.create(tmp_path / "m", cfg)
    return s


def _save_chapters(store, pairs: list[tuple[int, str, str]]):
    """模拟真实链路：落章节正文 + 摘要写回记忆库（append_summary）。"""
    for no, content, summary in pairs:
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=content, summary=summary))
        store.append_summary(no, summary)


_CH1 = "第一章正文：" + "、".join(f"甲事节点{i}" for i in range(400))   # 内容多样，避免触发防重复检测
_CH2 = "第二章正文：" + "、".join(f"乙事节点{i}" for i in range(400))


@pytest.mark.p1
def test_direct_read_default_on(store):
    """默认（chapters=1）时：含最近一章正文尾部，且不含更早章正文；摘要仍在。"""
    _save_chapters(store, [
        (1, _CH1, "第一章摘要"),
        (2, _CH2, "第二章摘要"),
    ])
    ctx = Memory(store, 5).build_context(None, up_to_chapter=3)
    assert "L4.5·近章正文直读" in ctx
    assert "乙事节点399" in ctx            # 最近一章正文尾部被直读
    assert "第二章摘要" in ctx              # L4 摘要仍在
    assert "甲事节点" not in ctx           # 只直读最近 1 章（第 1 章正文不进 ctx）


@pytest.mark.p1
def test_direct_read_two_chapters(store):
    """chapters=2 时：最近 2 章正文均直读。"""
    cfg = store.load_config()
    cfg.memory_direct_read_chapters = 2
    store.save_config(cfg)
    _save_chapters(store, [
        (1, _CH1, "s1"),
        (2, _CH2, "s2"),
    ])
    ctx = Memory(store, 5).build_context(None, up_to_chapter=3)
    assert "甲事节点" in ctx
    assert "乙事节点399" in ctx


@pytest.mark.p1
def test_direct_read_disabled(store):
    """chapters=0 时：不注入直读块，行为与旧版一致（仅摘要）。"""
    cfg = store.load_config()
    cfg.memory_direct_read_chapters = 0
    store.save_config(cfg)
    _save_chapters(store, [(1, "正文内容" * 200, "s1")])
    ctx = Memory(store, 5).build_context(None, up_to_chapter=2)
    assert "L4.5·近章正文直读" not in ctx
    assert "s1" in ctx


@pytest.mark.p1
def test_direct_read_char_limit(store):
    """chars 截断：只取章节尾部指定字符数，开头部分不注入。"""
    cfg = store.load_config()
    cfg.memory_direct_read_chars = 100
    store.save_config(cfg)
    head = "开" * 3000
    tail = "尾部标记ZZZ"
    _save_chapters(store, [(1, head + tail, "s1")])
    ctx = Memory(store, 5).build_context(None, up_to_chapter=2)
    assert "尾部标记ZZZ" in ctx
    assert head not in ctx          # 超出 chars 的开头部分被截断


@pytest.mark.p1
def test_direct_read_config_persist(store):
    """新配置字段 save/load 往返一致。"""
    cfg = store.load_config()
    assert cfg.memory_direct_read_chapters == 1
    assert cfg.memory_direct_read_chars == 1200  # REQ-token优化 P0-5：2000→1200 降输入
    cfg.memory_direct_read_chapters = 3
    cfg.memory_direct_read_chars = 800
    store.save_config(cfg)
    cfg2 = store.load_config()
    assert cfg2.memory_direct_read_chapters == 3
    assert cfg2.memory_direct_read_chars == 800
