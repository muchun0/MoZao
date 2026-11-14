# -*- coding: utf-8 -*-
"""章末状态「章号倒退」护栏（P1）。

`chapter_state.json` 是全书单条"最近一章"快照，没有版本维度：旧章被改写时若直接
覆盖，会把全书承接点倒退。本文件只测护栏判定与状态回传，不涉及结算口径。

marker 说明：账本不属于结算/鉴权等 P0 门禁范围，故标 P1。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                          # noqa: E402
from novel_gen.pipeline.chapter_ledger import (                   # noqa: E402
    sync_after_rewrite, write_chapter_state_guarded)
from novel_gen.pipeline.manager import NovelPipeline              # noqa: E402
from novel_gen.store.project import ProjectStore                  # noqa: E402
from novel_gen.store.schemas import Chapter                       # noqa: E402

PID = "p2"


def _cfg(**kw) -> NovelConfig:
    cfg = NovelConfig(title="账本护栏测试书", genre="玄幻", total_words=10000,
                      chapter_words=2500)
    cfg.pitfall_reconcile = False
    cfg.book_memory = False
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def store(tmp_path):
    return ProjectStore.create(tmp_path / PID, _cfg())


@pytest.mark.p1
def test_backward_not_overwritten_and_warns(store, caplog):
    """①倒退：不覆盖全局快照，返回 skipped_backward，warning 带溯源字段。"""
    store.save_chapter_state({"chapter": 5, "numbers": {"今日可死次数": "2"}})

    with caplog.at_level(logging.WARNING):
        status, detail = write_chapter_state_guarded(
            store, {"chapter": 3, "numbers": {}}, source="rewrite_sync")

    assert status == "skipped_backward"
    assert detail == {"target": 3, "current": 5, "source": "rewrite_sync"}
    # 较新快照原样保留
    assert store.load_chapter_state()["chapter"] == 5
    assert store.load_chapter_state()["numbers"] == {"今日可死次数": "2"}
    text = caplog.text
    assert "章号倒退" in text
    assert store.dir.name in text  # project_id（store.dir.name）
    assert "target=3" in text and "current=5" in text
    assert "source=rewrite_sync" in text


@pytest.mark.p1
def test_forward_write_keeps_working(store):
    """②正常前进：行为不变，照旧整条覆盖写入。"""
    store.save_chapter_state({"chapter": 2, "numbers": {"a": "1"}})

    status, detail = write_chapter_state_guarded(
        store, {"chapter": 3, "numbers": {"a": "2"}}, source="web_generation")

    assert status == "written"
    assert detail == {"target": 3, "current": 2, "source": "web_generation"}
    assert store.load_chapter_state() == {"chapter": 3, "numbers": {"a": "2"}}


@pytest.mark.p1
def test_empty_ledger_first_chapter_written(store):
    """③空账本首章：无既有记录时正常写入；同章再写按幂等覆盖。"""
    assert store.load_chapter_state() is None

    status, detail = write_chapter_state_guarded(
        store, {"chapter": 1, "numbers": {}}, source="web_generation")
    assert status == "written" and detail["current"] == 0
    assert store.load_chapter_state()["chapter"] == 1

    # 章号相等：不倒退，按原有覆盖语义写入（由上层 committer 负责幂等短路）
    status, _ = write_chapter_state_guarded(
        store, {"chapter": 1, "numbers": {"b": "9"}}, source="web_generation")
    assert status == "written"
    assert store.load_chapter_state()["numbers"] == {"b": "9"}


@pytest.mark.p1
def test_rewrite_old_chapter_reports_backward_skip(monkeypatch, store):
    """④R1 改写旧章：ledger 顶层状态体现"因倒退跳过"，快照不倒退。"""
    cfg = _cfg(character_state_tracking=False, reveal_tracking=False,
               reader_ledger=False, chapter_state_tracking=True)
    store.save_chapter(Chapter(no=1, title="第1章",
                               content="陆沉守碑至天明，雾散了一半。" * 20))
    store.save_chapter_state({"chapter": 5, "numbers": {}})
    pipe = NovelPipeline(cfg, store, MagicMock())
    # 摘要重生成走 LLM，这里不测摘要，直接返回空（sync 内部会跳过空摘要）
    monkeypatch.setattr(pipe.chapter_writer, "_gen_summary", lambda *a, **k: "")
    monkeypatch.setattr(
        "novel_gen.pipeline.chapter_state.extract_chapter_state",
        lambda *a, **k: {"chapter": 1, "numbers": {}})

    res = sync_after_rewrite(store, pipe, 1, version=2)

    assert res["chapter_state"] == "skipped_backward"
    assert res["commit"]["chapter_state_status"] == "skipped_backward"
    assert store.load_chapter_state()["chapter"] == 5
