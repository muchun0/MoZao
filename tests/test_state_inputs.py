# -*- coding: utf-8 -*-
"""REQ-R21 输入补齐回归：归属主体归一 / 事件参与人与物品事件 / 称谓登记接线 /
机制数字字段名归一。四个缺口各自一组用例，均基于真实样本暴露的形态。"""
import pytest

from novel_gen.pipeline.chapter_state import (canonical_field_name,
                                              canonicalize_number_list,
                                              canonicalize_numbers,
                                              mechanism_field_registry)
from novel_gen.pipeline.state.conflict_rules import (check_address_drift,
                                                     check_item_owner,
                                                     check_mechanism_delta,
                                                     chapters_from_scenes,
                                                     detect, parse_numbers)
from novel_gen.pipeline.state.events import classify_fact, derive_fact_events

REG = {"可死次数": "decrease", "累计进度": "increase"}


# ---------- ① 归属字符串按持有者主体归一 ----------
@pytest.mark.parametrize("owner,expect", [
    ("在陆沉怀中", "陆沉"),
    ("在陆沉怀中/领口", "陆沉"),
    ("苏妄言扔给陆沉，陆沉未开封，收进抽屉", "陆沉"),
    ("小九吐出，落在陆沉手心", "陆沉"),
    ("苏妄言持有，已收进袖里", "苏妄言"),
    ("砧板上", ""),
    ("", ""),
])
def test_owner_subject_normalizes_to_holder(owner, expect):
    from novel_gen.pipeline.state.conflict_rules import owner_subject
    assert owner_subject(owner, ["陆沉", "苏妄言", "小九"]) == expect


def test_item_owner_ignores_descriptive_drift():
    """真实样本误报形态：「怀中」→「怀中/领口」不得再报。"""
    chapters = [
        {"no": 7, "characters": {"陆沉": "清醒", "小九": "猫"},
         "items": {"小九": "在陆沉怀中"}},
        {"no": 16, "characters": {"陆沉": "清醒", "小九": "猫"},
         "items": {"小九": "在陆沉怀中/领口"}},
    ]
    assert check_item_owner(chapters, []) == []


def test_item_owner_still_flags_real_transfer_without_event():
    chapters = [
        {"no": 3, "characters": {"苏妄言": "醒", "陆沉": "醒"},
         "items": {"小瓷瓶": "苏妄言持有，已收进袖里"}},
        {"no": 9, "characters": {"苏妄言": "醒", "陆沉": "醒"},
         "items": {"小瓷瓶": "陆沉持有，收进抽屉"}},
    ]
    rows = check_item_owner(chapters, [])
    assert len(rows) == 1
    assert rows[0]["rule"] == "item_owner" and rows[0]["ch"] == 9


def test_item_owner_exempt_when_object_event_exists():
    chapters = [
        {"no": 3, "characters": {}, "items": {"小瓷瓶": "苏妄言持有"}},
        {"no": 9, "characters": {}, "items": {"小瓷瓶": "陆沉持有"}},
    ]
    events = [{"ch": 6, "kind": "object", "desc": "苏妄言把小瓷瓶扔给陆沉"}]
    assert check_item_owner(chapters, events) == []


# ---------- ② 事件参与人 + 物品事件 ----------
def test_classify_fact_participants_and_object_kind():
    names = ["陆沉", "苏妄言", "小九"]
    items = ["小瓷瓶", "空白标记符"]
    parts, kind = classify_fact("苏妄言把小瓷瓶扔给陆沉，陆沉收进抽屉", names, items)
    assert parts == ["苏妄言", "陆沉"]
    assert kind == "object"


def test_classify_fact_falls_back_to_state():
    parts, kind = classify_fact("陆沉被灰卒咬穿后颈未死", ["陆沉"], ["小瓷瓶"])
    assert parts == ["陆沉"]
    assert kind == "state"


def test_derive_fact_events_shapes_contract():
    rows = derive_fact_events(
        ["苏妄言把小瓷瓶扔给陆沉", "陆沉去墟里找碎晶"],
        ch=5, names=["陆沉", "苏妄言"], items=["小瓷瓶"])
    assert len(rows) == 2
    assert rows[0]["participants"] and rows[0]["kind"] == "object"
    assert rows[0]["ch"] == 5 and rows[0]["inferred"] is True
    assert rows[1]["kind"] == "state"


