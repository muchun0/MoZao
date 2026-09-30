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
    """普通模式 2500 字章节：走拆场景→场景→摘要链路；
    润色默认仅高质量模式（REQ-token优化 P0-3：普通模式不润色）。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    # 1) 拆场景 2) 场景正文 3) 摘要（普通模式不再调用润色）
    llm.chat.side_effect = [
        _SCENES_JSON,
        "场景正文：林小满推开医馆的门。" * 190,          # ~2280 字
        "场景链路摘要",
    ]
    ch = _writer(cfg, llm, store).write(1, "第一章", "林小满抵达落霞城", None, silent=True)
    assert llm.chat.call_count == 3
    assert "场景正文：林小满推开医馆的门" in ch.content
    assert ch.summary == "场景链路摘要"


@pytest.mark.p1
def test_normal_mode_polish_only_hq(store):
    """polish_hq_only=True：普通模式无润色调用；False 时恢复润色。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.side_effect = [
        _SCENES_JSON,
        "场景正文：林小满推开医馆的门。" * 190,
        "场景链路摘要",
    ]
    _writer(cfg, llm, store).write(1, "第一章", "林小满抵达落霞城", None, silent=True)
    assert llm.chat.call_count == 3  # 无润色

    cfg.polish_hq_only = False
    store.save_config(cfg)
    llm2 = MagicMock()
    llm2.chat.side_effect = [
        _SCENES_JSON,
        "场景正文：林小满推开医馆的门。" * 190,
        "润色后完整正文：" + "场景正文：林小满推开医馆的门。" * 195,
        "场景链路摘要",
    ]
    ch2 = _writer(cfg, llm2, store).write(1, "第一章", "林小满抵达落霞城", None, silent=True)
    assert llm2.chat.call_count == 4
    assert "润色后完整正文" in ch2.content


@pytest.mark.p1
def test_action_plan_from_scene_split(store):
    """REQ-token优化 P0-2：拆场景输出 characters_action → 场景 prompt 收到行动表，
    不再发起独立行动表调用。"""
    cfg = store.load_config()
    cfg.action_plan_first = False          # 默认：不独立调用
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    scenes_json = (
        '{"scenes": [{"location": "落霞城", "event": "林小满抵达", "emotion": "疲惫", '
        '"target_words": 1200}], '
        '"characters_action": [{"name": "林小满", "goal": "查清玉佩来历", '
        '"key_actions": ["去藏经阁"], "emotion": "警惕", "dialogue_intent": "套话"}]}'
    )
    llm.chat.side_effect = [
        scenes_json,
        "场景正文：林小满抵达。" * 280,     # ~2800 字，达标不触发扩写
        "场景摘要",
    ]
    _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 3        # 拆场景(含行动表) + 场景 + 摘要：无独立行动表调用
    # 场景 prompt 应包含行动表文本（确定性格式化注入）
    scene_call = llm.chat.call_args_list[1][0][1]
    assert "查清玉佩来历" in scene_call
    assert "去藏经阁" in scene_call


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


# ---------- 拆场景事件边界互斥（P1-1） ----------

_OVERLAP_SCENES_JSON = (
    '{"scenes": [{"location": "墟口", "event": "陆沉冲回墟口救苏妄言，被三只赤尉围住，'
    '苏妄言被学员架走", "emotion": "焦灼", "anchors": ["短刀"], "target_words": 1200},'
    '{"location": "墟口", "event": "陆沉再次冲上前去救苏妄言，三只赤尉围阵，'
    '苏妄言被架走撤离", "emotion": "绝望", "anchors": ["短刀"], "target_words": 1200}],'
    '"characters_action": []}'
)


@pytest.mark.p1
def test_scene_events_overlap_detector():
    """场景 event 互斥校验：同一事件两版本判重叠；不同次数标记（第一/二次死亡）不误报。"""
    from novel_gen.pipeline.stages import _scene_events_overlap
    # 同事件两版本（同构冲救）→ 重叠
    assert _scene_events_overlap([
        {"event": "陆沉冲回墟口救苏妄言，被三只赤尉围住，苏妄言被架走"},
        {"event": "陆沉再次冲上前去救苏妄言，三只赤尉围阵，苏妄言被架走撤离"},
    ]) is True
    # 不同次数标记（第一次死亡 vs 第二次死亡）→ 不同事件，不误报
    assert _scene_events_overlap([
        {"event": "陆沉第一次死亡复活，被灰卒咬穿喉咙"},
        {"event": "陆沉第二次死亡复活，蚀雾灌入鼻腔"},
    ]) is False
    # 不同地点/不同事件 → 不误报
    assert _scene_events_overlap([
        {"event": "陆沉在裂隙旁凿矿，铁镐崩出火花"},
        {"event": "苏妄言在学宫廊下等候，冷风灌进衣领"},
    ]) is False


@pytest.mark.p1
def test_scene_overlap_fallback(store):
    """拆出重叠场景（同一事件两版本）→ 自动回退整章生成，杜绝拼接源头。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    llm.chat.side_effect = [_OVERLAP_SCENES_JSON, "整章回退正文。" * 400 + "【摘要】回退摘要"]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 2  # 拆场景 1 次 + 整章回退 1 次（不逐场景生成）
    assert "整章回退正文" in ch.content
