"""R27 分步确认式生成流 P0 用例（阶段暂停/恢复、刷新后卡片仍在、不重复扣费、402 停在当前阶段、越权）。

隔离策略与 `test_agent_run_api.py` 一致：真实 ProjectStore（tmp 目录）+ monkeypatch db（不触资金）
+ 打桩 `generate_service.start_agent_run`（不触 LLM、不起线程），用 run 侧写模拟 run 完成。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.run_context import write_run_file
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline
from webapp import db
from webapp import generation_flow as gf


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="R27测试书", total_words=10000, chapter_words=2500,
                      genre="玄幻", with_research=False)
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def byok_db(monkeypatch):
    monkeypatch.setattr(db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(db, "get_user_llm", lambda uid: {"api_key": "k", "provider": "deepseek"})
    monkeypatch.setattr(db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(db, "get_user", lambda uid: {"id": uid, "balance": 100.0})


@pytest.fixture
def start_calls(monkeypatch):
    """打桩统一入口：记录 (agent, params)，返回可预测的 run_id/task_id。"""
    calls: list[tuple[str, dict]] = []

    def fake_start(store, req, user):
        calls.append((req.agent, dict(req.params or {})))
        n = len(calls)
        # 模拟真实入口：先落 task.json（reconcile 判定 running 的依据），线程由测试用侧写模拟结束
        (store.dir / "task.json").write_text(json.dumps({
            "kind": "agent_run", "task_id": f"t{n}", "agent": req.agent,
            "params": dict(req.params or {}), "run_id": f"r{n}",
            "started_at": time.time()}), encoding="utf-8")
        return {"started": True, "task_id": f"t{n}", "run_id": f"r{n}",
                "agent": req.agent, "billing_mode": "user_llm", "estimated_price": 0.0}

    from webapp import generate_service as gs
    monkeypatch.setattr(gs, "start_agent_run", fake_start)
    return calls


def _save_outline(store, intro="简介"):
    # commit=True 与真实链路一致（_exec_outline → _commit_and_settle），门禁取 v 版本号
    store.save_outline(Outline(title="R27测试书", intro=intro, main_line="主线",
                               chapters=[ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}")
                                         for i in range(1, 4)]),
                       commit=True, source="ai")


def _finish(store, agent: str, run_id: str, status: str = "ok") -> None:
    (store.dir / "task.json").unlink(missing_ok=True)  # 真实 run 收尾会清 task.json
    write_run_file(store.dir, {
        "run_id": run_id, "task_id": f"t_{run_id}", "project_id": store.dir.name,
        "agent": agent, "started_at": time.time() - 1, "finished_at": time.time(),
        "status": status, "params": {}, "prompt_version": f"{agent}@v1",
        "prompt_sha256": "x", "user_prompt": "", "skill_ids": [], "model": "",
        "temperature": 0.8, "seed": None, "trace_id": "", "billing_mode": "user_llm",
        "billing": {"billed_words": 0, "billed_price": 0.0, "entries": [], "wasted_words": 0},
        "outputs": [], "compliance_rejected": False, "error": "",
    })
    gf.on_run_finished(store, agent, run_id, status)


def _outline_done(store, calls):
    """跑到「大纲已完成、等确认」：启动流程 → 写产物 → 模拟 run 成功。"""
    gf.start_flow(store, {"id": 1}, "stepwise")
    assert calls and calls[-1][0] == "architect.outline"
    _save_outline(store)
    _finish(store, "architect.outline", "r1")
    return calls


# ---------- 模式优先级 ----------

def test_mode_priority_new_legacy_explicit(store):
    mode, source = gf.resolve_mode(store)
    assert (mode, source) == ("stepwise", "default_new")

    store.save_chapter(Chapter(no=1, title="第1章", content="正文"))
    assert gf.resolve_mode(store) == ("auto", "default_old")

    cfg = store.load_config()
    cfg.generation_mode = "stepwise"
    store.save_config(cfg)
    assert gf.resolve_mode(store) == ("stepwise", "project")


def test_mode_switch_three_times_keeps_state(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    for target in ("auto", "stepwise", "auto"):
        gf.set_mode(store, {"id": 1}, target)
        assert store.load_config().generation_mode == target
        body = gf.get_flow(store)
        assert body["flow"]["stages"][1]["status"] in ("done", "confirmed")


# ---------- 启动即暂停 ----------

def test_start_stepwise_stops_after_outline(store, byok_db, start_calls):
    calls = _outline_done(store, start_calls)
    assert calls == [("architect.outline", {})]  # 只起了大纲，没有自动起世界观
    body = gf.get_flow(store)
    stages = {s["key"]: s["status"] for s in body["flow"]["stages"]}
    assert stages["research"] == "skipped"
    assert stages["outline"] == "done"
    assert stages["world"] == "pending"
    assert stages["volume"] == "skipped"
    card = body["flow"]["cards"][0]
    assert card["stage"] == "outline"
    assert "continue" in card["actions"] and "edit" in card["actions"]
    assert card["digest"]


def test_refresh_keeps_card_and_no_resend(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    n = len(start_calls)
    for _ in range(3):  # 模拟刷新/重启后的多次读取
        body = gf.get_flow(store)
        assert body["flow"]["cards"][0]["stage"] == "outline"
    assert len(start_calls) == n  # 读取不触发任何新 run


# ---------- digest 与幂等 ----------

def test_confirm_wrong_digest_409(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    n = len(start_calls)
    with pytest.raises(HTTPException) as ei:
        gf.confirm(store, {"id": 1}, "outline", "continue", "sha1:stale")
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "STAGE_CHANGED"
    assert len(start_calls) == n


def test_double_click_confirm_is_idempotent(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    digest = gf.get_flow(store)["flow"]["cards"][0]["digest"]
    first = gf.confirm(store, {"id": 1}, "outline", "continue", digest)
    second = gf.confirm(store, {"id": 1}, "outline", "continue", digest)
    assert first["idempotent"] is False and second["idempotent"] is True
    assert [c[0] for c in start_calls] == ["architect.outline", "architect.world"]
    stages = {s["key"]: s["status"] for s in gf.get_flow(store)["flow"]["stages"]}
    assert stages["outline"] == "confirmed" and stages["world"] == "running"


def test_edit_then_confirm_requires_refresh(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    old = gf.get_flow(store)["flow"]["cards"][0]["digest"]
    _save_outline(store, intro="作者改过的一句话卖点")
    body = gf.get_flow(store)
    card = body["flow"]["cards"][0]
    assert card["edited"] is True and card["digest"] != old
    with pytest.raises(HTTPException) as ei:
        gf.confirm(store, {"id": 1}, "outline", "continue", old)
    assert ei.value.status_code == 409
    ok = gf.confirm(store, {"id": 1}, "outline", "continue", card["digest"])
    assert ok["stage_status"] == "confirmed"


# ---------- 重生成再次计费 ----------

def test_regenerate_starts_second_run(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    digest = gf.get_flow(store)["flow"]["cards"][0]["digest"]
    gf.confirm(store, {"id": 1}, "outline", "regenerate", digest, force=True)
    assert [c[0] for c in start_calls] == ["architect.outline", "architect.outline"]
    st = gf.get_flow(store)["flow"]["stages"][1]
    assert st["status"] == "running"
    # 第二次 run 完成后重新进入 done，可再次确认（每次重生成都是一次新版本结算）
    _finish(store, "architect.outline", "r2")
    st = gf.get_flow(store)["flow"]["stages"][1]
    assert st["status"] == "done"


def test_regenerate_requires_force_within_window(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    digest = gf.get_flow(store)["flow"]["cards"][0]["digest"]
    gf.confirm(store, {"id": 1}, "outline", "regenerate", digest, force=True)
    _finish(store, "architect.outline", "r2")
    digest = gf.get_flow(store)["flow"]["cards"][0]["digest"]
    with pytest.raises(HTTPException) as ei:
        gf.confirm(store, {"id": 1}, "outline", "regenerate", digest)
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "REGENERATE_CONFIRM"


# ---------- 失败与资金 ----------

def _reach_chapters_ready(store):
    gf.start_flow(store, {"id": 1}, "stepwise")
    flow, _ = gf.read_flow(store)
    for st in flow["stages"]:
        if st["key"] == "outline":
            st.update(status="confirmed")
        elif st["key"] == "world":
            st.update(status="confirmed")
        elif st["key"] == "chapters":
            st.update(status="ready")
        else:
            st.update(status="skipped", reason="test")
    gf._save(store, flow)
    return flow


def test_402_keeps_stage_ready_and_no_charge(store, byok_db, start_calls, monkeypatch):
    _reach_chapters_ready(store)
    from webapp import generate_service as gs

    def boom(*a, **k):
        raise HTTPException(status_code=402, detail="余额不足，请先充值后再生成")

    monkeypatch.setattr(gs, "start_agent_run", boom)
    with pytest.raises(HTTPException) as ei:
        gf.confirm(store, {"id": 1}, "chapters", "continue", "")
    assert ei.value.status_code == 402
    st = {s["key"]: s for s in gf.get_flow(store)["flow"]["stages"]}["chapters"]
    assert st["status"] == "ready"
    assert st["last_error"].get("code") in ("http_402", "insufficient_balance")


def test_start_402_leaves_ready_card(store, byok_db, monkeypatch):
    """首次启动就余额不足：不扣费、不写产物，阶段落 ready 并给出可重试卡片。"""
    from webapp import generate_service as gs
    monkeypatch.setattr(gs, "start_agent_run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            HTTPException(status_code=402, detail="余额不足，请先充值后再生成")))
    with pytest.raises(HTTPException):
        gf.start_flow(store, {"id": 1}, "stepwise")
    body = gf.get_flow(store)
    st = body["flow"]["stages"][1]
    assert st["status"] == "ready"
    assert st["last_error"].get("code") == "http_402"
    card = body["flow"]["cards"][0]
    assert card["stage"] == "outline" and "continue" in card["actions"]
    assert not gf.compute_digest(store, "outline")  # 没有产物


def test_run_failure_marks_failed_without_retry(store, byok_db, start_calls):
    _outline_done(store, start_calls)
    _finish(store, "architect.outline", "r1", status="failed")
    st = gf.get_flow(store)["flow"]["stages"][1]
    assert st["status"] == "failed"
    assert len(start_calls) == 1  # 不自动重试


# ---------- 恢复与对账 ----------

def test_recovery_uses_sidecar_not_missing_run(store, byok_db, start_calls):
    """run 侧写 ok 但 task.json 已清：必须恢复为 done，不判失败、不重发。"""
    _outline_done(store, start_calls)
    flow, _ = gf.read_flow(store)
    st = [s for s in flow["stages"] if s["key"] == "outline"][0]
    st.update(status="running", run_id="r1")
    gf._save(store, flow)
    st = gf.get_flow(store)["flow"]["stages"][1]
    assert st["status"] == "done"
    assert len(start_calls) == 1


def test_manual_agent_run_is_adopted(store, byok_db, start_calls):
    """Agent 面板手动触发大纲：run 完成后 flow 对账为 done，不重复起 run。"""
    gf.start_flow(store, {"id": 1}, "stepwise")
    # 还原成「流程未驱动本阶段」：清掉编排 run 绑定（真实场景是用户绕过确认卡手动触发）
    (store.dir / "task.json").unlink(missing_ok=True)
    flow, _ = gf.read_flow(store)
    st = [s for s in flow["stages"] if s["key"] == "outline"][0]
    st.update(status="pending", run_id="", task_id="")
    gf._save(store, flow)
    _save_outline(store)
    _finish(store, "architect.outline", "r_manual")
    body = gf.get_flow(store)
    st = body["flow"]["stages"][1]
    assert st["status"] == "done" and body["flow"]["cards"][0]["stage"] == "outline"
    assert [c[0] for c in start_calls] == ["architect.outline"]


def test_auto_mode_advances_after_run(store, byok_db, start_calls, monkeypatch):
    cfg = store.load_config()
    cfg.generation_mode = "auto"
    store.save_config(cfg)
    gf.start_flow(store, {"id": 1}, "auto")
    _save_outline(store)
    _finish(store, "architect.outline", "r1")
    for _ in range(40):  # 等后台 auto 推进线程（最多 10s，这里给 2s）
        if len(start_calls) >= 2:
            break
        time.sleep(0.05)
    assert [c[0] for c in start_calls] == ["architect.outline", "architect.world"]


# ---------- 边界 ----------

def test_expand_project_unsupported(store):
    cfg = store.load_config()
    cfg.mode = "expand"
    store.save_config(cfg)
    body = gf.get_flow(store)
    assert body["supported"] is False and body["flow"] is None
    with pytest.raises(HTTPException) as ei:
        gf.start_flow(store, {"id": 1}, None)
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "MODE_UNSUPPORTED"


def test_flow_requires_auth():
    from fastapi.testclient import TestClient
    from webapp.server import app

    client = TestClient(app)
    resp = client.get("/api/projects/any/generation/flow")
    assert resp.status_code in (401, 403)


def test_corrupt_flow_can_restart(store, byok_db, start_calls):
    gf.flow_path(store).write_text("{not json", encoding="utf-8")
    body = gf.get_flow(store)
    assert body["flow"] is None and body.get("corrupt") is True
    cfg = store.load_config()
    assert cfg.generation_mode in ("", "auto", "stepwise")
    url = gf.start_flow(store, {"id": 1}, "stepwise")
    assert url["flow"] is not None
