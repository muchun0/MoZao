# -*- coding: utf-8 -*-
"""REQ-R24 方案 B：items 结构化字段（owner/state/evidence）的读写口径。

语义判断（「是否还持有」）放在账本写入前（LLM 抽取），图与冲突规则只读字段。
本文件锁住三件事：归一不丢旧数据、lost/unknown 不建边、结构化 owner 优先。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import item_state
from novel_gen.pipeline.chapter_state import _clean_items, format_chapter_state
from novel_gen.pipeline.state.conflict_rules import check_item_owner
from novel_gen.pipeline.state.graph import CanonGraph, node_id
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Character, ItemCard, World


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(tmp_path / "book")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def cfg():
    c = NovelConfig(title="B测试")
    c.state_graph_v2 = True
    return c


def _seed(store, cfg):
    store.save_config(cfg)
    store.save_world(World(characters=[Character(name="陆沉"),
                                      Character(name="老陈头")],
                           items=[ItemCard(name="瓷瓶")]))


# ---------- 归一 ----------
def test_legacy_string_is_wrapped_not_misread():
    st = item_state.normalize_item("被老陈头砸碎在地")
    assert st["state"] == "unknown" and st["owner"] == ""
    assert st["text"] == "被老陈头砸碎在地"


def test_structured_value_is_trusted():
    st = item_state.normalize_item({"owner": "陆沉", "state": "held",
                                    "evidence": "陆沉把铜匣塞进铁匣"})
    assert (st["owner"], st["state"]) == ("陆沉", "held")
    assert st["evidence"].startswith("陆沉")


def test_bad_state_falls_back_by_owner_presence():
    assert item_state.normalize_item({"owner": "陆沉", "state": "??"})["state"] == "held"
    assert item_state.normalize_item({"state": "held"})["state"] == "unknown"


def test_label_renders_three_states():
    assert item_state.label({"owner": "陆沉", "state": "held"}) == "陆沉持有"
    assert item_state.label({"state": "lost", "evidence": "砸碎在地"}) == "已失去（砸碎在地）"
    assert item_state.label("在陆沉怀中") == "在陆沉怀中"


def test_clean_items_keeps_both_shapes_and_drops_empty_keys():
    out = _clean_items({"瓷瓶": {"owner": "", "state": "lost", "evidence": "砸碎在地"},
                        "铜匣": "在陆沉手中", "": "x"})
    assert out["瓷瓶"]["state"] == "lost"
    assert out["铜匣"] == "在陆沉手中"
    assert "" not in out


def test_format_chapter_state_uses_shared_label():
    text = format_chapter_state({"items": {"瓷瓶": {"state": "lost",
                                                   "evidence": "砸碎在地"},
                                           "铜匣": {"owner": "陆沉", "state": "held"}}})
    assert "物品归属：瓷瓶（已失去（砸碎在地））；铜匣（陆沉持有）" in text


# ---------- 图与规则 ----------
def test_structured_lost_builds_no_holds_edge(store, cfg):
    _seed(store, cfg)
    store.save_scenes(4, [], {"items": {"瓷瓶": {"owner": "", "state": "lost",
                                               "evidence": "被老陈头砸碎在地"}}})
    g = CanonGraph(store, cfg).build(at_ch=4)
    assert [e for e in g["edges"] if e["rel"] == "holds"] == []


def test_structured_held_builds_edge_with_evidence(store, cfg):
    _seed(store, cfg)
    store.save_scenes(4, [], {"items": {"瓷瓶": {"owner": "陆沉", "state": "held",
                                               "evidence": "陆沉把瓷瓶收进袖中"}}})
    g = CanonGraph(store, cfg).build(at_ch=4)
    holds = [e for e in g["edges"] if e["rel"] == "holds"]
    assert len(holds) == 1
    assert holds[0]["dst"] == node_id("character", "陆沉")
    assert holds[0]["evidence"]["snippet"].endswith("陆沉把瓷瓶收进袖中")


def test_structured_unknown_does_not_guess_owner(store, cfg):
    _seed(store, cfg)
    # 旧文本靠正则能猜出老陈头；结构化 unknown 明确表示「正文没写清」→ 不建边
    store.save_scenes(4, [], {"items": {"瓷瓶": {"owner": "", "state": "unknown",
                                               "evidence": "瓷瓶被老陈头带走了？"}}})
    g = CanonGraph(store, cfg).build(at_ch=4)
    assert [e for e in g["edges"] if e["rel"] == "holds"] == []


def test_check_item_owner_skips_lost_and_prefers_structured_owner(store, cfg):
    chapters = [{"no": 4, "characters": {"陆沉": "", "老陈头": ""},
                 "items": {"瓷瓶": {"owner": "", "state": "lost",
                                   "evidence": "砸碎在地"}},
                 "facts": []}]
    assert check_item_owner(chapters, []) == []
    chapters[0]["items"] = {"瓷瓶": {"owner": "老陈头", "state": "held",
                                     "evidence": "老陈头揣进怀里"}}
    rows = check_item_owner(chapters, [])
    assert rows == []  # 首章无前值可比
