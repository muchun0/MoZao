# -*- coding: utf-8 -*-
"""R26 剧情走向候选 P0 专项测试。

覆盖：3~5 条契约 / 卡点上下文注入 / 计费（kind=branch，一次、不双扣、失败不计费）/
BYOK 0 元 / 便签 v2 迁移与上限 / 自然键幂等与并发 / 便签读取删除 / 旧入口冻结且免费 /
apply_branch 写盘失败显式报错 / 越权 / 资金对账。纯 store + 假 LLM，不依赖真实模型。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store import continue_drafts, inline_drafts
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from webapp import branch_prompt, branch_service, core, inline_service, settle_ledger

PID = "p"


# ---------- fixtures ----------

@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=50000, chapter_words=2500, genre="玄幻")
    store = ProjectStore.create(tmp_path / PID, cfg)
    store.save_chapter(Chapter(no=1, title="第1章", content="他推门进院，檐下积水映着灯影。" * 5))
    return store


@pytest.fixture
def wallet(monkeypatch):
    state = {"balance": 100.0, "charge": [], "price": 0.0}
    monkeypatch.setattr(settle_ledger.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(settle_ledger.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(settle_ledger.db, "get_user",
                        lambda uid: {"id": uid, "balance": state["balance"]})
    monkeypatch.setattr(settle_ledger.db, "change_balance",
                        lambda uid, delta, note: (
                            state["charge"].append(delta),
                            state.update(balance=round(state["balance"] + delta, 4))))
    monkeypatch.setattr(settle_ledger.db, "update_project_price",
                        lambda pid, price: state.update(price=price))
    monkeypatch.setattr(settle_ledger, "_grant_commission", lambda *a, **k: None)
    monkeypatch.setattr(core, "_project_use_user_llm", lambda s: False)
    return state


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """不连 MySQL：开关走内存字典，全局覆盖不生效。"""
    overrides = {"branches_billing_enabled": False}
    monkeypatch.setattr(inline_service, "_flag", lambda key, default: overrides.get(key, default))
    monkeypatch.setattr(branch_service, "apply_global_overrides", lambda cfg: None)
    return overrides


class FakeLLM:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.last = None

    def chat(self, system, user, **kwargs):
        self.calls += 1
        self.last = (system, user, kwargs)
        if not self.payloads:
            return "[]"
        return self.payloads.pop(0)


_DIRECTIONS = json.dumps([
    {"direction": "收玉牌", "summary": "把玉牌交给师父，牵出旧案线索。",
     "source_tag": "foreshadow", "tags": ["玉牌", "旧案"]},
    {"direction": "当面对峙", "summary": "与师兄当场对质，冲突直接激化。",
     "source_tag": "conflict", "tags": ["对峙"]},
    {"direction": "身份反转", "summary": "师父的身份被揭穿，局势倒转。",
     "source_tag": "twist", "tags": ["揭秘"]},
    {"direction": "换场追查", "summary": "连夜下山追查，节奏加快。",
     "source_tag": "pacing", "tags": ["追查"]},
], ensure_ascii=False)


def _payload(store, count=4, token="bt1", stuck="", stuck_text=""):
    return {"ch": 1, "count": count, "stuck_type": stuck,
            "stuck_text": stuck_text, "client_token": token}


def _ledger(store) -> dict:
    return json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))


def _billing_on(_flags):
    _flags["branches_billing_enabled"] = True


# ---------- 生成契约 ----------

@pytest.mark.p0
def test_generate_returns_direction_fields_and_no_charge_by_default(store, wallet):
    res = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    assert 3 <= len(res["items"]) <= 5
    for item in res["items"]:
        assert item["id"] and item["direction"] and item["summary"]
        assert item["source_tag"] in branch_prompt.SOURCE_TAGS
        assert isinstance(item["tags"], list)
    assert res["charged"] == 0.0 and res["billing"] is False
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


@pytest.mark.p0
def test_generate_charges_once_kind_branch_and_replays_token(store, wallet, _flags):
    _billing_on(_flags)
    llm = FakeLLM(_DIRECTIONS)
    payload = _payload(store, token="same")
    first = branch_service.generate(store, llm, project_id=PID, uid=1, payload=payload)
    assert first["charged"] > 0 and first["billing"] is True
    entries = _ledger(store)["entries"]
    assert len(entries) == 1
    assert next(iter(entries)).startswith("branch:")
    assert all(not k.startswith("inline:") for k in entries)

    replay = branch_service.generate(store, llm, project_id=PID, uid=1, payload=payload)
    assert replay["replayed"] is True and replay["gen_id"] == first["gen_id"]
    assert llm.calls == 1
    assert len(_ledger(store)["entries"]) == 1
    assert len(wallet["charge"]) == 1


@pytest.mark.p0
def test_generate_empty_output_not_charged(store, wallet, _flags):
    _billing_on(_flags)
    with pytest.raises(branch_service.BranchError) as ei:
        branch_service.generate(store, FakeLLM("not json", "[]"), project_id=PID, uid=1,
                                payload=_payload(store))
    assert ei.value.status_code == 422 and ei.value.code == "LLM_EMPTY"
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


@pytest.mark.p0
def test_generate_insufficient_balance_when_billing_on(store, wallet, _flags):
    _billing_on(_flags)
    wallet["balance"] = 0.0
    with pytest.raises(branch_service.BranchError) as ei:
        branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                payload=_payload(store))
    assert ei.value.status_code == 402 and ei.value.code == "INSUFFICIENT_BALANCE"
    assert not (store.dir / "settle.json").exists()


@pytest.mark.p0
def test_generate_byok_zero_charge_but_audit_entry(store, wallet, _flags, monkeypatch):
    _billing_on(_flags)
    monkeypatch.setattr(core, "_project_use_user_llm", lambda s: True)
    res = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    assert res["charged"] == 0.0
    entry = next(iter(_ledger(store)["entries"].values()))
    assert entry["mode"] == "user_llm" and entry["charged"] == 0.0


@pytest.mark.p0
def test_bad_stuck_type_rejected_without_call(store, wallet):
    llm = FakeLLM(_DIRECTIONS)
    with pytest.raises(branch_service.BranchError) as ei:
        branch_service.generate(store, llm, project_id=PID, uid=1,
                                payload=_payload(store, stuck="nonsense"))
    assert ei.value.status_code == 400 and llm.calls == 0


# ---------- 上下文注入 ----------

@pytest.mark.p1
def test_foreshadow_and_commitment_context_injected(store, wallet, monkeypatch):
    monkeypatch.setattr(
        "novel_gen.pipeline.consistency_report.build_foreshadow",
        lambda s, cfg=None, chapter_no=None: [
            {"name": "半枚玉牌", "state": "overdue", "state_label": "逾期未收",
             "buried_ch": 1, "reveal_ch": 2}])
    store.save_pending_commitments(1, {"missed": ["把玉牌交给师父"], "must_carry_to_next": []})
    llm = FakeLLM(_DIRECTIONS)
    branch_service.generate(store, llm, project_id=PID, uid=1,
                            payload=_payload(store, stuck="foreshadow"))
    _, user, _ = llm.last
    assert "半枚玉牌" in user
    assert "把玉牌交给师父" in user
    assert "伏笔该收了" in user


@pytest.mark.p1
def test_context_blocks_degrade_when_missing(store, wallet, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no ledger")
    monkeypatch.setattr("novel_gen.pipeline.consistency_report.build_foreshadow", boom)
    llm = FakeLLM(_DIRECTIONS)
    res = branch_service.generate(store, llm, project_id=PID, uid=1, payload=_payload(store))
    assert len(res["items"]) == 4
    assert "未回收伏笔" not in llm.last[1]


# ---------- 便签 ----------

@pytest.mark.p0
def test_adopt_creates_note_without_charge_and_dedups(store, wallet, _flags):
    _billing_on(_flags)
    gen = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    body_before = store.load_chapter(1).content
    entries_before = dict(_ledger(store)["entries"])
    item_id = gen["items"][0]["id"]

    first = branch_service.adopt(store, project_id=PID, ch_no=1,
                                 payload={"gen_id": gen["gen_id"], "item_id": item_id})
    second = branch_service.adopt(store, project_id=PID, ch_no=1,
                                  payload={"gen_id": gen["gen_id"], "item_id": item_id})
    assert first["duplicated"] is False and second["duplicated"] is True
    assert first["draft_id"] == second["draft_id"]
    notes = continue_drafts.list_notes(store, 1)
    assert len(notes) == 1
    assert notes[0]["direction"] == gen["items"][0]["direction"]
    # 正文零改动、账本零新增、不二次扣费
    assert store.load_chapter(1).content == body_before
    assert _ledger(store)["entries"] == entries_before
    assert len(wallet["charge"]) == 1


@pytest.mark.p0
def test_adopt_concurrent_only_one_note(store, wallet):
    gen = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    item_id = gen["items"][0]["id"]
    results, errors = [], []

    def _run():
        try:
            results.append(branch_service.adopt(store, project_id=PID, ch_no=1,
                                                payload={"gen_id": gen["gen_id"],
                                                         "item_id": item_id}))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert len({r["draft_id"] for r in results}) == 1
    assert len(continue_drafts.list_notes(store, 1)) == 1


@pytest.mark.p0
def test_adopt_rejects_wrong_chapter_and_expired(store, wallet):
    gen = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    with pytest.raises(branch_service.BranchError) as ei:
        branch_service.adopt(store, project_id=PID, ch_no=2,
                             payload={"gen_id": gen["gen_id"], "item_id": "d1"})
    assert ei.value.status_code == 400
    item = inline_drafts.find_by_gen_id(store, gen["gen_id"])
    state = inline_drafts.load_state(store)
    for row in state["items"]:
        if row.get("gen_id") == gen["gen_id"]:
            row["expires_at"] = "2000-01-01T00:00:00Z"
    inline_drafts.save_state(store, state)
    assert item is not None
    with pytest.raises(branch_service.BranchError) as ei2:
        branch_service.adopt(store, project_id=PID, ch_no=1,
                             payload={"gen_id": gen["gen_id"], "item_id": "d1"})
    assert ei2.value.status_code == 410


@pytest.mark.p0
def test_note_remove_only_touches_note(store, wallet):
    gen = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    note = branch_service.adopt(store, project_id=PID, ch_no=1,
                                payload={"gen_id": gen["gen_id"], "item_id": "d1"})
    assert branch_service.remove_note(store, 1, note["draft_id"])["deleted"] is True
    assert continue_drafts.list_notes(store, 1) == []
    with pytest.raises(branch_service.BranchError) as ei:
        branch_service.remove_note(store, 1, note["draft_id"])
    assert ei.value.status_code == 404


@pytest.mark.p0
def test_notes_v1_migrates_to_v2_and_keeps_content(store):
    path = store.dir / "continue_drafts.json"
    path.write_text(json.dumps({"1": {"direction": "escalate", "label": "冲突升级",
                                      "preview": "旧草稿正文", "applied_at": 1700000000}},
                               ensure_ascii=False), encoding="utf-8")
    notes = continue_drafts.list_notes(store, 1)
    assert len(notes) == 1
    assert notes[0]["summary"] == "旧草稿正文" and notes[0]["origin"] == "legacy"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["chapters"]["1"]["items"]


@pytest.mark.p1
def test_notes_cap_evicts_oldest_per_chapter(store):
    for i in range(12):
        continue_drafts.add_note(store, 1, {"direction": f"方向{i}", "summary": f"说明{i}"})
    notes = continue_drafts.list_notes(store, 1)
    assert len(notes) == continue_drafts.MAX_PER_CHAPTER
    assert all(n["direction"] != "方向0" and n["direction"] != "方向1" for n in notes)
    data = json.loads((store.dir / "continue_drafts.json").read_text(encoding="utf-8"))
    assert len(data["chapters"]["1"]["_evicted"]) == 2


# ---------- 旧入口 / 写盘失败 ----------

def _old_branch_json():
    return json.dumps([{"direction": "escalate", "label": "冲突升级",
                        "preview": "冲突直接爆发的正文段落"}], ensure_ascii=False)


@pytest.mark.p0
def test_old_continue_branches_contract_and_free(store, wallet, monkeypatch):
    from webapp.routers import projects_chapters as pc
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: store)
    monkeypatch.setattr(pc, "_open_pipeline",
                        lambda s, trace_id="": SimpleNamespace(llm=FakeLLM(_old_branch_json()),
                                                              trace_id="t"))
    res = pc.continue_branches(PID, {"chapter_no": 1}, user={"id": 1})
    assert res["branches"][0]["direction"] == "escalate"
    assert res["branches"][0]["label"] == "冲突升级"
    assert res["branches"][0]["preview"]
    # 旧入口不计费
    res2 = pc.apply_branch(PID, {"chapter_no": 1, "direction": "escalate",
                                 "label": "冲突升级", "preview": "旧入口草稿"},
                           user={"id": 1})
    assert res2["saved"] is True
    notes = continue_drafts.list_notes(store, 1)
    assert notes and notes[0]["origin"] == "tools"
    assert not (store.dir / "settle.json").exists()
    assert wallet["charge"] == []


@pytest.mark.p0
def test_apply_branch_write_failure_returns_error(store, wallet, monkeypatch):
    from webapp.routers import projects_chapters as pc
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: store)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(store, "save_continue_draft", boom)
    with pytest.raises(HTTPException) as ei:
        pc.apply_branch(PID, {"chapter_no": 1, "preview": "正文"}, user={"id": 1})
    assert ei.value.status_code == 500
    assert "DRAFT_WRITE_FAILED" in str(ei.value.detail)


@pytest.mark.p0
def test_adopt_write_failure_returns_error(store, wallet, monkeypatch):
    gen = branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                  payload=_payload(store))
    monkeypatch.setattr(continue_drafts, "add_note",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(branch_service.BranchError) as ei:
        branch_service.adopt(store, project_id=PID, ch_no=1,
                             payload={"gen_id": gen["gen_id"], "item_id": "d1"})
    assert ei.value.status_code == 500 and ei.value.code == "WRITE_FAILED"
    assert continue_drafts.list_notes(store, 1) == []


# ---------- 越权 / 资金对账 ----------

@pytest.mark.p0
def test_router_forbidden_blocks_before_llm(store, wallet, monkeypatch):
    from webapp.routers import projects_branches as pb

    def _deny(*a, **k):
        raise HTTPException(status_code=403, detail="无权限")
    monkeypatch.setattr(pb, "_check_project_access", _deny)
    with pytest.raises(HTTPException) as ei:
        pb.branches_generate(PID, 1, req=_payload(store), user={"id": 999})
    assert ei.value.status_code == 403
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


@pytest.mark.p0
def test_money_reconciles_with_ledger_after_three_batches(store, wallet, _flags):
    _billing_on(_flags)
    for i in range(3):
        branch_service.generate(store, FakeLLM(_DIRECTIONS), project_id=PID, uid=1,
                                payload=_payload(store, token=f"t{i}"))
    entries = _ledger(store)["entries"]
    assert len(entries) == 3
    assert all(k.startswith("branch:") for k in entries)
    ledger_sum = round(sum(float(e["charged"]) for e in entries.values()), 2)
    paid = round(-sum(wallet["charge"]), 2)
    assert abs(ledger_sum - paid) < 0.005
    assert abs(ledger_sum - float(wallet["price"])) < 0.005
