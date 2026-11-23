# -*- coding: utf-8 -*-
"""R30 M0+M1 端到端冒烟：写章 → 硬红线撞单 → 待审 → 放行续跑（不重写/不重扣）。

真实链路组件：`ProjectStore` 落盘 + `generate_service._exec_chapters` 真实循环 +
真实 `settle_ledger` 结算 + `task_state` 状态机 + 交接/提案落盘。
仅替换 LLM 写作（FakeWriter）、章节账本提交与 DB 读写。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import World, Outline, Chapter
from novel_gen.pipeline import task_contract, task_state, handover, ledger_proposal
from webapp import core_runtime as core
from webapp import generate_service as gs
from webapp import settle_ledger

TASK_ID = "t-e2e-r30-001"


class FakeWriter:
    """按章号落盘正文 + review.json；第 2 章命中「设定冲突」硬红线。"""

    def __init__(self, store: ProjectStore):
        self.store = store
        self.reviews: list[dict] = []

    def write(self, no, title, goal, research, is_final=False, unstable_chapters=None):
        words = "陆沉在城里走访旧友，确认了灵气复苏的边界与代价。" * 40
        self.store.save_chapter(Chapter(no=no, title=title, goal=goal,
                                        content=f"第{no}章。{words}"))
        issues = [{"level": "大改", "type": "设定冲突",
                   "detail": "陆沉凭空使用未登记火球术"}] if no == 2 else []
        self.reviews.append({"chapter": no, "issues": issues,
                             "quality_check": {"passed": True, "score": 80}})
        self.store._write_json(self.store.review_path(), self.reviews)


class FakePipeline:
    def __init__(self, store: ProjectStore):
        self.store = store
        self.chapter_writer = FakeWriter(store)

    def commit_chapter_ledgers(self, no: int, source: str = "") -> None:
        if no == 1:
            # 模拟章末人物账本：世界观未登记人物触发 add 提案
            self.store.save_chapter_state({"chapter": 1, "characters": {"小九": "跟随"},
                                           "items": {}})


def _project(tmp_path: Path, *, on: bool = True) -> ProjectStore:
    cfg = NovelConfig(title="R30端到端", chapter_words=500, total_words=2000,
                      task_state_machine=on, handover_v1=on, ledger_proposal=on,
                      handover_sample_rate=0, handover_low_confidence_count=3)
    store = ProjectStore.create(tmp_path / "p-e2e", cfg)
    store.save_world(World.from_dict({
        "world_view": "灵气复苏",
        "characters": [{"name": "陆沉", "role": "主角", "desc": "剑修"}],
    }))
    store.save_outline(Outline.from_dict({
        "title": "R30端到端",
        "chapters": [{"title": f"第{i}章", "goal": f"推进目标{i}"} for i in range(1, 4)],
    }))
    return store


@pytest.fixture()
def db_stub(monkeypatch):
    monkeypatch.setattr(gs.db, "project_owner", lambda pid: 1, raising=False)
    monkeypatch.setattr(gs.db, "get_user", lambda uid: {"id": 1, "balance": 100.0},
                        raising=False)
    monkeypatch.setattr(gs.db, "get_user_llm", lambda uid: {}, raising=False)
    monkeypatch.setattr(gs.db, "change_balance", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(gs.db, "update_project_price", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(gs.db, "project_discount", lambda pid: 1.0, raising=False)
    monkeypatch.setattr(gs, "commit_chapter_account",
                        lambda store, cfg, no: None, raising=False)


def _ledger_entries(store: ProjectStore) -> dict:
    return dict((settle_ledger.load_ledger(store) or {}).get("entries") or {})


def test_e2e_pending_then_resume_without_recharge(tmp_path, db_stub, monkeypatch):
    store = _project(tmp_path)
    pipeline = FakePipeline(store)
    params = {"scope": "all", "_task_id": TASK_ID}
    manifest = task_contract.build_manifest(store, TASK_ID, "writer.chapter", params,
                                            operator="张阅")
    task_state.create_state(store, manifest)
    core._mark_task(store, "agent_run", task_id=TASK_ID, agent="writer.chapter",
                    params=params, run_id="r-e2e-1", user_prompt="", skill_ids=[])
    task_state.transition(store, TASK_ID, task_state.RUNNING, by="test")
    task_state.project_now(store, TASK_ID)

    billing = {"mode": "platform", "project_id": store.dir.name}
    handle = core._RunHandle()
    gs._exec_chapters(store, pipeline, handle, params, billing, [], [])

    # 第 2 章硬红线 → 停在待审，第 3 章未写
    assert handle.pending_review is True
    assert sorted(c.no for c in store.list_chapters()) == [1, 2]
    state = task_state.load_state(store, TASK_ID)
    assert state["state"] == task_state.PENDING_REVIEW
    assert state["resume"]["remaining_chapters"] == [3]
    meta = json.loads((Path(store.dir) / "task.json").read_text(encoding="utf-8"))
    assert meta["wait_human"] is True and meta["state"] == "pending_review"
    hs = handover.list_handovers(store, status=handover.OPEN)
    assert len(hs) == 1 and hs[0]["blocking"]
    assert hs[0]["dedupe_key"] == "ch:2:rule:设定冲突"
    assert "#step-" in hs[0]["snapshot_ref"]
    props = ledger_proposal.list_proposals(store, status=ledger_proposal.OPEN)
    assert any("小九" in p["path"] for p in props)
    entries_before = _ledger_entries(store)
    assert len(entries_before) == 2

    # 放行续跑：替换真实线程，只校验“沿用 task_id + 只跑剩余章”
    captured: dict = {}

    def _fake_run(store_, handle_, agent, params_, prompt, skills, task_id, run_id):
        captured.update({"agent": agent, "params": dict(params_), "task_id": task_id,
                         "run_id": run_id, "handle": handle_})

    monkeypatch.setattr(gs, "_run_agent_task", _fake_run)
    gs.resume_pending_run(store, TASK_ID, {"username": "张阅"})
    time.sleep(0.2)
    assert captured["task_id"] == TASK_ID
    assert captured["params"]["_chapters"] == [3]
    core._release_slot(store.dir.name)  # 假线程未走真实收尾，手动释放槽位

    state = task_state.load_state(store, TASK_ID)
    assert state["state"] == task_state.RUNNING
    assert state["pending_handover_ids"] == []
    meta = json.loads((Path(store.dir) / "task.json").read_text(encoding="utf-8"))
    assert meta["wait_human"] is False
    assert handover.list_handovers(store, status=handover.OPEN) == []
    assert any(h["resolution"] == "approve"
               for h in handover.list_handovers(store, status="", limit=20))

    # 续跑后按同一 params 继续：只写第 3 章，1/2 章账本逐字不动
    handle2 = core._RunHandle()
    gs._exec_chapters(store, pipeline, handle2, captured["params"], billing, [], [])
    assert sorted(c.no for c in store.list_chapters()) == [1, 2, 3]
    entries_after = _ledger_entries(store)
    assert len(entries_after) == 3
    for ek, row in entries_before.items():
        assert entries_after[ek] == row  # 已完成章未重写 → 未重复结算
    task_state.transition(store, TASK_ID, task_state.DONE, by="test")
    core._clear_task(store)
    assert task_state.load_state(store, TASK_ID)["state"] == task_state.DONE


def test_e2e_flags_off_behave_as_legacy(tmp_path, db_stub):
    """三开关全关：照常写章结算，不产 runs/ 状态机与任何工单（回退兼容）。"""
    store = _project(tmp_path, on=False)
    params = {"scope": "single", "ch_no": 1}
    core._mark_task(store, "agent_run", task_id="t-legacy", agent="writer.chapter",
                    params=params, run_id="r-legacy", user_prompt="", skill_ids=[])
    gs._exec_chapters(store, FakePipeline(store), core._RunHandle(), params,
                      {"mode": "platform", "project_id": store.dir.name}, [], [])
    assert [c.no for c in store.list_chapters()] == [1]
    assert not task_state.state_path(store, "t-legacy").exists()
    assert not (store.runs_dir() / "t-legacy").exists()
    assert handover.list_handovers(store, status="", limit=10) == []
    assert ledger_proposal.list_proposals(store, status="") == []
