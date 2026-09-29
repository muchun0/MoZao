# -*- coding: utf-8 -*-
"""P0-2 人物动态状态快照测试（P1）。

覆盖：确定性出场记录、LLM 状态细节合并、LLM 失败降级、L1.5 记忆注入、
配置字段持久化。
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
from novel_gen.store.schemas import Chapter, Character, World
from novel_gen.pipeline.character_state import extract_character_states, format_state_line
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    s = ProjectStore.create(tmp_path / "cs", cfg)
    return s


def _world():
    return World(
        world_view="玄幻大陆，灵气复苏",
        characters=[
            Character(name="林小满", role="女主", desc="出身落霞城，性格坚韧"),
            Character(name="江澈", role="男主", desc="剑修，沉默寡言"),
            Character(name="路人甲", role="配角", desc="茶摊老板"),
        ],
    )


@pytest.mark.p1
def test_deterministic_seen_record(store):
    """正文出现的人物记录 last_seen，未出现的人物不记录。"""
    w = _world()
    ch = Chapter(no=3, title="第三章", content="林小满推开旧书店的门，江澈在门口等着。" * 20, summary="s")
    states = extract_character_states(MagicMock(), store.load_config(), w, ch, 3, {})
    assert states["林小满"]["last_seen"] == 3
    assert states["江澈"]["last_seen"] == 3
    assert "路人甲" not in states


@pytest.mark.p1
def test_llm_details_merged(store):
    """LLM 返回状态细节时按人物名合并覆盖，保留 last_seen（freq=1 每章抽取=原行为）。"""
    cfg = store.load_config()
    cfg.state_extract_frequency = 1
    w = _world()
    ch = Chapter(no=2, title="第二章", content="林小满负伤。" * 50, summary="s")
    llm = MagicMock()
    llm.chat_json.return_value = {
        "updated": [
            {"name": "林小满", "location": "落霞城医馆", "body": "左臂受伤",
             "emotion": "警惕", "relations": ["与江澈结盟"], "new_info": ["得知仇人下落"],
             "goal": "疗伤并追踪线索"},
        ]
    }
    states = extract_character_states(llm, cfg, w, ch, 2, {"林小满": {"last_seen": 1}})
    assert states["林小满"]["last_seen"] == 2
    assert states["林小满"]["location"] == "落霞城医馆"
    assert states["林小满"]["body"] == "左臂受伤"
    assert "与江澈结盟" in states["林小满"]["relations"]


@pytest.mark.p1
def test_llm_extract_frequency(store):
    """REQ-token优化 P0-4：state_extract_frequency=3 时，no%3==1 的章才调 LLM，
    其余章仅确定性出场记录（零 LLM）。"""
    cfg = store.load_config()
    cfg.state_extract_frequency = 3
    w = _world()
    llm = MagicMock()
    llm.chat_json.return_value = {"updated": []}
    # no=2：不抽 LLM（2%3==2）
    ch2 = Chapter(no=2, title="第二章", content="林小满负伤。" * 50, summary="s")
    states2 = extract_character_states(llm, cfg, w, ch2, 2, {})
    assert states2["林小满"]["last_seen"] == 2
    llm.chat_json.assert_not_called()
    # no=4：抽 LLM（4%3==1）
    llm.reset_mock()
    ch4 = Chapter(no=4, title="第四章", content="林小满再战。" * 50, summary="s")
    extract_character_states(llm, cfg, w, ch4, 4, {"林小满": {"last_seen": 3}})
    llm.chat_json.assert_called_once()


@pytest.mark.p1
def test_llm_failure_degrades(store):
    """LLM 抛异常时静默降级为仅出场记录，不抛给调用方。"""
    w = _world()
    ch = Chapter(no=1, title="第一章", content="江澈出现。" * 50, summary="s")
    llm = MagicMock()
    llm.chat_json.side_effect = RuntimeError("provider down")
    states = extract_character_states(llm, store.load_config(), w, ch, 1, {})
    assert states["江澈"]["last_seen"] == 1  # 确定性保底仍在
    assert "location" not in states["江澈"]


@pytest.mark.p1
def test_llm_off_skips_llm_call(store):
    """character_state_llm=False 时仅确定性记录，不调用 LLM。"""
    cfg = store.load_config()
    cfg.character_state_llm = False
    w = _world()
    ch = Chapter(no=1, title="第一章", content="林小满出现。" * 50, summary="s")
    llm = MagicMock()
    states = extract_character_states(llm, cfg, w, ch, 1, {})
    assert states["林小满"]["last_seen"] == 1
    llm.chat_json.assert_not_called()


@pytest.mark.p1
def test_l15_injected_into_memory(store):
    """状态快照写入后，build_context 注入 L1.5 层。"""
    w = _world()
    store.save_world(w)
    ch = Chapter(no=1, title="第一章", content="林小满动身前往落霞城。" * 30, summary="s1")
    store.save_chapter(ch)
    llm = MagicMock()
    llm.chat_json.return_value = {
        "updated": [{"name": "林小满", "location": "落霞城", "body": "无明显变化",
                     "emotion": "平静", "goal": "寻找江澈"}],
    }
    states = extract_character_states(llm, store.load_config(), w, ch, 1, {})
    store.save_character_states(states)

    ctx = Memory(store, 5).build_context(w, up_to_chapter=2)
    assert "L1.5·人物当前状态" in ctx
    assert "林小满" in ctx
    assert "落霞城" in ctx


@pytest.mark.p1
def test_format_state_line(store):
    st = {"last_seen": 3, "location": "山谷", "body": "轻伤", "emotion": "焦虑",
          "relations": ["与江澈决裂"], "new_info": ["发现密道"], "goal": "脱困"}
    line = format_state_line("林小满", st)
    assert "林小满" in line and "第3章" in line and "位置：山谷" in line
    assert "与江澈决裂" in line and "发现密道" in line


@pytest.mark.p1
def test_config_persist(store):
    cfg = store.load_config()
    assert cfg.character_state_tracking is True
    assert cfg.character_state_llm is True
    cfg.character_state_tracking = False
    cfg.character_state_llm = False
    store.save_config(cfg)
    cfg2 = store.load_config()
    assert cfg2.character_state_tracking is False
    assert cfg2.character_state_llm is False
