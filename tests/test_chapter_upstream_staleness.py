# -*- coding: utf-8 -*-
"""档1 章节上游过期标记（读时推导）测试 —— P1。

为什么是 P1：本功能只做只读标记 + 前端提示，不触碰结算/鉴权/生成链路，
不属于 P0 门禁（资金、鉴权、数据一致性变更）；但判定错了会给作者错误指引，
故不放 P2。全部用临时 ProjectStore 构造数据，不连 MySQL、不启动 HTTP、不调 LLM。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                             # noqa: E402
from novel_gen.pipeline import chapter_staleness as st               # noqa: E402
from novel_gen.pipeline.manager import NovelPipeline                 # noqa: E402
from novel_gen.store.project import ProjectStore                     # noqa: E402
from novel_gen.store.schemas import Chapter                          # noqa: E402
from novel_gen.store.versioned import VersionedProduct               # noqa: E402
from webapp.routers import projects_chapters as pc                   # noqa: E402
from webapp.routers import projects_staleness as ps                  # noqa: E402

USER = {"id": 1, "username": "tester"}


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="上游标记测试书", total_words=10000,
                      chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _commit_outline(store, tag: str) -> int:
    return VersionedProduct(store, "outline").commit("all", {"intro": f"大纲{tag}"})


def _commit_world(store, tag: str) -> int:
    return VersionedProduct(store, "world").commit("all", {"intro": f"世界观{tag}"})


def _write_chapters(store, nos=(1, 2)) -> None:
    """模拟档2生成：版本库记录 outline/world，同时记录章节级依赖（前文各章 v1）。"""
    for no in nos:
        prev = [{"ch": k, "v": 1} for k in range(1, no)]
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=f"第{no}章正文。"),
                           prev_chapters=prev)


@pytest.fixture
def fresh_book(store):
    """大纲 v1 + 世界观 v1 下写好 1、2 章 → 初始全 fresh。"""
    _commit_outline(store, "v1")
    _commit_world(store, "v1")
    _write_chapters(store, (1, 2))
    return store


# ---------------------------------------------------------------- 判定逻辑

@pytest.mark.p1
def test_outline_bump_marks_stale_with_reason(fresh_book):
    """①大纲升版：受影响章判 stale，reason 指明维度/基准版/当前版/落后几版。"""
    assert st.build_chapter_staleness(fresh_book)["summary"]["stale_count"] == 0

    new_v = _commit_outline(fresh_book, "v2")
    data = st.build_chapter_staleness(fresh_book)

    assert data["summary"]["stale_chapters"] == [1, 2]
    assert data["summary"]["regenerate_from"] == 1
    assert data["summary"]["ranges"] == [{"from": 1, "to": 2}]
    assert data["summary"]["causes"][0]["kind"] == "outline"
    assert data["summary"]["causes"][0]["chapters"] == [1, 2]

    row = data["chapters"][0]
    assert row["status"] == "stale"
    reason = row["reason"]
    assert reason["outline_v"] == new_v
    assert reason["based_on_outline_v"] == 1
    assert reason["based_on_world_v"] == 1
    dim = reason["stale_dims"]["outline"]
    assert dim == {"based_on": 1, "current": new_v, "diff": new_v - 1,
                   "direction": "behind", "changed_at": reason["changed_at"]}
    assert reason["changed_at"]  # 新版 ts 应透出，供前端说明变更时间


@pytest.mark.p1
def test_world_bump_marks_only_world_dimension(fresh_book):
    """②世界观升版：只标记 world 维度，outline 不误报。"""
    _commit_world(fresh_book, "v2")
    data = st.build_chapter_staleness(fresh_book)

    assert data["summary"]["stale_chapters"] == [1, 2]
    for row in data["chapters"]:
        assert row["status"] == "stale"
        assert set(row["reason"]["stale_dims"]) == {"world"}
        assert row["reason"]["stale_dims"]["world"]["based_on"] == 1
        assert row["reason"]["stale_dims"]["world"]["current"] == 2


@pytest.mark.p1
def test_missing_upstream_is_unknown_not_fresh(tmp_path):
    """③无 upstream 记录的存量章：必须是 unknown，不能伪装成 fresh。"""
    store = ProjectStore.create(tmp_path / "legacy", NovelConfig(
        title="存量书", total_words=10000, chapter_words=2500, genre="玄幻"))
    _commit_outline(store, "v1")
    _commit_world(store, "v1")
    _write_chapters(store, (1,))
    # 模拟版本库上线前的老章：抹掉 upstream
    index = store.load_chapter_versions()
    for rec in index.values():
        for h in rec.get("history") or []:
            h.pop("upstream", None)
    store.save_chapter_versions(index)

    data = st.build_chapter_staleness(store)

    assert data["chapters"][0]["status"] == "unknown"
    assert data["chapters"][0]["reason"]["unknown_reason"] == "no_upstream_record"
    assert data["summary"]["unknown_chapters"] == [1]
    assert data["summary"]["fresh_count"] == 0
    assert any("无法判断" in gap for gap in data["data_gaps"])


@pytest.mark.p1
def test_all_latest_is_fresh(fresh_book):
    """④上游未变：全部 fresh，汇总为空且无 data_gaps 误导。"""
    data = st.build_chapter_staleness(fresh_book)

    assert [r["status"] for r in data["chapters"]] == ["fresh", "fresh"]
    summary = data["summary"]
    assert summary["fresh_count"] == 2 and summary["stale_count"] == 0
    assert summary["stale_chapters"] == [] and summary["ranges"] == []
    assert summary["regenerate_from"] is None
    assert summary["current"]["outline_v"] == 1
    assert summary["current"]["world_v"] == 1
    assert data["data_gaps"] == []


# ---------------------------------------------------------------- 档2：前文重写传播

@pytest.mark.p1
def test_prev_chapter_rewrite_marks_downstream_stale(fresh_book):
    """①第3章重写后：第4、5章判 stale，成因标 prev_chapter；1–3 章仍 fresh。"""
    _write_chapters(fresh_book, (1, 2, 3, 4, 5))
    fresh_book.save_chapter(Chapter(no=3, title="第3章", content="第3章重写后的正文。"),
                            source="rewrite")

    data = st.build_chapter_staleness(fresh_book)
    status = {r["chapter"]: r["status"] for r in data["chapters"]}

    assert status == {1: "fresh", 2: "fresh", 3: "fresh", 4: "stale", 5: "stale"}
    reason = data["chapters"][3]["reason"]
    assert reason["prev_rewrites"] == [
        {"ch": 3, "based_on": 1, "current": 2, "direction": "rewritten"}]
    assert reason["stale_causes"] == ["prev_chapter"]
    assert data["summary"]["prev_rewrites"] == [
        {"ch": 3, "based_on": 1, "current": 2,
         "direction": "rewritten", "affected": [4, 5]}]


@pytest.mark.p1
def test_both_causes_coexist(fresh_book):
    """②大纲升版 + 前文重写同时发生：两种成因都报出来。"""
    _write_chapters(fresh_book, (1, 2, 3, 4))
    fresh_book.save_chapter(Chapter(no=2, title="第2章", content="第2章重写。"),
                            source="rewrite")
    _commit_outline(fresh_book, "v2")

    data = st.build_chapter_staleness(fresh_book)
    row4 = data["chapters"][3]
    assert row4["status"] == "stale"
    assert set(row4["reason"]["stale_causes"]) == {"outline", "prev_chapter"}
    assert set(row4["reason"]["stale_dims"]) == {"outline"}
    assert row4["reason"]["prev_rewrites"][0]["ch"] == 2
    kinds = {c["kind"] for c in data["summary"]["causes"]}
    assert kinds == {"outline"}


@pytest.mark.p1
def test_legacy_book_with_bumped_outline_is_stale_not_unknown(fresh_book):
    """①口径修正：旧书（无 prev_chapters）但大纲已升版 → 直接 stale，成因 outline。"""
    index = fresh_book.load_chapter_versions()
    for rec in index.values():
        for h in rec.get("history") or []:
            (h.get("upstream") or {}).pop("prev_chapters", None)
    fresh_book.save_chapter_versions(index)
    _commit_outline(fresh_book, "v2")

    data = st.build_chapter_staleness(fresh_book)

    assert [r["status"] for r in data["chapters"]] == ["stale", "stale"]
    assert data["summary"]["stale_count"] == 2
    assert data["summary"]["unknown_count"] == 0          # 不再整本变 unknown
    assert data["chapters"][0]["reason"]["stale_causes"] == ["outline"]
    assert data["summary"]["causes"] == [
        {"kind": "outline", "label": "大纲", "chapters": [1, 2]}]


@pytest.mark.p1
def test_mixed_summary_counts_follow_new_priority(fresh_book):
    """③新旧混合：有依赖且一致=fresh、无依赖且维度一致=unknown、维度不一致=stale。"""
    _write_chapters(fresh_book, (1, 2, 3))
    index = fresh_book.load_chapter_versions()
    # 第1章模拟旧书：抹掉依赖记录（维度保持与当前一致）
    for h in index["1"]["history"]:
        (h.get("upstream") or {}).pop("prev_chapters", None)
    # 第3章模拟基于更早大纲生成（明确不一致）
    for h in index["3"]["history"]:
        (h.get("upstream") or {})["outline_v"] = 0
    fresh_book.save_chapter_versions(index)

    data = st.build_chapter_staleness(fresh_book)
    summary = data["summary"]

    assert {r["chapter"]: r["status"] for r in data["chapters"]} == {
        1: "unknown", 2: "fresh", 3: "stale"}
    assert summary["fresh_count"] == 1
    assert summary["stale_count"] == 1
    assert summary["unknown_count"] == 1
    assert summary["stale_chapters"] == [3]
    assert summary["unknown_chapters"] == [1]
    assert summary["regenerate_from"] == 3
    assert summary["causes"] == [{"kind": "outline", "label": "大纲", "chapters": [3]}]


@pytest.mark.p1
def test_legacy_chapter_without_prev_dependency_is_unknown(fresh_book):
    """③有 upstream 但没有 prev_chapters（档2前生成）→ unknown，不能判 fresh。"""
    _write_chapters(fresh_book, (1, 2))
    index = fresh_book.load_chapter_versions()
    for h in index["2"]["history"]:
        (h.get("upstream") or {}).pop("prev_chapters", None)
    fresh_book.save_chapter_versions(index)

    data = st.build_chapter_staleness(fresh_book)
    row2 = data["chapters"][1]
    assert row2["status"] == "unknown"
    assert row2["reason"]["unknown_reason"] == "no_prev_dependency_record"
    # 首章记了空依赖列表 → 仍是 fresh
    assert data["chapters"][0]["status"] == "fresh"
    assert data["summary"]["unknown_chapters"] == [2]
    assert any("前文是否被重写" in gap for gap in data["data_gaps"])


# ---------------------------------------------------------------- 档2：版本切换同步账本

def _prep_switch(store) -> NovelConfig:
    """准备一章两版正文 + 场景记录 + 只开章末状态的账本配置。"""
    _commit_outline(store, "v1")
    _commit_world(store, "v1")
    store.save_chapter(Chapter(no=1, title="第1章", content="A版正文。"), prev_chapters=[])
    store.save_chapter(Chapter(no=1, title="第1章", content="B版正文。"), prev_chapters=[])
    store.save_scenes(1, [{"no": 1, "goal": "场景A"}])
    cfg = store.load_config()
    cfg.character_state_tracking = False
    cfg.reveal_tracking = False
    cfg.reader_ledger = False
    cfg.book_memory = False
    cfg.chapter_state_tracking = True
    store.save_config(cfg)
    return cfg


def _patch_route(monkeypatch, store, pipe=None):
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: store)
    monkeypatch.setattr(pc, "_assert_not_locked", lambda *a, **k: None)
    monkeypatch.setattr(pc, "_open_pipeline",
                        lambda s, trace_id="": pipe or MagicMock())


@pytest.mark.p1
def test_version_switch_syncs_ledgers(store, monkeypatch):
    """④切换版本后走 sync_after_rewrite：摘要刷新、场景标脏、章末账本状态可读。"""
    cfg = _prep_switch(store)
    pipe = NovelPipeline(cfg, store, MagicMock())
    monkeypatch.setattr(pipe.chapter_writer, "_gen_summary", lambda *a, **k: "B版新摘要")
    monkeypatch.setattr(
        "novel_gen.pipeline.chapter_state.extract_chapter_state",
        lambda *a, **k: {"chapter": 1, "numbers": {}})
    _patch_route(monkeypatch, store, pipe)

    res = pc.select_chapter_version("p1", 1, 1, USER)

    assert res["selected_version"] == 1
    assert res["ledger"]["chapter_state"] in {"written", "skipped_backward"}
    assert res["ledger"]["summary"] is True
    assert res["ledger"]["scenes_stale"] is True
    assert any(s.get("summary") == "B版新摘要" for s in store.load_summaries())
    assert "stale" in (store.load_scenes().get("1") or {})


@pytest.mark.p1
def test_ledger_sync_failure_does_not_block_switch(store, monkeypatch, caplog):
    """⑤账本同步炸了：版本切换仍成功、正文已切换，失败留 ERROR 日志并可感知。"""
    _prep_switch(store)
    _patch_route(monkeypatch, store, MagicMock())

    def _boom(*a, **k):
        raise RuntimeError("ledger boom")

    monkeypatch.setattr(pc, "sync_after_rewrite", _boom)
    with caplog.at_level(logging.ERROR):
        res = pc.select_chapter_version("p1", 1, 1, USER)

    assert res["selected_version"] == 1
    assert res["ledger"]["status"] == "failed"
    assert "ledger boom" in caplog.text
    assert store.load_chapter(1).content == "A版正文。"


@pytest.mark.p1
def test_route_forbids_other_users_project(store, monkeypatch):
    """⑤越权访问：权限校验拒绝时原样抛 403，不读取项目数据。"""
    def _deny(_pid, _user):
        raise HTTPException(status_code=403, detail="无权访问该项目")

    monkeypatch.setattr(ps, "_check_project_access", _deny)
    with pytest.raises(HTTPException) as exc:
        ps.chapters_staleness("p1", USER)
    assert exc.value.status_code == 403


@pytest.mark.p1
def test_route_returns_404_for_unknown_project(store, monkeypatch):
    """⑤项目不存在：_find_project 抛 404，接口不吞成 200。"""
    monkeypatch.setattr(ps, "_check_project_access", lambda pid, user: None)

    def _missing(_pid):
        raise HTTPException(status_code=404, detail="项目不存在")

    monkeypatch.setattr(ps, "_find_project", _missing)
    with pytest.raises(HTTPException) as exc:
        ps.chapters_staleness("nope", USER)
    assert exc.value.status_code == 404


@pytest.mark.p1
def test_route_success_shape(fresh_book, monkeypatch):
    """接口成功路径：只读返回 project_id + chapters + summary + data_gaps。"""
    monkeypatch.setattr(ps, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(ps, "_find_project", lambda pid: fresh_book)

    data = ps.chapters_staleness("p1", USER)
    assert data["project_id"] == "p1"
    assert {"chapters", "summary", "data_gaps"} <= set(data)
