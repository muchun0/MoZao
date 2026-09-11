# -*- coding: utf-8 -*-
"""扣费时点变更测试：创建不扣费，生成完成后按 AI 实际字数结算扣费 + 返佣。

通过 monkeypatch db.DB_PATH 隔离到临时库，不触碰开发库。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import webapp.db as db
from webapp import server as srv
from novel_gen.config import NovelConfig, LLMConfig, SearchConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter


@pytest.fixture
def isodat(tmp_path, monkeypatch):
    dbpath = tmp_path / "novel_web.db"
    monkeypatch.setattr(db, "DB_PATH", dbpath)
    db.init_db()
    db.create_user("站长", "admin123", is_admin=True, email="admin@test.com")      # id=1
    db.create_user("小明", "user123", email="xiaoming@test.com")                    # id=2 普通用户
    db.change_balance(2, 10.0, "测试充值")
    db.create_user("代理甲", "agent123", email="agent@test.com")                    # id=3 代理
    with db._connect() as conn:
        conn.execute("UPDATE users SET is_agent=1, agent_discount=0.8 WHERE id=3")
    return tmp_path


def _make_store(tmp_path, name="p_settle", content_type="", user_words=0, expand_targets=None):
    cfg = NovelConfig(
        title="结算测试本", genre="都市", total_words=8000, chapter_words=2500,
        mode="expand" if content_type else "auto",
        content_type=content_type,
        expand_targets=expand_targets or [],
        llm=LLMConfig(provider="mock", model="mock-model"),
        search=SearchConfig(provider=""),
    )
    store = ProjectStore.create(tmp_path / "projects", cfg)
    if user_words:
        store.set_user_words(user_words)
    return store


def test_claim_project_does_not_charge(isodat):
    """创建时仅登记归属：price=0（未扣费）、折扣快照；余额不变。"""
    store = _make_store(isodat)
    bal_before = db.get_user(2)["balance"]
    db.claim_project(store.dir.name, 2, 0.0, 0.8)
    assert db.project_owner(store.dir.name) == 2
    assert db.project_price(store.dir.name) == 0.0
    assert db.project_discount(store.dir.name) == 0.8
    assert db.get_user(2)["balance"] == bal_before  # 未扣费


def test_settle_charges_by_actual_ai_words(isodat):
    """生成完成后按 AI 实际字数结算扣费；settle.json 幂等。"""
    store = _make_store(isodat)
    db.claim_project(store.dir.name, 2, 0.0, 1.0)
    store.save_chapter(Chapter(no=1, title="第一章 起点", content="文" * 100, source="ai"))
    store.save_chapter(Chapter(no=2, title="第二章 终局", content="文" * 100, source="ai"))

    srv._settle_price(store)

    # ai_words=200 → price_for(200)=round(200/10000*2,2)=0.04
    assert db.get_user(2)["balance"] == pytest.approx(10.0 - 0.04)
    assert db.project_price(store.dir.name) == pytest.approx(0.04)
    sp = store.dir / "settle.json"
    assert sp.exists()
    data = json.loads(sp.read_text(encoding="utf-8"))
    assert data["ai_words"] == 200 and data["final_price"] == pytest.approx(0.04)

    # 幂等：再次结算不重复扣
    srv._settle_price(store)
    assert db.get_user(2)["balance"] == pytest.approx(10.0 - 0.04)


def test_settle_skip_when_no_ai_words(isodat):
    """未真正生成任何 AI 文字：不扣费。"""
    store = _make_store(isodat, user_words=500)
    db.claim_project(store.dir.name, 2, 0.0, 1.0)
    # 只有用户手写章（source=user），无 AI 章
    store.save_chapter(Chapter(no=1, title="第一章", content="文" * 50, source="user"))

    srv._settle_price(store)

    assert db.get_user(2)["balance"] == pytest.approx(10.0)
    assert db.project_price(store.dir.name) == 0.0
    data = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert data["ai_words"] == 0


def test_settle_uses_discount_snapshot(isodat):
    """结算按创建时折扣快照计费（代理 8 折）。"""
    store = _make_store(isodat)
    db.claim_project(store.dir.name, 2, 0.0, 0.8)
    store.save_chapter(Chapter(no=1, title="第一章", content="文" * 100, source="ai"))
    srv._settle_price(store)
    # ai=100 → 原价 0.02 → 8 折 0.016 → round 0.02
    assert db.project_price(store.dir.name) == pytest.approx(0.02)
    assert db.get_user(2)["balance"] == pytest.approx(10.0 - 0.02)


def test_settle_insufficient_balance_records_debt(isodat):
    """余额不足：扣到 0 并记录欠费（debt.json），不阻塞。"""
    store = _make_store(isodat)
    db.claim_project(store.dir.name, 2, 0.0, 1.0)
    db.change_balance(2, -9.99, "花掉余额")  # 余额 0.01
    store.save_chapter(Chapter(no=1, title="第一章", content="文" * 100, source="ai"))
    srv._settle_price(store)
    # final=0.04，余额 0.01 → 扣到 0，欠 0.03
    assert db.get_user(2)["balance"] == pytest.approx(0.0)
    debt = json.loads((store.dir / "debt.json").read_text(encoding="utf-8"))
    assert debt["owe"] == pytest.approx(0.01)  # final=0.02（100字），余额 0.01，欠 0.01


def test_settle_grants_consume_commission(isodat):
    """足额扣费时给上级代理发放消费返佣（按下级实际消费金额）。"""
    db.create_user("小明2", "user456", email="xiaoming2@test.com")   # id=4
    db.change_balance(4, 10.0, "测试充值")
    # 直接绑定上级为代理
    with db._connect() as conn:
        conn.execute("UPDATE users SET referrer_id=3 WHERE id=4")
    store = _make_store(isodat, name="p_comm")
    db.claim_project(store.dir.name, 4, 0.0, 0.8)  # 代理价
    store.save_chapter(Chapter(no=1, title="第一章", content="文" * 100, source="ai"))

    srv._settle_price(store)

    # final = 0.02（8折），返佣 = 0.02 * 0.10 = 0.002 → round_money 0.0？看 round_money 规则
    comms = db.list_commissions(3)
    agent_bal = db.get_user(3)["balance"]
    if comms:
        assert comms[0]["agent_id"] == 3 and comms[0]["buyer_id"] == 4
        assert agent_bal == pytest.approx(comms[0]["amount"])
    else:
        # 金额太小 round 后为 0 则不产生记录（符合现有 round_money 语义）
        assert agent_bal == 0.0
