# -*- coding: utf-8 -*-
"""REQ-R24 P0/P1：Plan 编译、校验、派生约束、must 封顶与合并渲染。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.state import plan_graph
from novel_gen.pipeline.state.graph import node_id
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (ChapterPlan, Character, ItemCard,
                                     LocationCard, Outline, World)


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(tmp_path / "book")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def cfg():
    c = NovelConfig(title="P1测试")
    c.plan_graph = True
    c.book_facts = True
    c.conflict_guard = True
    return c


@pytest.fixture
def world():
    return World(characters=[Character(name="陆沉"), Character(name="独眼刘")],
                 items=[ItemCard(name="铜匣")],
                 locations=[LocationCard(name="墟内二层")])


@pytest.fixture
def outline():
    return Outline(chapters=[
        ChapterPlan(no=1, title="入墟", goal="陆沉进入墟内二层",
                    character_moves="陆沉：探查四周；独眼刘：暗中跟随"),
        ChapterPlan(no=2, title="夺匣", goal="陆沉从独眼刘手中夺回铜匣",
                    character_moves="陆沉：夺回铜匣"),
    ])


# ---------- 编译 ----------
@pytest.mark.p1
def test_compile_enumerates_constraints(outline, world):
    plan = plan_graph.compile_from_outline(outline, world)
    assert plan["ch_range"] == [1, 2]
    kinds = {c["kind"] for c in plan["constraints"]}
    assert {"must_present", "must_locate", "must_hold"} <= kinds
    ch1 = [c for c in plan["constraints"] if c["ch"] == 1]
    assert any(c["dst"] == node_id("character", "独眼刘") for c in ch1)
    assert any(c["kind"] == "must_locate" for c in ch1)
    # id 唯一、格式固定
    ids = [c["id"] for c in plan["constraints"]]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("cst_ch") for i in ids)


@pytest.mark.p1
def test_compile_is_deterministic(outline, world):
    a = plan_graph.compile_from_outline(outline, world)
    b = plan_graph.compile_from_outline(outline, world)
    assert a["constraints"] == b["constraints"]


@pytest.mark.p1
def test_compile_keeps_manual_constraints(outline, world):
    manual = {"id": "cst_ch1_99", "ch": 1, "kind": "forbidden_present",
              "dst": node_id("character", "独眼刘"), "strength": "must",
              "text": "手写：独眼刘不得在本章露面（作者锁定）", "source": "manual"}
    plan = plan_graph.compile_from_outline(outline, world,
                                           existing={"constraints": [manual]})
    assert any(c.get("source") == "manual" and c["id"] == "cst_ch1_99"
               for c in plan["constraints"])


@pytest.mark.p1
def test_validate_flags_structural_errors(outline, world):
    plan = plan_graph.compile_from_outline(outline, world)
    assert plan_graph.validate(plan, world)["ok"] is True

    bad = {"ch_range": [1, 2], "names": {}, "constraints": [
        {"id": "cst_ch1_01", "ch": 1, "kind": "nonsense", "strength": "must"},
        {"id": "cst_ch1_01", "ch": 9, "kind": "must_present", "strength": "must"},
        {"id": "cst_ch9_01", "ch": 9, "kind": "must_present", "strength": "must",
         "dst": ""},
    ], "intents": []}
    r = plan_graph.validate(bad, world)
    codes = {i["code"] for i in r["issues"]}
    assert r["ok"] is False
    assert {"bad_kind", "duplicate_id", "ch_out_of_range", "missing_dst"} <= codes


# ---------- 派生 ----------
@pytest.mark.p1
def test_derive_reads_plan_and_canon_holds(store, cfg):
    plan = {"ch_range": [1, 3], "names": {node_id("item", "铜匣"): "铜匣",
                                          node_id("character", "陆沉"): "陆沉",
                                          node_id("character", "独眼刘"): "独眼刘"},
            "constraints": [{"id": "cst_ch2_01", "ch": 2, "kind": "must_present",
                             "dst": node_id("character", "独眼刘"), "strength": "must",
                             "text": "独眼刘必须出场"}],
            "intents": []}
    canon = {"nodes": [
        {"id": node_id("item", "铜匣"), "type": "item", "name": "铜匣"},
        {"id": node_id("character", "陆沉"), "type": "character", "name": "陆沉"}],
        "edges": [
            {"rel": "holds", "src": node_id("item", "铜匣"),
             "dst": node_id("character", "陆沉"), "valid_from_ch": 2,
             "valid_to_ch": 5,
             "evidence": {"ch": 2, "offset": 10, "snippet": "陆沉握住铜匣"}},
            {"rel": "holds", "src": node_id("item", "铜匣"),
             "dst": node_id("character", "陆沉"), "valid_from_ch": 1,
             "valid_to_ch": 2,
             "evidence": {"ch": 1, "offset": 3, "snippet": "独眼刘夺走"}},
        ]}
    rows = plan_graph.derive_constraints(2, plan=plan, canon=canon)
    assert any(c["kind"] == "must_present" for c in rows)
    holds = [c for c in rows if c["kind"] == "must_hold"]
    assert len(holds) == 1 and "铜匣" in holds[0]["text"]
    # 第 1 章有效的边（valid_to_ch=2）不进入第 2 章；第 5 章也已关闭
    at5 = plan_graph.derive_constraints(5, plan=plan, canon=canon)
    assert all(c["kind"] != "must_hold" for c in at5)


@pytest.mark.p1
def test_derive_forbidden_present_from_death(store, cfg):
    canon = {"nodes": [{"id": node_id("character", "独眼刘"), "type": "character",
                        "name": "独眼刘", "attrs": {"dead_ch": 3}}], "edges": []}
    at5 = plan_graph.derive_constraints(5, canon=canon)
    assert [c["kind"] for c in at5] == ["forbidden_present"]
    # 死亡发生在本章内（第 3 章）→ 不禁止他出现在本章
    at3 = plan_graph.derive_constraints(3, canon=canon)
    assert at3 == []


@pytest.mark.p1
def test_derive_must_resolve_skips_resolved(store, cfg):
    fs = {"items": [{"key": "fp_a", "name": "铜匣来历", "due_ch": 4, "status": "due"},
                    {"key": "fp_b", "name": "旧伤", "due_ch": 4, "status": "resolved"}]}
    rows = plan_graph.derive_constraints(5, foreshadowing=fs)
    names = [c["text"] for c in rows]
    assert any("铜匣来历" in t for t in names)
    assert all("旧伤" not in t for t in names)


@pytest.mark.p1
def test_cap_must_downgrades_overflow():
    rows = [{"kind": "must_present", "strength": "must", "text": f"c{i}"}
            for i in range(8)]
    capped = plan_graph.cap_must(rows)
    assert sum(1 for c in capped if c["strength"] == "must") == plan_graph.MAX_MUST_PER_CH
    downgraded = [c for c in capped if c.get("downgraded")]
    assert len(downgraded) == 3
    assert all(c["strength"] == "should" for c in downgraded)


# ---------- 渲染与合并 ----------
@pytest.mark.p1
def test_render_block_respects_budget():
    rows = [{"kind": "must_present", "strength": "must",
             "text": "陆沉必须出场并且必须携带铜匣穿过墟内二层的所有危险区域"} for _ in range(10)]
    text = plan_graph.render_block(rows)
    assert text.startswith("【本章硬约束】")
    assert len(text) <= plan_graph.BLOCK_BUDGET
    assert text.count("\n") >= 1


@pytest.mark.p1
def test_merge_blocks_dedupes_and_caps():
    plan_block = "【本章硬约束】\n- 陆沉必须出场\n- 铜匣必须仍由陆沉持有"
    conflict_block = "【冲突提醒】\n- 陆沉必须出场\n- 第3章记为独眼刘（与第2章矛盾）"
    merged = plan_graph.merge_blocks(plan_block, conflict_block, budget=60)
    assert "【本章硬约束】" in merged and "【冲突提醒】" in merged
    assert merged.count("陆沉必须出场") == 1
    assert len(merged) <= 60


@pytest.mark.p1
def test_save_version_bumps_only_on_content_change(store, cfg, outline, world):
    plan = plan_graph.compile_from_outline(outline, world)
    v1 = plan_graph.save(store, plan)
    assert v1 == 1
    v2 = plan_graph.save(store, plan_graph.compile_from_outline(outline, world))
    assert v2 == 1  # 内容未变 → 不递增
    plan["constraints"] = plan["constraints"][:-1]
    v3 = plan_graph.save(store, plan)
    assert v3 == 2


@pytest.mark.p1
def test_constraints_block_empty_when_flag_off(store, cfg, outline, world):
    plan_graph.save(store, plan_graph.compile_from_outline(outline, world))
    cfg.plan_graph = False
    assert plan_graph.constraints_block(store, cfg, 1) == ""


@pytest.mark.p1
def test_constraints_block_renders_when_flag_on(store, cfg, outline, world):
    plan_graph.save(store, plan_graph.compile_from_outline(outline, world))
    text = plan_graph.constraints_block(store, cfg, 1)
    assert text.startswith("【本章硬约束】")
    assert "陆沉" in text


# ---------- P2：卡片 / 编辑 / diff / impact / 观察 ----------
@pytest.mark.p2
def test_cards_resolve_names_and_labels(outline, world):
    plan = plan_graph.compile_from_outline(outline, world)
    rows = plan_graph.cards(plan, ch=1)
    assert rows and all(r["ch"] == 1 for r in rows)
    present = [r for r in rows if r["kind"] == "must_present"][0]
    assert present["object"] == "陆沉" and present["kind_label"] == "必须出场"
    # ch=0 返回全部章
    assert len(plan_graph.cards(plan)) == len(plan["constraints"])


@pytest.mark.p2
def test_upsert_constraint_creates_then_replaces():
    plan = {"constraints": [], "intents": [], "names": {}}
    card = plan_graph.upsert_constraint(plan, {
        "ch": 3, "kind": "must_present", "dst_name": "陆沉",
        "strength": "must", "text": "陆沉必须在第3章出现"})
    assert card["id"].startswith("cst_ch3_m") and card["source"] == "manual"
    assert plan["names"][card["dst"]] == "陆沉"
    # 同 id 再保存 → 覆盖，不新增
    plan_graph.upsert_constraint(plan, {**card, "strength": "should"})
    assert len(plan["constraints"]) == 1
    assert plan["constraints"][0]["strength"] == "should"


@pytest.mark.p2
def test_upsert_constraint_hold_uses_item_and_owner():
    plan = {"constraints": [], "intents": [], "names": {}}
    card = plan_graph.upsert_constraint(plan, {
        "ch": 5, "kind": "must_hold", "src_name": "铜匣", "dst_name": "陆沉"})
    assert card["src"] == node_id("item", "铜匣")
    assert card["dst"] == node_id("character", "陆沉")
    assert "铜匣" in card["text"]


@pytest.mark.p2
@pytest.mark.parametrize("payload,fragment", [
    ({"ch": 1, "kind": "nope", "dst_name": "陆沉"}, "未知 kind"),
    ({"ch": 0, "kind": "must_present", "dst_name": "陆沉"}, "章号"),
    ({"ch": 1, "kind": "must_present"}, "dst"),
    ({"ch": 1, "kind": "must_hold", "dst_name": "陆沉"}, "src/dst"),
    ({"ch": 1, "kind": "must_present", "dst_name": "陆沉", "strength": "hard"}, "strength"),
])
def test_upsert_constraint_rejects_bad_input(payload, fragment):
    with pytest.raises(ValueError) as e:
        plan_graph.upsert_constraint({"constraints": []}, payload)
    assert fragment in str(e.value)


@pytest.mark.p2
def test_remove_constraint():
    plan = {"constraints": [{"id": "a"}, {"id": "b"}]}
    assert plan_graph.remove_constraint(plan, "a") is True
    assert [c["id"] for c in plan["constraints"]] == ["b"]
    assert plan_graph.remove_constraint(plan, "zzz") is False


@pytest.mark.p2
def test_diff_missing_costs_five_and_does_not_block():
    plan = {"names": {node_id("character", "陆沉"): "陆沉"},
            "constraints": [{"id": "c1", "ch": 2, "kind": "must_present",
                             "dst": node_id("character", "陆沉"), "strength": "must",
                             "text": "陆沉必须出场"}]}
    observed = {"available": True, "characters": {"独眼刘": "存活"}, "items": {}, "dead": []}
    r = plan_graph.diff(plan, 2, observed)
    assert r["counts"]["missing"] == 1 and r["score"] == -5
    assert r["blocking"] is False
    assert r["missing"][0]["label"] == "陆沉"


@pytest.mark.p2
def test_diff_satisfied_hold_counts_as_satisfied():
    plan = {"names": {node_id("item", "铜匣"): "铜匣", node_id("character", "陆沉"): "陆沉"},
            "constraints": [{"id": "c1", "ch": 4, "kind": "must_hold",
                             "src": node_id("item", "铜匣"),
                             "dst": node_id("character", "陆沉"),
                             "strength": "must", "text": "铜匣必须仍由陆沉持有"}]}
    observed = {"available": True, "characters": {"陆沉": "存活"},
                "items": {"铜匣": {"owner": "陆沉", "state": "held"}}, "dead": []}
    r = plan_graph.diff(plan, 4, observed)
    assert r["counts"]["satisfied"] == 1 and r["score"] == 0


@pytest.mark.p2
def test_diff_violation_blocks():
    plan = {"names": {node_id("character", "陆沉"): "陆沉"},
            "constraints": [{"id": "c1", "ch": 6, "kind": "forbidden_present",
                             "dst": node_id("character", "陆沉"), "strength": "must",
                             "text": "陆沉已被禁止出现"}]}
    observed = {"available": True, "characters": {"陆沉": "存活"}, "items": {}, "dead": []}
    r = plan_graph.diff(plan, 6, observed)
    assert r["blocking"] is True and r["counts"]["violation"] == 1
    assert "禁止项冲突" in r["violation"][0]["detail"]


@pytest.mark.p2
def test_diff_unplanned_holds_are_info_only():
    plan = {"names": {}, "constraints": []}
    observed = {"available": True, "characters": {"独眼刘": "存活"},
                "items": {"铜匣": {"owner": "独眼刘", "state": "held"}}, "dead": []}
    r = plan_graph.diff(plan, 7, observed)
    assert r["blocking"] is False and r["score"] == 0
    assert r["unplanned"][0]["kind"] == "item_transfer"


@pytest.mark.p2
def test_diff_must_resolve_needs_real_resolution():
    plan = {"names": {"fp_铜匣来历": "铜匣来历"}, "constraints": []}
    fs_unresolved = {"items": [{"key": "fp_铜匣来历", "name": "铜匣来历",
                                "due_ch": 4, "status": "overdue"}]}
    fs_resolved = {"items": [{"key": "fp_铜匣来历", "name": "铜匣来历", "due_ch": 4,
                              "status": "resolved", "reveal_ch": 5}]}
    observed = {"available": True, "characters": {}, "items": {}, "dead": []}
    miss = plan_graph.diff(plan, 5, observed, foreshadowing=fs_unresolved)
    assert any(m["kind"] == "must_resolve" for m in miss["missing"])
    ok = plan_graph.diff(plan, 5, observed, foreshadowing=fs_resolved)
    assert any(s["kind"] == "must_resolve" for s in ok["satisfied"])


@pytest.mark.p2
def test_diff_forbidden_from_death_and_unplanned_death():
    canon = {"nodes": [{"id": node_id("character", "独眼刘"), "type": "character",
                        "name": "独眼刘", "attrs": {"dead_ch": 3}}], "edges": []}
    observed = {"available": True, "characters": {"独眼刘": "已死"}, "items": {}, "dead": ["独眼刘"]}
    r = plan_graph.diff({"names": {}, "constraints": []}, 5, observed, canon=canon)
    assert r["blocking"] is True  # 死人在本章出现
    # 同一章内的死亡若 Plan 未声明 → info
    r2 = plan_graph.diff({"names": {}, "constraints": []}, 3,
                         {"available": True, "characters": {"独眼刘": "身亡"},
                          "items": {}, "dead": ["独眼刘"]}, canon=canon)
    assert any(u["kind"] == "character_death" for u in r2["unplanned"])


@pytest.mark.p2
def test_observe_reads_scenes_state(store, cfg):
    store.save_scenes(5, [], {"characters": {"陆沉": "存活"},
                              "items": {"铜匣": {"owner": "陆沉", "state": "held"}},
                              "time": "墟内二层"})
    obs = plan_graph.observe(store, 5)
    assert obs["available"] is True
    assert obs["characters"] == {"陆沉": "存活"}
    assert obs["items"]["铜匣"]["state"] == "held"
    # 没写过的章：明确不可用，不造数据
    assert plan_graph.observe(store, 9)["available"] is False


@pytest.mark.p2
def test_plan_impact_lists_affected_chapters():
    plan = {"names": {node_id("character", "陆沉"): "陆沉"},
            "constraints": [{"id": "c1", "ch": 2, "kind": "must_present",
                             "dst": node_id("character", "陆沉"), "strength": "must",
                             "text": "陆沉必须出场"},
                            {"id": "c2", "ch": 6, "kind": "must_present",
                             "dst": node_id("character", "陆沉"), "strength": "must",
                             "text": "陆沉必须出场"},
                            {"id": "c3", "ch": 4, "kind": "must_locate",
                             "dst": node_id("place", "墟内二层"), "strength": "must",
                             "text": "必须在墟内二层"}]}
    r = plan_graph.impact(plan, "陆沉")
    assert r["found"] is True and r["chapters"] == [2, 6]
    assert plan_graph.impact(plan, "墟内二层")["chapters"] == [4]
    assert plan_graph.impact(plan, "不存在")["found"] is False


# ---------- P2 接口 ----------
@pytest.mark.p2
def test_api_cards_validate_and_edit(store, outline, world, monkeypatch):
    import webapp.routers.projects_plan as router

    store.save_config(cfg_with_plan())
    store.save_world(world)
    store.save_outline(outline)
    import webapp.routers.projects_plan as r2
    monkeypatch.setattr(r2, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(r2, "_find_project", lambda project_id: store)
    monkeypatch.setattr(r2, "apply_global_overrides", lambda c: None)
    res = router.post_plan_compile(project_id="p2", payload={}, user={"open_id": "t"})
    assert res["ok"] is True and res["plan_version"] == 1

    cards = router.get_plan_graph(project_id="p2", view="cards", ch=1, seed="", up_to=0,
                                  user={"open_id": "t"})
    assert cards["cards"]

    put = router.put_plan_constraint(project_id="p2",
                                     payload={"ch": 1, "kind": "forbidden_present",
                                              "dst_name": "独眼刘", "strength": "must"},
                                     user={"open_id": "t"})
    assert put["plan_version"] == 2
    del_res = router.delete_plan_constraint(project_id="p2",
                                            cid=put["constraint"]["id"],
                                            user={"open_id": "t"})
    assert del_res["removed"] == put["constraint"]["id"]

    diff = router.get_plan_graph(project_id="p2", view="diff", ch=1, seed="", up_to=0,
                                 user={"open_id": "t"})
    assert diff["has_observation"] is False and diff["counts"]["missing"] == 0


@pytest.mark.p2
def test_api_impact_preview_merges_plan_and_canon(store, world, monkeypatch):
    import webapp.routers.projects_plan as router

    cfg = cfg_with_plan()
    cfg.state_graph_v2 = True
    store.save_config(cfg)
    store.save_world(world)
    plan = {"names": {node_id("character", "陆沉"): "陆沉"}, "constraints": [
        {"id": "c1", "ch": 3, "kind": "must_present",
         "dst": node_id("character", "陆沉"), "strength": "must", "text": "陆沉必须出场"}],
        "intents": []}
    store.save_plan_graph(plan)
    store.save_scenes(5, [], {"items": {"铜匣": {"owner": "陆沉", "state": "held",
                                              "evidence": "陆沉握住铜匣"}}})
    store.save_chapter_state({"chapter": 5, "characters": {"陆沉": ""},
                              "items": {"铜匣": {"owner": "陆沉", "state": "held"}},
                              "facts": [], "numbers": []})
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)
    monkeypatch.setattr(router, "apply_global_overrides", lambda c: None)
    res = router.post_plan_impact_preview(project_id="p3", payload={"entities": ["陆沉"]},
                                          user={"open_id": "t"})
    assert res["enabled"] is True and res["canon_available"] is True
    row = res["entities"][0]
    assert 3 in row["plan_chapters"]
    assert 5 in row["canon_chapters"]


def cfg_with_plan():
    c = NovelConfig(title="P2测试")
    c.plan_graph = True
    c.book_facts = True
    c.conflict_guard = True
    return c

@pytest.mark.p2
def test_diff_marks_character_checks_unverifiable_without_characters():
    plan = {"names": {node_id("character", "陆沉"): "陆沉"},
            "constraints": [{"id": "c1", "ch": 9, "kind": "must_present",
                             "dst": node_id("character", "陆沉"), "strength": "must",
                             "text": "陆沉必须出场"}]}
    observed = {"available": True, "characters": {},
                "items": {"铜匣": {"owner": "陆沉", "state": "held"}}, "dead": []}
    r = plan_graph.diff(plan, 9, observed)
    # 只抽到物品、没抽到人物：判「无法核实」，不当作没做到扣分
    assert r["counts"]["missing"] == 0 and r["score"] == 0
    assert r["unverifiable"][0]["reason"].startswith("本章未抽取到人物状态")

def test_manager_compiles_plan_before_writing(store, outline, world):
    """接线：开 plan_graph 时写章前必须已落 plan_graph.json（否则约束注入为空）。"""
    from unittest.mock import MagicMock
    from novel_gen.pipeline.manager import NovelPipeline

    cfg = cfg_with_plan()
    store.save_config(cfg)
    pipe = NovelPipeline(cfg, store, MagicMock())
    store.save_outline(outline)
    store.save_world(world)
    before = plan_graph.load(store)
    assert before is None
    assert pipe._ensure_plan_graph(silent=True) is True
    plan = plan_graph.load(store)
    assert plan and plan["constraints"], "写章前必须有可用约束"
    # 幂等：再次调用不递增版本（大纲未变）
    v1 = plan.get("plan_version")
    assert pipe._ensure_plan_graph(silent=True) is True
    assert plan_graph.load(store).get("plan_version") == v1


def test_manager_skips_plan_when_flag_off(store, outline, world):
    from unittest.mock import MagicMock
    from novel_gen.pipeline.manager import NovelPipeline

    cfg = cfg_with_plan()
    cfg.plan_graph = False
    store.save_config(cfg)
    pipe = NovelPipeline(cfg, store, MagicMock())
    store.save_outline(outline)
    store.save_world(world)
    assert pipe._ensure_plan_graph(silent=True) is True
    assert plan_graph.load(store) is None
