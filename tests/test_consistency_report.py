# -*- coding: utf-8 -*-
"""R2 全书体检纯函数测试（账本覆盖 / 伏笔台账 / 一致性冲突 / 提及索引）。

只读逻辑：不触 LLM、不连 MySQL；数据全部由临时 ProjectStore 落盘后读回。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import consistency_report as cr
from novel_gen.pipeline.commit import check_projection
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (
    Chapter, Character, ForeshadowItem, ItemCard, LocationCard, Outline, TerminologyItem, World,
)


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="体检书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def book(store):
    """10 章正文 + 世界观（人物/地点/物件/术语别名）+ 两条伏笔。"""
    store.save_outline(Outline(title="体检书", intro="简介"))
    world = World(
        world_view="云隐镇",
        characters=[Character(name="林舟", role="主角")],
        locations=[LocationCard(name="云隐镇")],
        items=[ItemCard(name="铜钥匙")],
        terminology=[TerminologyItem(name="蚀痕", aliases=["疤痕"])],
        foreshadowing=[
            ForeshadowItem(name="铜钥匙", content="祖传铜钥匙", buried_ch=1, reveal_ch=3),
            ForeshadowItem(name="旧信", content="父亲留下的信", buried_ch=2, reveal_ch=15),
        ],
    )
    store.save_world(world)
    texts = {
        1: "林舟在云隐镇醒来，手里握着铜钥匙，胳膊上有蚀痕。",
        2: "林舟发现父亲留下的旧信，镇口的疤痕隐隐作痛。",
        3: "林舟用铜钥匙打开地窖，蚀痕在灯下泛红。",
        4: "林舟和阿舟一起赶路，云隐镇在后头。",
        5: "铜钥匙被抢走了。",
    }
    for no in range(1, 11):
        body = texts.get(no, f"平淡的第{no}章，没有提到旧信。")
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=body))
    return store


# ---------------- 账本覆盖 ----------------

def test_coverage_matches_cli_projection(book):
    """体检页与 tools/projection_check.py 同源：结果逐条一致。"""
    got = cr.build_coverage(book)
    expected = check_projection(book)
    assert [{k: r[k] for k in ("view", "covered_ch", "status")} for r in got["views"]] == expected
    assert got["baseline_ch"] == 10
    assert "lagging_views" in got and isinstance(got["lagging_views"], list)


def test_coverage_status_labels_and_severity(book):
    rows = {r["view"]: r for r in cr.build_coverage(book, up_to_chapter=10)["views"]}
    assert rows["chapter_commit"]["label"] in ("同步", "落后", "超前", "未知")
    assert rows["chapter_commit"]["severity"] in cr.SEVERITY_RANK


# ---------------- 伏笔台账 ----------------

def test_foreshadow_states_overdue_stalled_and_revealed(book):
    # 铜钥匙 reveal_ch=3，已写到第 10 章且未落 revealed → overdue
    # 旧信 reveal_ch=15，埋后 8 章无推进且不在回收窗口 → stalled
    book.save_foreshadow_commitment({"version": 1, "updated_ch": 10, "items": [
        {"name": "铜钥匙", "revealed_ch": 3},
    ]})
    items = {i["name"]: i for i in cr.build_foreshadow(book)}
    assert items["铜钥匙"]["state"] == "revealed"
    assert items["铜钥匙"]["revealed_ch"] == 3
    assert items["旧信"]["state"] == "stalled"
    assert items["旧信"]["severity"] == "medium"


def test_foreshadow_overdue_when_not_revealed(book):
    items = {i["name"]: i for i in cr.build_foreshadow(book)}
    assert items["铜钥匙"]["state"] == "overdue"
    assert items["铜钥匙"]["severity"] == "high"
    assert items["铜钥匙"]["state_label"] == "逾期未回收"


def test_foreshadow_empty_without_chapters(store):
    """未写章节时不产生伏笔条目（不做「全部一致」结论）。"""
    store.save_world(World(foreshadowing=[ForeshadowItem(name="空伏笔", buried_ch=1, reveal_ch=2)]))
    assert cr.build_foreshadow(store) == []


# ---------------- 一致性冲突 ----------------

def test_conflicts_include_verify_and_chapter_quality(book):
    book.append_verify_issues(7, [{
        "problem": "第3章写主角已死，第7章仍在行动", "suggestion": "统一生死状态",
    }])
    book.save_chapter(Chapter(no=11, title="第11章", content="新正文"), quality={
        "passed": False, "score": 60, "issue_count": 1,
        "issues": [
            {"type": "自相矛盾-设定", "detail": "本章铜钥匙又出现在手里，前章已被抢走",
             "level": "大改", "evidence": "他攥紧铜钥匙", "fix_suggestion": "补一句取回过程"},
            {"type": "文风", "detail": "连接词堆砌", "level": "小改"},
        ],
    })
    items = cr.build_conflicts(book)
    sources = {i["source"] for i in items}
    assert sources == {"verify", "chapter_quality"}
    verify = next(i for i in items if i["source"] == "verify")
    assert verify["severity"] == "medium" and verify["chapter"] == 7
    quality = next(i for i in items if i["source"] == "chapter_quality")
    assert quality["severity"] == "high"
    assert quality["evidence"] == "他攥紧铜钥匙"
    assert quality["suggestion"] == "补一句取回过程"
    # 非一致性类质检问题不纳入
    assert all("文风" not in i["type"] for i in items)


def test_conflicts_strong_word_maps_high(book):
    book.append_verify_issues(2, [{"problem": "主线严重崩塌", "suggestion": "重写"}])
    item = next(i for i in cr.build_conflicts(book) if i["source"] == "verify")
    assert item["severity"] == "high"


def test_conflicts_conflict_sort_and_empty(store):
    assert cr.build_conflicts(store) == []


# ---------------- 提及索引 ----------------

def test_entity_index_counts_alias_and_zero_hits(book):
    book.save_world(World(
        characters=[Character(name="林舟"), Character(name="配角甲")],
        locations=[LocationCard(name="云隐镇")],
        items=[ItemCard(name="铜钥匙")],
        terminology=[TerminologyItem(name="蚀痕", aliases=["疤痕"])],
        foreshadowing=[],
    ))
    data = cr.build_entity_index(book, limit=10)
    by_name = {i["name"]: i for i in data["items"]}
    assert by_name["林舟"]["total_mentions"] >= 4
    assert by_name["林舟"]["first_ch"] == 1
    assert by_name["蚀痕"]["aliases"] == ["疤痕"]
    # 别名「疤痕」计入蚀痕：第1、2、3章各有命中
    assert by_name["蚀痕"]["total_mentions"] >= 3
    # 从未出现的角色仍保留（覆盖缺口可见），排在末尾
    assert by_name["配角甲"]["total_mentions"] == 0
    assert data["items"][-1]["name"] == "配角甲"
    assert data["truncated"] is False


def test_entity_index_kind_filter_and_query(book):
    assert all(i["kind"] == "character"
               for i in cr.build_entity_index(book, kind="character")["items"])
    assert [i["name"] for i in cr.build_entity_index(book, query="铜")["items"]] == ["铜钥匙"]


def test_search_mentions_returns_snippet_with_offset(book):
    res = cr.search_mentions(book, "林舟", ["阿舟"])
    assert res["total_chapters"] >= 4
    first = res["chapters"][0]
    assert first["no"] == 1
    occ = first["occurrences"][0]
    assert "林舟" in occ["snippet"]
    assert first["count"] >= 1
    # snippet 来自正文命中位置（fixture 第1章以林舟开头）
    assert occ["offset"] == 0


def test_search_mentions_chapter_limit_truncates(book):
    res = cr.search_mentions(book, "林舟", limit_chapters=1)
    assert len(res["chapters"]) == 1
    assert res["truncated"] is True


# ---------------- 首屏汇总 ----------------

def test_overview_empty_project_is_honest(store):
    """无数据：计数为 0、保留数据缺口声明，不输出「全部一致」。"""
    data = cr.build_overview(store, store.load_config())
    assert data["baseline_ch"] == 0
    assert data["chapters_written"] == 0
    assert data["foreshadow"]["total"] == 0
    assert data["conflicts"]["total"] == 0
    assert data["has_world"] is False
    assert data["data_gaps"] and "未记录" in data["data_gaps"][1]


def test_overview_aggregates_counts(book):
    data = cr.build_overview(book, book.load_config())
    assert data["baseline_ch"] == 10
    assert data["foreshadow"]["counts"]["overdue"] >= 1
    assert data["entities"]["candidates"] >= 4
