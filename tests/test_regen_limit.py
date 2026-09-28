# -*- coding: utf-8 -*-
"""章节重生成每日限额（_check_regen_limit）回归测试。

背景：2026-09-20 路由拆分（commit e1c68d1）把 server.py 中
`REGEN_DAILY_LIMIT = 10` 的定义丢失，导致单章重写 / 从第 N 章重生成
两个接口每次调用都抛 NameError → 500（无日志、regen_count.json 不落盘）。
本测试覆盖限额正常路径 / 超限 429 / 损坏文件容错 / 跨天重置。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from webapp import core


def _store(tmp_path) -> ProjectStore:
    return ProjectStore.create(
        tmp_path / "p", NovelConfig(title="测试书", total_words=10000, genre="玄幻"))


def _count(store: ProjectStore, today: str) -> int:
    p = store.dir / "regen_count.json"
    if not p.exists():
        return 0
    import json
    return int(json.loads(p.read_text(encoding="utf-8")).get(today, 0))


@pytest.mark.p1
def test_first_call_writes_count(tmp_path):
    """首次调用不抛异常，且 regen_count.json 写入当日计数 1。"""
    store = _store(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    core._check_regen_limit(store, store.dir.name)  # 修复前此处 NameError -> 500
    assert _count(store, today) == 1


@pytest.mark.p1
def test_within_limit_ok(tmp_path):
    """连续调用至上限（10 次）均不抛异常。"""
    store = _store(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    for _ in range(core.REGEN_DAILY_LIMIT):
        core._check_regen_limit(store, store.dir.name)
    assert _count(store, today) == core.REGEN_DAILY_LIMIT


@pytest.mark.p1
def test_over_limit_raises_429(tmp_path):
    """第 11 次调用抛 HTTPException 429，且不再写入计数。"""
    store = _store(tmp_path)
    for _ in range(core.REGEN_DAILY_LIMIT):
        core._check_regen_limit(store, store.dir.name)
    with pytest.raises(HTTPException) as ei:
        core._check_regen_limit(store, store.dir.name)
    assert ei.value.status_code == 429
    today = datetime.now().strftime("%Y-%m-%d")
    assert _count(store, today) == core.REGEN_DAILY_LIMIT  # 超限不累加


@pytest.mark.p1
def test_corrupt_count_file_reset(tmp_path):
    """regen_count.json 损坏时从 0 重新计数，不抛异常。"""
    store = _store(tmp_path)
    (store.dir / "regen_count.json").write_text("{not-json", encoding="utf-8")
    core._check_regen_limit(store, store.dir.name)
    assert _count(store, datetime.now().strftime("%Y-%m-%d")) == 1


@pytest.mark.p1
def test_next_day_reset(tmp_path, monkeypatch):
    """当日达上限后，次日计数归零可继续。"""
    store = _store(tmp_path)

    class _FakeDT(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 9, 28, 12, 0, 0)

    monkeypatch.setattr(core, "datetime", _FakeDT)
    for _ in range(core.REGEN_DAILY_LIMIT):
        core._check_regen_limit(store, store.dir.name)
    with pytest.raises(HTTPException) as ei:
        core._check_regen_limit(store, store.dir.name)
    assert ei.value.status_code == 429

    class _FakeDT2(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 9, 29, 12, 0, 0)

    monkeypatch.setattr(core, "datetime", _FakeDT2)
    core._check_regen_limit(store, store.dir.name)  # 次日不抛
    assert _count(store, "2026-09-29") == 1
