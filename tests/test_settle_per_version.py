# -*- coding: utf-8 -*-
"""版本级结算测试（webapp/settle_ledger.py，P0 资金链路）。

核心口径回归：
- 一个产物版本 = 一次结算，按该版本 AI 实际生成字数扣费（同章重写要再扣一次）；
- 幂等键 `{kind}:{key}:v{v}` 保证重启/重入不双扣；
- 人工编辑（source=user）与存量版本（source=legacy）不计费；
- 自备 Key（user_llm）不计费但仍写账本供审计；
- 余额不足只扣到 0，差额记 debt.json，不自动补扣。
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
from webapp import settle_ledger

PID = "p"


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=50000, genre="玄幻")
    return ProjectStore.create(tmp_path / PID, cfg)


@pytest.fixture
def wallet(monkeypatch):
    """内存钱包：记录扣费流水，余额随扣费递减。"""
    state = {"balance": 100.0, "charge": [], "price": 0.0}
    monkeypatch.setattr(settle_ledger.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(settle_ledger.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(settle_ledger.db, "get_user", lambda uid: {"id": uid, "balance": state["balance"]})

    def _charge(uid, delta, note):
        state["charge"].append(delta)
        state["balance"] = round(state["balance"] + delta, 4)

    monkeypatch.setattr(settle_ledger.db, "change_balance", _charge)
    monkeypatch.setattr(settle_ledger.db, "update_project_price",
                        lambda pid, price: state.update(price=price))
    # 返佣依赖 core，测试中打桩避免耦合
    monkeypatch.setattr(settle_ledger, "_grant_commission", lambda *a, **k: None)
    return state


def _write_chapter(store, no: int, content: str, source: str = "ai") -> int:
    """写一章并返回分配的版本号（写前取快照由调用方负责）。"""
    before = settle_ledger.snapshot_chapter_versions(store, no)
    ver = store.save_chapter(Chapter(no=no, title=f"第{no}章", content=content), source=source)
    assert ver is not None
    settle_ledger.settle_chapter_versions(store, no, before, mode="platform",
                                          hq=False, project_id=PID)
    return ver


def _ledger(store) -> dict:
    return json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))


# ---------- 章节按版本计费 ----------

def test_first_version_charged_by_words(store, wallet):
    """首版 2000 字 → 按 2 元/万字扣 0.4 元。"""
    before = settle_ledger.snapshot_chapter_versions(store, 1)
    store.save_chapter(Chapter(no=1, title="第一章", content="内容" * 1000))
    res = settle_ledger.settle_chapter_versions(store, 1, before, mode="platform",
                                                hq=False, project_id=PID)
    assert len(res) == 1
    assert res[0]["charged"] == pytest.approx(0.4, abs=0.01)
    assert wallet["charge"] == [pytest.approx(-0.4, abs=0.01)]
    assert _ledger(store)["entries"]["chapter:1:v1"]["words"] == 2000


def test_rewrite_charged_again(store, wallet):
    """同章重写产生新版本 → 再扣一次（旧口径「重写免费」已被反转）。"""
    _write_chapter(store, 1, "内容" * 1000)   # 2000 字
    _write_chapter(store, 1, "改写" * 1000)   # 2000 字（本节同字数，便于断言金额）
    ledger = _ledger(store)
    assert set(ledger["entries"]) == {"chapter:1:v1", "chapter:1:v2"}
    assert ledger["billed_price"] == pytest.approx(0.8, abs=0.02)
    assert len(wallet["charge"]) == 2


def test_identical_content_no_version_no_charge(store, wallet):
    """内容未变 → save_chapter 幂等返回 None，不产生版本、不扣费。"""
    _write_chapter(store, 1, "内容" * 1000)
    before = settle_ledger.snapshot_chapter_versions(store, 1)
    assert store.save_chapter(Chapter(no=1, title="第一章", content="内容" * 1000)) is None
    res = settle_ledger.settle_chapter_versions(store, 1, before, mode="platform",
                                                hq=False, project_id=PID)
    assert res == []
    assert len(wallet["charge"]) == 1


def test_user_source_not_charged(store, wallet):
    """人工编辑（source=user）不计费。"""
    before = settle_ledger.snapshot_chapter_versions(store, 1)
    store.save_chapter(Chapter(no=1, title="第一章", content="我手写的" * 100), source="user")
    res = settle_ledger.settle_chapter_versions(store, 1, before, mode="platform",
                                                hq=False, project_id=PID)
    assert res == []
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


def test_legacy_version_not_charged(store, wallet):
    """存量单版本章节首次保存：旧 HEAD 归档为 legacy v1，不补扣；只扣新版本 v2。"""
    # 直接写 HEAD（模拟升级前的存量章节，未走版本库）
    (store.chapters_dir).mkdir(parents=True, exist_ok=True)
    store.chapter_path(1).write_text(
        json.dumps({"no": 1, "title": "第一章", "content": "旧内容" * 1000}, ensure_ascii=False),
        encoding="utf-8")
    before = settle_ledger.snapshot_chapter_versions(store, 1)
    store.save_chapter(Chapter(no=1, title="第一章", content="新内容" * 1000))
    res = settle_ledger.settle_chapter_versions(store, 1, before, mode="platform",
                                                hq=False, project_id=PID)
    assert [r["entry"] for r in res] == ["chapter:1:v2"]
    assert "chapter:1:v1" not in _ledger(store)["entries"]


def test_user_llm_zero_charge_but_entry(store, wallet):
    """自备 Key：charged=0，但仍写账本供审计。"""
    before = settle_ledger.snapshot_chapter_versions(store, 1)
    store.save_chapter(Chapter(no=1, title="第一章", content="内容" * 1000))
    res = settle_ledger.settle_chapter_versions(store, 1, before, mode="user_llm",
                                                hq=False, project_id=PID)
    assert res[0]["charged"] == 0.0
    assert res[0]["due"] == 0.0
    assert wallet["charge"] == []
    entry = _ledger(store)["entries"]["chapter:1:v1"]
    assert entry["mode"] == "user_llm"
    assert entry["words"] == 2000


def test_settle_version_idempotent(store, wallet):
    """同一版本重复结算（重启/重入）不双扣。"""
    settle_ledger.settle_version(store, "chapter", "1", 1, 2000, mode="platform",
                                 hq=False, project_id=PID)
    again = settle_ledger.settle_version(store, "chapter", "1", 1, 2000, mode="platform",
                                         hq=False, project_id=PID)
    assert again["duplicate"] is True
    assert again["charged"] == 0.0
    assert len(wallet["charge"]) == 1
    assert _ledger(store)["billed_price"] == pytest.approx(0.4, abs=0.01)


def test_zero_words_skipped(store, wallet):
    """无 AI 正文（0 字）不写账本、不扣费。"""
    res = settle_ledger.settle_version(store, "chapter", "1", 1, 0, mode="platform",
                                       hq=False, project_id=PID)
    assert res["charged"] == 0.0
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


def test_insufficient_balance_charges_to_zero_and_records_debt(store, wallet):
    """余额 0.1 < 应付 0.4 → 只扣 0.1，记欠费 0.3，返回应停止。"""
    wallet["balance"] = 0.1
    res = settle_ledger.settle_version(store, "chapter", "1", 1, 2000, mode="platform",
                                       hq=False, project_id=PID)
    assert res["charged"] == pytest.approx(0.1, abs=0.01)
    assert wallet["balance"] == pytest.approx(0.0, abs=0.01)
    assert res["should_stop"] is True
    debt = json.loads((store.dir / "debt.json").read_text(encoding="utf-8"))
    assert debt["owe"] == pytest.approx(0.3, abs=0.01)


def test_project_price_synced(store, wallet):
    """项目累计金额（DB）随账本 billed_price 同步。"""
    _write_chapter(store, 1, "内容" * 1000)
    assert wallet["price"] == pytest.approx(0.4, abs=0.01)


# ---------- 无版本库产物（0.5 临时通道） ----------

def test_settle_product_same_content_duplicate(store, wallet):
    """同内容重复产出不重复收费。"""
    sha = settle_ledger.content_digest("世界观内容")
    first = settle_ledger.settle_product(store, "world", "all", 500, mode="platform",
                                         hq=False, project_id=PID, content_sha=sha)
    second = settle_ledger.settle_product(store, "world", "all", 500, mode="platform",
                                          hq=False, project_id=PID, content_sha=sha)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(wallet["charge"]) == 1


def test_settle_product_new_content_allocates_next_version(store, wallet):
    """内容变化 → 版本号自增并再结算一次。"""
    settle_ledger.settle_product(store, "outline", "all", 1000, mode="platform",
                                 hq=False, project_id=PID,
                                 content_sha=settle_ledger.content_digest("v1"))
    res = settle_ledger.settle_product(store, "outline", "all", 1000, mode="platform",
                                       hq=False, project_id=PID,
                                       content_sha=settle_ledger.content_digest("v2"))
    assert res["entry"] == "outline:all:v2"
    assert set(_ledger(store)["entries"]) == {"outline:all:v1", "outline:all:v2"}


def test_settle_product_volume_keyed_by_volume(store, wallet):
    """分卷按卷号独立记账，互不干扰幂等键。"""
    settle_ledger.settle_product(store, "volume", "1", 300, mode="platform", hq=False,
                                 project_id=PID, content_sha=settle_ledger.content_digest("a"))
    settle_ledger.settle_product(store, "volume", "2", 300, mode="platform", hq=False,
                                 project_id=PID, content_sha=settle_ledger.content_digest("b"))
    entries = set(_ledger(store)["entries"])
    assert entries == {"volume:1:v1", "volume:2:v1"}
