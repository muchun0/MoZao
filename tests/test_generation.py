# -*- coding: utf-8 -*-
"""生成控制接口测试（P1）：整本重写（chapters/regenerate）接口语义。

覆盖：无大纲 400 守卫、有大纲时按 1..N 启动批量重写、并发槽位获取。
用 monkeypatch 直调路由函数，不真连 DB、不真起线程。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.routers import projects_generation as gen  # noqa: E402


@pytest.fixture
def fake_outline():
    o = MagicMock()
    o.chapters = [MagicMock(no=1), MagicMock(no=2), MagicMock(no=3)]
    return o


def _call_regenerate(project_id="p1", user=None):
    return gen.regenerate_all_chapters(project_id, user or {"username": "tester", "is_admin": True})


@pytest.mark.p1
def test_regenerate_no_outline_400(monkeypatch):
    """无大纲章节时拒绝整本重写。"""
    store = MagicMock()
    store.load_outline.return_value = None
    monkeypatch.setattr(gen, "_find_project", lambda pid: store)
    with pytest.raises(HTTPException) as ei:
        _call_regenerate()
    assert ei.value.status_code == 400


@pytest.mark.p1
def test_regenerate_empty_outline_400(monkeypatch):
    """大纲存在但无章节时同样拒绝。"""
    store = MagicMock()
    o = MagicMock()
    o.chapters = []
    store.load_outline.return_value = o
    monkeypatch.setattr(gen, "_find_project", lambda pid: store)
    with pytest.raises(HTTPException) as ei:
        _call_regenerate()
    assert ei.value.status_code == 400


@pytest.mark.p1
def test_regenerate_starts_batch_1_to_total(monkeypatch):
    """有大纲时启动 _run_batch(store, handle, 1, N)，并返回 start/end。"""
    store = MagicMock()
    store.dir.name = "p1"
    outline = MagicMock()
    outline.chapters = [MagicMock(no=i) for i in range(1, 6)]  # 5 章
    store.load_outline.return_value = outline
    monkeypatch.setattr(gen, "_find_project", lambda pid: store)

    handle = MagicMock()
    monkeypatch.setattr(gen, "_acquire_slot", lambda pid: handle)

    captured = {}

    class FakeThread:
        def __init__(self, target=None, args=None, daemon=False):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(gen.threading, "Thread", FakeThread)

    resp = _call_regenerate()
    assert resp["started"] is True
    assert resp["start"] == 1 and resp["end"] == 5
    assert captured["started"] is True
    assert captured["args"][1] is handle
    assert captured["args"][2] == 1 and captured["args"][3] == 5
    # 线程异常时释放槽位
    assert handle.thread is not None


@pytest.mark.p1
def test_regenerate_acquire_failure_releases_slot(monkeypatch):
    """线程创建抛异常时释放并发槽位并上抛。"""
    store = MagicMock()
    store.dir.name = "p1"
    outline = MagicMock()
    outline.chapters = [MagicMock(no=1)]
    store.load_outline.return_value = outline
    monkeypatch.setattr(gen, "_find_project", lambda pid: store)
    monkeypatch.setattr(gen, "_acquire_slot", lambda pid: MagicMock())

    def _boom(*a, **k):
        raise RuntimeError("thread boom")

    monkeypatch.setattr(gen.threading, "Thread", _boom)
    released = []
    monkeypatch.setattr(gen, "_release_slot", lambda pid: released.append(pid))
    with pytest.raises(RuntimeError):
        _call_regenerate()
    assert released == ["p1"]
