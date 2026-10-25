# -*- coding: utf-8 -*-
"""阶段 0.5 计费接线冒烟（P0）：验证真实调用链确实产出结算条目。

背景：`settle_ledger.py` 单测绿不代表接线生效（AGENTS §11.2「建了能力却没接线」是历史坑）。
本文件用假 pipeline 走**真实的** `core._run_generation` / `rewrite_chapter` 代码路径，
只桩掉 LLM、管道内部推进与鉴权，断言 `settle.json` 里确实出现 outline / world / chapter
条目，且金额符合「按版本按字数」口径。不连 MySQL、不发网络请求。

覆盖：
- 新建书生成：大纲、世界观、首章各一笔；
- 同章重写：新增 `chapter:1:v2` 再扣一次（首版不重复扣）；
- 自备 Key（user_llm）+ 余额 0：不扣费但仍记账，且不因平台余额停止；
- 平台项目余额 0：本章前停止，不产生章节条目并给出余额提示。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline, World
from webapp import core as server


def _new_store(tmp_path) -> ProjectStore:
    cfg = NovelConfig(title="接线测试书", total_words=5000, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _ledger(store) -> dict:
    return json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))


def _entries(store) -> dict:
    return _ledger(store)["entries"]


class _FakePipeline:
    """最小假管道：只负责把大纲/世界观/章节写进真实 store（跳过 LLM 与质检）。"""

    def __init__(self, store: ProjectStore, chapters: int = 1):
        self.store = store
        self._total = chapters

    def run_research(self):
        pass

    def run_outline(self):
        self.store.save_outline(Outline(
            title="接线测试书",
            intro="总纲简介" * 50,
            chapters=[ChapterPlan(no=i, title=f"第{i}章", goal="本章目标" * 50)
                      for i in range(1, self._total + 1)],
        ))

    def run_editor_review(self):
        pass

    def run_world(self):
        self.store.save_world(World(world_view="世界观设定" * 100))

    def _mark_state(self, *_args, **_kwargs):
        pass

    def progress(self):
        return 0, self._total

    def write_chapter(self, no: int, silent: bool = False):
        content = "正文内容" * 500
        self.store.save_chapter(Chapter(no=no, title=f"第{no}章", content=content), source="ai")
        return Chapter(no=no, title=f"第{no}章", content=content)

    def export(self):
        pass


@pytest.fixture
def wallet(monkeypatch):
    """内存钱包 + 用户配置桩（webapp.db 是同一模块对象，settle_ledger 同享）。"""
    state = {"balance": 100.0, "charge": [], "use_user_llm": False, "project_price": 0.0}
    monkeypatch.setattr(server.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(server.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(server.db, "get_user", lambda uid: {"id": uid, "balance": state["balance"]})
    # 真实 get_user_llm 无记录时返回 None，但 `_user_llm_cfg` 直接 .get()，业务上按空 dict 处理
    monkeypatch.setattr(server.db, "get_user_llm", lambda uid: (
        {"provider": "deepseek", "api_key": "sk-test"} if state["use_user_llm"] else {}))
    monkeypatch.setattr(server.db, "change_balance", lambda uid, delta, note: (
        state["charge"].append(delta),
        state.update(balance=round(state["balance"] + delta, 4))))
    monkeypatch.setattr(server.db, "update_project_price",
                        lambda pid, price: state.update(project_price=price))
    monkeypatch.setattr(server.db, "get_project_owner", lambda pid: 1, raising=False)
    return state


@pytest.fixture
def wired(monkeypatch):
    """桩掉注入器、拆书库沉淀与并发信号量，保留结算链路原样。"""
    for name in ("_inject_teardown_style_anchor", "_inject_user_memory", "_inject_teardown_material"):
        monkeypatch.setattr(server, name, lambda *a, **k: None)
    monkeypatch.setattr(server.teardown_lib, "sink_research", lambda *a, **k: None)

    class _Sem:
        def release(self):
            return None

        def acquire(self, **kwargs):
            return True

    monkeypatch.setattr(server, "_GEN_SEMAPHORE", _Sem())


@pytest.mark.p0
def test_generation_settles_outline_world_and_chapter(tmp_path, wallet, wired, monkeypatch):
    """新建书一次生成 → 大纲、世界观、首章各产生一笔结算（均真实扣费）。"""
    store = _new_store(tmp_path)
    fake = _FakePipeline(store, chapters=1)
    monkeypatch.setattr(server, "_open_pipeline", lambda s, trace_id="": fake)

    handle = server._RunHandle()
    server._run_generation(store, handle)

    assert not handle.error, f"生成不应报错: {handle.error}"
    entries = _entries(store)
    assert "outline:all:v1" in entries
    assert "world:all:v1" in entries
    assert "chapter:1:v1" in entries
    # 三笔都是平台付费模式、都真实扣了钱（字数 > 0）
    assert {e["mode"] for e in entries.values()} == {"platform"}
    assert all(e["words"] > 0 and e["charged"] > 0 for e in entries.values())
    assert len(wallet["charge"]) == 3
    # 项目累计金额同步回写（前端/后台展示口径）
    assert wallet["project_price"] > 0


@pytest.mark.p0
def test_rewrite_settles_new_chapter_version_only(tmp_path, wallet, monkeypatch):
    """同章重写 → 只结算新增的 v2，首版不重复扣。"""
    from webapp.routers import projects_chapters as chapters_mod

    store = _new_store(tmp_path)
    store.save_outline(Outline(
        title="接线测试书",
        chapters=[ChapterPlan(no=1, title="第1章", goal="目标")],
    ))
    store.save_chapter(Chapter(no=1, title="第1章", content="初稿" * 500), source="ai")

    class _FakeWriter:
        def write(self, no, title, goal, research, is_final=False):
            content = "重写正文" * 800
            store.save_chapter(Chapter(no=no, title=title, content=content), source="rewrite")
            return Chapter(no=no, title=title, content=content)

    class _FakeRewritePipeline:
        def __init__(self):
            self.chapter_writer = _FakeWriter()

        def _update_chapter_state(self, no, silent=True):
            pass

    monkeypatch.setattr(chapters_mod, "_open_pipeline",
                        lambda s, trace_id="": _FakeRewritePipeline())
    monkeypatch.setattr(chapters_mod, "_check_project_access", lambda pid, u: None)
    monkeypatch.setattr(chapters_mod, "_find_project", lambda pid: store)
    monkeypatch.setattr(chapters_mod, "_assert_not_locked", lambda *a, **k: None)
    monkeypatch.setattr(chapters_mod, "_check_regen_limit", lambda *a, **k: None)

    res = chapters_mod.rewrite_chapter(store.dir.name, 1, user={"id": 1, "username": "u"})

    assert res["no"] == 1
    entries = _entries(store)
    assert "chapter:1:v2" in entries          # 本次重写新增版本已结算
    assert "chapter:1:v1" not in entries      # 首版不在本次结算范围内（不重复扣）
    assert len(wallet["charge"]) == 1


@pytest.mark.p0
def test_byok_project_books_but_never_charges_nor_stops(tmp_path, wallet, wired, monkeypatch):
    """自备 Key + 平台余额 0：照常生成，条目 charged=0，不因平台余额停止。"""
    wallet["balance"] = 0.0
    wallet["use_user_llm"] = True
    store = _new_store(tmp_path)
    fake = _FakePipeline(store, chapters=1)
    monkeypatch.setattr(server, "_open_pipeline", lambda s, trace_id="": fake)

    handle = server._RunHandle()
    server._run_generation(store, handle)

    assert not handle.error, f"BYOK 不应被平台余额停止: {handle.error}"
    entries = _entries(store)
    assert {"outline:all:v1", "world:all:v1", "chapter:1:v1"} <= set(entries)
    assert {e["mode"] for e in entries.values()} == {"user_llm"}
    assert all(e["charged"] == 0 for e in entries.values())
    assert wallet["charge"] == []


@pytest.mark.p0
def test_platform_zero_balance_stops_before_chapter(tmp_path, wallet, wired, monkeypatch):
    """平台项目余额 0：写章前停止，不产生章节条目，并回传余额提示。"""
    wallet["balance"] = 0.0
    store = _new_store(tmp_path)
    fake = _FakePipeline(store, chapters=1)
    monkeypatch.setattr(server, "_open_pipeline", lambda s, trace_id="": fake)

    handle = server._RunHandle()
    server._run_generation(store, handle)

    assert "余额已耗尽" in (handle.error or "")
    entries = _entries(store)
    assert "chapter:1:v1" not in entries
    # 大纲/世界观已产出，仍按 0 字余额记账（charged=0，记欠费）
    assert "outline:all:v1" in entries
    assert all(e["charged"] == 0 for e in entries.values())
