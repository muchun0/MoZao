# -*- coding: utf-8 -*-
"""P1-1 单章人物行动表先行测试（P1）。

覆盖：行动表 JSON 解析成功注入、解析失败回退原流程、开关关闭不调用。
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
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "ap", cfg)


def _writer(cfg, llm, store):
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


@pytest.mark.p1
def test_action_plan_parsed(store):
    """LLM 返回合法 JSON → 行动表格式化为可注入文本（含人物/目标/行动/对话意图）。"""
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = (
        '{"characters": [{"name": "林小满", "goal": "查清玉佩来历", '
        '"key_actions": ["去藏经阁翻旧档", "试探江澈"], "emotion": "警惕", '
        '"dialogue_intent": "借话头套江澈关于玉佩的话"}]}'
    )
    plan = _writer(cfg, llm, store)._gen_action_plan(2, "第二章", "林小满追查玉佩", "ctx")
    assert "林小满" in plan
    assert "查清玉佩来历" in plan
    assert "去藏经阁翻旧档" in plan
    assert "借话头套江澈" in plan
    llm.chat.assert_called_once()


@pytest.mark.p1
def test_action_plan_bad_json_falls_back(store):
    """返回非 JSON → 返回空串（自动回退原流程，不中断）。"""
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = "这不是 JSON"
    plan = _writer(cfg, llm, store)._gen_action_plan(1, "第一章", "目标", "ctx")
    assert plan == ""


@pytest.mark.p1
def test_action_plan_llm_error_falls_back(store):
    """LLM 抛异常 → 返回空串（降级不阻塞）。"""
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("provider down")
    plan = _writer(cfg, llm, store)._gen_action_plan(1, "第一章", "目标", "ctx")
    assert plan == ""


@pytest.mark.p1
def test_write_injects_action_plan(store):
    """write() 集成：开启时行动表注入生成（普通模式整章走 user prompt），落盘正常。"""
    cfg = store.load_config()
    cfg.action_plan_first = True
    cfg.scene_split = False            # 整章生成，便于断言
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    # 调用顺序：1) 行动表 2) 整章正文
    llm.chat.side_effect = [
        '{"characters": [{"name": "林小满", "goal": "抵达落霞城", "key_actions": ["连夜赶路"], "emotion": "疲惫", "dialogue_intent": "询问路人"}]}',
        "林小满连夜赶路，风尘仆仆。" * 300 + "【摘要】林小满抵达落霞城",
    ]
    ch = _writer(cfg, llm, store).write(1, "第一章", "林小满启程", None, silent=True)
    assert "林小满抵达落霞城" in ch.summary
    # 行动表内容确实被请求（先于正文的调用是行动表）
    first_call_user = llm.chat.call_args_list[0][0][1]
    assert "本章目标" in first_call_user and "行动表" in first_call_user


@pytest.mark.p1
def test_switch_off_no_action_plan_call(store):
    """action_plan_first=False → 不发起行动表调用，直接写正文。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.scene_split = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.return_value = "整章正文内容。" * 400 + "【摘要】章节摘要"
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 1     # 只有正文一次调用（字数达标，无扩写）
    assert "整章正文内容" in ch.content
