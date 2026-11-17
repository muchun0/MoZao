# -*- coding: utf-8 -*-
"""REQ-R24 / TECH-R24 G1'：时间维 + 主体归一 + 两规则改读图 + impact。

覆盖：
- 时间维：持有者换人时旧边 valid_to_ch 关闭、at_ch 视图只给当章有效边
- 主体归一：账本登记的别名并到同一节点、别名可检索、封顶 20 条
- 两规则改读图：death_state 读节点 dead_ch、item_owner 读图时间轴
- impact：受影响章节与未回收伏笔
- 体检新增：overlapping_holds / alias_conflict
- 接口：view=impact
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.state.conflict_rules import (check_death_state,
                                                     check_item_owner)
from novel_gen.pipeline.state.graph import MAX_ALIAS, CanonGraph, node_id
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (Character, ForeshadowItem, ItemCard,
                                     LocationCard, TerminologyItem, World)


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(tmp_path / "book")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def cfg():
    c = NovelConfig(title="G1测试")
    c.book_facts = True
    c.event_stream = True
    c.state_graph = True
    c.state_graph_v2 = True
    return c


def _world(terms=None) -> World:
    return World(
        characters=[Character(name="陆沉", role="主角"),
                    Character(name="独眼刘", role="反派")],
        items=[ItemCard(name="铜匣", origin="墟内二层")],
        locations=[LocationCard(name="墟内二层"), LocationCard(name="墟口")],
        terminology=[TerminologyItem(name="执能", aliases=["异能"])] + list(terms or []),
        foreshadowing=[ForeshadowItem(name="铜匣来历", buried_ch=1, reveal_ch=20,
                                      content="匣子来自墟内二层")],
    )


def _seed(store, cfg, world=None):
    store.save_config(cfg)
    store.save_world(world or _world())
    mem = BookMemory(store, cfg)
    mem.sync_terms(store.load_world())
    mem.sync_baseline(store.load_world())
    mem.save()


# ---------- 时间维 ----------
@pytest.mark.p1
def test_holds_timeline_closes_old_edge_when_owner_changes(store, cfg):
    _seed(store, cfg)
    store.save_scenes(3, [], {"items": {"铜匣": "在陆沉怀中"},
                              "characters": {"陆沉": "", "独眼刘": ""}})
    store.save_scenes(7, [], {"items": {"铜匣": "被独眼刘抢走"},
                              "characters": {"陆沉": "", "独眼刘": ""}})
    store.save_chapter_state({"chapter": 9,
                              "characters": {"陆沉": "", "独眼刘": ""},
                              "items": {"铜匣": "在独眼刘手中"},
                              "facts": [], "numbers": []})

    g = CanonGraph(store, cfg).build()
    holds = [e for e in g["edges"] if e["rel"] == "holds"]
    assert len(holds) == 2
    old = [e for e in holds if e["dst"] == node_id("character", "陆沉")][0]
    assert old["valid_from_ch"] == 3 and old["valid_to_ch"] == 7
    assert g["sources"]["chapters"] == 3

    # 第 5 章时点：东西还在陆沉手上
    at5 = CanonGraph(store, cfg).build(at_ch=5)
    holders = {e["dst"] for e in at5["edges"] if e["rel"] == "holds"}
    assert holders == {node_id("character", "陆沉")}

    # 第 8 章时点：已经易主
    at8 = CanonGraph(store, cfg).build(at_ch=8)
    holders = {e["dst"] for e in at8["edges"] if e["rel"] == "holds"}
    assert holders == {node_id("character", "独眼刘")}


@pytest.mark.p1
def test_same_owner_across_chapters_is_not_a_new_edge(store, cfg):
    _seed(store, cfg)
    store.save_scenes(2, [], {"items": {"铜匣": "在陆沉怀中"}})
    store.save_scenes(4, [], {"items": {"铜匣": "在陆沉怀中"}})
    store.save_scenes(6, [], {"items": {"铜匣": "仍在陆沉怀中"}})
    g = CanonGraph(store, cfg).build()
    holds = [e for e in g["edges"] if e["rel"] == "holds"]
    assert len(holds) == 1 and holds[0]["valid_from_ch"] == 2
    assert holds[0]["valid_to_ch"] is None


@pytest.mark.p1
def test_lost_item_description_builds_no_holds_edge(store, cfg):
    """账本写「被老陈头砸碎在地」「从袖口滚落」= 已不再持有，不能硬判持有者。"""
    _seed(store, cfg)
    store.save_scenes(5, [], {"items": {"瓷瓶": "被老陈头砸碎在地",
                                        "结晶": "从陆沉袖口滚落至石地"}})
    g = CanonGraph(store, cfg).build(at_ch=5)
    assert [e for e in g["edges"] if e["rel"] == "holds"] == []


# ---------- 主体归一 ----------
@pytest.mark.p1
def test_alias_merges_into_single_node_and_is_searchable(store, cfg):
    _seed(store, cfg, _world(terms=[TerminologyItem(name="陆先生", aliases=["老陆"])]))
    store.save_chapter_state({"chapter": 5, "characters": {"陆沉": "左臂受伤"},
                              "items": {}, "facts": [], "numbers": []})
    g = CanonGraph(store, cfg).build(at_ch=5)
    chars = [n for n in g["nodes"] if n.get("name") == "陆沉"]
    assert len(chars) == 1
    # 术语表里 canonical 缺省为 name 本身，别名挂在「陆先生」上；归一表只登记
    # 别名→标准名的映射，不制造第二个陆沉节点
    assert g["sources"]["aliases"] >= 1
    r = CanonGraph(store, cfg).subgraph("陆沉", depth=1)
    assert r["found"] is True


@pytest.mark.p1
def test_alias_index_is_capped(store, cfg):
    terms = [TerminologyItem(name=f"别称{i}", aliases=[f"俗称{i}"]) for i in range(40)]
    _seed(store, cfg, _world(terms=terms))
    g = CanonGraph(store, cfg).build()
    assert g["sources"]["aliases"] <= MAX_ALIAS


@pytest.mark.p1
def test_alias_conflict_reported_as_warning(store, cfg):
    g = {"version": 1, "at_ch": 1,
         "nodes": [{"id": "ent_char_a", "type": "character", "name": "甲",
                    "aliases": ["老大"]},
                   {"id": "ent_char_b", "type": "character", "name": "乙",
                    "aliases": ["老大"]}],
         "edges": []}
    r = CanonGraph(store, cfg).validate(g)
    assert any(i["code"] == "alias_conflict" for i in r["issues"])


# ---------- 两规则改读图 ----------
@pytest.mark.p1
def test_death_state_reads_graph_dead_mark(store, cfg):
    _seed(store, cfg)
    store.save_character_states({"陆沉": {"last_seen": 5, "state": "陆沉已死"}})
    graph = CanonGraph(store, cfg).build(at_ch=5)
    dead = [n for n in graph["nodes"]
            if n["type"] == "character" and n["name"] == "陆沉"][0]
    assert dead["attrs"]["dead_ch"] == 5
    events = [{"ch": 8, "desc": "陆沉推开石门", "participants": ["陆沉"]}]
    rows = check_death_state([], events, graph)
    assert len(rows) == 1 and rows[0]["rule"] == "death_state"
    # 不给图时退回原逻辑：空 chapters → 无死亡信息 → 不报
    assert check_death_state([], events) == []


@pytest.mark.p1
def test_item_owner_reads_graph_timeline(store, cfg):
    _seed(store, cfg)
    store.save_scenes(5, [], {"items": {"铜匣": "在陆沉怀中"}})
    graph = CanonGraph(store, cfg).build(at_ch=6)
    chapters = [{"no": 6, "characters": {"陆沉": "", "独眼刘": ""},
                 "items": {"铜匣": "在独眼刘手中"}, "facts": []}]
    rows = check_item_owner(chapters, [], graph)
    assert len(rows) == 1
    assert "按时间轴" in rows[0]["expected"]
    # 同输入不给图：只拿逐章账本自比（首章无 prev）→ 不报
    assert check_item_owner(chapters, []) == []


@pytest.mark.p1
def test_item_owner_graph_skips_item_without_covering_edge(store, cfg):
    _seed(store, cfg)
    store.save_scenes(5, [], {"items": {"铜匣": "在陆沉怀中"}})
    graph = CanonGraph(store, cfg).build(at_ch=6)
    chapters = [{"no": 6, "characters": {"陆沉": "", "独眼刘": ""},
                 "items": {"符箓": "在独眼刘手中"}, "facts": []}]
    assert check_item_owner(chapters, [], graph) == []


# ---------- impact ----------
@pytest.mark.p1
def test_impact_lists_later_chapters_and_foreshadows(store, cfg):
    _seed(store, cfg)
    store.save_scenes(3, [], {"items": {"铜匣": "在陆沉怀中"},
                              "characters": {"陆沉": "", "独眼刘": ""}})
    store.save_scenes(8, [], {"items": {"铜匣": "被独眼刘抢走"},
                              "characters": {"陆沉": "", "独眼刘": ""}})
    r = CanonGraph(store, cfg).impact("铜匣", ch=3, at_ch=9)
    assert r["found"] is True
    assert r["chapters"] == [8]
    assert any(f["name"] == "铜匣来历" for f in r["foreshadows"])
    assert r["counts"]["edges"] == 1


@pytest.mark.p1
def test_impact_unknown_entity_returns_empty(store, cfg):
    _seed(store, cfg)
    r = CanonGraph(store, cfg).impact("不存在的东西")
    assert r["found"] is False and r["chapters"] == []


@pytest.mark.p1
def test_validate_detects_overlapping_holds(store, cfg):
    g = {"version": 1, "at_ch": 1,
         "nodes": [{"id": "ent_item_a", "type": "item", "name": "匣"},
                   {"id": "ent_char_a", "type": "character", "name": "甲"},
                   {"id": "ent_char_b", "type": "character", "name": "乙"}],
         "edges": [
             {"id": "e1", "src": "ent_item_a", "dst": "ent_char_a", "rel": "holds",
              "valid_from_ch": 1, "valid_to_ch": None,
              "evidence": {"ch": 1, "offset": 0, "snippet": "匣在甲手中"}},
             {"id": "e2", "src": "ent_item_a", "dst": "ent_char_b", "rel": "holds",
              "valid_from_ch": 3, "valid_to_ch": None,
              "evidence": {"ch": 3, "offset": 0, "snippet": "匣在乙手中"}},
         ]}
    r = CanonGraph(store, cfg).validate(g)
    assert any(i["code"] == "overlapping_holds" for i in r["issues"])
    assert r["ok"] is False


# ---------- 接口 ----------
@pytest.mark.p1
def test_api_impact_view(store, cfg, monkeypatch):
    import webapp.routers.projects_chapters as router

    _seed(store, cfg)
    store.save_scenes(2, [], {"items": {"铜匣": "在陆沉怀中"}})
    store.save_chapter_state({"chapter": 2, "characters": {"陆沉": ""},
                              "items": {"铜匣": "在陆沉怀中"},
                              "facts": [], "numbers": []})
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    res = router.get_book_facts_canon_graph(project_id="p1", view="impact",
                                            seed="铜匣", at_ch=2,
                                            user={"open_id": "t"})
    assert res["found"] is True and res["entity"] == "铜匣"
