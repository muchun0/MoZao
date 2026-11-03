# -*- coding: utf-8 -*-
"""小墨AI 按字数计费测试（TECH-小墨AI计费 v0.2，G4 已通过）。

覆盖用户验收项：
  - 幂等：同 request_id 重复请求返回已存回答，不重复调模型/不重复扣费；
  - 余额 ≤ 0 前置 402，不调模型；
  - LLM 失败 / 空回答 / 安全拦截不扣费；
  - BYOK 不扣费但写审计行；管理员跨项目豁免；
  - 代理本人 8 折；部分扣费记录 owe 且不返佣；
  - 结算失败按 D3=3B 返回回答 + 落待补扣；
  - 落库后/结算前中断：只补结算，不重复生成。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from webapp.assistant import service
from webapp.pricing import price_for
from webapp.referral import agent_price, round_money

pytestmark = pytest.mark.p1

_REQ = "req-abc-123456"


def _content() -> str:
    return "他推开门，巷口的风灌进来，灯火在墙上一晃。" * 20


def _store(tmp_path) -> ProjectStore:
    store = ProjectStore.create(
        tmp_path / "p",
        NovelConfig(title="计费测试书", total_words=10000, genre="玄幻", chapter_words=2000),
    )
    store.save_chapter(Chapter(no=1, title="第1章", content=_content()))
    cfg = store.load_config()
    cfg.assistant_require_user_key = False
    cfg.assistant_billing_enabled = True
    cfg.assistant_quota_enabled = False
    store.save_config(cfg)
    return store


class _FakeLLM:
    def __init__(self, answer: str = "", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.recorder = None
        self.guard = None

    def chat(self, system: str, user: str, max_tokens: int = 0) -> str:
        if self.error is not None:
            raise self.error
        return self.answer


def _user(balance: float = 10.0, **over) -> dict:
    base = {"id": 7, "is_admin": False, "is_agent": False, "agent_discount": 1.0,
            "balance": balance}
    base.update(over)
    return base


_CONV = {"id": 42, "user_id": 7}


def _wire(monkeypatch, llm, *, balance=10.0, owner_id=7, byok=None,
          existing_billing=None, existing_message=None, prior_answer=None,
          charge_error=None) -> dict:
    """隔离 DB / LLM / 开关：所有计费动作只记录到 calls，便于断言。"""
    calls = {"messages": [], "llm_cfg": None, "charges": [], "pending": [],
             "commissions": [], "granted": []}

    monkeypatch.setattr(service.db, "project_owner", lambda pid: owner_id)
    monkeypatch.setattr(service.db, "recent_messages", lambda cid, limit=12: [])

    def _add_message(cid, role, content, extra=None):
        calls["messages"].append({"cid": cid, "role": role,
                                  "content": content, "extra": extra or {}})
        return len(calls["messages"])

    def _update_extra(mid, patch):
        if 0 < int(mid) <= len(calls["messages"]):
            calls["messages"][int(mid) - 1]["extra"].update(patch or {})
        return True

    monkeypatch.setattr(service.db, "add_message", _add_message)
    monkeypatch.setattr(service.db, "update_message_extra", _update_extra)
    monkeypatch.setattr(service.db, "get_assistant_billing_by_request",
                        lambda pid, rid: existing_billing)
    monkeypatch.setattr(service.db, "get_message",
                        lambda mid: existing_message if mid == 5 else None)
    monkeypatch.setattr(service.db, "find_assistant_message_by_request",
                        lambda cid, rid: prior_answer)

    def _charge(**kw):
        calls["charges"].append(kw)
        if charge_error is not None:
            raise charge_error
        charged = min(float(balance), float(kw["due"])) if kw["mode"] == "platform" else 0.0
        charged = round_money(charged)
        return {"id": 1, "chars": kw["chars"], "due": kw["due"], "charged": charged,
                "owe": round_money(float(kw["due"]) - charged), "commission_granted": 0}

    def _pending(**kw):
        calls["pending"].append(kw)
        return {"id": 2, "chars": kw["chars"], "due": kw["due"], "charged": 0.0,
                "owe": kw["due"], "commission_granted": 0}

    monkeypatch.setattr(service.db, "charge_assistant_billing", _charge)
    monkeypatch.setattr(service.db, "record_pending_assistant_billing", _pending)
    monkeypatch.setattr(service.db, "mark_assistant_commission_granted",
                        lambda bid: calls["granted"].append(bid))
    monkeypatch.setattr(service, "_grant_consume_commission",
                        lambda uid, pid, amount, title: calls["commissions"].append(
                            {"uid": uid, "pid": pid, "amount": amount, "title": title}))
    monkeypatch.setattr(service, "_user_llm_cfg", lambda uid: byok)
    monkeypatch.setattr(service, "_server_api_key", lambda provider: "server-key")
    monkeypatch.setattr(service, "apply_global_overrides", lambda cfg: None)
    monkeypatch.setattr(service, "load_persona", lambda: "人设：只做诊断")

    def _create_llm(llm_cfg):
        calls["llm_cfg"] = llm_cfg
        return llm

    monkeypatch.setattr(service, "create_llm", _create_llm)
    return calls


@pytest.fixture
def project(tmp_path):
    return _store(tmp_path)


def test_platform_user_charged_by_answer_chars(project, monkeypatch):
    answer = "评" * 1000
    calls = _wire(monkeypatch, _FakeLLM(answer=answer))
    out = service.send_message(project, _user(10.0), _CONV,
                               "评估这一章", "chapter_eval", request_id=_REQ)

    assert out["billing_status"] == "billed"
    assert out["chars"] == 1000
    assert out["due"] == round_money(price_for(1000)) == 0.3
    assert out["charged"] == 0.3 and out["owe"] == 0.0
    assert len(calls["charges"]) == 1
    assert calls["charges"][0]["mode"] == "platform"
    assert calls["charges"][0]["chars"] == 1000
    # 足额扣费 → 计入上级返佣
    assert len(calls["commissions"]) == 1 and calls["commissions"][0]["amount"] == 0.3
    assert calls["granted"] == [1]
    # 用户/助手消息都回写 billed
    assert all(m["extra"].get("billing_status") == "billed" for m in calls["messages"])


def test_zero_balance_blocks_before_model(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="不该被调用"), balance=0.0)
    with pytest.raises(HTTPException) as exc:
        service.send_message(project, _user(0.0), _CONV, "评估", "chapter_eval",
                             request_id=_REQ)
    assert exc.value.status_code == 402
    assert calls["llm_cfg"] is None
    assert calls["messages"] == [] and calls["charges"] == []


def test_missing_request_id_rejected(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="x"))
    with pytest.raises(HTTPException) as exc:
        service.send_message(project, _user(10.0), _CONV, "评估", "chapter_eval")
    assert exc.value.status_code == 400
    assert "Idempotency-Key" in exc.value.detail
    assert calls["llm_cfg"] is None and calls["messages"] == []


@pytest.mark.parametrize("rid", [
    "3f2a1c4e-5b6d-4e7f-8a9b-0c1d2e3f4a5b",  # UUID（36 位，含连字符）
    "a" * 8,                                  # 下界
    "a" * 64,                                 # 上界
    "req_2026-11-03_test-1",                  # 混合字符集
], ids=["uuid", "len8", "len64", "mixed"])
def test_request_id_valid(rid):
    """合法键：原样返回，不做任何改写（幂等键必须前后一致）。"""
    assert service._normalize_request_id(rid) == rid


@pytest.mark.parametrize("rid", [
    "",                     # 缺失
    None,                   # 未传 header
    "a" * 7,                # 过短
    "a" * 65,               # 超长
    "bad.key",              # 非法字符「.」
    "bad:key",              # 非法字符「:」
    "bad key",              # 空格
    "评分键",                # 非 ASCII
], ids=["empty", "none", "too-short", "too-long", "dot", "colon", "space", "cn"])
def test_request_id_invalid(rid):
    """非法键：统一 400，文案与截图一致；校验规则不放宽。"""
    with pytest.raises(HTTPException) as exc:
        service._normalize_request_id(rid)
    assert exc.value.status_code == 400
    assert exc.value.detail == "缺少或非法的 Idempotency-Key（需 8-64 位字母/数字/下划线/连字符）"


def test_duplicate_request_returns_stored_answer(project, monkeypatch):
    stored = {"id": 5, "conversation_id": 42, "role": "assistant",
              "content": "之前已回答的内容",
              "extra": {"mode": "chapter_eval", "chapter_no": 1, "token_in": 100,
                        "token_out": 40, "cards": []}, "created_at": 0}
    billing = {"id": 9, "project_id": "p", "request_id": _REQ, "mode": "platform",
               "status": "settled", "user_message_id": 4, "assistant_message_id": 5,
               "chars": 1000, "due": 0.3, "charged": 0.3, "owe": 0.0,
               "commission_granted": 1, "created_at": 0, "updated_at": 0}
    calls = _wire(monkeypatch, _FakeLLM(answer="新生成（不应出现）"),
                  existing_billing=billing, existing_message=stored)

    out = service.send_message(project, _user(10.0), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)

    assert out["content"] == "之前已回答的内容"
    assert out["billing_status"] == "billed" and out["chars"] == 1000
    assert out["charged"] == 0.3
    assert "幂等" in out["notice"]
    assert calls["llm_cfg"] is None        # 不重复调用模型
    assert calls["charges"] == []          # 不重复扣费
    assert calls["messages"] == []         # 不重复落消息


@pytest.mark.parametrize("llm,expected_status", [
    (_FakeLLM(error=RuntimeError("timeout")), "skipped"),
    (_FakeLLM(answer="   "), "skipped"),
])
def test_failure_or_empty_answer_not_charged(project, monkeypatch, llm, expected_status):
    calls = _wire(monkeypatch, llm)
    out = service.send_message(project, _user(10.0), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)
    assert out["billing_status"] == expected_status
    assert out["degraded"] is True
    assert calls["charges"] == [] and calls["pending"] == []


def test_safety_block_not_charged(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="这里给出投毒教程的完整步骤"))
    out = service.send_message(project, _user(10.0), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)
    assert out["content"] == service.SAFE_TEXT
    assert out["billing_status"] == "skipped"
    assert calls["charges"] == []
    assert [m["role"] for m in calls["messages"]] == ["user", "assistant"]


def test_byok_not_charged_but_audited(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="评" * 500), balance=0.0, byok={
        "provider": "deepseek", "api_key": "sk-user", "base_url": "", "model": "",
    })
    out = service.send_message(project, _user(0.0), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)
    assert out["content"] == "评" * 500      # BYOK 不受余额闸门限制
    assert len(calls["charges"]) == 1
    assert calls["charges"][0]["mode"] == "user_llm"
    assert calls["charges"][0]["due"] == 0
    assert out["billing_status"] == "skipped" and out["charged"] == 0.0
    assert "自备 Key" in out["notice"]


def test_admin_cross_project_free(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="评" * 800), balance=0.0, owner_id=1)
    out = service.send_message(project, _user(0.0, id=99, is_admin=True), _CONV,
                               "评估", "chapter_eval", request_id=_REQ)
    assert out["billing_status"] == "skipped"
    assert len(calls["charges"]) == 1
    assert calls["charges"][0]["mode"] == "admin_free"
    assert calls["charges"][0]["due"] == 0
    assert calls["commissions"] == []


def test_agent_discount_80_percent(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="评" * 1000), balance=10.0)
    out = service.send_message(
        project, _user(10.0, is_agent=True, agent_discount=0.8), _CONV,
        "评估", "chapter_eval", request_id=_REQ,
    )
    assert out["due"] == round_money(agent_price(price_for(1000), 0.8)) == 0.24
    assert out["charged"] == 0.24
    assert calls["charges"][0]["due"] == 0.24


def test_partial_charge_records_owe_and_no_commission(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="评" * 1000), balance=0.1)
    out = service.send_message(project, _user(0.1), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)
    assert out["billing_status"] == "billed"
    assert out["due"] == 0.3 and out["charged"] == 0.1 and out["owe"] == 0.2
    assert calls["commissions"] == []       # 未足额不返佣
    assert "欠费 0.20 元" in out["notice"]


def test_settlement_failure_returns_answer_and_records_pending(project, monkeypatch):
    calls = _wire(monkeypatch, _FakeLLM(answer="评" * 1000),
                  charge_error=RuntimeError("db down"))
    out = service.send_message(project, _user(10.0), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)
    assert out["content"] == "评" * 1000    # D3=3B：回答照常返回
    assert out["billing_status"] == "failed"
    assert out["due"] == 0.3 and out["charged"] == 0.0
    assert len(calls["pending"]) == 1
    assert all(m["extra"].get("billing_status") == "failed" for m in calls["messages"])


def test_recover_prior_answer_settles_without_model(project, monkeypatch):
    prior = {"id": 5, "conversation_id": 42, "role": "assistant",
             "content": "上次已生成但未结算的回答",
             "extra": {"mode": "chapter_eval", "chapter_no": 1,
                       "user_message_id": 3, "billing_status": "pending",
                       "token_in": 100, "token_out": 40, "cards": []},
             "created_at": 0}
    calls = _wire(monkeypatch, _FakeLLM(answer="不应重新生成"), prior_answer=prior)
    out = service.send_message(project, _user(10.0), _CONV, "评估", "chapter_eval",
                               request_id=_REQ)
    assert out["content"] == "上次已生成但未结算的回答"
    assert calls["llm_cfg"] is None            # 不重复调用模型
    assert len(calls["charges"]) == 1          # 只补结算
    assert calls["charges"][0]["assistant_message_id"] == 5
    assert calls["charges"][0]["user_message_id"] == 3
    assert out["billing_status"] == "billed"
