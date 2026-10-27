# -*- coding: utf-8 -*-
"""结算逻辑单元测试（webapp/core.py `_compute_settle` / `_settle_incremental` / `_settle_price`）。

用真实 ProjectStore（临时目录）+ monkeypatch 掉 db 层，不触碰真实资金。

v2 口径（TECH-Agent用户化与版本化 §4.4）后本文件聚焦：
- `_compute_settle` 旧口径计算（仅存量项目兜底仍在用，故保留覆盖）；
- `_settle_incremental` 只做停止判定、不再扣费；
- `_settle_price` 只对「无任何账本」的历史项目兜底结算一次。
版本级结算见 test_settle_per_version.py / test_settle_migration.py。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
# 第三批拆分：结算逻辑位于 webapp/core.py（server.py 仅为组装壳）；core.db 即 webapp.db
from webapp import core as server
from webapp import settle_ledger


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, genre="玄幻")
    s = ProjectStore.create(tmp_path / "p", cfg)
    s.save_chapter(Chapter(no=1, title="第一章", content="内容" * 1000))  # 2000 字
    return s


@pytest.fixture
def fake_db(monkeypatch):
    """把 server 依赖的 db 层全部换成内存桩。"""
    calls = {"charge": []}
    monkeypatch.setattr(server.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(server.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": 100.0})
    monkeypatch.setattr(server.db, "update_project_price", lambda pid, price: None)
    monkeypatch.setattr(server.db, "project_price", lambda pid: 0.0)
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: False)
    monkeypatch.setattr(server, "_grant_consume_commission", lambda *a, **k: None)

    def _charge(uid, delta, note):
        calls["charge"].append(delta)

    monkeypatch.setattr(server.db, "change_balance", _charge)
    return calls


# ---------- _compute_settle 统一计算（存量兜底仍在使用） ----------

def test_compute_settle_basic(store):
    calc = server._compute_settle(store)
    assert calc["ai_words"] == 2000
    assert calc["target_ai_words"] == 10000
    assert calc["billable_words"] == 2000
    assert calc["price"] == pytest.approx(0.6, rel=0.02)  # 2000/10000 * 3 元/万字（统一单价）


def test_compute_settle_cap(store):
    """AI 多写超过目标字数 → 旧口径按目标 AI 字数封顶（仅存量兜底生效）。"""
    for i in range(2, 8):
        store.save_chapter(Chapter(no=i, title=f"第{i}章", content="内容" * 1000))
    calc = server._compute_settle(store)
    assert calc["ai_words"] == 14000
    assert calc["billable_words"] == 10000
    assert calc["price"] == pytest.approx(3.0, rel=0.02)  # 10000/10000 * 3 元


def test_compute_settle_user_words(store, fake_db):
    """用户手写正文不计费：ai_words = 总字数 - user_words（config 记录的原字数）。"""
    store.set_user_words(4000)  # 创建时记录手写 4000 字
    store.save_chapter(Chapter(no=2, title="第二章", content="AI内容" * 1000))  # "AI内容"=4字 ×1000=4000
    calc = server._compute_settle(store)
    # 总 6000 = 2000(第一章) + 4000(第二章)；ai = 6000 - 4000 = 2000
    assert calc["ai_words"] == 2000
    assert calc["billable_words"] == 2000


# ---------- _settle_incremental：只做停止判定 ----------

def test_incremental_does_not_charge_or_write_ledger(store, fake_db):
    """v2：中断判定不再扣费、不再写 settle.json（结算由版本提交点负责）。"""
    ok, why = server._settle_incremental(store)
    assert ok is False and why == ""
    assert fake_db["charge"] == []
    assert not (store.dir / "settle.json").exists()


def test_incremental_zero_balance_stops_for_platform(store, fake_db, monkeypatch):
    """平台付费项目余额 0 → 返回停止。"""
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": 0.0})
    ok, why = server._settle_incremental(store)
    assert ok is True
    assert "余额" in why


def test_incremental_zero_balance_keeps_byok_running(store, fake_db, monkeypatch):
    """自备 Key 项目余额 0 → 继续生成（阶段 0 修正）。"""
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": 0.0})
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: True)
    ok, why = server._settle_incremental(store)
    assert ok is False and why == ""


# ---------- _settle_price：仅存量项目兜底 ----------

def test_settle_price_legacy_charges_once(store, fake_db):
    """无任何账本的历史项目：兜底结算一次，再调用不重扣。"""
    server._settle_price(store)
    assert fake_db["charge"] == [pytest.approx(-0.6, abs=0.01)]
    ledger = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert ledger["mode"] == settle_ledger.LEDGER_MODE
    assert ledger["entries"]["legacy:all:v1"]["charged"] == pytest.approx(0.6, abs=0.01)
    # 已进入 v2：再调用 no-op
    server._settle_price(store)
    assert fake_db["charge"] == [pytest.approx(-0.6, abs=0.01)]


def test_settle_price_byok_free(store, fake_db, monkeypatch):
    """BYOK 项目兜底结算不扣费，但仍落账本（charged=0 供审计）。"""
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: True)
    server._settle_price(store)
    assert fake_db["charge"] == []
    ledger = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    entry = ledger["entries"]["legacy:all:v1"]
    assert entry["mode"] == "user_llm"
    assert entry["charged"] == 0.0
    assert ledger["billed_price"] == 0.0
