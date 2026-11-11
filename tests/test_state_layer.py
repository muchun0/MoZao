# -*- coding: utf-8 -*-
"""REQ-R21 / TECH-R21 M1：状态层（事件流 / 伏笔单一源 / 冲突规则 / 迁移对账）单测。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import FEATURE_FLAGS, NovelConfig, feature_default
from novel_gen.pipeline.book_facts import BookFacts
from novel_gen.pipeline.state.conflict_rules import (
    chapters_from_scenes, check_address_drift, check_death_state, check_item_owner,
    check_mechanism_delta, check_time_reverse, detect, parse_numbers, render_block)
from novel_gen.pipeline.state.events import EventError, EventStream, make_id
from novel_gen.pipeline.state.foreshadow import (
    ForeshadowError, ForeshadowLedger, key_for, status_for)
from novel_gen.pipeline.state.migrate import migrate
from novel_gen.pipeline.state.reconcile import reconcile
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Character, ForeshadowItem, World


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
    c.foreshadow_unified = True
    c.conflict_guard = True
    c.timeline_view = True
    return c


def _world():
    return World(
        world_view="九域大陆",
        characters=[Character(name="陆沉", role="主角"), Character(name="独眼刘", role="反派")],
        foreshadowing=[
            ForeshadowItem(name="铜匣来历", buried_ch=3, reveal_ch=15, content="匣底刻字与祖谱对上"),
            ForeshadowItem(name="只埋不收", buried_ch=1, reveal_ch=None, content="无回收章"),
        ],
    )


# ---------- 事件流 ----------
@pytest.mark.p1
def test_event_append_is_idempotent_and_dedupes(store):
    s = EventStream(store)
    rows = [{"ch": 12, "scene": 3, "desc": "林舟取出铜匣", "participants": ["林舟"],
             "cause": "ev_ch09_s02_004", "kind": "object"}]
    first = s.append(rows)
    second = s.append(rows)   # 同 id 重跑：跳过
    assert first["written"] == 1 and second["written"] == 0 and second["skipped"] == 1
    assert s.all()[0]["id"] == make_id(12, 3, 1)


@pytest.mark.p1
def test_isolated_event_requires_inferred_flag(store):
    s = EventStream(store)
    with pytest.raises(EventError):
        s.normalize({"ch": 5, "desc": "孤立事件"})
    ok = s.append([{"ch": 5, "desc": "孤立事件", "inferred": True}], allow_inferred=True)
    assert ok["written"] == 1


@pytest.mark.p1
def test_query_filters_and_consumption_sidecar(store):
    s = EventStream(store)
    s.append([
        {"ch": 3, "desc": "A 发生", "participants": ["陆沉"], "cause": "", "effect": [],
         "inferred": True},
        {"ch": 5, "desc": "B 发生", "participants": ["独眼刘"], "cause": "ev_ch03_s00_001"},
    ], allow_inferred=True)
    assert [r["desc"] for r in s.query(participant="陆沉")] == ["A 发生"]
    assert [r["desc"] for r in s.query(cause="ev_ch03_s00_001")] == ["B 发生"]
    ids = [r["id"] for r in s.all()]
    assert ids[0].startswith("ev_ch03_s00_001") and ids[1].startswith("ev_ch05_s00_002")
    s.mark_consumed([ids[1]], 9)
    rows = s.query_with_consumption(to_ch=5)
    by_id = {r["id"]: r for r in rows}
    assert by_id[ids[1]]["consumed_ch"] == 9
    # 事件行本体未被改动（append-only）
    raw = store.events_path().read_text(encoding="utf-8").strip().splitlines()
    assert "consumed_ch" not in json.loads(raw[-1])


# ---------- 伏笔单一源 ----------
@pytest.mark.p1
def test_resolved_requires_evidence_and_keeps_old_status(store):
    led = ForeshadowLedger(store)
    led.upsert({"name": "铜匣来历", "planted_ch": 3, "due_ch": 15, "level": "main"})
    with pytest.raises(ForeshadowError):
        led.upsert({"name": "铜匣来历", "status": "resolved"})
    assert led.by_name("铜匣来历")["status"] == "buried"
    got = led.resolve(key_for("铜匣来历"),
                      {"ch": 15, "offset": 812, "snippet": "匣底那行字..."})
    assert got["status"] == "resolved" and got["evidence"]["offset"] == 812


@pytest.mark.p1
def test_computed_status_cannot_be_handwritten(store):
    led = ForeshadowLedger(store)
    with pytest.raises(ForeshadowError):
        led.upsert({"name": "铜匣来历", "status": "overdue"})


@pytest.mark.p1
def test_status_for_four_states_and_stalled():
    assert status_for({"due_ch": 15, "planted_ch": 3}, 12, lead=3) == "due"
    assert status_for({"due_ch": 15, "planted_ch": 3}, 15) == "overdue"
    assert status_for({"due_ch": 0, "planted_ch": 3}, 13) == "stalled"
    assert status_for({"due_ch": 15, "status": "resolved"}, 20) == "resolved"


@pytest.mark.p1
def test_key_frozen_and_rename_keeps_alias(store):
    led = ForeshadowLedger(store)
    key = key_for("铜匣来历")
    led.upsert({"name": "铜匣来历", "planted_ch": 3, "due_ch": 15})
    # 换称呼必须显式带原 key（key 由名称哈希冻结，改名不改 key），旧名留作别名
    led.upsert({"key": key, "name": "铜匣的来历", "planted_ch": 3, "due_ch": 15})
    item = led.by_key(key)
    assert item["name"] == "铜匣的来历"
    assert item["aliases"] == ["铜匣来历"]
    assert led.by_name("铜匣来历")["key"] == key


# ---------- 五类规则 ----------
def _chapters():
    return [
        {"no": 1, "numbers": {"累计进度": 5}, "characters": {"疤脸": "已死（死在墟内二层）"},
         "items": {"灰髓袋": "在陆沉手中"}, "time": "第3天，夜间", "facts": [], "body": []},
        {"no": 2, "numbers": {"累计进度": 1}, "characters": {"疤脸": "已死"},
         "items": {"灰髓袋": "被独眼刘抢走"}, "time": "第2天，清晨", "facts": [], "body": []},
    ]


@pytest.mark.p1
def test_mechanism_delta_direction_reversal():
    v = check_mechanism_delta(_chapters())
    assert len(v) == 1 and v[0]["rule"] == "mechanism_delta" and v[0]["ch"] == 2


@pytest.mark.p1
def test_death_state_flags_action_after_death():
    events = [{"ch": 4, "desc": "疤脸领人围住陆沉", "participants": ["疤脸"]}]
    v = check_death_state(_chapters(), events)
    assert len(v) == 1 and v[0]["evidence_ch"] == 1
    # 死亡前/同一章的行动、以及未死亡者的出场都不报
    assert check_death_state(_chapters(), [{"ch": 1, "participants": ["疤脸"]}]) == []
    assert check_death_state(_chapters(), [{"ch": 4, "participants": ["陆沉"]}]) == []


@pytest.mark.p1
def test_item_owner_needs_transfer_event():
    assert len(check_item_owner(_chapters(), [])) == 1
    moved = [{"ch": 2, "kind": "object", "desc": "独眼刘抢走灰髓袋"}]
    assert check_item_owner(_chapters(), moved) == []


@pytest.mark.p1
def test_address_drift_uses_registered_terms():
    terms = {"匣子": {"canonical": "铜匣"}}
    chapters = [{"no": 4, "characters": {}, "facts": ["匣子被人动过"], "body": []}]
    v = check_address_drift(chapters, terms)
    assert len(v) == 1 and "铜匣" in v[0]["expected"]
    assert check_address_drift(chapters, {}) == []


@pytest.mark.p1
def test_time_reverse_needs_explicit_day_markers():
    v = check_time_reverse(_chapters())
    assert len(v) == 1 and v[0]["rule"] == "time_reverse"
    fuzzy = [{"no": 1, "time": "傍晚"}, {"no": 2, "time": "清晨"}]
    assert check_time_reverse(fuzzy) == []


@pytest.mark.p1
def test_detect_sorts_by_severity_and_render_caps_length():
    v = detect(_chapters(), [{"ch": 5, "participants": ["疤脸"], "desc": "疤脸复活"}],
               {"匣子": {"canonical": "铜匣"}})
    assert v and v[0]["severity"] == "high"
    block = render_block(v)
    assert block.startswith("【冲突提醒")
    assert len(block) <= 220
    assert render_block([]) == ""


@pytest.mark.p1
def test_parse_numbers_and_scenes_adapter():
    assert parse_numbers(["可死次数：3", "累计进度：7/10"]) == {"可死次数": 3, "累计进度": 7}
    chapters = chapters_from_scenes({"7": {"scenes": [], "state": {"numbers": ["累计进度：5"]}}})
    assert chapters[0]["no"] == 7 and chapters[0]["numbers"]["累计进度"] == 5


# ---------- 迁移与对账 ----------
@pytest.mark.p1
def test_migrate_is_idempotent(store):
    store.save_world(_world())
    first = migrate(store)
    second = migrate(store)
    assert first["created"] == second["created"] == 0 or first["created"] >= 1
    assert first["total"] == second["total"]
    assert first["sources"] == second["sources"]
    items = ForeshadowLedger(store).items()
    names = {it["name"] for it in items}
    assert {"铜匣来历", "只埋不收"} <= names
    # 只埋不收：无埋设以外信息 → 标 inferred（埋设章存在时不标）
    assert all(it.get("planted_ch") for it in items if it["name"] == "铜匣来历")


@pytest.mark.p1
def test_reconcile_detects_status_and_evidence_diff(store):
    led = ForeshadowLedger(store)
    led.upsert({"name": "铜匣来历", "planted_ch": 3, "due_ch": 15})
    store.save_foreshadow_commitment({"items": [
        {"name": "铜匣来历", "revealed_ch": 15, "due_ch": 15}], "updated_ch": 15})
    rep = reconcile(store, at_ch=14)
    fields = {d["field"] for d in rep["diffs"]}
    assert "status" in fields and "evidence" in fields
    assert rep["zero_diff_streak"] == 0 and not rep["can_disable_legacy_write"]
    assert store.reconcile_report_path().exists()


# ---------- 接线 ----------
@pytest.mark.p1
def test_book_facts_reads_events_and_threads_from_state_layer(store, cfg):
    store.save_world(_world())
    EventStream(store).append([{"ch": 3, "desc": "铜匣出土", "inferred": True}],
                              allow_inferred=True)
    migrate(store)
    facts = BookFacts(store, cfg)
    snap = facts.snapshot(at_ch=3)
    assert snap["events"][-1]["desc"] == "铜匣出土"
    assert any(t["name"] == "铜匣来历" for t in snap["open_threads"])


@pytest.mark.p1
def test_conflict_block_empty_when_no_violation(store, cfg):
    facts = BookFacts(store, cfg)
    assert facts.violation_list() == []
    assert facts.render_violation_block() == ""


@pytest.mark.p1
def test_flags_registered_in_registry_and_model():
    for name in ("event_stream", "foreshadow_unified", "conflict_guard", "timeline_view"):
        assert any(f.key == name for f in FEATURE_FLAGS)
        assert feature_default(name) is False
        assert getattr(NovelConfig(title="t"), name) is False


@pytest.mark.p1
def test_book_facts_flag_off_keeps_m0_behavior(store):
    """四个 M1 开关全关时，快照与 M0 结构一致（事件仍来自 book_memory）。"""
    cfg = NovelConfig(title="t")
    cfg.book_facts = True
    store.save_book_memory({"events": {"consumed": [{"ch": 2, "desc": "旧事件"}]}})
    snap = BookFacts(store, cfg).snapshot(at_ch=2)
    assert [e["desc"] for e in snap["events"]] == ["旧事件"]
    assert snap["open_threads"] == []
