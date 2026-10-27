# -*- coding: utf-8 -*-
"""run 侧写 provenance 留痕测试（P1，TECH §3.3 / §3.9 / §14）。

覆盖：run 侧写字段完整、prompt_version 格式 <agent>@v<n>、user_prompt 4KB 截断标记、
无 run 上下文时版本 commit 降级 run_id 缺省（不报错不中断）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.run_context import RunContext, read_run_file, write_run_file
from novel_gen.store.schemas import ChapterPlan, Outline
from novel_gen.store.versioned import VersionedProduct
from webapp import core
from webapp import db
from webapp import generate_service as gs

# TECH §3.3 必备字段清单
REQUIRED_FIELDS = {
    "run_id", "task_id", "project_id", "agent", "started_at", "finished_at",
    "status", "params", "prompt_version", "prompt_sha256", "user_prompt",
    "skill_ids", "model", "temperature", "seed", "trace_id", "billing_mode",
    "billing", "outputs", "compliance_rejected",
}


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def byok_db(monkeypatch):
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


class _MockOutlinePipeline:
    """假 pipeline：run_outline 直接落一份大纲（不触 LLM）。"""

    def __init__(self, store):
        self.store = store
        self.cfg = store.load_config()

    def run_outline(self, silent=False):
        self.store.save_outline(Outline(
            title="测试书", intro="简介",
            chapters=[ChapterPlan(no=1, title="第一章", goal="目标")]))

    def run_editor_review(self, silent=False):
        pass


@pytest.mark.p1
def test_run_sidecar_fields_complete(store, byok_db, monkeypatch, cleanup_runs):
    """architect.outline 全链路：run 侧写字段齐全，prompt_version 为 <agent>@v1。"""
    pipeline = _MockOutlinePipeline(store)
    monkeypatch.setattr(core, "_open_pipeline", lambda s, trace_id="": pipeline)
    handle = core._acquire_slot(store.dir.name)
    gs._run_agent_task(store, handle, "architect.outline", {}, "帮我加强冲突",
                       (1, 2), "task_prov1", "run_prov1")
    run = read_run_file(store.dir, "run_prov1")
    assert run is not None
    assert REQUIRED_FIELDS <= set(run), f"缺字段: {REQUIRED_FIELDS - set(run)}"
    assert run["status"] == "ok"
    assert run["prompt_version"] == "architect.outline@v1"
    assert len(run["prompt_sha256"]) == 64
    assert run["user_prompt"] == "帮我加强冲突"
    assert run["skill_ids"] == [1, 2]
    assert run["seed"] is None
    assert run["billing_mode"] == "user_llm"
    assert run["billing"]["billed_price"] == 0.0  # BYOK 不扣费
    # 大纲产物已提交版本并记账
    assert run["outputs"] and run["outputs"][0]["kind"] == "outline"
    assert run["billing"]["entries"], "产物版本应产生结算条目"
    # 人设已注入 pipeline 内存态 persona_override（默认=角色底色 md，不落 config.json）
    assert "结构设计师" in pipeline.cfg.persona_override
    assert "帮我加强冲突" in pipeline.cfg.persona_override
    # 人设不再覆盖 custom_prompt（建书时为空，落盘也为空）
    assert store.load_config().custom_prompt == ""


@pytest.mark.p1
def test_user_prompt_truncation(store):
    """user_prompt >4KB 截断并打标记（§3.3）。"""
    big = "长" * 5000  # 15000 字节
    path = write_run_file(store.dir, {
        "run_id": "r_trunc", "task_id": "t", "project_id": store.dir.name,
        "agent": "writer.chapter", "started_at": 1.0, "finished_at": 2.0,
        "status": "ok", "params": {}, "prompt_version": "writer.chapter@v1",
        "prompt_sha256": "x", "user_prompt": big, "skill_ids": [],
        "model": "", "temperature": 0.8, "seed": None, "trace_id": "",
        "billing_mode": "user_llm",
        "billing": {"billed_words": 0, "billed_price": 0.0, "entries": [],
                    "wasted_words": 0},
        "outputs": [], "compliance_rejected": False})
    run = json.loads(path.read_text(encoding="utf-8"))
    assert run["user_prompt_truncated"] is True
    assert len(run["user_prompt"].encode("utf-8")) <= 4 * 1024


@pytest.mark.p1
def test_run_files_prune_keep_200(store):
    """run 侧写保留最近 200 个（按 finished_at 删最旧，I-4）。"""
    from novel_gen.store.run_context import AGENT_RUNS_KEEP
    base = {"task_id": "t", "project_id": store.dir.name, "agent": "writer.chapter",
            "params": {}, "prompt_version": "writer.chapter@v1", "prompt_sha256": "x",
            "user_prompt": "", "skill_ids": [], "model": "", "temperature": 0.8,
            "seed": None, "trace_id": "", "billing_mode": "user_llm",
            "billing": {"billed_words": 0, "billed_price": 0.0, "entries": [],
                        "wasted_words": 0},
            "outputs": [], "compliance_rejected": False}
    for i in range(AGENT_RUNS_KEEP + 5):
        write_run_file(store.dir, {**base, "run_id": f"r{i:03d}",
                                   "started_at": float(i), "finished_at": float(i),
                                   "status": "ok"})
    remaining = sorted(p.stem for p in (store.dir / "agent_runs").glob("*.json"))
    assert len(remaining) == AGENT_RUNS_KEEP
    assert "r000" not in remaining and "r004" not in remaining  # 最旧 5 个已删
    assert f"r{AGENT_RUNS_KEEP + 4:03d}" in remaining


@pytest.mark.p1
def test_commit_without_run_context_degrades(store):
    """无 run 上下文：commit_head 不写 run_id（降级不中断，§3.9 方案 A）。"""
    assert store.current_run() is None
    store.save_outline(Outline(title="t", intro="i",
                               chapters=[ChapterPlan(no=1, title="a", goal="g")]))
    v = VersionedProduct(store, "outline").commit_head("all")
    assert v == 1
    versions = json.loads(
        (store.dir / "outline" / "versions.json").read_text(encoding="utf-8"))
    assert "run_id" not in versions["all"]["history"][-1]


@pytest.mark.p1
def test_commit_with_run_context_records_run_id(store):
    """begin_run 后 commit 自动带 run_id（run 上下文透传，评审 B-3）。"""
    ctx = RunContext(run_id="run_ctx1", agent="architect.outline")
    store.begin_run(ctx)
    try:
        store.save_outline(Outline(title="t", intro="i",
                                   chapters=[ChapterPlan(no=1, title="a", goal="g")]))
        v = VersionedProduct(store, "outline").commit_head("all")
        assert v == 1
        versions = json.loads(
            (store.dir / "outline" / "versions.json").read_text(encoding="utf-8"))
        assert versions["all"]["history"][-1].get("run_id") == "run_ctx1"
    finally:
        store.end_run()
    assert store.current_run() is None
