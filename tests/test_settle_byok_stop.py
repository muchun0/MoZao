# -*- coding: utf-8 -*-
"""阶段 0 回归：BYOK 项目不被平台余额误停（webapp/core.py _settle_incremental）。

背景：旧实现只看 `new_bal <= 0` 就返回停止，导致「自备 Key + 平台余额 0」的
用户明明不消耗平台额度，却被中断生成。本用例锁定修正后的停止口径。
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
from webapp import core as server


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="BYOK测试书", total_words=10000, genre="玄幻")
    s = ProjectStore.create(tmp_path / "p", cfg)
    s.save_chapter(Chapter(no=1, title="第一章", content="内容" * 1000))  # 2000 字
    return s


def _patch_db(monkeypatch, balance: float) -> None:
    """把 core 依赖的 db 层换成内存桩，余额固定为给定值。"""
    monkeypatch.setattr(server.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(server.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": balance})
    monkeypatch.setattr(server.db, "update_project_price", lambda pid, price: None)
    monkeypatch.setattr(server.db, "change_balance", lambda uid, delta, note: None)
    monkeypatch.setattr(server, "_grant_consume_commission", lambda *a, **k: None)


def test_byok_zero_balance_not_stopped(store, monkeypatch):
    """自备 Key + 平台余额 0：不扣费、不停止。"""
    _patch_db(monkeypatch, balance=0.0)
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: True)
    stop, why = server._settle_incremental(store)
    assert stop is False
    assert why == ""


def test_platform_zero_balance_stopped(store, monkeypatch):
    """平台付费 + 余额 0：返回停止并给出余额原因。"""
    _patch_db(monkeypatch, balance=0.0)
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: False)
    stop, why = server._settle_incremental(store)
    assert stop is True
    assert "余额" in why


def test_byok_sufficient_balance_not_stopped(store, monkeypatch):
    """自备 Key + 余额充足：正常不停止。"""
    _patch_db(monkeypatch, balance=100.0)
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: True)
    stop, why = server._settle_incremental(store)
    assert stop is False
    assert why == ""
