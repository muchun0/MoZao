# -*- coding: utf-8 -*-
"""REQ-R20 / TECH-R20 M0：book_facts 单一只读状态视图 单测。

覆盖验收判据里可离线验证的部分：
- US-1 九字段齐全、缺数据给空结构
- US-2 两段预算（硬区不裁剪 / 活区整条丢弃）
- US-6 开关关闭 → 空串（回滚与改造前等价）
- US-7 无来源条目丢弃并计数
- US-8 版本不一致置 stale 并在块尾提示
- 开关注册两条（FEATURE_FLAGS + NovelConfig）
- 落盘原子写往返
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import FEATURE_FLAGS, NovelConfig, feature_default
from novel_gen.pipeline.book_facts import AGENT_BUDGETS, BookFacts, empty_snapshot
from novel_gen.pipeline.memory import Memory
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Character, ForeshadowItem, World

SNAPSHOT_KEYS = {"version", "at_ch", "world_v", "outline_v", "stale",
                 "invariants", "characters", "open_threads", "events", "terminology"}


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(tmp_path / "book")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def cfg():
    c = NovelConfig(title="测试书")
    c.book_facts = True   # 开关默认 False（A/B 期由后台打开），单测显式开启
    return c


def _world() -> World:
    return World(
        world_view="九域大陆，灵气复苏",
        characters=[
            Character(name="陆沉", role="主角"),
            Character(name="独眼刘", role="反派"),
        ],
        foreshadowing=[
            ForeshadowItem(name="灰髓袋来历", buried_ch=1, reveal_ch=3, content="袋子来自墟内二层"),
            ForeshadowItem(name="人头押金", buried_ch=2, reveal_ch=5, content="押金由独眼刘垫付"),
            ForeshadowItem(name="只埋不收", buried_ch=1, reveal_ch=None, content="无回收章"),
        ],
    )


# ---------- US-1：结构 ----------
@pytest.mark.p1
def test_empty_project_returns_empty_structure_not_null(store, cfg):
    snap = BookFacts(store, cfg).snapshot(at_ch=0)
    assert set(snap.keys()) == SNAPSHOT_KEYS
    assert snap["version"] == 1 and snap["at_ch"] == 0
    assert snap["characters"] == [] and snap["open_threads"] == [] and snap["events"] == []
    assert snap["invariants"]["mechanism"] == {}
    assert snap["terminology"] == {"terms": [], "forbidden": []}


# ---------- US-7：来源过滤 + 人物/伏笔构造 ----------
@pytest.mark.p1
def test_characters_and_threads_require_source(store, cfg):
    store.save_world(_world())
    store.save_chapter_state({"chapter": 4, "characters": {"陆沉": "左臂受伤"}, "numbers": ["可死次数:1"]})
    store.save_character_states({
        "陆沉": {"last_seen": 4, "location": "墟外坡底", "goal": "找回灰髓袋"},
        "无来源人物": {"location": "未知"},
    })
    store.save_book_memory({
        "canon": {"terms": {"灰髓袋": {"canonical": "灰髓袋", "first_ch": 1}},
                  "disciplines": [{"rule": "不得复活"}],
                  "mechanism_baseline": {"可死次数": "1"}},
        "events": {"consumed": [{"desc": "进入墟内二层", "ch": 3}]},
    })
    facts = BookFacts(store, cfg)
    snap = facts.snapshot(at_ch=4)

    names = [c["name"] for c in snap["characters"]]
    assert names == ["陆沉"], "无 last_ch 的人物必须丢弃，不进入快照"
    assert snap["characters"][0]["source"].startswith("chapter_state:4")
    assert snap["characters"][0]["state"] == "左臂受伤"

    keys = [t["key"] for t in snap["open_threads"]]
    assert "只埋不收" not in keys, "无 due_ch 的伏笔丢弃"
    status = {t["key"]: t["status"] for t in snap["open_threads"]}
    assert status["灰髓袋来历"] == "overdue"      # due 3，写到第 4 章仍未回收
    assert status["人头押金"] == "due_soon"       # due 5，距 4 章 1 章

    assert snap["events"][0]["desc"] == "进入墟内二层"
    assert snap["terminology"]["terms"] == ["灰髓袋"]
    assert snap["invariants"]["disciplines"] == ["不得复活"]
    assert facts.last_dropped >= 2                # 无来源人物 + 只埋不收


@pytest.mark.p1
def test_open_threads_sorted_by_due_then_planted(store, cfg):
    store.save_world(_world())
    rows = BookFacts(store, cfg).open_threads(at_ch=4)
    assert [r["due_ch"] for r in rows] == [3, 5]
    assert [r["status"] for r in rows] == ["overdue", "due_soon"]


# ---------- US-2：预算 ----------
@pytest.mark.p1
def test_hard_zone_order_and_active_zone_budget(store, cfg):
    store.save_world(_world())
    store.save_chapter_state({"chapter": 5, "characters": {f"人物{i}": f"状态{i}" for i in range(12)}})
    store.save_character_states({f"人物{i}": {"last_seen": 5, "location": f"地点{i}"} for i in range(12)})
    store.save_book_memory({
        "canon": {"mechanism_baseline": {"刻度": "3"}, "disciplines": [{"rule": "禁用现代名词"}]},
        "events": {"consumed": [{"desc": f"事件{i}", "ch": i} for i in range(1, 11)]},
    })
    facts = BookFacts(store, cfg)
    full = facts.block_for("writer", at_ch=5)
    tight = facts.block_for("writer", at_ch=5, budget=300)
    assert "【机制基线·权威口径】" in full
    assert len(tight) < len(full), "budget 收紧后活区必须整条丢弃"
    assert "【机制基线·权威口径】" in tight, "硬区永不裁剪"
    assert AGENT_BUDGETS["writer"] == (1200, 1500)


@pytest.mark.p1
def test_writer_filters_threads_and_architect_has_no_events(store, cfg):
    store.save_world(World(characters=[Character(name="陆沉")], foreshadowing=[
        ForeshadowItem(name="远期伏笔", buried_ch=10, reveal_ch=60),
        ForeshadowItem(name="临期伏笔", buried_ch=12, reveal_ch=14),
    ]))
    store.save_book_memory({"events": {"consumed": [{"desc": "旧事", "ch": 3}]}})
    facts = BookFacts(store, cfg)
    writer_block = facts.block_for("writer", at_ch=13)
    assert "临期伏笔" in writer_block
    assert "远期伏笔" not in writer_block, "due 远且未老化的伏笔不进活区"
    architect_block = facts.block_for("architect", at_ch=13)
    assert "旧事" not in architect_block, "architect 视图不含事件流"


# ---------- US-6：开关关闭 ----------
@pytest.mark.p1
def test_flag_off_returns_empty_and_memory_blocks_unchanged(store):
    cfg = NovelConfig(title="测试书")
    cfg.book_facts = False
    store.save_world(_world())
    store.save_chapter_state({"chapter": 2, "characters": {"陆沉": "受伤"}})
    store.save_character_states({"陆沉": {"last_seen": 2, "location": "墟内"}})

    assert BookFacts(store, cfg).block_for("writer", at_ch=2) == ""
    mem = Memory(store, memory_window=0)
    legacy = mem.build_context(store.load_world(), up_to_chapter=3)
    explicit_off = mem.build_context(store.load_world(), up_to_chapter=3, skip_state_blocks=False)
    assert legacy == explicit_off, "关闭开关时必须与改造前逐字等价"
    on = mem.build_context(store.load_world(), up_to_chapter=3, skip_state_blocks=True)
    assert "[L1.5·人物当前状态·最近更新]" not in on
    assert "[L1.6·世界状态·承接上一章]" not in on


# ---------- US-8：stale ----------
@pytest.mark.p1
def test_stale_when_versions_drift(store, cfg, monkeypatch):
    store.save_world(_world())
    facts = BookFacts(store, cfg)
    snap = facts.snapshot(at_ch=2)
    snap["outline_v"] = 1
    store.save_book_facts(snap)

    monkeypatch.setattr(BookFacts, "_current_versions", lambda self: (2, snap.get("world_v")))
    loaded = BookFacts(store, cfg).snapshot(at_ch=2)
    assert loaded["stale"] is True
    assert "以下状态基于旧版设定" in BookFacts(store, cfg).block_for("writer", at_ch=2)


# ---------- 降级 ----------
@pytest.mark.p1
def test_degraded_on_construction_failure(store, cfg, monkeypatch):
    def boom():
        raise RuntimeError("world.json 损坏")

    monkeypatch.setattr(store, "load_world", boom)
    facts = BookFacts(store, cfg)
    snap = facts.snapshot(at_ch=1)
    assert set(snap.keys()) == SNAPSHOT_KEYS
    assert facts.degraded is True
    assert facts.block_for("writer", at_ch=1) == "", "构造失败不得把空结构当事实注入"


# ---------- 落盘 ----------
@pytest.mark.p1
def test_store_roundtrip_uses_atomic_file(store, cfg):
    facts = BookFacts(store, cfg)
    snap = facts.snapshot(at_ch=1)
    assert store.book_facts_path().exists()
    assert store.load_book_facts()["version"] == 1
    assert not store.book_facts_path().with_name("book_facts.json.tmp").exists()


@pytest.mark.p1
def test_source_write_invalidates_snapshot(store, cfg):
    """同章重写时 at_ch 不变：源账本一写就必须让派生快照失效，不能复用旧状态。"""
    store.save_chapter_state({"chapter": 1, "characters": {"陆沉": "受伤"}})
    store.save_character_states({"陆沉": {"last_seen": 1, "location": "墟内"}})
    facts = BookFacts(store, cfg)
    facts.snapshot(at_ch=1)
    assert store.book_facts_path().exists()

    store.save_chapter_state({"chapter": 1, "characters": {"陆沉": "重伤昏迷"}})
    assert not store.book_facts_path().exists(), "源账本写入后必须删派生快照"
    snap = BookFacts(store, cfg).snapshot(at_ch=1)
    assert snap["characters"][0]["state"] == "重伤昏迷"


# ---------- 开关注册 ----------
@pytest.mark.p1
def test_feature_flag_registered_two_steps():
    keys = [f.key for f in FEATURE_FLAGS]
    assert "book_facts" in keys
    assert feature_default("book_facts") is False
    assert NovelConfig(title="x").book_facts is False


# ---------- 接线（US-4：替换而非叠加） ----------
@pytest.mark.p1
def test_writer_injects_facts_block_and_drops_legacy_blocks(store, cfg):
    from novel_gen.pipeline.memory import Memory
    from novel_gen.pipeline.stages import ChapterWriter

    store.save_world(_world())
    store.save_chapter_state({"chapter": 1, "characters": {"陆沉": "左臂受伤"}})
    store.save_character_states({"陆沉": {"last_seen": 1, "location": "墟外坡底"}})
    writer = ChapterWriter(cfg, llm=None, store=store, memory=Memory(store, memory_window=0))
    ctx, _, _ = writer._inject_memory_blocks(2, store.load_world(), "", "", "")
    assert "【人物当前态·来源可追溯】" in ctx, "开启开关后写章必须拿到 book_facts 块"
    assert "【读者已知" not in ctx, "读者认知账本已被统一视图替换，不得重复注入"
    assert writer._book_canon() == "", "设定账本由硬区替换，不得再单独注入"


# ---------- 只读接口（US-3） ----------
@pytest.mark.p1
def test_api_returns_snapshot_block_and_rejects_unknown_agent(store, cfg, monkeypatch):
    from fastapi import HTTPException

    import webapp.routers.projects_chapters as router

    store.save_config(cfg)
    store.save_world(_world())
    store.save_chapter_state({"chapter": 1, "characters": {"陆沉": "受伤"}})
    store.save_character_states({"陆沉": {"last_seen": 1, "location": "墟内"}})
    monkeypatch.setattr(router, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(router, "_find_project", lambda project_id: store)

    result = router.get_book_facts(project_id="p1", at_ch=1, agent="writer",
                                  budget=800, user={"open_id": "test"})
    assert set(result.keys()) == {"snapshot", "block", "degraded", "stale", "dropped"}
    assert result["snapshot"]["at_ch"] == 1
    assert "【人物当前态·来源可追溯】" in result["block"]
    assert result["degraded"] is False

    # agent 为空 → 只给快照、不渲染注入块
    assert router.get_book_facts(project_id="p1", at_ch=1, agent="",
                                 budget=None, user={})["block"] == ""
    with pytest.raises(HTTPException) as exc:
        router.get_book_facts(project_id="p1", at_ch=1, agent="reviser",
                              budget=None, user={})
    assert exc.value.status_code == 400

