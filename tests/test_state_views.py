# -*- coding: utf-8 -*-
"""REQ-R22 / TECH-R22 M2：状态图谱只读服务（StateGraph）单测。

覆盖：
- 四视图齐全、缺数据给空结构而不是 null
- 版本指纹稳定（同源同 version）与变更（加事件后变）
- 时间线过滤（kind/participant/limit）与按章分组统计
- 未知视图抛错、不静默给空
- 单测消费方（生成链路/前端）读同一契约：canon 术语/注册表、threads 未回收
- 接口层开/关（state_graph=False → 404，完全回退）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.state.events import EventStream
from novel_gen.pipeline.state.views import VIEWS, StateGraph
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (Character, ForeshadowItem, TerminologyItem,
                                     World)


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(tmp_path / "book")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def cfg():
    c = NovelConfig(title="测试书")
    c.book_facts = True
    c.event_stream = True
    c.conflict_guard = True
    c.timeline_view = True
    c.state_graph = True
    return c


def _world() -> World:
    return World(
        characters=[Character(name="陆沉", role="主角"),
                    Character(name="独眼刘", role="反派")],
        items=[],
        terminology=[TerminologyItem(name="执能", aliases=["异能"])],
        foreshadowing=[ForeshadowItem(name="灰髓袋来历", buried_ch=1, reveal_ch=20,
                                      content="袋子来自墟内二层"),
                       ForeshadowItem(name="铜匣押金", buried_ch=3, reveal_ch=24,
                                      content="押金来源未交代")],
    )


def _seed(store, cfg):
    store.save_config(cfg)
    store.save_world(_world())
    mem = BookMemory(store, cfg)
    mem.sync_terms(_world())
    mem.sync_baseline(_world())
    data = mem.load()
    data["canon"]["mechanism_baseline"] = {"panel_format": "今日剩余：N；蚀痕：N寸"}
    mem.save()
    store.save_chapter_state({"chapter": 5, "characters": {"陆沉": "左臂受伤"},
                              "numbers": ["今日剩余：2"]})
    store.save_character_states({"陆沉": {"last_seen": 5, "location": "墟内二层",
                                          "goal": "找回灰髓袋"}})
    EventStream(store).append([
        {"ch": 2, "scene": 0, "desc": "陆沉在墟口捡到灰髓袋", "participants": ["陆沉"],
         "kind": "object", "cause": "翻找废铁", "effect": ["得灰髓袋"]},
        {"ch": 5, "scene": 1, "desc": "独眼刘抢走铜匣", "participants": ["独眼刘", "陆沉"],
         "kind": "object", "cause": "债务", "effect": ["陆沉失匣"]},
        {"ch": 5, "scene": 2, "desc": "陆沉夜里发烧", "participants": ["陆沉"],
         "kind": "state", "cause": "旧伤", "effect": ["状态恶化"]},
    ], ch=5)


# ---------- 结构 ----------
@pytest.mark.p1
def test_empty_project_returns_empty_graph_not_null(store, cfg):
    store.save_config(cfg)
    g = StateGraph(store, cfg).snapshot()
    assert set(g) == {"version", "generated_at", "sources", "views"}
    assert set(g["views"]) == set(VIEWS)
    assert g["views"]["canon"]["terms"] == []
    assert g["views"]["characters"]["characters"] == []
    assert g["views"]["timeline"]["events"] == []
    assert g["views"]["threads"]["threads"] == []
    assert g["sources"]["events"] == 0


@pytest.mark.p1
def test_unknown_view_raises_instead_of_empty(store, cfg):
    store.save_config(cfg)
    with pytest.raises(ValueError):
        StateGraph(store, cfg).view("graph_all")


# ---------- 四视图内容 ----------
@pytest.mark.p1
def test_canon_view_has_terms_registry_and_invariants(store, cfg):
    _seed(store, cfg)
    canon = StateGraph(store, cfg).canon()
    assert any(t["term"] == "异能" and t["canonical"] == "执能" for t in canon["terms"])
    assert canon["mechanism_baseline"]  # 世界观基线已登记
    assert canon["invariants"].get("mechanism") is not None
    assert canon["registry"]  # 由基线派生的字段注册表


@pytest.mark.p1
def test_threads_view_lists_open_foreshadow(store, cfg):
    _seed(store, cfg)
    data = StateGraph(store, cfg).threads()
    names = {t["name"] for t in data["threads"]}
    assert "灰髓袋来历" in names and "铜匣押金" in names
    assert data["count"] == len(data["threads"])


@pytest.mark.p1
def test_timeline_view_groups_and_filters(store, cfg):
    _seed(store, cfg)
    g = StateGraph(store, cfg)
    tl = g.timeline()
    assert tl["count"] == 3
    assert tl["kinds"].get("object") == 2 and tl["kinds"].get("state") == 1
    assert any(x["ch"] == 5 and x["count"] == 2 for x in tl["by_ch"])
    assert g.timeline(kind="object")["count"] == 2
    assert g.timeline(participant="独眼刘")["count"] == 1
    assert g.timeline(limit=1)["count"] == 1


@pytest.mark.p1
def test_characters_view_carries_source_ch(store, cfg):
    _seed(store, cfg)
    row = StateGraph(store, cfg).characters()["characters"][0]
    assert row["name"] == "陆沉" and row["last_ch"] == 5 and row["source"]


# ---------- 版本指纹 ----------
@pytest.mark.p1
def test_version_is_stable_and_changes_with_data(store, cfg):
    _seed(store, cfg)
    g = StateGraph(store, cfg)
    v1 = g.snapshot()["version"]
    assert v1 == StateGraph(store, cfg).snapshot()["version"]  # 同源 → 同指纹
    assert len(v1) == 16
    EventStream(store).append([{"ch": 6, "desc": "陆沉离开墟口",
                                "participants": ["陆沉"], "cause": "追查铜匣",
                                "effect": ["离开墟口"]}], ch=6)
    assert StateGraph(store, cfg).snapshot()["version"] != v1


@pytest.mark.p1
def test_version_differs_by_view_subset(store, cfg):
    _seed(store, cfg)
    g = StateGraph(store, cfg)
    whole = g.snapshot()["version"]
    only_canon = g.snapshot(views=["canon"])["version"]
    assert whole != only_canon


# ---------- 接口层 ----------
@pytest.mark.p1
def test_api_graph_returns_all_views(store, cfg, monkeypatch):
    import webapp.routers.projects_chapters as router

    _seed(store, cfg)
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    # 单测不读真实 sys_config（全局覆盖另有用例 test_api_graph_honors_global_flag_override）
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    res = router.get_book_facts_graph(project_id="p1", user={"open_id": "test"})
    assert set(res) == {"version", "generated_at", "sources", "views"}
    assert set(res["views"]) == set(VIEWS)

    one = router.get_book_facts_graph(project_id="p1", view="threads",
                                      user={"open_id": "test"})
    assert one["view"] == "threads" and one["data"]["count"] >= 2
    assert one["version"] and one["sources"]["threads"] >= 2


@pytest.mark.p1
def test_api_graph_404_when_flag_off(store, cfg, monkeypatch):
    from fastapi import HTTPException

    import webapp.routers.projects_chapters as router

    cfg2 = NovelConfig(title="关")
    store.save_config(cfg2)
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    # 隔离真实 sys_config：本用例验证「项目快照 + 无全局覆盖」时的关闭行为
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    with pytest.raises(HTTPException) as exc:
        router.get_book_facts_graph(project_id="p1", user={"open_id": "test"})
    assert exc.value.status_code == 404


@pytest.mark.p1
def test_api_graph_honors_global_flag_override(store, monkeypatch):
    """后台开关是唯一权威源：项目快照没有该字段时，sys_config 打开即应可读。

    真实故障场景：老项目 config.json 里没有 timeline_view/state_graph，
    后台把开关打开后，前端面板仍显示「未启用」——读接口必须合并全局覆盖。
    """
    import webapp.routers.projects_chapters as router

    cfg2 = NovelConfig(title="老项目快照")  # 字段默认关，模拟老项目
    store.save_config(cfg2)
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)

    def fake_overrides(c):
        c.state_graph = True
        c.timeline_view = True

    monkeypatch.setattr(router, "apply_global_overrides", fake_overrides)
    res = router.get_book_facts_graph(project_id="p1", user={"open_id": "test"})
    assert set(res["views"]) == set(VIEWS)

    timeline = router.get_book_facts_timeline(project_id="p1", user={"open_id": "test"})
    assert timeline["view"] == "chapter"


@pytest.mark.p1
def test_api_graph_rejects_unknown_view(store, cfg, monkeypatch):
    from fastapi import HTTPException

    import webapp.routers.projects_chapters as router

    _seed(store, cfg)
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    with pytest.raises(HTTPException) as exc:
        router.get_book_facts_graph(project_id="p1", view="foo",
                                    user={"open_id": "test"})
    assert exc.value.status_code == 400