# ---------- ③ 称谓登记接线（ensure_term 有调用点） ----------
def test_sync_terms_registers_aliases_into_canon(tmp_path):
    from novel_gen.store.project import ProjectStore
    from novel_gen.pipeline.book_memory import BookMemory
    from novel_gen.store.schemas import TerminologyItem, World
    from novel_gen.config import NovelConfig

    store = ProjectStore.create(tmp_path, NovelConfig(title="t"))
    mem = BookMemory(store, NovelConfig(title="t"))
    world = World(terminology=[TerminologyItem(
        name="苏妄言", aliases=["苏先生", "苏执刑"])])
    added = mem.sync_terms(world)
    assert added == 2
    terms = (mem.load().get("canon") or {}).get("terms") or {}
    assert terms["苏先生"]["canonical"] == "苏妄言"
    assert terms["苏执刑"]["canonical"] == "苏妄言"
    # 标准写法自身不登记（不构成漂移）
    assert "苏妄言" not in terms


def test_sync_terms_skips_alias_containing_canonical(tmp_path):
    """「蚀气/蚀群」含标准写法「蚀」，是另一个名词，登记进来只会空转。"""
    from novel_gen.store.project import ProjectStore
    from novel_gen.pipeline.book_memory import BookMemory
    from novel_gen.store.schemas import TerminologyItem, World
    from novel_gen.config import NovelConfig

    store = ProjectStore.create(tmp_path, NovelConfig(title="t"))
    mem = BookMemory(store, NovelConfig(title="t"))
    world = World(terminology=[TerminologyItem(
        name="蚀", aliases=["蚀气", "蚀群"])])
    assert mem.sync_terms(world) == 0
    assert (mem.load().get("canon") or {}).get("terms") in ({}, None)


def test_address_drift_fires_on_registered_alias():
    canon = {"苏先生": {"canonical": "苏妄言", "category": "terminology"}}
    chapters = [{"no": 4, "facts": ["苏先生盘问了陆沉三件事"], "characters": {}}]
    rows = check_address_drift(chapters, canon)
    assert len(rows) == 1 and rows[0]["rule"] == "address_drift"


# ---------- ④ 机制数字字段名归一 ----------
@pytest.mark.parametrize("label,expect", [
    ("可死次数", "可死次数"),
    ("剩余次数", "可死次数"),
    ("今日可死次数", "可死次数"),
    ("累计进度", "累计进度"),
    ("累积进度", "累计进度"),
    ("命中率", "命中率"),          # 注册表没有 → 保留原写法，不臆造
])
def test_canonical_field_name(label, expect):
    assert canonical_field_name(label, REG) == expect


def test_canonicalize_numbers_list_and_dict():
    assert canonicalize_numbers(["剩余次数：2", "累积进度：7/10"], REG) == {
        "可死次数": 2, "累计进度": 7}
    assert canonicalize_numbers({"今日可死次数": "3"}, REG) == {"可死次数": 3}
    assert canonicalize_number_list(["剩余次数：2"], REG) == ["可死次数：2"]


def test_mechanism_delta_compares_after_normalization():
    """归一前：相邻章写法不同 → 同键为空、规则空转；归一后：同键可比。"""
    chs = [
        {"no": 1, "numbers": parse_numbers(["可死次数：3"], REG)},
        {"no": 2, "numbers": parse_numbers(["剩余次数：5"], REG)},
    ]
    rows = check_mechanism_delta(chs)
    assert len(rows) == 1 and rows[0]["rule"] == "mechanism_delta"

    raw = [
        {"no": 1, "numbers": parse_numbers(["可死次数：3"])},
        {"no": 2, "numbers": parse_numbers(["剩余次数：5"])},
    ]
    assert check_mechanism_delta(raw) == []


def test_chapters_from_scenes_applies_registry():
    scenes = {"3": {"state": {"numbers": ["剩余次数：2"], "characters": {}, "items": {}},
                    "scenes": []}}
    plain = chapters_from_scenes(scenes)
    canoned = chapters_from_scenes(scenes, REG)
    assert plain[0]["numbers"] == {"剩余次数": 2}
    assert canoned[0]["numbers"] == {"可死次数": 2}


