# -*- coding: utf-8 -*-
"""REQ-R22 L3：作者计划事件（normalize / 渲染 / 视图回读）。"""
import pytest

from novel_gen.config import NovelConfig
from novel_gen.pipeline.planned_events import from_store, normalize, render_block
from novel_gen.store.project import ProjectStore


def test_normalize_cleans_and_sorts():
    items = normalize([
        {"title": "后发生的事", "ch": 16},
        {"title": "先发生的事", "ch": "15", "participants": ["苏妄言", " 苏妄言 ", "陆沉"],
         "kind": "unknown", "status": "weird"},
    ])
    assert [r["ch"] for r in items] == [15, 16]
    assert items[0]["participants"] == ["苏妄言", "陆沉"]
    assert items[0]["id"].startswith("pe_")
    assert items[0]["kind"] == "planned" and items[0]["status"] == "planned"


@pytest.mark.parametrize("bad", [
    [{"title": "", "ch": 1}],
    [{"title": "缺章"}],
    [{"title": "章越界", "ch": 0}],
    [{"title": "坏 id", "ch": 1, "id": "带 空格"}],
    [{"title": "重复", "ch": 1, "id": "pe_same"}, {"title": "重复2", "ch": 2, "id": "pe_same"}],
])
def test_normalize_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize(bad)


def test_render_block_only_target_chapter():
    items = normalize([{"title": "灯壁刻字", "ch": 15, "participants": ["苏妄言"],
                        "thread": "嵌在岩壁里的灯", "desc": "发现上一轮的名字"}])
    block = render_block(items, 15)
    assert "本章作者计划事件" in block and "灯壁刻字" in block and "苏妄言" in block
    assert render_block(items, 14) == ""


def test_store_roundtrip_and_timeline_planned(tmp_path):
    cfg = NovelConfig()
    cfg.state_graph = True
    store = ProjectStore.create(tmp_path, cfg)
    items = normalize([{"title": "计划 A", "ch": 3, "participants": ["陆沉"]}])
    store.save_planned_events(items)
    assert from_store(store)[0]["title"] == "计划 A"

    from novel_gen.pipeline.state.views import StateGraph
    data = StateGraph(store, cfg).timeline()
    assert data["planned_count"] == 1
    assert data["planned"][0]["planned"] is True
    # 计划事件不进入已发生事件流
    assert data["count"] == 0
