# -*- coding: utf-8 -*-
"""章内/章间状态账本测试（REQ-章内自洽与机制账本，P1）。

覆盖：章末/场景状态 LLM 抽取、确定性面板数字兜底、失败降级、
状态合并与格式化、L1.6 记忆注入、配角动机卡注入、配置字段持久化。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, Character, CharacterMotive, World
from novel_gen.pipeline.chapter_state import (
    _deterministic_numbers,
    extract_chapter_state,
    extract_scene_state,
    format_chapter_state,
    format_scene_state,
    merge_scene_state,
)
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    s = ProjectStore.create(tmp_path / "cs", cfg)
    return s


def _world():
    return World(
        world_view="墟界设定",
        characters=[
            Character(name="陆沉", role="主角", desc="十六岁，沉默寡言"),
            Character(name="疤脸", role="配角", desc="下过十七次墟的老手"),
            Character(name="独眼刘", role="配角", desc="墟口看门人"),
        ],
        character_motives=[
            CharacterMotive(name="独眼刘", goal="收回垫付的押金",
                            behavior_logic="押金收不回就毁掉欠债人的还款来源",
                            link_to_protagonist="陆沉欠他一人头押金"),
        ],
    )


@pytest.mark.p1
def test_deterministic_numbers_panel():
    """确定性兜底能抓全面板数字：今日可死次数、蚀痕（无前缀后缀）。"""
    t = "今日可死次数：3。他往墟口走。蚀痕：1/10 已经爬到小臂。"
    nums = _deterministic_numbers(t)
    assert "今日可死次数：3" in nums
    assert "蚀痕：1/10" in nums


@pytest.mark.p1
def test_deterministic_numbers_no_panel():
    assert _deterministic_numbers("他往墟口走，风很大。") == []


@pytest.mark.p1
def test_extract_scene_state_llm(store):
    """LLM 抽取场景状态 → 结构化 dict。"""
    llm = MagicMock()
    llm.chat_json.return_value = {
        "numbers": ["可死次数：2"],
        "characters": {"疤脸": "已死"},
        "items": {"灰髓袋": "还在陆沉腰侧"},
        "time": "同日午后，墟内一层",
    }
    st = extract_scene_state(llm, "正文：可死次数：2。疤脸死了。")
    assert st["numbers"] == ["可死次数：2"]
    assert st["characters"]["疤脸"] == "已死"
    assert st["time"] == "同日午后，墟内一层"


@pytest.mark.p1
def test_extract_scene_state_failure_degrades():
    """LLM 抛异常 → 降级为确定性面板数字。"""
    llm = MagicMock()
    llm.chat_json.side_effect = RuntimeError("provider down")
    st = extract_scene_state(llm, "正文：今日可死次数：2。他坐着。")
    assert st == {"numbers": ["今日可死次数：2"]}


@pytest.mark.p1
def test_extract_scene_state_failure_no_numbers():
    """LLM 失败且无面板数字 → None（调用方跳过，不阻塞）。"""
    llm = MagicMock()
    llm.chat_json.side_effect = RuntimeError("provider down")
    assert extract_scene_state(llm, "他往墟口走，风很大。") is None


@pytest.mark.p1
def test_merge_scene_state_later_wins():
    acc = {"numbers": ["可死次数：2"], "characters": {"疤脸": "已死"}, "time": "同日午后"}
    upd = {"numbers": ["可死次数：1"], "items": {"灰髓袋": "被独眼刘抢走"}, "time": "入夜"}
    out = merge_scene_state(acc, upd)
    assert out["numbers"] == ["可死次数：2", "可死次数：1"]  # 累积不丢旧账
    assert out["characters"]["疤脸"] == "已死"
    assert out["items"]["灰髓袋"] == "被独眼刘抢走"
    assert out["time"] == "入夜"  # 后写覆盖


@pytest.mark.p1
def test_format_scene_state_hint():
    acc = {"numbers": ["可死次数：1"], "characters": {"疤脸": "已死"},
           "items": {"灰髓袋": "被独眼刘抢走"}, "time": "第9天夜间"}
    hint = format_scene_state(acc)
    assert "本章已推进状态" in hint
    assert "可死次数：1" in hint
    assert "疤脸（已死）" in hint
    assert "灰髓袋（被独眼刘抢走）" in hint


@pytest.mark.p1
def test_extract_chapter_state_llm(store):
    """章末状态 LLM 抽取（含 chapter 号）。"""
    w = _world()
    store.save_world(w)
    ch = Chapter(no=1, title="第一章", content="疤脸死了。今日可死次数：1。" * 20, summary="s")
    llm = MagicMock()
    llm.chat_json.return_value = {
        "numbers": ["可死次数：1"],
        "characters": {"疤脸": "已死（死在墟内二层）"},
        "items": {"灰髓袋": "被独眼刘抢走"},
        "time": "第9天夜间，墟外坡底",
        "facts": ["人头押金由独眼刘垫付，陆沉欠独眼刘"],
    }
    st = extract_chapter_state(llm, store.load_config(), w, ch, 1)
    assert st["chapter"] == 1
    assert st["numbers"] == ["可死次数：1"]
    assert st["characters"]["疤脸"].startswith("已死")
    assert any("人头押金由独眼刘垫付" in f for f in st["facts"])


@pytest.mark.p1
def test_extract_chapter_state_llm_off(store):
    """chapter_state_llm=False → 仅确定性面板数字，不调用 LLM。"""
    w = _world()
    ch = Chapter(no=1, title="第一章", content="今日可死次数：2。蚀痕：3/10。" * 20, summary="s")
    cfg = store.load_config()
    cfg.chapter_state_llm = False
    llm = MagicMock()
    st = extract_chapter_state(llm, cfg, w, ch, 1)
    assert st["chapter"] == 1
    assert st["numbers"] == ["今日可死次数：2", "蚀痕：3/10"]
    llm.chat_json.assert_not_called()


@pytest.mark.p1
def test_extract_chapter_state_failure_degrades(store):
    """LLM 抛异常 → 降级为确定性面板数字，不抛给调用方。"""
    w = _world()
    ch = Chapter(no=1, title="第一章", content="可死次数：1。" * 30, summary="s")
    llm = MagicMock()
    llm.chat_json.side_effect = RuntimeError("provider down")
    st = extract_chapter_state(llm, store.load_config(), w, ch, 1)
    assert st["chapter"] == 1
    assert st["numbers"] == ["可死次数：1"]


@pytest.mark.p1
def test_format_chapter_state_hint():
    st = {"chapter": 1, "numbers": ["可死次数：1", "蚀痕：3/10"],
          "characters": {"疤脸": "已死"}, "items": {"灰髓袋": "被独眼刘抢走"},
          "time": "第9天夜间", "facts": ["人头押金由独眼刘垫付"]}
    hint = format_chapter_state(st)
    assert "上一章结束时的世界状态" in hint
    assert "可死次数：1" in hint and "蚀痕：3/10" in hint
    assert "疤脸（已死）" in hint
    assert "人头押金由独眼刘垫付" in hint


@pytest.mark.p1
def test_l16_injected_into_memory(store):
    """上一章账本写入后，生成下一章时注入 L1.6 世界状态承接。"""
    w = _world()
    store.save_world(w)
    ch = Chapter(no=1, title="第一章", content="疤脸死了。可死次数：1。" * 30, summary="s")
    store.save_chapter(ch)
    store.save_chapter_state({
        "chapter": 1, "numbers": ["可死次数：1"],
        "characters": {"疤脸": "已死"}, "time": "第9天夜间",
    })
    ctx = Memory(store, 5).build_context(w, up_to_chapter=2)
    assert "L1.6·世界状态·承接上一章" in ctx
    assert "可死次数：1" in ctx
    assert "疤脸（已死）" in ctx


@pytest.mark.p1
def test_l16_not_injected_for_current_chapter(store):
    """账本章号不小于当前章时不注入（避免把本章自己的账本喂给本章）。"""
    w = _world()
    store.save_world(w)
    store.save_chapter_state({"chapter": 2, "numbers": ["可死次数：0"]})
    ctx = Memory(store, 5).build_context(w, up_to_chapter=2)
    assert "L1.6·世界状态" not in ctx


@pytest.mark.p1
def test_character_motives_injected(store):
    """配角动机卡注入正文提示词：独眼刘的 goal/行为逻辑必须可见。"""
    w = _world()
    store.save_world(w)
    store.save_chapter(Chapter(no=1, title="第一章", content="独眼刘站在墟口。" * 20, summary="s"))
    ctx = Memory(store, 5).build_context(w, up_to_chapter=1)
    assert "人物动机卡" in ctx
    assert "独眼刘" in ctx
    assert "收回垫付的押金" in ctx
    assert "行为逻辑" in ctx


@pytest.mark.p1
def test_config_persist(store):
    cfg = store.load_config()
    assert cfg.chapter_state_tracking is True
    assert cfg.chapter_state_llm is True
    cfg.chapter_state_tracking = False
    cfg.chapter_state_llm = False
    store.save_config(cfg)
    cfg2 = store.load_config()
    assert cfg2.chapter_state_tracking is False
    assert cfg2.chapter_state_llm is False
