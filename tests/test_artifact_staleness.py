# -*- coding: utf-8 -*-
"""R13 派生记录标脏：只读判定口径的单元测试。

不连 MySQL、不启动 HTTP 服务、不触发生成；全部用临时 ProjectStore 构造数据。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from novel_gen.config import NovelConfig
from novel_gen.pipeline import artifact_staleness as st
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, ForeshadowItem, World
from webapp.routers import projects_consistency as pc

USER = {"id": 1, "username": "tester"}
OLD = 1_000     # 生成时的版本/正文时间（固定旧时间，保证判定确定性）
BASE = 1_010    # 派生记录写入时间（略晚于生成，正常态应是 fresh）


def _store(tmp_path) -> ProjectStore:
    cfg = NovelConfig(title="标脏测试书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _age_versions(store):
    """把版本历史时间统一拨回 OLD，避免真实时钟让判定不可复现。"""
    index = store.load_chapter_versions()
    for rec in index.values():
        for h in rec.get("history") or []:
            h["ts"] = OLD
            h["source"] = "ai"
    store.save_chapter_versions(index)


def _seed_artifacts(store, nos, finished_at=BASE):
    """给每章写齐 4 类派生记录，并写入指定入账时间。"""
    chapters = {}
    for no in nos:
        store.save_scenes(no, [{"location": f"地点{no}", "event": f"事件{no}"}], {})
        store.save_beat_gate(no, {"coverage": 1.0, "verdict": "ok", "planned": [], "missing": []})
        store.append_summary(no, f"第{no}章摘要")
        chapters[str(no)] = {"status": "done", "done": ["character_state"], "failed": [],
                             "finished_at": finished_at}
    store.save_chapter_commit({"chapters": chapters, "updated_ch": max(nos)})


@pytest.fixture
def book(tmp_path):
    store = _store(tmp_path)
    for no in (1, 2):
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=f"林舟在第{no}章行动。"))
        # HEAD mtime 拨回旧时间，模拟「生成完成后没有动过」
        os.utime(store.chapter_path(no), (OLD, OLD))
    _age_versions(store)
    _seed_artifacts(store, [1, 2])
    return store


def test_manual_edit_marks_stale(book):
    edited = book.load_chapter(1)
    edited.content = "林舟在第1章改成了另一条行动线。"
    book.save_chapter(edited, source="user")

    data = st.analyze(book)
    assert data["has_stale"] is True
    assert data["stale_chapters"] == [1]
    item = data["items"][0]
    assert item["chapter"] == 1 and item["manual"] is True
    assert item["artifacts"] == ["scenes", "beat_gate", "summaries", "chapter_commit"]
    assert data["notice"]["level"] == "warning"
    assert "第 1 章" in data["notice"]["text"] and "场景清单" in data["notice"]["text"]
    # 提示必须给出可执行的消除方式，且不在保存时自动重算
    assert "重新生成" in data["notice"]["advice"]


def test_untouched_chapter_not_reported(book):
    """未改章的章节不误报（第 2 章仍停留在入账之后的基线）。"""
    data = st.analyze(book)
    assert data["has_stale"] is False
    assert data["stale_chapters"] == []
    assert data["notice"] is None


def test_recomputed_after_edit_is_fresh(book):
    """改章后又重新生成（入账时间晚于改稿）→ 不再标脏。"""
    edited = book.load_chapter(1)
    edited.content = "改过的正文"
    book.save_chapter(edited, source="user")
    _seed_artifacts(book, [1, 2], finished_at=int(time.time()) + 5)

    data = st.analyze(book)
    assert data["has_stale"] is False


def test_missing_baseline_degrades_to_unknown(tmp_path):
    """老数据没有入账时间 / 版本时间 → 如实降级为「无法判断」，不猜成过期。"""
    store = _store(tmp_path)
    store.save_chapter(Chapter(no=1, title="第1章", content="正文。"))
    _seed_artifacts(store, [1])
    store.save_chapter_commit({"chapters": {}, "updated_ch": 0})
    store.chapter_versions_path().unlink()  # 抹掉版本基线，模拟老书
    store.projection_log_path().unlink(missing_ok=True)

    data = st.analyze(store)
    assert data["has_stale"] is False
    assert data["unknown_chapters"] == [1]
    single = st.analyze(store, chapter_no=1)
    assert single["notice"]["level"] == "info"
    assert "无法判断" in single["notice"]["title"]


def test_cross_chapter_isolation(book):
    """只标改过的那一章，其他章的记录与视图不受影响。"""
    edited = book.load_chapter(1)
    edited.content = "第1章新正文"
    book.save_chapter(edited, source="user")

    data = st.analyze(book)
    assert data["stale_chapters"] == [1]
    assert all(no == 1 for no in data["stale_by_view"]["scenes"])
    assert set(data["stale_by_view"]) == {"scenes", "beat_gate", "summaries", "chapter_commit"}


def test_single_chapter_view_only_checks_that_chapter(book):
    data = st.analyze(book, chapter_no=2)
    assert data["checked_chapters"] == 1
    assert data["has_stale"] is False


def test_only_existing_artifacts_are_listed(tmp_path):
    """该章只有场景清单时，不把不存在的记录也算作受影响。"""
    store = _store(tmp_path)
    store.save_chapter(Chapter(no=1, title="第1章", content="正文。"))
    _age_versions(store)
    os.utime(store.chapter_path(1), (OLD, OLD))
    store.save_scenes(1, [{"event": "事件"}], {})
    store.save_chapter_commit({"chapters": {"1": {"status": "done", "finished_at": BASE}},
                               "updated_ch": 1})
    edited = store.load_chapter(1)
    edited.content = "改过"
    store.save_chapter(edited, source="user")

    data = st.analyze(store)
    assert data["items"][0]["artifacts"] == ["scenes", "chapter_commit"]


def test_analyze_never_raises_on_broken_store(monkeypatch, tmp_path):
    """读取异常时必须降级为「逐章 unknown」，不能把 5xx 冒到接口层，也不能伪装成没问题。"""
    store = _store(tmp_path)
    store.save_chapter(Chapter(no=1, title="第1章", content="正文。"))

    def _boom(_store):
        raise RuntimeError("boom")

    monkeypatch.setattr(st, "_artifact_sets", _boom)
    data = st.analyze(store)
    assert data["has_stale"] is False and data["items"] == []
    assert data["degraded"] is True
    assert data["unknown_chapters"] == [1]
    assert data["notice"]["level"] == "info"


# ---------------------------------------------------------------- 接口层接线

def test_overview_carries_staleness_and_marks(book, monkeypatch):
    import json
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: book)
    (book.dir / "verify_issues.json").write_text(json.dumps([
        {"chapter": 1, "problem": "时间线矛盾", "suggestion": "统一时间线"},
    ], ensure_ascii=False), encoding="utf-8")

    edited = book.load_chapter(1)
    edited.content = "改过之后的正文"
    book.save_chapter(edited, source="user")

    data = pc.consistency_overview("p1", USER)
    assert data["staleness"]["has_stale"] is True
    assert data["staleness"]["stale_chapters"] == [1]
    # 冲突条目与覆盖视图都带上了「记录可能过期」的定位信息
    assert any(it.get("record_stale") for it in data["conflicts"]["items"])
    assert data["coverage"]["views"][0]["stale_chapters"] == [1]


def test_foreshadow_route_marks_stale(book, monkeypatch):
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: book)
    book.save_world(World(foreshadowing=[
        ForeshadowItem(name="密令", content="半张密令", buried_ch=1, reveal_ch=2),
    ]))
    book.save_foreshadow_commitment({"version": 1, "updated_ch": 2, "items": [
        {"name": "密令", "revealed_ch": 1},
    ]})
    edited = book.load_chapter(1)
    edited.content = "改过之后的正文"
    book.save_chapter(edited, source="user")

    data = pc.consistency_foreshadow("p1", state="", offset=0, limit=20, user=USER)
    assert data["staleness"]["has_stale"] is True
    assert data["items"] and data["items"][0]["record_stale"] is True


def test_chain_of_thought_carries_staleness(book, monkeypatch):
    from webapp.routers import projects_chapters as pch
    monkeypatch.setattr(pch, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pch, "_find_project", lambda pid: book)

    edited = book.load_chapter(1)
    edited.content = "改过之后的正文"
    book.save_chapter(edited, source="user")

    data = pch.chapter_chain_of_thought("p1", 1, USER)
    assert data["staleness"]["has_stale"] is True
    assert data["staleness"]["notice"]["title"] == "记录可能过期"
    # 同章未改时不应出现提示
    assert pch.chapter_chain_of_thought("p1", 2, USER)["staleness"]["notice"] is None


def test_coverage_route_rows_carry_stale_chapters(book, monkeypatch):
    """独立 GET /consistency/coverage 的行也要带 stale_chapters（REQ §4.2 判据）。"""
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: book)

    # 未改章时：每行 stale_chapters 存在且为空
    data = pc.consistency_coverage("p1", 0, USER)
    rows = data["views"]
    assert rows and all("stale_chapters" in r for r in rows)
    assert all(r["stale_chapters"] == [] for r in rows)

    # 改第 2 章后：四类章级记录的行都精确标出 [2]，其他视图不受影响
    edited = book.load_chapter(2)
    edited.content = "改过之后的正文"
    book.save_chapter(edited, source="user")
    data = pc.consistency_coverage("p1", 0, USER)
    by_view = {r["view"]: r["stale_chapters"] for r in data["views"]}
    for view in ("scenes", "beat_gate", "summaries", "chapter_commit"):
        assert by_view[view] == [2]


def test_select_version_marks_stale(book):
    """回退（select 旧版本）后正文与派生记录对不上，必须被判成 stale。

    依赖：VersionedProduct.select 在索引上记 selected_at；判定取 max(版本 ts, selected_at)。
    回退不改历史 ts，若不记 selected_at 这一场景会漏报。
    """
    chapter = book.load_chapter(1)
    chapter.content = "第1章第二版正文"
    book.save_chapter(chapter)
    _age_versions(book)  # 版本时间拨回 OLD：保证「回退」是唯一的新事件
    assert book.select_version(1, 1) is not None

    data = st.analyze(book)
    assert data["has_stale"] is True
    assert data["stale_chapters"] == [1]
    assert data["notice"]["level"] == "warning"
    assert data["notice"]["title"] == "有 1 章的记录可能过期"
    assert "回退到旧版本" in data["notice"]["text"]  # 回退不是「手动保存」，文案要区分


def test_manual_flag_is_strictly_greater(tmp_path):
    """同一秒内 AI 版本与人工版本 ts 相等时，不应算作「手动改过」（必须严格大于）。"""
    store = _store(tmp_path)
    store.save_chapter(Chapter(no=1, title="第1章", content="正文。"))
    index = store.load_chapter_versions()

    index["1"]["history"] = [
        {"v": 1, "ts": 2_000, "source": "ai"},
        {"v": 2, "ts": 2_000, "source": "user"},
    ]
    assert st._change_info(store, 1, index)["manual"] is False

    index["1"]["history"][1]["ts"] = 2_001
    info = st._change_info(store, 1, index)
    assert info["manual"] is True and info["changed_at"] == 2_001


def test_foreshadow_marks_tolerate_non_numeric_fields():
    """伏笔条目的章号字段异常时降级为「未过期」，不能抛异常打出 500。"""
    items = [{"buried_ch": "甲", "reveal_ch": None}, "not-a-dict",
             {"buried_ch": 2, "reveal_ch": 0}]
    pc._mark_foreshadow(items, {2})
    assert items[0]["record_stale"] is False
    assert items[2]["record_stale"] is True


def test_all_unknown_shows_total_notice(tmp_path):
    """全书没有可比对基线时，体检页必须出现总提示，而不是当成「没问题」。"""
    store = _store(tmp_path)
    store.save_chapter(Chapter(no=1, title="第1章", content="正文。"))
    store.save_chapter(Chapter(no=2, title="第2章", content="正文。"))
    _seed_artifacts(store, [1, 2])
    store.save_chapter_commit({"chapters": {}, "updated_ch": 0})
    store.chapter_versions_path().unlink(missing_ok=True)
    store.projection_log_path().unlink(missing_ok=True)

    data = st.analyze(store)
    assert data["unknown_chapters"] == [1, 2]
    assert data["notice"]["level"] == "info"
    assert "无法判断" in data["notice"]["title"]


def test_partial_unknown_is_mentioned_in_advice():
    """只有部分章 unknown 时，过期提示里要如实带上一句，不能静默。"""
    items = [{"chapter": 2, "manual": True, "artifacts": ["scenes"]}]
    notice = st._build_notice(items, [4], single=False)
    assert notice["level"] == "warning"
    assert "另有 1 章" in notice["advice"]
    assert "第 4 章" in notice["advice"]
