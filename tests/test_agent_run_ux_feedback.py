"""Agent run UX 评审修复测试（P0：U1 进度 / F1 重生成反馈 / U2 预估 / F2 每日上限）。

用真实 ProjectStore（临时目录）+ mock pipeline（不触 LLM）+ monkeypatch db 层（不触真实资金）。
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
from novel_gen.store.run_context import read_run_file, write_run_file
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


# ---------- U1：running 态返回 progress（进度条数据源） ----------

@pytest.mark.p0
def test_running_returns_progress(store, byok_db, cleanup_runs):
    pid = store.dir.name
    (store.dir / "task.json").write_text(json.dumps({
        "kind": "agent_run", "task_id": "t_prog", "agent": "writer.chapter",
        "run_id": "r_prog", "started_at": time.time(),
        "progress": {"phase": "chapter", "chapters_done": 2, "chapters_total": 3,
                     "current_chapter": 3}}), encoding="utf-8")
    handle = core._RunHandle()
    handle.thread = threading.Thread(target=time.sleep, args=(30,), daemon=True)
    handle.thread.start()
    core._RUNS[pid] = handle
    try:
        body = gs.get_agent_run(store, "t_prog")
        assert body["status"] == "running"
        prog = body["progress"]
        assert prog["phase"] == "chapter"
        assert prog["chapters_done"] == 2
        assert prog["chapters_total"] == 3
        assert prog["current_chapter"] == 3
        assert prog["elapsed"] >= 0
    finally:
        handle.stop_event.set()
        handle.thread.join(timeout=1)
        core._RUNS.pop(pid, None)
        core._GEN_SEMAPHORE.release()  # 平衡手工放入（本用例未 acquire，仅防状态泄漏）


# ---------- U2：只读预估接口（不占槽不扣费） ----------

@pytest.mark.p0
def test_estimate_returns_price(store_with_outline, byok_db, monkeypatch):
    req = gs.AgentRunReq(agent="writer.chapter", params={"scope": "single", "ch_no": 2})
    body = gs.estimate_agent_price(store_with_outline, req, {"id": 1, "username": "u"})
    assert body["agent"] == "writer.chapter"
    assert body["billing_mode"] == "user_llm"
    assert body["estimated_price"] == 0.0  # BYOK 恒 0

    monkeypatch.setattr(db, "get_user_llm", lambda uid: {"api_key": "", "provider": ""})  # 平台模式
    body = gs.estimate_agent_price(store_with_outline, req, {"id": 1, "username": "u"})
    assert body["billing_mode"] == "platform"
    assert body["estimated_price"] > 0

    # 非法参数复用守卫：400 不落任何账
    bad = gs.AgentRunReq(agent="writer.chapter", params={"scope": "single"})
    with pytest.raises(HTTPException) as ei:
        gs.estimate_agent_price(store_with_outline, bad, {"id": 1, "username": "u"})
    assert ei.value.status_code == 400


# ---------- F2：单项目每日 Agent run 上限（0=不限） ----------

def _write_run(store, run_id: str, started_at: float) -> None:
    write_run_file(store.dir, {"run_id": run_id, "task_id": f"t_{run_id}",
                               "project_id": store.dir.name, "agent": "writer.chapter",
                               "started_at": started_at, "finished_at": started_at,
                               "status": "ok", "params": {}, "prompt_version": "x",
                               "prompt_sha256": "x", "user_prompt": "", "skill_ids": [],
                               "model": "", "temperature": 0.8, "seed": None, "trace_id": "",
                               "billing_mode": "user_llm", "billing": {}, "outputs": [],
                               "compliance_rejected": False})


@pytest.mark.p0
def test_daily_run_limit_blocks(store_with_outline, byok_db, monkeypatch, cleanup_runs):
    store = store_with_outline
    runs_dir = store.dir / "agent_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    _write_run(store, "r1", time.time())
    _write_run(store, "r2", time.time())

    monkeypatch.setattr(db, "get_config", lambda key: {"config_key": key, "config_value": "2"}
                        if key == "agent_run_daily_limit" else None)
    monkeypatch.setattr(gs, "_run_agent_task", lambda *a: None)
    req = gs.AgentRunReq(agent="writer.chapter", params={"scope": "single", "ch_no": 1})
    with pytest.raises(HTTPException) as ei:
        gs.start_agent_run(store, req, {"id": 1, "username": "u"})
    assert ei.value.status_code == 429

    # 关闭上限（0/缺省）→ 放行
    monkeypatch.setattr(db, "get_config", lambda key: None)
    body = gs.start_agent_run(store, req, {"id": 1, "username": "u"})
    assert body["started"] is True


# ---------- F1：重生成完成反馈（kept_previous / quality / candidate） ----------

class _MockWriterV:
    """写两版：v1 正常顶替（selected=1）；v2 质检未过 promote=False → 候选（保留 v1）。"""

    def __init__(self, store):
        self.store = store

    def write(self, no, title, goal, research, is_final=False):
        self.store.save_chapter(Chapter(no=no, title=title, content=f"第一版正文{no}" * 300))
        self.store.save_chapter(Chapter(no=no, title=title, content=f"第二版正文{no}" * 300),
                                quality={"score": 55, "passed": False, "issue_count": 3,
                                         "auto_revised": True, "issues": []},
                                promote=False)


def _mock_pipeline(store, writer=None):
    return SimpleNamespace(cfg=store.load_config(), llm=None,
                           chapter_writer=writer or _MockWriterV(store))


@pytest.mark.p0
def test_chapter_feedback_kept_previous(store_with_outline, byok_db,
                                        monkeypatch, cleanup_runs):
    store = store_with_outline
    handle = core._acquire_slot(store.dir.name)
    monkeypatch.setattr(core, "_open_pipeline",
                        lambda s, trace_id="": _mock_pipeline(store))
    gs._run_agent_task(store, handle, "writer.chapter",
                       {"scope": "single", "ch_no": 1}, "", (),
                       "task_f1", "run_f1")
    run = read_run_file(store.dir, "run_f1")
    outs = [o for o in run["outputs"] if o.get("kind") == "chapter"]
    assert outs, run["outputs"]
    fb = outs[0]
    assert fb["key"] == "1"
    assert fb["v"] == 2
    assert fb["kept_previous"] is True
    assert fb["candidate_version"] == 2
    assert fb["selected"] == 1
    assert fb["quality"]["passed"] is False
    assert fb["quality"]["score"] == 55
    assert len(run["billing"]["entries"]) == 2  # 两版都结算（候选同样计费）


# ---------- U1-A：进度分母口径（chapters_done 为书级绝对完成数，与 chapters_total 同口径） ----------

class _MockWriterSimple:
    """只写一版：满足 settle 链路，聚焦进度口径断言。"""

    def __init__(self, store):
        self.store = store

    def write(self, no, title, goal, research, is_final=False):
        self.store.save_chapter(Chapter(no=no, title=title, content=f"正文{no}" * 300))


def _store_5ch_3done(store):
    """5 章大纲 + 预置完成 1-3 章。"""
    store.save_outline(Outline(title="测试书", intro="简介",
                               chapters=[ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}")
                                         for i in range(1, 6)]))
    for no in (1, 2, 3):
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=f"旧正文{no}" * 300))
    return store


@pytest.mark.p0
def test_exec_chapters_progress_all_reaches_total(store, byok_db, cleanup_runs):
    """U1-A：scope=all 补完剩余章 → progress.chapters_done 与 chapters_total 同口径（100%）。"""
    store = _store_5ch_3done(store)
    pipeline = SimpleNamespace(cfg=store.load_config(), llm=None,
                               chapter_writer=_MockWriterSimple(store))
    handle = core._RunHandle()
    gs._exec_chapters(store, pipeline, handle, {"scope": "all"},
                      {"mode": "user_llm", "hq": False, "project_id": store.dir.name},
                      [], [])
    prog = json.loads((store.dir / "task.json").read_text(encoding="utf-8"))["progress"]
    assert prog["phase"] == "chapter"
    assert prog["chapters_total"] == 5
    assert prog["chapters_done"] == 5  # baseline 3 + 本次 2 = 全书 5，而非只报本次 2
    assert prog["current_chapter"] == 5


@pytest.mark.p0
def test_exec_chapters_progress_single_absolute(store, byok_db, cleanup_runs):
    """U1-A：单章重生成 → 进度为书级绝对完成数（baseline+1），而非恒定 20%。"""
    store = _store_5ch_3done(store)
    pipeline = SimpleNamespace(cfg=store.load_config(), llm=None,
                               chapter_writer=_MockWriterSimple(store))
    handle = core._RunHandle()
    gs._exec_chapters(store, pipeline, handle, {"scope": "single", "ch_no": 4},
                      {"mode": "user_llm", "hq": False, "project_id": store.dir.name},
                      [], [])
    prog = json.loads((store.dir / "task.json").read_text(encoding="utf-8"))["progress"]
    assert prog["chapters_total"] == 5
    assert prog["chapters_done"] == 4  # 运行前 3 章 + 本次 1 章
    assert prog["current_chapter"] == 4
