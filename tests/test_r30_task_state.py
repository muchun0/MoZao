# -*- coding: utf-8 -*-
"""R30 M0：任务契约 / 状态机 / 快照（P0-1 / P0-2 单测）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline import task_contract, task_snapshot, task_state


def _store(tmp_path: Path) -> ProjectStore:
    cfg = NovelConfig(title="R30状态机", chapter_words=500, total_words=2000)
    return ProjectStore.create(tmp_path / "p-state", cfg)


def test_contract_scope_and_baseline(tmp_path):
    store = _store(tmp_path)
    manifest = task_contract.build_manifest(
        store, "t-001", "writer.chapter", {"scope": "all", "_chapters": [1, 2, "3"]},
        operator="张阅")
    assert manifest["kind"] == "chapter_write"
    assert manifest["scope"] == {"mode": "explicit", "ch": [1, 2, 3]}
    assert manifest["baseline"]["book_facts_at_ch"] == 0
    assert manifest["handover_policy"]["sample_rate"] == 20
    # 只读：建契约不得写 state
    assert not task_state.state_path(store, "t-001").exists()


def test_transition_legal_and_rejection_logged(tmp_path):
    store = _store(tmp_path)
    manifest = task_contract.build_manifest(store, "t-002", "writer.chapter", {})
    task_state.create_state(store, manifest, state=task_state.QUEUED)
    task_state.transition(store, "t-002", task_state.RUNNING, by="start")
    task_state.transition(store, "t-002", task_state.DONE, by="system")
    with pytest.raises(task_state.TaskStateError):
        task_state.transition(store, "t-002", task_state.RUNNING, by="system")
    log = task_state.rejection_path(store, "t-002")
    assert log.exists()
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert row["from"] == "done" and row["to"] == "running"
    # done→queued 允许（重跑），随后可 running
    task_state.transition(store, "t-002", task_state.QUEUED, by="user", reason="rerun")
    task_state.transition(store, "t-002", task_state.RUNNING, by="start")


def test_progress_projection_to_task_json(tmp_path):
    store = _store(tmp_path)
    manifest = task_contract.build_manifest(store, "t-003", "writer.chapter", {})
    task_state.create_state(store, manifest)
    (Path(store.dir) / "task.json").write_text(
        json.dumps({"kind": "agent_run", "task_id": "t-003", "agent": "writer.chapter"},
                   ensure_ascii=False), encoding="utf-8")
    task_state.update_progress(store, "t-003", phase="chapter", chapters_done=1,
                               task_total=3)
    task_state.project_now(store, "t-003")
    meta = json.loads((Path(store.dir) / "task.json").read_text(encoding="utf-8"))
    assert meta["progress"]["chapters_done"] == 1
    assert meta["progress"]["task_total"] == 3
    assert meta["state"] == "queued" and meta["wait_human"] is False
    state = task_state.load_state(store, "t-003")
    assert state["progress"]["chapters_done"] == 1


def test_pending_records_remaining_and_wait_human(tmp_path):
    store = _store(tmp_path)
    manifest = task_contract.build_manifest(store, "t-004", "writer.chapter", {})
    task_state.create_state(store, manifest)
    task_state.transition(store, "t-004", task_state.RUNNING)
    task_state.mark_pending(store, "t-004", handover_ids=["h-1"],
                            remaining_chapters=[3], reason="rule_hit")
    state = task_state.load_state(store, "t-004")
    assert state["state"] == "pending_review"
    assert state["resume"]["remaining_chapters"] == [3]
    assert state["pending_handover_ids"] == ["h-1"]


def test_snapshot_steps_cap_and_ref(tmp_path):
    store = _store(tmp_path)
    ref = task_snapshot.record_step(store, "t-005", "run_start",
                                    in_refs=["world/v1.json"], model="m",
                                    switches={"handover_v1": True})
    assert ref == "runs/t-005/snapshot.json#step-1"
    for i in range(2, 502):
        task_snapshot.record_step(store, "t-005", f"n{i}")
    snap = task_snapshot.read_snapshot(store, "t-005")
    assert len(snap["steps"]) == task_snapshot.MAX_STEPS
    assert snap["steps"][0]["node"] == "run_start"
    assert snap["next_seq"] == 502


def test_snapshot_degrades_on_bad_task_id(tmp_path):
    store = _store(tmp_path)
    assert task_snapshot.record_step(store, "", "run_start") == ""
