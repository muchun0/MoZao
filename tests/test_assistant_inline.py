# -*- coding: utf-8 -*-
"""R28 小墨AI 内联段落协同：上下文 span / 锚点落库 / 应用到正文（计费与越权）。

覆盖：
- `context.span_snapshot` 段落窗口、零宽扩段、issue evidence 最近命中与定位失败降级；
- `context.build_prompt` span 渲染分支（不再注入 <chapter_text>）、预算 600–2400；
- `assistant.service.send_message` 锚点入参与 `extra.anchor` 落库、漂移标记、越界 400；
- `assistant.inline_apply.apply_suggestion` 来源校验、单段/跨段、幂等重放、令牌复用、
  越权、写盘失败回滚、BYOK 0 元审计、校验失败不扣费。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.inline_edit import content_hash
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from webapp import core, core_settle, inline_service, settle_ledger
from webapp.assistant import context as ctx
from webapp.assistant import inline_apply, service

PID = "p"
PARA1 = "第一段：他推开门，巷口的风灌进来。"
PARA2 = "第二段：她数着手里的铜钱，指尖停在最冷的那一枚上。"
PARA3 = "第三段：远处传来钟声，低沉而缓慢。"
CONTENT = f"{PARA1}\n\n{PARA2}\n\n{PARA3}"
SUGGEST = "第二段：她捏着铜钱，指尖停在最冷的那一枚上，没有抬头。"


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, chapter_words=2000, genre="玄幻")
    s = ProjectStore.create(tmp_path / PID, cfg)
    s.save_chapter(Chapter(no=1, title="第1章", content=CONTENT))
    return s


@pytest.fixture
def wallet(monkeypatch):
    """隔离资金：余额、扣费流水、项目累计金额。"""
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
    return state


def _ledger(store) -> dict:
    p = store.dir / "settle.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"entries": {}}


def _version(store, no=1) -> int:
    return int(store.load_chapter_versions()[str(no)]["selected"])


def _anchor_of(store, needle: str, no=1) -> tuple[int, int]:
    text = store.load_chapter(no).content
    start = text.index(needle)
    return start, start + len(needle)


def _write_review(store, issues: list[dict], no: int = 1) -> None:
    (store.dir / "review.json").write_text(
        json.dumps([{"chapter": no, "issues": issues}], ensure_ascii=False), encoding="utf-8")


# ---------- 上下文 span ----------

@pytest.mark.p0
def test_span_snapshot_paragraph_window(store):
    start, end = _anchor_of(store, PARA2)
    span = ctx.span_snapshot(store, 1, {"start": start, "end": end})
    assert span["text"] == PARA2
    assert span["prev"] == PARA1 and span["next"] == PARA3
    assert span["index"] == 1 and span["total"] == 3
    assert span["stale"] is False


@pytest.mark.p0
def test_span_snapshot_zero_width_expands_to_paragraph(store):
    start, _ = _anchor_of(store, PARA2)
    span = ctx.span_snapshot(store, 1, {"start": start + 2, "end": start + 2})
    assert span["text"] == PARA2


@pytest.mark.p0
def test_span_issue_evidence_nearest_and_missing(store):
    _write_review(store, [
        {"level": "中改", "type": "节奏", "detail": "本段拖沓",
         "evidence": PARA2[:6], "fix_suggestion": "压缩动作链"},
        {"level": "小改", "type": "用词", "detail": "旧证据", "evidence": "正文里没有的句子"},
    ])
    start, end = _anchor_of(store, PARA2)
    span = ctx.span_snapshot(store, 1, {"start": start + 8, "end": end})
    assert len(span["issues"]) == 2
    assert span["issues"][0]["pos"] == start
    assert span["issues"][0]["suggestion"] == "压缩动作链"
    # 定位不到：只保留类型与改法，不报错、不给位置
    assert span["issues"][1]["pos"] is None


@pytest.mark.p0
def test_build_prompt_span_mode_uses_paragraph_window(store):
    start, end = _anchor_of(store, PARA2)
    snap = ctx.snapshot(store, 1, target_words=2000,
                        span={"start": start, "end": end, "text_sha": ""})
    positive = ctx.positive_snapshot(store, 1, store.load_config(), snap=snap)
    built = ctx.build_prompt("人设P", snap, positive, [], "这段哪里不好", "inline_span", 20000)
    assert built["context_mode"] == "span"
    assert built["budget_tier"] == built["level"]
    assert "<paragraph_context>" in built["user"]
    assert "<chapter_text>" not in built["user"]
    assert PARA1 in built["user"] and PARA3 in built["user"]
    assert "<answer_requirements>" in built["user"]
    # 预算折算：段落窗口不超过 2400 字
    assert ctx._span_budget(12000) == 2400
    assert ctx._span_budget(0) == 600


@pytest.mark.p0
def test_span_text_sha_mismatch_marks_stale(store):
    start, end = _anchor_of(store, PARA2)
    span = ctx.span_snapshot(store, 1, {"start": start, "end": end, "text_sha": "sha1:deadbeef"})
    assert span["stale"] is True


# ---------- service 锚点链路 ----------

class _FakeLLM:
    def __init__(self, answer: str = "这一段动作链偏长。"):
        self.answer = answer
        self.system = self.user = ""
        self.recorder = None
        self.guard = None

    def chat(self, system: str, user: str, max_tokens: int = 0) -> str:
        self.system, self.user = system, user
        return self.answer


def _wire_service(monkeypatch, llm) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(service.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(service.db, "recent_messages", lambda cid, limit=12: [])

    def _add_message(cid, role, content, extra=None):
        calls.append({"cid": cid, "role": role, "content": content, "extra": extra or {}})
        return len(calls)

    def _update_extra(mid, patch):
        if 0 < int(mid) <= len(calls):
            calls[int(mid) - 1]["extra"].update(patch or {})
        return True

    monkeypatch.setattr(service.db, "add_message", _add_message)
    monkeypatch.setattr(service.db, "update_message_extra", _update_extra)
    monkeypatch.setattr(service, "_user_llm_cfg", lambda uid: None)
    monkeypatch.setattr(service, "_server_api_key", lambda p: "server-key")
    monkeypatch.setattr(service, "apply_global_overrides", lambda cfg: None)
    monkeypatch.setattr(service, "load_persona", lambda: "人设：只做诊断")
    monkeypatch.setattr(service, "create_llm", lambda llm_cfg: llm)
    return calls


@pytest.fixture
def chat_store(store):
    cfg = store.load_config()
    cfg.assistant_require_user_key = False
    cfg.assistant_billing_enabled = False
    store.save_config(cfg)
    return store


@pytest.mark.p0
def test_send_message_records_anchor_and_span_mode(chat_store, monkeypatch):
    llm = _FakeLLM()
    calls = _wire_service(monkeypatch, llm)
    start, end = _anchor_of(chat_store, PARA2)
    user = {"id": 7, "is_admin": False}
    res = service.send_message(
        chat_store, user, {"id": 42, "user_id": 7}, "这段哪里不好", "inline_span",
        chapter_no=1,
        anchor={"ch": 1, "start": start, "end": end,
                "text_sha": ctx._sha1(PARA2), "issue_refs": ["节奏"]})
    assert res["context_mode"] == "span"
    assert res["anchor"]["start"] == start and res["anchor_stale"] is False
    # 段落窗口进入提示词，整章正文不再注入
    assert PARA2 in llm.user and "<chapter_text>" not in llm.user
    # 用户/助手消息 extra 均带 anchor（P1-2 历史回溯依赖）
    assert calls[0]["extra"]["anchor"]["ch"] == 1
    assert calls[1]["extra"]["anchor"]["end"] == end


@pytest.mark.p0
def test_send_message_anchor_out_of_range_400(chat_store, monkeypatch):
    _wire_service(monkeypatch, _FakeLLM())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        service.send_message(chat_store, {"id": 7, "is_admin": False},
                             {"id": 42, "user_id": 7}, "问", "inline_span",
                             chapter_no=1, anchor={"ch": 1, "start": 0, "end": 99999})
    assert ei.value.status_code == 400


# ---------- 应用到正文 ----------

@pytest.fixture
def apply_env(store, wallet, monkeypatch):
    """隔离会话/消息/锁/忙碌/计费模式：默认平台计费、章节未锁、项目空闲。"""
    state = {"conversation": {"id": 88, "user_id": 7, "project_id": store.dir.name},
             "message": None}
    start, end = _anchor_of(store, PARA2)
    state["message"] = {
        "id": 902, "conversation_id": 88, "role": "assistant",
        "content": f"## 诊断\n这一段的动作链偏长。\n<suggestion>{SUGGEST}</suggestion>",
        "extra": {"anchor": {"ch": 1, "start": start, "end": end}},
    }
    monkeypatch.setattr(inline_apply.db, "get_conversation",
                        lambda cid, pid: state["conversation"]
                        if int(cid) == 88 and pid == store.dir.name else None)
    monkeypatch.setattr(inline_apply.db, "get_message",
                        lambda mid: state["message"] if int(mid) == 902 else None)
    monkeypatch.setattr(core, "_is_locked", lambda s, kind, no: False)
    monkeypatch.setattr(inline_service, "_project_busy", lambda pid: False)
    monkeypatch.setattr(core_settle, "_settle_billing_mode", lambda s: "platform")
    state["anchor"] = (start, end)
    return state


def _apply_payload(store, state, *, text=SUGGEST, token="tk-1", edited=False,
                   start=None, end=None, base_version=None, base_hash=None) -> dict:
    s, e = state["anchor"]
    return {
        "conversation_id": 88, "message_id": 902,
        "anchor": {"start": s if start is None else start,
                   "end": e if end is None else end},
        "base_version": _version(store) if base_version is None else base_version,
        "base_hash": content_hash(store.load_chapter(1).content) if base_hash is None else base_hash,
        "text": text, "client_token": token, "edited": edited,
    }


@pytest.mark.p0
def test_apply_suggestion_writes_only_anchor_and_charges_once(store, apply_env, wallet):
    before = store.load_chapter(1).content
    res = inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                        payload=_apply_payload(store, apply_env))
    after = store.load_chapter(1).content
    assert after == before[:apply_env["anchor"][0]] + SUGGEST + before[apply_env["anchor"][1]:]
    assert res["version"] == 2 and res["replayed"] is False and res["charged"] > 0
    rec = store.load_chapter_versions()["1"]
    assert rec["selected"] == 2 and rec["user_managed"] is True
    entry = _ledger(store)["entries"]
    assert len(entry) == 1 and list(entry)[0].startswith("inline:apply:tk-1:v")
    assert len(wallet["charge"]) == 1


@pytest.mark.p0
def test_apply_suggestion_same_token_replays_without_double_charge(store, apply_env, wallet):
    payload = _apply_payload(store, apply_env)
    first = inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1, payload=payload)
    second = inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1, payload=payload)
    assert second["replayed"] is True and second["charged"] == 0
    assert second["version"] == first["version"]
    assert len(_ledger(store)["entries"]) == 1
    assert len(wallet["charge"]) == 1


@pytest.mark.p0
def test_apply_suggestion_token_reused_with_different_text_409(store, apply_env, wallet):
    inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                  payload=_apply_payload(store, apply_env))
    # 第二条消息：换文本、复用同一令牌
    apply_env["message"] = {
        "id": 902, "conversation_id": 88, "role": "assistant",
        "content": "改写二：她捏紧铜钱。", "extra": {"anchor": {
            "ch": 1, "start": apply_env["anchor"][0], "end": apply_env["anchor"][1]}},
    }
    with pytest.raises(inline_apply.ApplyError) as ei:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env, text="改写二：她捏紧铜钱。"))
    assert ei.value.code == "TOKEN_REUSED" and ei.value.status_code == 409
    assert len(_ledger(store)["entries"]) == 1 and len(wallet["charge"]) == 1


@pytest.mark.p0
def test_apply_suggestion_edited_or_mismatched_message_rejected(store, apply_env, wallet):
    with pytest.raises(inline_apply.ApplyError) as ei:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env, edited=True))
    assert ei.value.code == "MESSAGE_MISMATCH"
    with pytest.raises(inline_apply.ApplyError) as ei2:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env, text="消息里没有的句子"))
    assert ei2.value.code == "MESSAGE_MISMATCH"
    # 校验失败不扣费、不写盘
    assert _ledger(store)["entries"] == {} and wallet["charge"] == []
    assert store.load_chapter(1).content == CONTENT


@pytest.mark.p0
def test_apply_suggestion_cross_paragraph_400(store, apply_env, wallet):
    start, _ = _anchor_of(store, PARA1)
    _, end = _anchor_of(store, PARA3)
    # 消息锚点置空：本用例只验证「跨段」判定，不触发锚点一致性前置校验
    apply_env["message"]["extra"] = {}
    with pytest.raises(inline_apply.ApplyError) as ei:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env, start=start, end=end))
    assert ei.value.code == "CROSS_PARAGRAPH" and ei.value.status_code == 400
    assert _ledger(store)["entries"] == {} and wallet["charge"] == []


@pytest.mark.p0
def test_apply_suggestion_stale_base_409_no_charge(store, apply_env, wallet):
    with pytest.raises(inline_apply.ApplyError) as ei:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env, base_version=999))
    assert ei.value.code == "STALE_BASE" and ei.value.status_code == 409
    assert _ledger(store)["entries"] == {} and wallet["charge"] == []


@pytest.mark.p0
def test_apply_suggestion_forbidden_conversation_403(store, apply_env, wallet):
    apply_env["conversation"] = {"id": 88, "user_id": 99, "project_id": PID}
    with pytest.raises(inline_apply.ApplyError) as ei:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env))
    assert ei.value.code == "FORBIDDEN" and ei.value.status_code == 403
    assert _ledger(store)["entries"] == {} and wallet["charge"] == []


@pytest.mark.p0
def test_apply_suggestion_write_failure_rolls_back_ledger_and_balance(store, apply_env, wallet,
                                                                      monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(inline_apply, "apply_inline_text", _boom)
    with pytest.raises(inline_apply.ApplyError) as ei:
        inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                      payload=_apply_payload(store, apply_env))
    assert ei.value.code == "WRITE_FAILED" and ei.value.status_code == 500
    # 账本条目回滚、余额回到初始值、正文不变
    assert _ledger(store)["entries"] == {}
    assert wallet["balance"] == 100.0
    assert store.load_chapter(1).content == CONTENT


@pytest.mark.p1
def test_apply_suggestion_byok_charges_zero_but_audits(store, apply_env, wallet, monkeypatch):
    monkeypatch.setattr(core_settle, "_settle_billing_mode", lambda s: "user_llm")
    res = inline_apply.apply_suggestion(store, project_id=store.dir.name, uid=7, ch_no=1,
                                        payload=_apply_payload(store, apply_env))
    assert res["charged"] == 0.0
    entry = list(_ledger(store)["entries"].values())[0]
    assert entry["mode"] == "user_llm" and entry["charged"] == 0.0
    assert wallet["charge"] == []

