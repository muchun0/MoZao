# -*- coding: utf-8 -*-
"""结算账本 v1 → v2 迁移测试（存量项目兼容）。

约束（TECH §4.4 / docs/decisions.md）：
- v1 `settle.json` 迁移只搬基线（`settled_ai_words/settled_price` → legacy 字段），**不重扣**；
- 已是 v2 的项目，`_settle_price` 必须 no-op，绝不二次结算；
- 旧 `target_ai_words` 封顶只对存量兜底生效，版本级结算不再使用。
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
from webapp import core as server
from webapp import settle_ledger


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="存量书", total_words=10000, genre="玄幻")
    s = ProjectStore.create(tmp_path / "p", cfg)
    s.save_chapter(Chapter(no=1, title="第一章", content="内容" * 1000))
    return s


@pytest.fixture
def wallet(monkeypatch):
    state = {"balance": 100.0, "charge": []}
    monkeypatch.setattr(server.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(server.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": state["balance"]})
    monkeypatch.setattr(server.db, "update_project_price", lambda pid, price: None)
    monkeypatch.setattr(server, "_project_use_user_llm", lambda s: False)
    monkeypatch.setattr(server, "_grant_consume_commission", lambda *a, **k: None)

    def _charge(uid, delta, note):
        state["charge"].append(delta)
        state["balance"] = round(state["balance"] + delta, 4)

    monkeypatch.setattr(server.db, "change_balance", _charge)
    return state


def _write_v1(store, ai_words: int, price: float) -> None:
    (store.dir / "settle.json").write_text(json.dumps({
        "settled_ai_words": ai_words, "settled_price": price,
        "target_ai_words": 10000, "status": "incremental",
    }, ensure_ascii=False), encoding="utf-8")


def test_migrate_v1_no_recharge(store, wallet):
    """v1 → v2：迁移基线不扣费，legacy 字段保留旧已结算金额。"""
    _write_v1(store, ai_words=2000, price=0.4)
    ledger = settle_ledger.migrate_v1(store)
    assert wallet["charge"] == []
    assert ledger["legacy_ai_words"] == 2000
    assert ledger["legacy_billed_price"] == pytest.approx(0.4, abs=0.01)
    disk = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert disk["mode"] == settle_ledger.LEDGER_MODE
    assert disk["billed_price"] == 0.0  # 迁移不新增扣费
    assert disk["entries"] == {}


def test_settle_price_migrates_v1_without_charge(store, wallet):
    """`_settle_price` 遇到 v1 只做迁移，绝不重扣。"""
    _write_v1(store, ai_words=2000, price=0.4)
    server._settle_price(store)
    assert wallet["charge"] == []
    disk = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert disk["mode"] == settle_ledger.LEDGER_MODE


def test_settle_price_noop_when_v2_exists(store, wallet):
    """已有 v2 账本：`_settle_price` 直接返回，不产生扣费条目。"""
    settle_ledger.settle_version(store, "chapter", "1", 1, 2000, mode="platform",
                                 hq=False, project_id="p")
    before = set(json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))["entries"])
    server._settle_price(store)
    after = set(json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))["entries"])
    assert before == after
    assert wallet["charge"] == [pytest.approx(-0.4, abs=0.01)]


def test_corrupt_ledger_treated_as_settled(store, wallet):
    """账本损坏按已结算处理（宁可漏扣不重扣）：不扣费、重建空 v2 账本。"""
    (store.dir / "settle.json").write_text("{not json", encoding="utf-8")
    server._settle_price(store)
    assert wallet["charge"] == []
    disk = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))
    assert disk["mode"] == settle_ledger.LEDGER_MODE
    assert disk["entries"] == {}


def test_version_billing_ignores_target_cap(store, wallet):
    """版本级结算按实际生成字数计费，不再受 target_ai_words 封顶。"""
    words = 20000  # 目标只有 10000 字，旧口径会把计费字数封到 10000
    res = settle_ledger.settle_version(store, "chapter", "1", 1, words, mode="platform",
                                       hq=False, project_id="p")
    assert res["due"] == pytest.approx(4.0, abs=0.02)  # 20000/10000 * 2 元
    assert res["charged"] == pytest.approx(4.0, abs=0.02)
