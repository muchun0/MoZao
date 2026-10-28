"""Agent 统一入口 API 级测试（P0：202 返回体 / 四态状态机 / 停止语义 / 重启沿用 task_id）。

用真实 ProjectStore（临时目录）+ mock pipeline（不触 LLM）+ monkeypatch db 层（不触真实资金）。
停止语义与重启沿用为 TECH §4.1 评审 I-5 / I-6 的验收点。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.run_context import read_run_file
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline
from webapp import core
from webapp import db
from webapp import generate_service as gs


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def store_with_outline(store):
    store.save_outline(Outline(title="测试书", intro="简介",
                               chapters=[ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}")
                                         for i in range(1, 4)]))
    return store


@pytest.fixture
def byok_db(monkeypatch):
    """BYOK 模式 db 桩：平台不扣费仍记账（避免触碰真实资金）。"""
    monkeypatch.setattr(db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(db, "get_user_llm", lambda uid: {"api_key": "k", "provider": "deepseek"})
    monkeypatch.setattr(db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(db, "get_user", lambda uid: {"id": uid, "balance": 100.0})
    monkeypatch.setattr(db, "change_balance", lambda *a, **k: None)
    monkeypatch.setattr(db, "update_project_price", lambda *a, **k: None)
    monkeypatch.setattr(db, "project_price", lambda pid: 0.0)


@pytest.fixture
def cleanup_runs():
    yield
    for pid in list(core._RUNS):
        h = core._RUNS.pop(pid, None)
        if h and h.thread and h.thread.is_alive():
            h.stop_event.set()
            h.thread.join(timeout=5)
        core._GEN_SEMAPHORE.release()


class _MockWriter:
    """假写手：写章即落盘 save_chapter（产生真实版本），可在指定章置停止标记。"""

    def __init__(self, store, handle, stop_at: int = 0):
        self.store = store
        self.handle = handle
        self.stop_at = stop_at
        self.written: list[int] = []

    def write(self, no, title, goal, research, is_final=False):
        self.store.save_chapter(Chapter(no=no, title=title, content="正文片段" * 300))
        self.written.append(no)
        if no == self.stop_at:
            self.handle.stop_event.set()


def _mock_pipeline(store, writer=None):
    return SimpleNamespace(cfg=store.load_config(), llm=None,
                           chapter_writer=writer or _MockWriter(store, SimpleNamespace()))


# ---------- 202 返回体 + task.json 落盘 ----------

@pytest.mark.p0
def test_start_returns_task_id_and_marks_task(store_with_outline, byok_db,
                                              monkeypatch, cleanup_runs):
    monkeypatch.setattr(gs, "_run_agent_task", lambda *a: None)  # 不起真实执行
    req = gs.AgentRunReq(agent="writer.chapter", params={"scope": "all"}, user_prompt="写快点")
    body = gs.start_agent_run(store_with_outline, req, {"id": 1, "username": "u"})
    assert body["started"] is True
    assert len(body["task_id"]) == 12
    assert body["agent"] == "writer.chapter"
    assert body["billing_mode"] == "user_llm"
    assert body["estimated_price"] == 0.0  # BYOK 恒 0
    meta = json.loads((store_with_outline.dir / "task.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "agent_run"
    assert meta["task_id"] == body["task_id"]
    assert meta["agent"] == "writer.chapter"
    assert meta["run_id"]


@pytest.mark.p0
def test_start_rejects_when_agent_invalid(store_with_outline, byok_db, cleanup_runs):
    req = gs.AgentRunReq(agent="editor.review", params={})
    with pytest.raises(HTTPException) as ei:
        gs.start_agent_run(store_with_outline, req, {"id": 1, "username": "u"})
    assert ei.value.status_code == 400


# ---------- 四态状态机（I-6） ----------

@pytest.mark.p0
def test_status_machine(store, byok_db, cleanup_runs):
    pid = store.dir.name
    # not_found
    with pytest.raises(HTTPException) as ei:
        gs.get_agent_run(store, "t_none")
    assert ei.value.status_code == 404

    # running：task.json 在 + _RUNS 线程存活
    (store.dir / "task.json").write_text(json.dumps(
        {"kind": "agent_run", "task_id": "t_run", "agent": "writer.chapter",
         "run_id": "r1", "started_at": time.time()}), encoding="utf-8")
    handle = core._RunHandle()
    handle.thread = threading.Thread(target=time.sleep, args=(30,), daemon=True)
    handle.thread.start()
    core._RUNS[pid] = handle
    body = gs.get_agent_run(store, "t_run")
    assert body["status"] == "running" and body["run_id"] == "r1"
    handle.stop_event.set()
    handle.thread.join(timeout=1)
    core._RUNS.pop(pid, None)
    core._GEN_SEMAPHORE.release()  # 平衡手工放入（本用例未 acquire，仅防状态泄漏）

    # resumed：task.json 在但无线程（重启竞态兜底）
    body = gs.get_agent_run(store, "t_run")
    assert body["status"] == "resumed"

    # done / stopped / failed：task.json 清除 + run 侧写存在
    (store.dir / "task.json").unlink()
    for status, expect in (("ok", "done"), ("stopped", "stopped"), ("failed", "failed")):
        tid = f"t_{status}"
        from novel_gen.store.run_context import write_run_file
        write_run_file(store.dir, {"run_id": f"r_{status}", "task_id": tid,
                                   "project_id": pid, "agent": "writer.chapter",
                                   "started_at": 1.0, "finished_at": 2.0, "status": status,
                                   "params": {}, "prompt_version": "writer.chapter@v1",
                                   "prompt_sha256": "x", "user_prompt": "", "skill_ids": [],
                                   "model": "", "temperature": 0.8, "seed": None,
                                   "trace_id": "", "billing_mode": "user_llm",
                                   "billing": {"billed_words": 0, "billed_price": 0.0,
                                               "entries": [], "wasted_words": 0},
                                   "outputs": [], "compliance_rejected": False})
        body = gs.get_agent_run(store, tid)
        assert body["status"] == expect, status


# ---------- 停止语义：不提交半截章（I-5，P0） ----------

@pytest.mark.p0
def test_stop_semantics_no_partial_commit(store_with_outline, byok_db,
                                          monkeypatch, cleanup_runs):
    """range 1..3 生成，第 2 章写完置停止 → 第 3 章不开工；run 侧写 status=stopped。"""
    store = store_with_outline
    handle = core._acquire_slot(store.dir.name)
    writer = _MockWriter(store, handle, stop_at=2)
    monkeypatch.setattr(core, "_open_pipeline",
                        lambda s, trace_id="": _mock_pipeline(store, writer))
    gs._run_agent_task(store, handle, "writer.chapter",
                       {"scope": "range", "from_ch": 1, "to_ch": 3},
                       "", (), "task_stop1", "run_stop1")
    assert writer.written == [1, 2]  # 第 3 章未生成
    assert store.load_chapter(1) is not None
    assert store.load_chapter(2) is not None
    assert store.load_chapter(3) is None
    run = read_run_file(store.dir, "run_stop1")
    assert run["status"] == "stopped"
    assert {o["key"] for o in run["outputs"]} == {"1", "2"}
    # 账目：两章各一个新版本，BYOK charged=0 但 words 有记录
    assert len(run["billing"]["entries"]) == 2
    assert run["billing"]["billed_words"] > 0
    assert run["billing"]["billed_price"] == 0.0
    # 任务标记与槽位已清
    assert not (store.dir / "task.json").exists()
    assert store.dir.name not in core._RUNS


# ---------- 重启沿用 task_id（I-6，P0） ----------

@pytest.mark.p0
def test_resume_reuses_task_id(store_with_outline, byok_db, monkeypatch, cleanup_runs):
    meta = {"kind": "agent_run", "task_id": "abc123def456", "agent": "writer.chapter",
            "params": {"scope": "all"}, "run_id": "run_resume1", "user_prompt": "",
            "skill_ids": [], "started_at": time.time()}
    called: dict = {}
    monkeypatch.setattr(gs, "_run_agent_task",
                        lambda *a: called.update(args=a))
    handle = core._RunHandle()
    gs.resume_agent_run(store_with_outline, handle, meta)
    handle.thread.join(timeout=5)
    assert handle.task_id == "abc123def456"
    # _run_agent_task 位置参数：(..., user_prompt, skill_ids, task_id, run_id)
    assert called["args"][-2] == "abc123def456"
    assert called["args"][-1] == "run_resume1"


@pytest.mark.p0
def test_resume_rejects_invalid_meta(store, byok_db):
    handle = core._RunHandle()
    with pytest.raises(ValueError):
        gs.resume_agent_run(store, handle, {"task_id": "", "agent": "writer.chapter"})
    with pytest.raises(ValueError):
        gs.resume_agent_run(store, handle, {"task_id": "t", "agent": "not.agent"})
