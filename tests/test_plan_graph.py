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
