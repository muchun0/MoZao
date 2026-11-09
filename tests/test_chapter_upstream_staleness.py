# -*- coding: utf-8 -*-
"""档1 章节上游过期标记（读时推导）测试 —— P1。

为什么是 P1：本功能只做只读标记 + 前端提示，不触碰结算/鉴权/生成链路，
不属于 P0 门禁（资金、鉴权、数据一致性变更）；但判定错了会给作者错误指引，
故不放 P2。全部用临时 ProjectStore 构造数据，不连 MySQL、不启动 HTTP、不调 LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                             # noqa: E402
from novel_gen.pipeline import chapter_staleness as st               # noqa: E402
from novel_gen.store.project import ProjectStore                     # noqa: E402
from novel_gen.store.schemas import Chapter                          # noqa: E402
from novel_gen.store.versioned import VersionedProduct               # noqa: E402
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
    """在既有上游版本下写章节：版本库会自动记录 upstream。"""
    for no in nos:
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=f"第{no}章正文。"))


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


# ---------------------------------------------------------------- 接口层鉴权

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
