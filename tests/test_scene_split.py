# -*- coding: utf-8 -*-
"""P1-2 场景级生成下沉普通模式测试（P1）。

覆盖：普通模式 ≥2000 字章节走场景链路、≤1500 字章节仍整章生成、
scene_split 关闭回退整章、拆场景失败自动回退整章。
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
    return ProjectStore.create(tmp_path / "sc", cfg)


def _writer(cfg, llm, store):
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


_SCENES_JSON = (
    '{"scenes": [{"location": "落霞城", "event": "林小满抵达寻医", "emotion": "疲惫", '
    '"anchors": ["玉佩"], "target_words": 1200}]}'
)


@pytest.mark.p1
def test_normal_mode_long_chapter_uses_scenes(store):
    """普通模式 2500 字章节：走拆场景→场景→润色链路。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    # 1) 拆场景 2) 场景正文 3) 润色 4) 摘要
    llm.chat.side_effect = [
        _SCENES_JSON,
        "场景正文：林小满推开医馆的门。" * 190,          # ~2280 字
        "润色后完整正文：" + "场景正文：林小满推开医馆的门。" * 195,  # 润色更长生效
        "场景链路摘要",
    ]
    ch = _writer(cfg, llm, store).write(1, "第一章", "林小满抵达落霞城", None, silent=True)
    assert llm.chat.call_count == 4
    assert "润色后完整正文" in ch.content
    assert ch.summary == "场景链路摘要"


@pytest.mark.p1
def test_short_chapter_stays_whole(store):
    """普通模式 1500 字章节：仍整章生成（不拆场景）。"""
    cfg = NovelConfig(title="短章书", chapter_words=1500, total_words=6000)
    s = ProjectStore.create(store.dir / "short", cfg)
    cfg = s.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    s.save_config(cfg)
    llm = MagicMock()
    llm.chat.side_effect = ["整章正文。" * 400 + "【摘要】短章摘要"]
    ch = _writer(cfg, llm, s).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 1
    assert "整章正文" in ch.content


@pytest.mark.p1
def test_scene_split_off_whole(store):
    """scene_split=False → 2500 字章节也整章生成。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.scene_split = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.side_effect = ["整章正文。" * 500 + "【摘要】整章摘要"]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 1
    assert "整章正文" in ch.content


@pytest.mark.p1
def test_split_fail_fallback(store):
    """拆场景返回非法 JSON → 自动回退整章生成（不中断）。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.side_effect = ["不是JSON", "整章回退正文。" * 400 + "【摘要】回退摘要"]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 2
    assert "整章回退正文" in ch.content


@pytest.mark.p1
def test_hq_keeps_dynamic_module(store):
    """高质量模式仍带动态模块（_generate_chapter_scenes dynamic=True 时不回归）。"""
    cfg = store.load_config()
    cfg.high_quality_mode = True
    cfg.action_plan_first = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.side_effect = [
        _SCENES_JSON,
        "HQ场景正文。" * 400,
        "HQ润色正文。" * 450,
        "HQ摘要",
    ]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert "HQ润色正文" in ch.content
    # 高质量模式写章后仍走自动改稿/读者审校（reviser 调用 mock 为空时跳过不抛）
