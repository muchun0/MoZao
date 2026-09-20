# -*- coding: utf-8 -*-
"""结算逻辑单元测试（webapp/core.py _compute_settle / _settle_incremental / _settle_price）。

用真实 ProjectStore（临时目录）+ monkeypatch 掉 db 层，不触碰真实资金。
覆盖：统一计算口径、计费封顶、用户手写字数扣除、增量结算幂等、
P0 资金安全（结算基线先落盘再扣费，崩溃后不重扣）、BYOK 免费。
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
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: False)
    monkeypatch.setattr(server, "_grant_consume_commission", lambda *a, **k: None)

    def _charge(uid, delta, note):
        calls["charge"].append(delta)

    monkeypatch.setattr(server.db, "change_balance", _charge)
    return calls


# ---------- _compute_settle 统一计算 ----------

def test_compute_settle_basic(store):
    calc = server._compute_settle(store)
    assert calc["ai_words"] == 2000
    assert calc["target_ai_words"] == 10000
    assert calc["billable_words"] == 2000
    assert calc["price"] == pytest.approx(0.4, rel=0.02)  # 2000/10000 * 2 元/万字


def test_compute_settle_cap(store):
    """AI 多写超过目标字数 → 按目标 AI 字数封顶计费。"""
    for i in range(2, 8):
        store.save_chapter(Chapter(no=i, title=f"第{i}章", content="内容" * 1000))
    calc = server._compute_settle(store)
    assert calc["ai_words"] == 14000
    assert calc["billable_words"] == 10000
    assert calc["price"] == pytest.approx(2.0, rel=0.02)


def test_compute_settle_user_words(store, fake_db):
    """用户手写正文不计费：ai_words = 总字数 - user_words（config 记录的原字数）。"""
    store.set_user_words(4000)  # 创建时记录手写 4000 字
    store.save_chapter(Chapter(no=2, title="第二章", content="AI内容" * 1000))  # "AI内容"=4字 ×1000=4000
    calc = server._compute_settle(store)
    # 总 6000 = 2000(第一章) + 4000(第二章)；ai = 6000 - 4000 = 2000
    assert calc["ai_words"] == 2000
    assert calc["billable_words"] == 2000


# ---------- _settle_incremental 增量结算 ----------

def test_incremental_charge_once(store, fake_db):
    """首次结算扣一次费；无新章节再结算不重复扣。"""
    ok, why = server._settle_incremental(store)
    assert ok is False and why == ""
    assert fake_db["charge"] == [-0.4]
    sp = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert sp["status"] == "incremental"
    assert sp["settled_ai_words"] == 2000
    # 幂等：无新增章节
    server._settle_incremental(store)
    assert fake_db["charge"] == [-0.4]


def test_incremental_baseline_written_before_charge(store, fake_db, monkeypatch):
    """P0 资金安全：结算基线先落盘再扣费——扣费失败后重跑不重扣。"""
    def _boom(uid, delta, note):
        raise RuntimeError("charge failed")

    monkeypatch.setattr(server.db, "change_balance", _boom)
    ok, why = server._settle_incremental(store)
    assert ok is False
    sp = store.dir / "settle.json"
    assert sp.exists(), "扣费前必须已落盘结算基线（宁可漏扣不重扣）"
    data = json.loads(sp.read_text(encoding="utf-8"))
    assert data["settled_ai_words"] == 2000
    # 恢复扣费后重跑：基线已推进，不应再次计费
    monkeypatch.setattr(server.db, "change_balance",
                        lambda uid, delta, note: fake_db["charge"].append(delta))
    server._settle_incremental(store)
    assert fake_db["charge"] == [], "崩溃重跑不应重复扣费"


def test_incremental_balance_exhausted(store, fake_db, monkeypatch):
    """余额不足以扣到 0 并返回 should_stop。"""
    bal = {"v": 0.2}
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": bal["v"]})

    def _charge(uid, delta, note):
        bal["v"] = round(bal["v"] + delta, 4)

    monkeypatch.setattr(server.db, "change_balance", _charge)
    ok, why = server._settle_incremental(store)
    assert ok is True
    assert "余额" in why


# ---------- _settle_price 整批结算 ----------

def test_settle_price_byok_free(store, fake_db, monkeypatch):
    """BYOK 项目整批结算不扣费。"""
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: True)
    server._settle_price(store)
    sp = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert sp["mode"] == "user_llm_free"
    assert sp["final_price"] == 0.0
    assert fake_db["charge"] == []


def test_settle_price_skip_when_incremental_done(store, fake_db):
    """增量结算已落盘 → 整批结算跳过，不双扣。"""
    server._settle_incremental(store)
    server._settle_price(store)
    assert fake_db["charge"] == [-0.4]


def test_settle_price_idempotent_marker(store, fake_db, monkeypatch):
    """P0：整批结算先落盘 charging 标记再扣费。"""
    def _boom(uid, delta, note):
        raise RuntimeError("charge failed")

    monkeypatch.setattr(server.db, "change_balance", _boom)
    sp = store.dir / "settle.json"
    if sp.exists():  # 清掉增量结算基线，走整批路径
        sp.unlink()
    server._settle_price(store)
    data = json.loads(sp.read_text(encoding="utf-8"))
    assert data["status"] == "charging"
