# -*- coding: utf-8 -*-
"""R25 内联写作（基座）P0 专项测试。

覆盖：文本规范化/哈希、apply_inline_text 写入与乐观锁、source=inline 五处消费方、
结算免扣与账本分档淘汰、inline_drafts 幂等/作废、生成→采纳两段式、BYOK 审计、
失败不计费、并发采纳、全文检索。纯 store + 假 LLM，不依赖真实模型/MySQL。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store import inline_drafts
from novel_gen.store.inline_edit import (
    StaleBaseError, apply_inline_text, content_hash, normalize_text)
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, World
from webapp import core, inline_service, search_service, settle_ledger
from webapp.generate_service import guard_user_chapter, _is_user_chapter, _user_chapter_numbers

PID = "p"


# ---------- fixtures ----------

@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=50000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / PID, cfg)


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
    monkeypatch.setattr(settle_ledger.db, "list_user_projects", lambda uid: [PID])
    monkeypatch.setattr(settle_ledger, "_grant_commission", lambda *a, **k: None)
    monkeypatch.setattr(core, "_project_use_user_llm", lambda s: False)
    return state


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """测试不连 MySQL 读开关/全局覆盖：直接走默认值（与实现默认一致）。"""
    monkeypatch.setattr(inline_service, "_flag", lambda key, default: default)
    monkeypatch.setattr(inline_service, "apply_global_overrides", lambda cfg: None)


class FakeLLM:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def chat(self, system, user, **kwargs):
        self.calls += 1
        if not self.payloads:
            return "[]"
        return self.payloads.pop(0)


def _seed_chapter(store, content="第一段文字。\n第二段文字。", no=1, quality=None):
    store.save_chapter(Chapter(no=no, title=f"第{no}章", content=content), quality=quality)
    rec = store.load_chapter_versions()[str(no)]
    return rec["selected"]


def _payload(store, no=1, mode="continue", start=0, end=0, token="t1",
             candidates=2, target=200, instruction=""):
    ch = store.load_chapter(no)
    v = store.load_chapter_versions()[str(no)]["selected"]
    return {"ch": no, "mode": mode, "anchor": {"start": start, "end": end},
            "base_version": v, "base_hash": content_hash(ch.content),
            "client_token": token, "candidates": candidates,
            "target_words": target, "instruction": instruction}


def _ledger(store) -> dict:
    return json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))


# ---------- 规范化 / 写入 ----------

@pytest.mark.p0
def test_normalize_and_hash_crlf():
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"
    assert content_hash("a\r\nb") == content_hash("a\nb")


@pytest.mark.p0
def test_apply_inline_insert_exact_and_version_semantics(store):
    text = "开头。\r\n中间。\r\n结尾。"
    _seed_chapter(store, text)
    norm = normalize_text(text)
    pos = norm.index("中间")
    res = apply_inline_text(store, 1, pos, pos, "【插入】", base_version=1,
                            base_hash=content_hash(text), label="inline:adopt:ig_x")
    assert res["selected"] is True and res["created"] is True
    cur = store.load_chapter(1)
    assert cur.content == norm[:pos] + "【插入】" + norm[pos:]
    assert cur.source == "inline" and cur.summary_stale is True
    rec = store.load_chapter_versions()["1"]
    assert rec["selected"] == res["version"]
    assert rec["user_managed"] is True
    assert [h for h in rec["history"] if h["v"] == res["version"]][0]["source"] == "inline"
    assert [h for h in rec["history"] if h["v"] == res["version"]][0]["label"] == "inline:adopt:ig_x"


@pytest.mark.p0
def test_apply_inline_replace_only_selection_keeps_rest(store):
    base = "AAA\nBBB\nCCC"
    _seed_chapter(store, base)
    start, end = base.index("BBB"), base.index("BBB") + 3
    apply_inline_text(store, 1, start, end, "换代", base_version=1,
                      base_hash=content_hash(base))
    assert store.load_chapter(1).content == "AAA\n换代\nCCC"


@pytest.mark.p0
def test_apply_inline_stale_base_and_bad_anchor(store):
    _seed_chapter(store, "正文内容")
    with pytest.raises(StaleBaseError):
        apply_inline_text(store, 1, 0, 0, "x", base_version=1, base_hash="sha1:deadbeef")
    with pytest.raises(StaleBaseError):
        apply_inline_text(store, 1, 0, 0, "x", base_version=99,
                          base_hash=content_hash("正文内容"))
    from novel_gen.store.inline_edit import BadAnchorError
    with pytest.raises(BadAnchorError):
        apply_inline_text(store, 1, 999, 999, "x", base_version=1,
                          base_hash=content_hash("正文内容"))


@pytest.mark.p0
def test_inline_version_not_overridden_by_auto_select(store):
    """inline 版本置 user_managed 后，自动选分不得切回更高分的 AI 旧版。"""
    _seed_chapter(store, "旧稿", quality={"passed": True, "score": 95,
                                         "issue_count": 0, "auto_revised": False, "issues": []})
    apply_inline_text(store, 1, 0, 0, "内联", base_version=1,
                      base_hash=content_hash("旧稿"))
    rec = store.load_chapter_versions()["1"]
    assert rec["selected"] == 2
    assert store.load_chapter(1).content.startswith("内联")


# ---------- 手写守卫 ----------

@pytest.mark.p0
def test_guard_treats_inline_as_protected(store):
    _seed_chapter(store, "原正文")
    apply_inline_text(store, 1, 0, 0, "内联", base_version=1,
                      base_hash=content_hash("原正文"))
    assert _is_user_chapter(store, 1) is True
    assert 1 in _user_chapter_numbers(store)
    with pytest.raises(HTTPException) as ei:
        guard_user_chapter(store, "writer.chapter", {"scope": "single", "ch_no": 1})
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        guard_user_chapter(store, "writer.chapter", {"scope": "range", "from_ch": 1})


# ---------- 结算 ----------

@pytest.mark.p0
def test_settle_chapter_versions_skips_inline(store, wallet):
    _seed_chapter(store, "原正文")
    apply_inline_text(store, 1, 0, 0, "内联", base_version=1,
                      base_hash=content_hash("原正文"))
    before = settle_ledger.snapshot_chapter_versions(store, 1)
    results = settle_ledger.settle_chapter_versions(store, 1, before,
                                                    mode="platform", project_id=PID)
    assert results == []
    assert wallet["charge"] == []


@pytest.mark.p0
def test_ledger_eviction_protects_chapter_entries(store):
    now = int(time.time())
    entries = {f"chapter:{i}:v1": {"words": 10, "charged": 0.0, "ts": now} for i in range(1, 4)}
    for i in range(600):
        entries[f"inline:ig_{i}:v1"] = {"words": 10, "charged": 0.003, "ts": now}
    settle_ledger._write_ledger(store, {"entries": dict(entries)})
    saved = _ledger(store)["entries"]
    assert len(saved) == 500
    assert all(f"chapter:{i}:v1" in saved for i in range(1, 4))


@pytest.mark.p0
def test_ledger_eviction_drops_expired_inline_first(store):
    old = int(time.time()) - 72 * 3600
    entries = {f"chapter:{i}:v1": {"words": 10, "charged": 0.0, "ts": old} for i in range(1, 4)}
    for i in range(500):
        entries[f"inline:old{i}:v1"] = {"words": 10, "charged": 0.003, "ts": old}
    settle_ledger._write_ledger(store, {"entries": dict(entries)})
    saved = _ledger(store)["entries"]
    assert len(saved) == 500
    assert all(f"chapter:{i}:v1" in saved for i in range(1, 4))
    # 只淘汰了 3 条过期 inline（过期优先），其余 inline 仍在
    assert sum(1 for k in saved if k.startswith("inline:")) == 497


# ---------- 生成记录 ----------

def _item(idx: int, token: str = "", ch: int = 1, expired: bool = False) -> dict:
    now = time.time()
    return {"gen_id": f"ig_{idx}", "client_token": token or f"tok{idx}", "ch": ch,
            "mode": "continue", "anchor": {"start": 0, "end": 0},
            "candidates": [{"id": "c1", "text": "候选正文" * 10}],
            "words": 40, "charged": 0.01, "charge_entry": f"inline:ig_{idx}:v1",
            "adopted": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(now - 10 if expired else now + 3600))}


@pytest.mark.p0
def test_inline_drafts_token_replay_and_item_cap(store):
    item, replayed = inline_drafts.upsert_generation(store, _item(0, token="same"))
    assert replayed is False
    again, replayed2 = inline_drafts.upsert_generation(store, _item(1, token="same"))
    assert replayed2 is True and again["gen_id"] == item["gen_id"]
    for i in range(2, 60):
        inline_drafts.upsert_generation(store, _item(i))
    state = inline_drafts.load_state(store)
    assert len(state["items"]) == inline_drafts.MAX_ITEMS
    # token 独立于 items：即使 item 被挤出，token 仍可用于判过期
    assert inline_drafts.find_by_token(store, "same")["gen_id"] == "ig_0"


@pytest.mark.p0
def test_inline_drafts_invalidate_and_expire(store):
    inline_drafts.upsert_generation(store, _item(1))
    inline_drafts.upsert_generation(store, _item(2))
    assert inline_drafts.invalidate_chapter(store, 1) == 2
    assert inline_drafts.list_for_chapter(store, 1) == []
    inline_drafts.upsert_generation(store, _item(3, expired=True))
    assert inline_drafts.find_by_gen_id(store, "ig_3") is None


# ---------- 生成 → 采纳 ----------

_CANDS = json.dumps([
    {"text": "他推门进院，檐下积水映着灯影，脚步声在青石上散开。"},
    {"text": "院门吱呀一响，他停在门槛外，先听见屋里有人压低嗓子说话。"},
], ensure_ascii=False)


@pytest.mark.p0
def test_generate_charges_once_and_replays_token(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    llm = FakeLLM(_CANDS, _CANDS)
    payload = _payload(store, token="tok-a")
    first = inline_service.generate(store, llm, project_id=PID, uid=1, payload=payload)
    assert len(first["candidates"]) == 2 and first["replayed"] is False
    assert first["charged"] > 0
    assert llm.calls == 1
    words = sum(len(c["text"]) for c in first["candidates"])
    assert first["words"] > 0
    entries = _ledger(store)["entries"]
    assert len(entries) == 1 and next(iter(entries)).startswith("inline:")

    # 同 token 重放：不调 LLM、不扣费
    replay = inline_service.generate(store, llm, project_id=PID, uid=1, payload=payload)
    assert replay["replayed"] is True and llm.calls == 1
    assert len(_ledger(store)["entries"]) == 1
    assert len(wallet["charge"]) == 1
    assert words > 0


@pytest.mark.p0
def test_generate_empty_output_not_charged(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    llm = FakeLLM("[]", "[]")
    with pytest.raises(inline_service.InlineError) as ei:
        inline_service.generate(store, llm, project_id=PID, uid=1, payload=_payload(store))
    assert ei.value.status_code == 422 and ei.value.code == "LLM_EMPTY"
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


@pytest.mark.p0
def test_generate_compliance_blocked_not_charged(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    banned = json.dumps([{"text": "这一段是赤裸的性器官描写的违规示例，用于触发拦截校验。"}],
                        ensure_ascii=False)
    llm = FakeLLM(banned)
    with pytest.raises(inline_service.InlineError) as ei:
        inline_service.generate(store, llm, project_id=PID, uid=1,
                                payload=_payload(store, candidates=2))
    assert ei.value.status_code == 422 and ei.value.code == "COMPLIANCE_BLOCKED"
    assert wallet["charge"] == []


@pytest.mark.p0
def test_generate_bad_anchor_and_stale_base(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    llm = FakeLLM(_CANDS)
    bad = _payload(store, mode="rewrite", start=0, end=3)
    with pytest.raises(inline_service.InlineError) as ei:
        inline_service.generate(store, llm, project_id=PID, uid=1, payload=bad)
    assert ei.value.code == "BAD_ANCHOR" and wallet["charge"] == []
    stale = _payload(store)
    stale["base_hash"] = "sha1:deadbeef"
    with pytest.raises(inline_service.InlineError) as ei2:
        inline_service.generate(store, llm, project_id=PID, uid=1, payload=stale)
    assert ei2.value.status_code == 409 and llm.calls == 0


@pytest.mark.p0
def test_generate_byok_zero_charge_but_audit_entry(store, wallet, monkeypatch):
    monkeypatch.setattr(core, "_project_use_user_llm", lambda s: True)
    _seed_chapter(store, "前文内容。" * 10)
    res = inline_service.generate(store, FakeLLM(_CANDS), project_id=PID, uid=1,
                                  payload=_payload(store))
    assert res["charged"] == 0.0
    entry = next(iter(_ledger(store)["entries"].values()))
    assert entry["mode"] == "user_llm" and entry["charged"] == 0.0
    assert wallet["charge"] == []


@pytest.mark.p0
def test_adopt_writes_version_without_second_charge(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    base = store.load_chapter(1).content
    gen = inline_service.generate(store, FakeLLM(_CANDS), project_id=PID, uid=1,
                                  payload=_payload(store, start=len(base), end=len(base)))
    entries_before = dict(_ledger(store)["entries"])
    adopt = inline_service.adopt(store, project_id=PID, uid=1, ch_no=1, payload={
        "gen_id": gen["gen_id"], "candidate_id": "c1",
        "base_version": 1, "base_hash": content_hash(base), "client_token": "adopt-1"})
    assert adopt["selected"] is True and adopt["version"] == 2
    assert adopt["chapter"]["content"] == normalize_text(base) + gen["candidates"][0]["text"]
    assert adopt["chapter"]["summary_stale"] is True
    # 采纳不新增账本条目、不二次扣费
    assert _ledger(store)["entries"] == entries_before
    assert len(wallet["charge"]) == 1
    # 重复采纳（同 token）回放；不同 token 拒绝
    replay = inline_service.adopt(store, project_id=PID, uid=1, ch_no=1, payload={
        "gen_id": gen["gen_id"], "candidate_id": "c1",
        "base_version": 1, "base_hash": content_hash(base), "client_token": "adopt-1"})
    assert replay["replayed"] is True
    with pytest.raises(inline_service.InlineError) as ei:
        inline_service.adopt(store, project_id=PID, uid=1, ch_no=1, payload={
            "gen_id": gen["gen_id"], "candidate_id": "c2",
            "base_version": 1, "base_hash": content_hash(base), "client_token": "adopt-2"})
    assert ei.value.status_code == 409


@pytest.mark.p0
def test_adopt_after_version_switch_conflicts(store, wallet):
    """生成后切换版本再采纳 → 409，候选保留、不扣费。"""
    _seed_chapter(store, "第一版" * 10)
    base = store.load_chapter(1).content
    gen = inline_service.generate(store, FakeLLM(_CANDS), project_id=PID, uid=1,
                                  payload=_payload(store))
    store.save_chapter(Chapter(no=1, title="第1章", content="第二版" * 10), source="rewrite")
    with pytest.raises(inline_service.InlineError) as ei:
        inline_service.adopt(store, project_id=PID, uid=1, ch_no=1, payload={
            "gen_id": gen["gen_id"], "candidate_id": "c1",
            "base_version": 1, "base_hash": content_hash(base), "client_token": "a2"})
    assert ei.value.status_code == 409
    assert ei.value.code == "STALE_BASE_KEEP_CANDIDATES"
    assert inline_drafts.find_by_gen_id(store, gen["gen_id"])["adopted"] is None


@pytest.mark.p0
def test_generate_inflight_same_token_single_llm(store, wallet):
    """两个线程同 token 并发：只调用一次 LLM、只扣一次费。"""
    _seed_chapter(store, "前文内容。" * 10)
    payload = _payload(store, token="race")
    llm = FakeLLM(_CANDS)  # 仅一份；若被调用两次第二次会返回 "[]"
    results, errors = [], []

    def _run():
        try:
            results.append(inline_service.generate(store, llm, project_id=PID, uid=1,
                                                   payload=payload))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert llm.calls == 1
    assert len(wallet["charge"]) == 1
    assert {r["gen_id"] for r in results} and len({r["gen_id"] for r in results}) == 1


# ---------- 全文检索 ----------

@pytest.mark.p1
def test_search_body_and_settings(store):
    _seed_chapter(store, "他摸到腰间的铜铃，铃声很轻。", no=1)
    _seed_chapter(store, "铜铃再次响起。", no=2)
    res = search_service.search_project(store, q="铜铃", limit=10, offset=0, scope="body")
    assert [i["ch"] for i in res["items"]] == [1, 2]
    assert res["items"][0]["offset"] == store.load_chapter(1).content.index("铜铃")
    assert "铜铃" in res["items"][0]["snippet"]
    page = search_service.search_project(store, q="铜铃", limit=1, offset=1, scope="body")
    assert [i["ch"] for i in page["items"]] == [2]
    with pytest.raises(search_service.SearchError):
        search_service.search_project(store, q="铜", limit=10)


@pytest.mark.p1
def test_search_rejects_control_chars(store):
    with pytest.raises(search_service.SearchError):
        search_service.search_project(store, q="铜\x00铃", limit=10)


@pytest.mark.p1
def test_search_settings_scope(store):
    store.save_world(World(world_view="山门外的铜铃镇，终年雾气不散。"))
    res = search_service.search_project(store, q="铜铃", limit=10, scope="settings")
    assert res["items"] and res["items"][0]["kind"] == "settings"
    assert res["items"][0]["ch_title"] == "世界观"


# ---------- 越权 / 资金对账 / 并发 ----------

@pytest.mark.p0
def test_router_forbidden_blocks_before_llm(store, wallet, monkeypatch):
    from webapp.routers import projects_inline as pi

    def _deny(*a, **k):
        raise HTTPException(status_code=403, detail="无权限")

    monkeypatch.setattr(pi, "_check_project_access", _deny)
    _seed_chapter(store, "前文内容。" * 10)
    with pytest.raises(HTTPException) as ei:
        pi.inline_generate(PID, 1, req=_payload(store), user={"id": 999})
    assert ei.value.status_code == 403
    assert wallet["charge"] == []
    assert not (store.dir / "settle.json").exists()


@pytest.mark.p0
def test_money_reconciles_with_ledger_after_three_runs(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    for i in range(3):
        inline_service.generate(store, FakeLLM(_CANDS), project_id=PID, uid=1,
                                payload=_payload(store, token=f"t{i}"))
    entries = _ledger(store)["entries"]
    ledger_sum = round(sum(float(e["charged"]) for e in entries.values()), 2)
    paid = round(-sum(wallet["charge"]), 2)
    assert len(entries) == 3
    assert abs(ledger_sum - paid) < 0.005
    assert abs(ledger_sum - float(wallet["price"])) < 0.005


@pytest.mark.p0
def test_concurrent_adopt_only_one_version(store, wallet):
    _seed_chapter(store, "前文内容。" * 10)
    base = store.load_chapter(1).content
    gen = inline_service.generate(store, FakeLLM(_CANDS), project_id=PID, uid=1,
                                  payload=_payload(store, start=len(base), end=len(base)))
    ok, err = [], []

    def _adopt(token):
        try:
            ok.append(inline_service.adopt(store, project_id=PID, uid=1, ch_no=1, payload={
                "gen_id": gen["gen_id"], "candidate_id": "c1", "base_version": 1,
                "base_hash": content_hash(base), "client_token": token}))
        except Exception as exc:  # noqa: BLE001
            err.append(exc)

    threads = [threading.Thread(target=_adopt, args=(f"a{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(ok) == 1 and len(err) == 1
    assert err[0].status_code == 409
    assert len(store.load_chapter_versions()["1"]["versions"]) == 2
    assert len(wallet["charge"]) == 1


@pytest.mark.p1
def test_settle_audit_inline_association(store, wallet, capsys):
    from tools import settle_audit
    _seed_chapter(store, "前文内容。" * 10)
    base = store.load_chapter(1).content
    gen = inline_service.generate(store, FakeLLM(_CANDS), project_id=PID, uid=1,
                                  payload=_payload(store, start=len(base), end=len(base)))
    inline_service.adopt(store, project_id=PID, uid=1, ch_no=1, payload={
        "gen_id": gen["gen_id"], "candidate_id": "c1", "base_version": 1,
        "base_hash": content_hash(base), "client_token": "audit-adopt"})
    problems = settle_audit._audit_inline_versions(store, _ledger(store)["entries"])
    assert problems == []
    assert "计费关联 1 条完整" in capsys.readouterr().out