def test_registry_from_baseline_derives_direction():
    """真实基线用 N 占位（「今日剩余：N；蚀痕：N寸」），注册表必须认出来。"""
    reg = mechanism_field_registry({
        "panel_format": "今日剩余：N；蚀痕：N寸",
        "reset_rule": "每日子时重置为3，蚀痕不重置",
    })
    assert reg.get("剩余") == "decrease"
    assert reg.get("蚀痕") == "increase"


def test_detect_no_longer_crashes_on_list_numbers():
    """旧实现 `nums.items()` 对 list 抛 AttributeError；归一后按注册表解析。"""
    chs = [{"no": 1, "numbers": ["剩余次数：2"]}, {"no": 2, "numbers": ["剩余次数：4"]}]
    rows = detect(chs, [], {})
    assert rows == []


# ---------- 真实样本回归（2026-11-12）：白名单误报 + 注册表导入路径 ----------
def test_unknown_mechanism_fields_accepts_registered_label():
    """「剩余次数」在注册表内，去前缀后变「次数」不得误报为未声明字段。"""
    from novel_gen.pipeline.chapter_state import unknown_mechanism_fields
    reg = {"剩余次数": "decrease", "寸": "increase"}
    assert unknown_mechanism_fields("今日剩余次数：3；蚀痕：7寸", reg) == []
    assert unknown_mechanism_fields("剩余次数：3", reg) == []


def test_unknown_mechanism_fields_still_flags_undeclared():
    from novel_gen.pipeline.chapter_state import unknown_mechanism_fields
    reg = {"剩余次数": "decrease"}
    rows = unknown_mechanism_fields("累计死亡次数：39/37", reg)
    assert rows and "未声明机制字段" in rows[0]


def test_violation_list_reads_registry_without_import_failure(tmp_path, caplog):
    """book_facts 的注册表导入曾写成 `..chapter_state`（=novel_gen.chapter_state），
    导致每章静默降级；真实样本第 1 章即暴露。"""
    from novel_gen.config import NovelConfig
    from novel_gen.pipeline.book_facts import BookFacts
    from novel_gen.pipeline.book_memory import BookMemory
    from novel_gen.store.project import ProjectStore

    cfg = NovelConfig(title="t")
    cfg.conflict_guard = True
    store = ProjectStore(tmp_path / "b")
    store.dir.mkdir(parents=True, exist_ok=True)
    store.save_config(cfg)
    mem = BookMemory(store, cfg)
    data = mem.load()
    data["canon"]["mechanism_baseline"] = {"panel_format": "今日剩余：N；蚀痕：N寸"}
    mem.save()
    with caplog.at_level("ERROR", logger="novel_gen.pipeline.book_facts"):
        BookFacts(store, cfg).violation_list()
    assert "机制基线读取失败" not in caplog.text


# ---------- 真实样本回归（2026-11-12）：状态层必须真的写 events.jsonl ----------
def test_update_state_layer_writes_events(tmp_path):
    """章后入账曾漏掉 EventStream 实例化（NameError 被吞），真实生成 14 章 0 事件。"""
    from novel_gen.config import NovelConfig
    from novel_gen.pipeline.manager import NovelPipeline
    from novel_gen.pipeline.state.events import EventStream
    from novel_gen.store.project import ProjectStore

    cfg = NovelConfig(title="t")
    cfg.event_stream = True
    cfg.foreshadow_unified = False
    store = ProjectStore(tmp_path / "b")
    store.dir.mkdir(parents=True, exist_ok=True)
    store.save_config(cfg)
    store.save_chapter_state({
        "chapter": 2,
        "facts": ["陆沉把锁钥碎片交给苏妄言", "苏妄言答应拖三天"],
        "characters": {"陆沉": "存活", "苏妄言": "存活"},
        "items": {"锁钥碎片": "苏妄言收进袖里"},
        "numbers": [],
    })

    class _Stub:
        pass

    stub = _Stub()
    stub.cfg = cfg
    stub.store = store
    assert NovelPipeline._update_state_layer(stub, 2) is True
    rows = EventStream(store).all()
    assert rows, "状态层没有写入任何事件"
    assert any(r.get("participants") for r in rows)
    assert any(r.get("kind") == "object" for r in rows)
