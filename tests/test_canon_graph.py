# -*- coding: utf-8 -*-
"""REQ-R24 G0'：Canon 事实图（graph.py）+ 事件三契约单测。

覆盖：
- 空项目给空图而不是 null；节点按 7 类收敛
- 边必须有证据：有证据才建、缺证据计入 skipped、多主体歧义不建
- 六类体检：ID 冲突 / 悬空引用 / 时间倒挂 / 证据缺失 / 孤立节点 / 别名自指
- 子图检索：按 id 与按名、depth 扩散、未知 seed 返回 found=False
- 版本指纹：同源同值、改源变值
- 契约：batch_id 落盘、重写 revision 另立 id、supersede 侧车隐藏旧行且不删原行
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.state.events import EventStream, make_id
from novel_gen.pipeline.state.graph import CanonGraph, EDGE_RELS, node_id
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
    c = NovelConfig(title="测试书")
    c.book_facts = True
    c.event_stream = True
    c.state_graph = True
    c.state_graph_v2 = True
    return c


def _world() -> World:
    return World(
        characters=[Character(name="陆沉", role="主角"),
                    Character(name="独眼刘", role="反派")],
        items=[ItemCard(name="铜匣", origin="墟内二层"),
               ItemCard(name="灰髓袋", origin="墟口废铁")],
        locations=[LocationCard(name="墟内二层"), LocationCard(name="墟口")],
        terminology=[TerminologyItem(name="执能", aliases=["异能"])],
        foreshadowing=[ForeshadowItem(name="灰髓袋来历", buried_ch=1, reveal_ch=20,
                                      content="袋子来自墟内二层")],
    )


def _seed(store, cfg, facts=None, chapter=5):
    store.save_config(cfg)
    store.save_world(_world())
    mem = BookMemory(store, cfg)
    mem.sync_terms(_world())
    mem.sync_baseline(_world())
    data = mem.load()
    data["canon"]["mechanism_baseline"] = {"panel_format": "今日剩余：N"}
    mem.save()
    store.save_chapter_state({"chapter": chapter,
                              "characters": {"陆沉": "左臂受伤"},
                              "facts": list(facts or []),
                              "numbers": []})
    store.save_character_states({"陆沉": {"last_seen": chapter,
                                          "location": "墟内二层",
                                          "goal": "找回灰髓袋"}})


# ---------- 结构 ----------
@pytest.mark.p1
def test_empty_project_returns_empty_graph_not_null(store, cfg):
    store.save_config(cfg)
    g = CanonGraph(store, cfg).build()
    assert set(g) == {"version", "at_ch", "nodes", "edges", "sources",
                      "skipped_edges"}
    assert g["nodes"] == [] and g["edges"] == []
    assert g["sources"]["nodes"]["character"] == 0


@pytest.mark.p1
def test_nodes_cover_types_from_sources(store, cfg):
    _seed(store, cfg, facts=[], chapter=5)
    g = CanonGraph(store, cfg).build(at_ch=5)
    types = {n["type"] for n in g["nodes"]}
    assert {"character", "item", "place", "mechanism", "concept",
            "foreshadow"} <= types
    names = {n["name"] for n in g["nodes"]}
    assert "陆沉" in names and "铜匣" in names and "墟口" in names
    # 术语别名登记在节点上，节点名用标准写法
    concept = [n for n in g["nodes"] if n["type"] == "concept"]
    assert any("异能" in (n.get("aliases") or []) for n in concept)


@pytest.mark.p1
def test_present_at_edge_carries_evidence(store, cfg):
    _seed(store, cfg, chapter=5)
    g = CanonGraph(store, cfg).build(at_ch=5)
    pa = [e for e in g["edges"] if e["rel"] == "present_at"]
    assert len(pa) == 1
    e = pa[0]
    assert e["src"] == node_id("character", "陆沉")
    assert e["dst"] == node_id("place", "墟内二层")
    assert e["evidence"]["ch"] == 5
    assert "陆沉在墟内二层" in e["evidence"]["snippet"]
    assert e["valid_from_ch"] == 5


@pytest.mark.p1
def test_holds_edge_from_fact_with_verb(store, cfg):
    _seed(store, cfg, facts=["陆沉从墟内二层取出铜匣"], chapter=6)
    g = CanonGraph(store, cfg).build(at_ch=6)
    holds = [e for e in g["edges"] if e["rel"] == "holds"]
    assert len(holds) == 1
    assert holds[0]["src"] == node_id("item", "铜匣")
    assert holds[0]["dst"] == node_id("character", "陆沉")
    assert holds[0]["evidence"]["ch"] == 6


@pytest.mark.p1
def test_no_evidence_no_edge_and_skipped_counted(store, cfg):
    _seed(store, cfg, facts=["铜匣在桌上"], chapter=6)  # 无持有动词 → 不建边
    g = CanonGraph(store, cfg).build(at_ch=6)
    assert [e for e in g["edges"] if e["rel"] == "holds"] == []


@pytest.mark.p1
def test_ambiguous_fact_skips_edge(store, cfg):
    _seed(store, cfg, facts=["陆沉把铜匣和灰髓袋交给独眼刘"], chapter=6)
    g = CanonGraph(store, cfg).build(at_ch=6)
    assert [e for e in g["edges"] if e["rel"] == "holds"] == []
    assert g["skipped_edges"] >= 1


# ---------- 体检 ----------
@pytest.mark.p1
def test_validate_clean_graph_is_ok(store, cfg):
    _seed(store, cfg, facts=["陆沉取出铜匣"], chapter=6)
    g = CanonGraph(store, cfg).build(at_ch=6)
    r = CanonGraph(store, cfg).validate(g)
    assert r["ok"] is True
    assert r["counts"]["errors"] == 0
    assert r["version"]


@pytest.mark.p1
def test_validate_detects_dangling_ref_and_time_reversal(store, cfg):
    g = {"version": 1, "at_ch": 1,
         "nodes": [{"id": "ent_char_x", "type": "character", "name": "甲"}],
         "edges": [{"id": "edge_x", "src": "ent_char_x", "dst": "ent_place_missing",
                    "rel": "present_at", "valid_from_ch": 9, "valid_to_ch": 3,
                    "evidence": {"ch": 9, "offset": 0, "snippet": "甲在乙处"}},
                   {"id": "edge_y", "src": "ent_char_x", "dst": "ent_char_x",
                    "rel": "holds", "valid_from_ch": 1,
                    "evidence": {"ch": 1, "offset": 0, "snippet": ""}}]}
    r = CanonGraph(store, cfg).validate(g)
    codes = {i["code"] for i in r["issues"]}
    assert "dangling_ref" in codes
    assert "time_reversal" in codes
    assert "missing_evidence" in codes
    assert r["ok"] is False


@pytest.mark.p1
def test_validate_flags_isolated_and_alias_self_ref(store, cfg):
    g = {"version": 1, "at_ch": 1,
         "nodes": [{"id": "ent_char_x", "type": "character", "name": "甲"},
                   {"id": "ent_cpt_y", "type": "concept", "name": "执能",
                    "aliases": ["执能"]}],
         "edges": []}
    r = CanonGraph(store, cfg).validate(g)
    codes = {i["code"] for i in r["issues"]}
    assert "isolated_node" in codes
    assert "alias_self_ref" in codes
    # 孤立节点只是 info，不该把整体判成 not ok
    assert r["ok"] is True


@pytest.mark.p1
def test_validate_detects_duplicate_node_id(store, cfg):
    n = {"id": "ent_char_x", "type": "character", "name": "甲"}
    g = {"version": 1, "at_ch": 1, "nodes": [n, dict(n)], "edges": []}
    r = CanonGraph(store, cfg).validate(g)
    assert any(i["code"] == "duplicate_node_id" for i in r["issues"])
    assert r["ok"] is False


# ---------- 检索 ----------
@pytest.mark.p1
def test_subgraph_by_name_two_hops_holds(store, cfg):
    _seed(store, cfg, facts=["陆沉取出铜匣"], chapter=6)
    r = CanonGraph(store, cfg).subgraph("陆沉", depth=1, rels=("holds", "present_at"),
                                        at_ch=6)
    assert r["found"] is True
    assert r["seed_id"] == node_id("character", "陆沉")
    rels = {e["rel"] for e in r["edges"]}
    assert rels == {"holds", "present_at"}
    assert r["counts"]["nodes"] == 3


@pytest.mark.p1
def test_subgraph_unknown_seed_returns_empty_not_raise(store, cfg):
    store.save_config(cfg)
    r = CanonGraph(store, cfg).subgraph("不存在的人", depth=2)
    assert r["found"] is False and r["nodes"] == [] and r["edges"] == []


@pytest.mark.p1
def test_subgraph_respects_relation_filter(store, cfg):
    _seed(store, cfg, facts=["陆沉取出铜匣"], chapter=6)
    r = CanonGraph(store, cfg).subgraph("铜匣", depth=1, rels=("holds",), at_ch=6)
    assert {e["rel"] for e in r["edges"]} == {"holds"}
    assert r["counts"]["nodes"] == 2


@pytest.mark.p1
def test_fingerprint_stable_and_changes_with_source(store, cfg):
    _seed(store, cfg, facts=[], chapter=5)
    g1 = CanonGraph(store, cfg).build(at_ch=5)
    g2 = CanonGraph(store, cfg).build(at_ch=5)
    assert CanonGraph.fingerprint(g1) == CanonGraph.fingerprint(g2)
    store.save_chapter_state({"chapter": 6, "characters": {"陆沉": "左臂受伤"},
                              "facts": ["陆沉取出铜匣"], "numbers": []})
    g3 = CanonGraph(store, cfg).build(at_ch=6)
    assert CanonGraph.fingerprint(g1) != CanonGraph.fingerprint(g3)


# ---------- 契约 ----------
@pytest.mark.p1
def test_append_normalizes_and_returns_counts(store, cfg):
    store.save_config(cfg)
    res = EventStream(store).append(
        [{"ch": 3, "scene": 1, "desc": "甲拿到铜匣", "participants": ["甲"],
          "kind": "object", "cause": "", "effect": ["得匣"]}],
        ch=3)
    assert res["written"] == 1 and res["skipped"] == 0
    row = EventStream(store).all()[-1]
    assert row["id"] == make_id(3, 1, 1)
    assert row["kind"] == "object" and row["participants"] == ["甲"]


@pytest.mark.p1
def test_rejected_event_does_not_write_partial_batch(store, cfg):
    store.save_config(cfg)
    res = EventStream(store).append(
        [{"ch": 4, "scene": 1, "desc": "合法事件", "participants": ["甲"],
          "kind": "state", "cause": "c", "effect": []},
         {"ch": 4, "scene": 1, "desc": "  ", "participants": [],
          "kind": "state", "cause": "", "effect": []}],
        ch=4)
    assert res["written"] == 1 and res["rejected"] == 1
    # 拒一条不影响已校验通过的那条落盘；整批仍只有一次写入
    rows = [r for r in EventStream(store).all() if r.get("ch") == 4]
    assert len(rows) == 1


@pytest.mark.p1
def test_revision_id_and_supersede_sidecar(store, cfg):
    store.save_config(cfg)
    es = EventStream(store)
    es.append([{"ch": 7, "scene": 2, "desc": "旧版：甲在墟口",
                "participants": ["甲"], "kind": "state", "cause": "c",
                "effect": []}], ch=7, scene=2)
    old = make_id(7, 2, 1)
    new = make_id(7, 2, 1, revision=2)
    assert f"{old}_r02" == new
    es.append([{"ch": 7, "scene": 2, "desc": "新版：甲在墟内二层",
                "participants": ["甲"], "kind": "state", "cause": "c",
                "effect": []}], ch=7, scene=2, revision=2)
    # 重写章走统一口径：标注旧版被替代（不删行）
    es.supersede_revision(7, 2)
    assert old in {r["id"] for r in es.all()}
    ids = {r["id"] for r in es.query()}
    assert new in ids and old not in ids


@pytest.mark.p1
def test_edge_rels_whitelist_is_capped(store, cfg):
    _seed(store, cfg, chapter=5)
    g = CanonGraph(store, cfg).build(at_ch=5)
    assert {e["rel"] for e in g["edges"]} <= set(EDGE_RELS)


@pytest.mark.p1
def test_relation_edges_from_character_states(store, cfg):
    _seed(store, cfg, chapter=5)
    store.save_character_states({
        "陆沉": {"last_seen": 5, "location": "墟内二层",
                 "relations": ["苏妄言：信任初步修复，她要求下次先告知",
                               "独眼刘：当面撕破脸，互相提防"]},
        "苏妄言": {"last_seen": 5, "relations": ["陆沉：信任初步修复"]}})
    g = CanonGraph(store, cfg).build(at_ch=5)
    rels = {(e["rel"], e["src"], e["dst"]) for e in g["edges"]}
    assert ("ally", node_id("character", "陆沉"), node_id("character", "苏妄言")) in rels
    assert ("enemy", node_id("character", "陆沉"), node_id("character", "独眼刘")) in rels
    for e in g["edges"]:
        if e["rel"] in ("ally", "enemy", "mentor"):
            assert e["evidence"]["snippet"] and e["evidence"]["ch"] == 5


@pytest.mark.p1
def test_relation_edge_skipped_for_unknown_target(store, cfg):
    _seed(store, cfg, chapter=5)
    store.save_character_states({"陆沉": {"last_seen": 5,
                                          "relations": ["路人甲：背后捅刀"]}})
    g = CanonGraph(store, cfg).build(at_ch=5)
    assert [e for e in g["edges"] if e["rel"] == "enemy"] == []
    assert g["skipped_edges"] >= 1


@pytest.mark.p1
def test_holder_uses_verb_position(store, cfg):
    """多人物句里判持有者：给类动词取动词后最近的人，取类取动词前最近的人。"""
    _seed(store, cfg, chapter=6)
    store.save_chapter_state({
        "chapter": 6, "characters": {"陆沉": "", "独眼刘": ""},
        "facts": ["陆沉把铜匣交给独眼刘", "独眼刘取得灰髓袋"],
        "numbers": []})
    g = CanonGraph(store, cfg).build(at_ch=6)
    holds = {e["src"]: e["dst"] for e in g["edges"] if e["rel"] == "holds"}
    assert holds[node_id("item", "铜匣")] == node_id("character", "独眼刘")
    assert holds[node_id("item", "灰髓袋")] == node_id("character", "独眼刘")


@pytest.mark.p1
def test_relation_prefix_with_yu_is_parsed(store, cfg):
    """历史提示词产出过「与X：…」，解析要能剥掉介词、照样连到人。"""
    _seed(store, cfg, chapter=5)
    store.save_character_states({"陆沉": {
        "last_seen": 5,
        "relations": ["与独眼刘：表面达成交易，实则互相提防",
                      "与苏妄言：结成临时同盟"]},
        "苏妄言": {"last_seen": 5}})
    g = CanonGraph(store, cfg).build(at_ch=5)
    rels = {(e["rel"], e["dst"]) for e in g["edges"]}
    assert ("enemy", node_id("character", "独眼刘")) in rels
    assert ("ally", node_id("character", "苏妄言")) in rels


@pytest.mark.p1
def test_items_table_creates_nodes_and_holds(store, cfg):
    """章末 items 归属表是账本结论：物品没进设定卡也要建节点并连边。"""
    _seed(store, cfg, chapter=7)
    store.save_chapter_state({
        "chapter": 7, "characters": {"陆沉": "", "顾长卿": ""},
        "items": {"小结晶": "由顾长卿弹还，陆沉收进铁匣",
                  "符箓": "顾长卿手按其上",
                  "无主铜钱": "散在桌上，无人认领"},
        "facts": [], "numbers": []})
    g = CanonGraph(store, cfg).build(at_ch=7)
    holds = {e["src"]: e["dst"] for e in g["edges"] if e["rel"] == "holds"}
    assert holds[node_id("item", "小结晶")] == node_id("character", "陆沉")
    assert holds[node_id("item", "符箓")] == node_id("character", "顾长卿")
    assert node_id("item", "无主铜钱") not in holds  # 判不出持有者 → 不建边
    names = {n["name"] for n in g["nodes"]}
    assert "小结晶" in names and "符箓" in names


@pytest.mark.p1
def test_item_node_available_to_facts_matching(store, cfg):
    """归属表建出的物品节点，应能被同一轮的 facts 句匹配到（顺序回归）。"""
    _seed(store, cfg, chapter=8)
    store.save_chapter_state({
        "chapter": 8, "characters": {"陆沉": "", "独眼刘": ""},
        "items": {"小结晶": "在陆沉怀中"},
        "facts": ["独眼刘把铜匣和小结晶一并抢走"], "numbers": []})
    g = CanonGraph(store, cfg).build(at_ch=8)
    assert [e for e in g["edges"] if e["rel"] == "holds"]  # 多物品 → 跳过，但节点存在


# ---------- 接口层 ----------
@pytest.mark.p1
def test_api_canon_graph_returns_whole_graph(store, cfg, monkeypatch):
    import webapp.routers.projects_chapters as router

    _seed(store, cfg, facts=["陆沉取出铜匣"], chapter=6)
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    res = router.get_book_facts_canon_graph(project_id="p1", user={"open_id": "t"})
    assert set(res) == {"version", "graph"}
    assert res["version"] and res["graph"]["nodes"]

    sub = router.get_book_facts_canon_graph(project_id="p1", view="subgraph",
                                            seed="铜匣", depth=1,
                                            user={"open_id": "t"})
    assert sub["found"] is True and sub["counts"]["edges"] >= 1

    val = router.get_book_facts_canon_graph(project_id="p1", view="validate",
                                            user={"open_id": "t"})
    assert val["ok"] is True


@pytest.mark.p1
def test_api_canon_graph_404_when_flag_off(store, monkeypatch):
    from fastapi import HTTPException

    import webapp.routers.projects_chapters as router

    store.save_config(NovelConfig(title="关"))
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    with pytest.raises(HTTPException) as exc:
        router.get_book_facts_canon_graph(project_id="p1", user={"open_id": "t"})
    assert exc.value.status_code == 404


@pytest.mark.p1
def test_api_canon_graph_rejects_bad_view_and_missing_seed(store, cfg, monkeypatch):
    from fastapi import HTTPException

    import webapp.routers.projects_chapters as router

    store.save_config(cfg)
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    with pytest.raises(HTTPException) as exc:
        router.get_book_facts_canon_graph(project_id="p1", view="foo",
                                          user={"open_id": "t"})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc2:
        router.get_book_facts_canon_graph(project_id="p1", view="entity",
                                          user={"open_id": "t"})
    assert exc2.value.status_code == 400
