# -*- coding: utf-8 -*-
"""章节原子提交 + 投影一致性（REQ-借鉴webnovel-章节提交与投影一致性，A/B）专项测试。

覆盖（按 REQ §5 验收标准）：
- P0：提交原子性留痕（incomplete 记录失败项）、幂等（同章 committed 跳过 / force 重跑）、
      开关 disabled 时不落提交记录、store chapter_commit/projection_log 往返
- P1：投影日志、check_projection 检出落后视图（lagging）与同步（synced）

注意：纯 store 操作，不依赖真实 LLM；占位正文避开节拍词（§11.8）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                 # noqa: E402
from novel_gen.store.project import ProjectStore          # noqa: E402
from novel_gen.store.schemas import Chapter               # noqa: E402
from novel_gen.pipeline.commit import (                   # noqa: E402
    ChapterCommitter, check_projection, PROJECTION_VIEWS,
)


# ---------- fixtures ----------
@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _ok_action(store, name="summary"):
    """成功动作：写一条本章摘要（可被 check_projection 观测）。"""
    def fn(no):
        store.append_summary(no, f"第{no}章摘要")
    return name, fn


def _fail_action():
    """抛异常的动作（模拟账本写入失败）。"""
    def fn(_no):
        raise RuntimeError("模拟账本写入失败")
    return "broken", fn


def _false_action():
    """返回 False 的动作（视为失败）。"""
    def fn(_no):
        return False
    return "falsey", fn


# ---------- P0：原子提交 / 幂等 / 开关 ----------
@pytest.mark.p0
def test_commit_ok_records_done(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_ok_action(store))
    res = c.commit_chapter(1)
    assert res["status"] == "committed"
    assert res["done"] == ["summary"]
    assert res["failed"] == []
    # 提交记录落盘
    d = store.load_chapter_commit()
    assert d is not None
    assert d["chapters"]["1"]["status"] == "committed"
    assert d["chapters"]["1"]["done"] == ["summary"]


@pytest.mark.p0
def test_commit_idempotent_skips_committed(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_ok_action(store))
    assert c.commit_chapter(1)["status"] == "committed"
    # 非 force：已 committed → skipped，不重复执行
    res = c.commit_chapter(1)
    assert res["status"] == "skipped"
    # 摘要只写一次（幂等）
    assert len(store.load_summaries()) == 1


@pytest.mark.p0
def test_commit_force_reruns(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_ok_action(store))
    assert c.commit_chapter(1)["status"] == "committed"
    # force=True：允许重跑，摘要仍幂等覆盖为一条
    res = c.commit_chapter(1, force=True)
    assert res["status"] == "committed"
    assert len(store.load_summaries()) == 1


@pytest.mark.p0
def test_commit_incomplete_records_failure(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_ok_action(store))
    c.add_action(*_fail_action())
    res = c.commit_chapter(1)
    assert res["status"] == "incomplete"
    assert res["done"] == ["summary"]
    assert len(res["failed"]) == 1
    assert res["failed"][0]["name"] == "broken"
    assert "error" in res["failed"][0]
    # 提交记录反映 incomplete + 失败项（可被体检/重试定位）
    d = store.load_chapter_commit()
    assert d["chapters"]["1"]["status"] == "incomplete"
    assert d["chapters"]["1"]["failed"][0]["name"] == "broken"


@pytest.mark.p0
def test_commit_false_return_marks_failure(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_false_action())
    res = c.commit_chapter(1)
    assert res["status"] == "incomplete"
    assert res["failed"][0]["name"] == "falsey"


@pytest.mark.p0
def test_commit_disabled_falls_back_without_record(store):
    cfg = NovelConfig(title="t", genre="玄幻", chapter_commit=False)
    c = ChapterCommitter(store, cfg)
    c.add_action(*_ok_action(store))
    res = c.commit_chapter(1)
    assert res["status"] == "disabled"
    # disabled 不落提交记录，但动作仍执行（保持原行为）
    assert store.load_chapter_commit() is None
    assert len(store.load_summaries()) == 1


@pytest.mark.p0
def test_store_chapter_commit_roundtrip(store):
    data = {"chapters": {"1": {"status": "committed", "done": ["a"], "failed": []}},
            "updated_ch": 1}
    store.save_chapter_commit(data)
    loaded = store.load_chapter_commit()
    assert loaded["chapters"]["1"]["status"] == "committed"


# ---------- P1：投影日志 / 一致性体检 ----------
@pytest.mark.p1
def test_projection_log_written(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_ok_action(store))
    c.commit_chapter(1)
    log = store.load_projection_log()
    assert len(log) == 1
    assert log[0]["chapter"] == 1
    assert log[0]["status"] == "committed"
    assert log[0]["synced"] == ["summary"]


@pytest.mark.p1
def test_projection_log_incomplete_missing(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_fail_action())
    c.commit_chapter(1)
    log = store.load_projection_log()
    assert log[0]["status"] == "incomplete"
    assert log[0]["missing"] == ["broken"]


@pytest.mark.p1
def test_check_projection_lagging(store):
    # chapter_state 覆盖到第 2 章，基准第 5 章 → lagging
    store.save_chapter_state({"chapter": 2, "numbers": {}})
    rows = {r["view"]: r for r in check_projection(store, up_to_chapter=5)}
    assert rows["chapter_state"]["status"] == "lagging"
    assert rows["chapter_state"]["covered_ch"] == 2


@pytest.mark.p1
def test_check_projection_synced(store):
    # chapter_state / summaries 覆盖到基准第 3 章 → synced
    store.save_chapter_state({"chapter": 3, "numbers": {}})
    store.append_summary(3, "s3")
    rows = {r["view"]: r for r in check_projection(store, up_to_chapter=3)}
    assert rows["chapter_state"]["status"] == "synced"
    assert rows["summaries"]["status"] == "synced"


@pytest.mark.p1
def test_check_projection_unknown_views_no_crash(store):
    # character_states / book_memory 无法推断章号 → unknown，不误报
    rows = {r["view"]: r for r in check_projection(store, up_to_chapter=3)}
    assert rows["character_states"]["status"] == "unknown"
    assert rows["book_memory"]["status"] == "unknown"
    assert rows["chapter_commit"]["status"] == "unknown"  # 无提交记录


@pytest.mark.p1
def test_commit_visible_in_check_projection(store):
    c = ChapterCommitter(store, NovelConfig(title="t", genre="玄幻"))
    c.add_action(*_ok_action(store))
    c.commit_chapter(3)
    rows = {r["view"]: r for r in check_projection(store, up_to_chapter=3)}
    assert rows["chapter_commit"]["status"] == "synced"
    assert rows["summaries"]["status"] == "synced"


@pytest.mark.p1
def test_all_views_registered():
    # 体检视图清单非空且覆盖核心账本
    assert "chapter_state" in PROJECTION_VIEWS
    assert "reveal_status" in PROJECTION_VIEWS
    assert "reader_ledger" in PROJECTION_VIEWS or "foreshadow_commitment" in PROJECTION_VIEWS
