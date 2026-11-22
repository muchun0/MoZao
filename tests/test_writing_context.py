# -*- coding: utf-8 -*-
"""R29 写作侧栏只读聚合（writing-context）专项测试。

覆盖：章计划字段（character_moves）、出场人物名称匹配、本章相关伏笔筛选与五态计数、
reader_ledger 关闭降级、空项目降级、入参校验、越权 403、调用前后零写盘。
纯 store + 产物文件，不依赖真实模型/MySQL。
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
from novel_gen.store.schemas import (
    Chapter, ChapterPlan, Character, ForeshadowItem, Outline, World)
from webapp import writing_context
from webapp.writing_context import WritingContextError, build_writing_context

PID = "p"


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=50000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / PID, cfg)


@pytest.fixture
def seeded(store):
    """第 3 章计划 + 两个人物 + 两条伏笔（本章到期 / 远期）。"""
    store.save_chapter(Chapter(no=1, title="第1章", content="第一章正文。"))
    store.save_outline(Outline(chapters=[
        ChapterPlan(no=3, title="夜探铜铃镇", goal="找回铜铃",
                    core_event="林昭夜探山门", conflict="与守门人冲突",
                    character_moves="林昭推门，老周抬头。", cliffhanger="铜铃自己响了"),
    ]))
    store.save_world(World(characters=[
        Character(name="林昭", role="主角", personality="冷静", speech_style="短句"),
        Character(name="老周", role="配角", personality="多疑"),
        Character(name="无关者", role="路人"),
    ], foreshadowing=[
        ForeshadowItem(name="铜铃", buried_ch=1, reveal_ch=3, content="铜铃来历"),
        ForeshadowItem(name="血玉", buried_ch=1, reveal_ch=20, content="血玉来历"),
    ]))
    return store


@pytest.mark.p0
def test_context_returns_plan_characters_and_chapter_foreshadow(seeded):
    cfg = seeded.load_config()
    data = build_writing_context(seeded, 3, cfg=cfg)

    assert data["ch"] == 3
    plan = data["chapter"]
    assert plan["title"] == "夜探铜铃镇"
    assert plan["core_event"] == "林昭夜探山门"
    assert plan["character_moves"].startswith("林昭推门")   # 字段名是 character_moves
    assert set(data["available"]) == {"outline", "world", "foreshadow"}

    names = [c["name"] for c in data["characters"]]
    assert names == ["林昭", "老周"]
    assert data["characters"][0]["speech_style"] == "短句"

    items = data["foreshadow"]["items"]
    assert [i["name"] for i in items] == ["铜铃"]          # 远期伏笔不进本章
    assert items[0]["state"] == "overdue"                  # 回收章当天即逾期（R10 口径）
    assert items[0]["state_label"] == "逾期未回收"
    assert items[0]["reveal_in"] == 0
    counts = data["foreshadow"]["counts"]
    assert counts["overdue"] == 1 and counts["planted"] == 1


@pytest.mark.p0
def test_context_follows_chapter_number(seeded):
    """前端切章后按新 ch 重新请求；章计划与本章伏笔范围随之变化。"""
    cfg = seeded.load_config()
    data = build_writing_context(seeded, 4, cfg=cfg)
    assert data["ch"] == 4
    assert data["chapter"] is None                          # 第 4 章还没建计划
    assert data["characters"] == []
    # 铜铃已逾期仍在待处理清单，且距回收章为负数（由后端计算）
    overdue = [i for i in data["foreshadow"]["items"] if i["name"] == "铜铃"]
    assert overdue and overdue[0]["reveal_in"] == -1


@pytest.mark.p0
def test_context_degrades_when_reader_ledger_off(seeded):
    cfg = seeded.load_config()
    cfg.reader_ledger = False
    data = build_writing_context(seeded, 3, cfg=cfg)
    assert data["chapter"] is not None                      # 章计划不依赖账本
    assert data["foreshadow"]["items"] == []
    assert set(data["foreshadow"]["counts"].values()) == {0}
    assert data["available"]["foreshadow"] is False


@pytest.mark.p1
def test_context_empty_project_is_friendly(store):
    data = build_writing_context(store, 1)
    assert data["chapter"] is None
    assert data["characters"] == []
    assert data["foreshadow"]["items"] == []
    assert data["available"] == {"outline": False, "world": False, "foreshadow": False}


@pytest.mark.p0
def test_context_rejects_bad_chapter(store):
    with pytest.raises(WritingContextError):
        build_writing_context(store, 0)


@pytest.mark.p0
def test_context_is_read_only(seeded):
    """调用前后项目目录内容与 mtime 完全不变（只读红线）。"""
    def snapshot():
        return sorted((str(p.relative_to(seeded.dir)), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in seeded.dir.rglob("*") if p.is_file())

    before = snapshot()
    build_writing_context(seeded, 3)
    assert snapshot() == before


@pytest.mark.p0
def test_router_forbidden_blocks_before_read(seeded, monkeypatch):
    from webapp.routers import projects_writing as pw

    def _deny(*a, **k):
        raise HTTPException(status_code=403, detail="无权限")

    monkeypatch.setattr(pw, "_check_project_access", _deny)
    with pytest.raises(HTTPException) as ei:
        pw.get_writing_context(PID, ch=3, user={"id": 999})
    assert ei.value.status_code == 403


@pytest.mark.p0
def test_router_maps_bad_chapter_to_400(seeded, monkeypatch):
    from webapp.routers import projects_writing as pw

    monkeypatch.setattr(pw, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pw, "_find_project", lambda pid: seeded)
    with pytest.raises(HTTPException) as ei:
        pw.get_writing_context(PID, ch=0, user={"id": 1})
    assert ei.value.status_code == 400
    assert "BAD_CHAPTER" in str(ei.value.detail)
