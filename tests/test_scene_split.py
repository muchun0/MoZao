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
    """拆场景非法 JSON（字段兜底也救不回）→ 重试 1 次仍失败 → 自动回退整章生成（不中断）。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    # 拆场景 2 次都返回不可解析文本（第 1 次解析失败→字段兜底失败→重试 1 次，
    # 第 2 次仍失败）→ 回退整章生成（第 3 次调用）
    llm.chat.side_effect = ["不是JSON", "还不是JSON", "整章回退正文。" * 400 + "【摘要】回退摘要"]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 3
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


# ---------- 拆场景设计重构（2026-09-30）：事件清单先行 + 结构化校验 ----------

def test_scene_plan_validation():
    """拆场景结构校验：场景数超上限（>4=凑数拆重复场景）、事件编号重复引用、
    多场景 target_words 偏离目标 → 判不合格回退整章；正常规划通过。"""
    from novel_gen.pipeline.stages import _validate_scene_plan
    # 场景数 5（6316 字版"苏妄言被围"拆 5 个变体场景的形态）→ 不合格
    ok, why = _validate_scene_plan([{"target_words": 500} for _ in range(5)], 2500)
    assert not ok and "场景数" in why
    # 同一事件编号被两个场景引用 → 不合格（事件引用唯一性）
    ok, why = _validate_scene_plan([
        {"scene_event_ref": "事件1", "target_words": 1200},
        {"scene_event_ref": "事件1", "target_words": 1300},
    ], 2500)
    assert not ok and "引用" in why
    # 多场景 target_words 之和偏离目标 ±10% → 不合格
    ok, why = _validate_scene_plan([
        {"event": "陆沉在矿道被灰卒咬死复活", "target_words": 400},
        {"event": "陆沉爬出墟口遇苏妄言被围", "target_words": 400},
    ], 2500)
    assert not ok and "target_words" in why
    # 正常 2 场景（新格式引用不同事件编号、字数接近目标）→ 通过
    ok, _ = _validate_scene_plan([
        {"scene_event_ref": "事件1", "event": "陆沉在铁精矿脉被灰卒咬穿喉咙，面板3→2，复活", "target_words": 1300},
        {"scene_event_ref": "事件2", "event": "陆沉爬出墟口救苏妄言引开三只赤尉", "target_words": 1200},
    ], 2500)
    assert ok
    # 单场景不判失败（拆不出时按整章单场景写，交给质量审计兜底）
    ok, _ = _validate_scene_plan([{"event": "陆沉救苏妄言", "target_words": 1400}], 2500)
    assert ok


@pytest.mark.p1
def test_scene_event_ref_expansion():
    """场景 scene_event_ref 展开为事件清单原文：模型无法再靠换措辞把同一事件塞进两场景。"""
    from novel_gen.pipeline.stages import _expand_scene_event_refs, _validate_scene_plan
    scenes = [
        {"scene_event_ref": "事件1", "target_words": 1300},
        {"scene_event_ref": "事件2", "target_words": 1200},
    ]
    events = [
        {"id": "事件1", "desc": "陆沉在铁精矿脉被灰卒咬穿喉咙，面板3→2，复活"},
        {"id": "事件2", "desc": "陆沉爬出墟口救苏妄言引开三只赤尉"},
    ]
    _expand_scene_event_refs(scenes, events)
    assert scenes[0]["event"] == events[0]["desc"]
    assert scenes[1]["event"] == events[1]["desc"]
    ok, _ = _validate_scene_plan(scenes, 2500)
    assert ok
    # 同编号引用两次 → 判重叠
    scenes2 = [{"scene_event_ref": "事件1"}, {"scene_event_ref": "事件1"}]
    _expand_scene_event_refs(scenes2, events)
    ok, why = _validate_scene_plan(scenes2, 2500)
    assert not ok and "事件1" in why


@pytest.mark.p1
def test_scene_adjacent_repeat_retry(store):
    """场景生成端兜底：场景 N 与上一场景全文高度相似（重演上一场景）→ 重试 1 次并强制
    从上一场景结局后续写；重试结果不同则采用，从拼接发生点拦截（不整章重写）。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    scenes_json = (
        '{"scenes": [{"location": "墟口空地", "event": "陆沉引开赤尉救苏妄言", "emotion": "焦灼", '
        '"target_words": 1300}, '
        '{"location": "墟口坡地", "event": "陆沉带苏妄言撤往补给站", "emotion": "紧迫", '
        '"target_words": 1200}], "characters_action": []}'
    )
    # 场景 1 正文（1300+ 字，避免触发字数兜底扩写）；场景 2 第一版 = 场景 1 原样复刻（重演）；
    # 场景 2 重试版 = 全新推进（1200+ 字）；摘要
    scene1 = ("陆沉伏在墟口边缘，苏妄言被三只赤尉围住，他攥紧短凿，心跳一下一下撞着肋骨。"
              "碎石在指缝间滚落，他听见她急促的呼吸，面板还剩两次，蚀痕烧到小臂中段。" * 20)
    scene2_new = ("陆沉已经冲到坡底，一把拽住苏妄言的后领往外拖，赤尉的甲壳在身后重新合拢，"
                  "碎石顺着坡面滚下去，他吼她快带受伤的人往裂隙退。" * 15)
    llm.chat.side_effect = [
        scenes_json,
        scene1,
        scene1,                    # 场景 2 第一版：整段复刻上一场景
        scene2_new,                # 场景 2 重试版：全新推进
        "摘要",
    ]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 5  # 拆场景 + 场景1 + 场景2第一版 + 场景2重试 + 摘要
    assert "拽住苏妄言的后领" in ch.content   # 采用重试版（全新推进）
    # 重试 prompt 含强制从结局后续写规则
    retry_call = llm.chat.call_args_list[3]
    retry_user = retry_call.args[1] if retry_call.args else retry_call[1]
    assert "本场景重试强制规则" in retry_user
    assert "禁止重演上一场景" in retry_user


# ---------- 事前防线：拆场景/写场景 prompt 硬规则注入（用户要求'事前解决'） ----------

def test_split_prompt_event_dedup_rule():
    """拆场景 prompt 含事件去重规则：事件清单先行、场景引用事件编号、同类事件本章至多一次、
    机制数字规划到场景、专有名词锁定。"""
    from novel_gen.pipeline.templates import split_scenes_prompt
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    _, user = split_scenes_prompt(cfg, 1, "第一章", "目标", core_event="死亡-复活")
    assert "第一步：规划本章事件清单" in user          # 事件清单先行（设计重构根因）
    assert "scene_event_ref" in user                    # 场景逐字引用事件编号（防措辞漂移）
    assert "场景数 = 事件数" in user                    # 1 事件 = 1 场景
    assert "conflict" in user and "action" in user      # 事件结构化字段（语义槽位去重）
    assert "final_state" in user                        # 事件结束状态增量标注
    assert "事件去重与机制规划硬规则" in user
    assert "同类事件本章至多一次" in user              # 防"三次救苏妄言"循环
    assert "机制数字规划到场景" in user                # 每场景结束时标注 X/X
    assert "专有名词锁定" in user                       # 光盾/学宫短褐 称谓固定


def test_scene_events_semantic_slot_dedup():
    """事件语义槽位互斥（2026-10-01）：同冲突对象+同动作=重复事件（救苏妄言石殿版/墟口外版），
    即使 desc 措辞/地点不同；不同动作/不同冲突对象不误报。"""
    from novel_gen.pipeline.stages import _scene_events_semantically_overlap
    # 同冲突（三头赤尉）+同动作（救援），地点不同 → 重复（4500 字版根因形态）
    assert _scene_events_semantically_overlap([
        {"id": "事件2", "desc": "陆沉在穹顶石殿引开赤尉把苏妄言拖进窄缝", "conflict": "三头赤尉", "action": "救援"},
        {"id": "事件3", "desc": "陆沉在墟口外碎石坡从围阵中拽走苏妄言", "conflict": "三头赤尉", "action": "救援"},
    ]) is True
    # 无结构化字段时从 desc 兜底提取（救 vs 围）→ 救援/围攻不同动作不误报
    assert _scene_events_semantically_overlap([
        {"id": "事件1", "desc": "陆沉被灰卒咬穿喉咙，面板3→2，复活"},
        {"id": "事件2", "desc": "陆沉在石殿救出苏妄言"},
    ]) is False
    # 同动作但冲突对象不同（救苏妄言 vs 救受伤弟子）→ 不误报
    assert _scene_events_semantically_overlap([
        {"id": "事件2", "desc": "救苏妄言", "conflict": "苏妄言", "action": "救援"},
        {"id": "事件3", "desc": "救受伤弟子", "conflict": "受伤弟子", "action": "救援"},
    ]) is False
    # 2026-10-01 第四版根因形态：同一"救苏妄言"事件，敌人措辞不同
    # （'三只灰卒' vs '三只赤尉伴生灰卒'）→ 人物优先判重（旧版按敌人词双漏检）
    assert _scene_events_semantically_overlap([
        {"id": "事件2", "desc": "陆沉冲上岩台用半截镐柄救被三只灰卒围困的苏妄言，砸碎蚀核、掰开咬住她左臂的灰卒，苏妄言获救", "action": "救援"},
        {"id": "事件3", "desc": "苏妄言被三只赤尉伴生灰卒围在巨石旁，陆沉冲出去用铁镐砸碎蚀核救她，苏妄言再次获救", "action": "救援"},
    ]) is True
    # 同一人物但动作不同（救 vs 察觉）→ 不误报
    assert _scene_events_semantically_overlap([
        {"id": "事件2", "desc": "陆沉救出苏妄言", "action": "救援"},
        {"id": "事件3", "desc": "苏妄言察觉陆沉死而复生的异常", "action": "察觉"},
    ]) is False


def test_long_sentence_overlap_ratio():
    """相邻场景长段复刻率（2026-10-01）：场景 2 含上一场景原样长句（局部复刻）→ 命中；
    全新推进 → 0。"""
    from novel_gen.pipeline.stages import _long_sentence_overlap_ratio
    prev = "陆沉伏在墟口往下看，苏妄言被三只赤尉围住，他攥紧短凿，心跳撞着肋骨。"
    # 场景 2 复刻了上一场景的整句（原样 ≥15 字）+ 新内容 = 局部复刻
    cur_bad = (
        "窄缝里一片漆黑，他听见自己的心跳，也听见苏妄言的喘息。"
        "陆沉伏在墟口往下看，苏妄言被三只赤尉围住，他攥紧短凿，心跳撞着肋骨。"
        "剧痛从喉管炸开，温热的血涌进气管，视野碎成一片灰白。"
        "黑暗，冷，矿壁的凉意贴着后背，他猛地睁眼。"
    )
    assert _long_sentence_overlap_ratio(prev, cur_bad) > 0.2
    # 全新推进（仅个别短词相同）→ 0
    cur_new = (
        "陆沉已经冲到坡底，一把拽住苏妄言的后领往外拖，赤尉的甲壳在身后重新合拢，"
        "碎石顺着坡面滚下去，他吼她快带受伤的人往裂隙退。"
    )
    assert _long_sentence_overlap_ratio(prev, cur_new) < 0.2
    # 整段复刻 → 高复刻率
    assert _long_sentence_overlap_ratio(prev, prev) >= 1.0


def test_strip_json_block_robust():
    """2026-10-01 拆场景 JSON 健壮化：代码块包裹、前后赘述、尾逗号 → 均可解析。"""
    import json
    from novel_gen.pipeline.stages import _strip_json_block
    # 代码块 + 尾逗号 + 赘述
    raw = '以下是拆场景结果：\n```json\n{"events": [{"id": "事件1", "desc": "a",},],\n"scenes": []}\n```\n（完）'
    parsed = json.loads(_strip_json_block(raw))
    assert parsed["events"][0]["desc"] == "a"
    # 整体有代码块/赘述时剥净；字段级截断（events 完整、scenes 截断）→ 整体救不回，
    # 但 _extract_json_field 可救回 events（回退整章注入事件序列用）
    from novel_gen.pipeline.stages import _extract_json_field
    raw2 = '{"events": [{"id": "事件1", "desc": "b", "conflict": "赤尉", "action": "救援"}], "scenes": [{"location": "墟口"'
    ev2 = _extract_json_field(raw2, "events")
    assert len(ev2) == 1 and ev2[0]["desc"] == "b" and ev2[0]["action"] == "救援"
    # 更极端截断（events 对象未闭合）→ 字段级救不回该字段，返回 []
    raw3 = '{"events": [{"id": "事件1", "desc": "b"'
    assert _extract_json_field(raw3, "events") == []


def test_scene_text_rewrites_event():
    """2026-10-01 场景正文事件级复写检测：场景正文把已发生事件换措辞又写一遍
    （坡顶救人版/墟口外救人版：conflict 措辞不同但同为'赤尉+救援'）→ 命中；
    不同冲突对象（灰卒死亡 vs 救人）→ 不误报。"""
    from novel_gen.pipeline.stages import _scene_text_rewrites_event
    # 已完成事件：场景1（灰卒死亡复活）、场景2（坡顶救人）
    done = [
        "陆沉被灰卒咬穿喉咙，面板 3/3→2/3，复活",
        "陆沉在坡顶引开三条赤尉救出被围的苏妄言，苏妄言金光重创赤尉",
    ]
    # 场景3 正文把'救苏妄言'重演（冲突对象赤尉+救援动作 冲/砸/挡）
    text_dup = (
        "陆沉冲了出去，把铁精矿石抡圆砸向巨型赤尉的复眼，苏妄言趁机结印，"
        "金光直贯赤尉，三人退入裂隙。"
    )
    assert _scene_text_rewrites_event(text_dup, done) == done[1]
    # 全新推进（无救援动作/不同冲突对象）→ 不命中
    text_new = (
        "裂隙深处一片死寂，陆沉靠着岩壁喘匀气，摸出火折子点上，"
        "他低头看了一眼面板，还剩两次。"
    )
    assert _scene_text_rewrites_event(text_new, done) == ""
    # 场景1 正文（灰卒死亡）不误报救人
    text_death = (
        "灰卒咬穿了他的喉管，血喷出来，他抬手去挡，视野发黑。"
        "面板浮现：今日可死次数：2/3。"
    )
    assert _scene_text_rewrites_event(text_death, done) == ""
    # 2026-10-01 第四版根因形态：场景正文复写"救苏妄言"但敌人措辞不同
    # （赤尉伴生灰卒 vs 灰卒）→ 人物优先命中；纯撤离场景不误报
    done4 = ["陆沉冲上岩台用半截镐柄救被三只灰卒围困的苏妄言，砸碎蚀核、苏妄言获救"]
    text_dup4 = (
        "墟口外的空地上，苏妄言被三只赤尉伴生灰卒围在巨石旁，"
        "陆沉再次冲出去，用铁镐砸碎蚀核救她，她脱险后喘着气看他。"
    )
    assert _scene_text_rewrites_event(text_dup4, done4) == done4[0]
    text_retreat = (
        "陆沉拽住苏妄言的后领往外拖，赤尉的甲壳在身后重新合拢，"
        "碎石顺着坡面滚下去，他吼她快带受伤的人往裂隙退。"
    )
    assert _scene_text_rewrites_event(text_retreat, done4) == ""


def test_scene_prompt_forbid_event():
    """2026-10-01 场景重试注入 forbid_event：禁止重写已发生事件。"""
    from novel_gen.pipeline.templates import scene_prompt
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    scene = {"location": "墟口", "event": "陆沉带伤者退入裂隙", "emotion": "紧绷",
             "anchors": [], "target_words": 1200}
    _, user = scene_prompt(cfg, 1, "第一章", 3, scene, "记忆", "", "",
                           force_new_progression=True,
                           forbid_event="陆沉在坡顶引开三条赤尉救出被围的苏妄言")
    assert "重试强制规则" in user
    assert "已发生事件又写了一遍" in user
    assert "陆沉在坡顶引开三条赤尉救出被围的苏妄言" in user
    assert "禁止以任何形式再次发生" in user
    # 无 forbid_event 时不注入该块
    _, user2 = scene_prompt(cfg, 1, "第一章", 3, scene, "记忆", "", "")
    assert "已发生事件又写了一遍" not in user2


def test_extract_json_field_partial():
    """2026-10-01 拆场景 JSON 截断时字段级兜底：events/scenes 可分别救回。"""
    from novel_gen.pipeline.stages import _extract_json_field
    # 输出在 characters_action 处截断 → events/scenes 完整可救
    raw = ('{"events": [{"id": "事件1", "desc": "陆沉被灰卒咬穿喉咙复活", "conflict": "灰卒", '
           '"action": "击杀"}, {"id": "事件2", "desc": "救被围的苏妄言", "conflict": "三只赤尉", '
           '"action": "救援"}], "scenes": [{"location": "墟口", "scene_event_ref": "事件1", '
           '"target_words": 1200}, {"location": "碎石坡", "scene_event_ref": "事件2", '
           '"target_words": 1300}], "characters_action": [{"name": "苏妄言", "goal": }')
    ev = _extract_json_field(raw, "events")
    sc = _extract_json_field(raw, "scenes")
    assert len(ev) == 2 and ev[1]["action"] == "救援"
    assert len(sc) == 2 and sc[1]["scene_event_ref"] == "事件2"
    assert _extract_json_field(raw, "characters_action") == []


def test_scene_prompt_prevention_rules():
    """写场景 prompt 含事前硬规则：数字结算时机、术语锁定、同模式动作去重、死亡多样化、
    面板显示纪律、状态度量单一口径、角色身份锁定、摘要覆盖最终状态。"""
    from novel_gen.pipeline.templates import scene_prompt
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    scene = {"location": "墟口", "event": "陆沉引开赤尉", "emotion": "焦灼",
             "anchors": [], "target_words": 1200, "final_state": "面板 2/3，蚀痕已越肘弯"}
    _, user = scene_prompt(cfg, 1, "第一章", 1, scene, "前情记忆", "", "")
    assert "机制数字结算（硬约束）" in user             # 死亡瞬间 vs 复活后 数字口径
    assert "术语与状态口径" in user                     # 禁止'光盾'变'命执盾'
    assert "同模式动作不重复" in user                   # 冲回去救人不得原样 2 次
    assert "死因必须不同" in user                       # 连续死亡死因不同
    assert "面板数字只出现一次且即时结算" in user       # 数字只在结算瞬间出现一次
    assert "累积口径" in user                           # 蚀痕累积口径，禁止多处重新计量
    assert "增长量只用" in user                         # 蚀痕增长只用'寸'，禁'三指/一指宽'
    assert "外貌/服装/代称全局唯一" in user             # 苏妄言青袍短发不得第二套描述
    assert "写本章最终结算后的面板状态" in user         # 摘要写最终结算数字，不写开篇状态
    assert "禁止再次描写同一事件" in user               # 本章已发生事件不得再次描写（两版本）
    assert "final_state" in user and "面板 2/3" in user  # 事件结束状态增量注入（防蚀痕无增量）


def test_split_prompt_word_count_hardcap():
    """拆场景字数规划（超长根因）：场景数由 chapter_words 反推、target_words 总和硬等于目标。"""
    from novel_gen.pipeline.templates import split_scenes_prompt
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    sys_s, user = split_scenes_prompt(cfg, 1, "第一章", "目标", core_event="死亡-复活")
    assert "2-4 个连续场景" in sys_s                  # 不再 3-5 个（3×1000>2500 自相矛盾）
    assert "由本章总字数反推" in sys_s
    assert "之和必须等于 2500" in user                # target_words 硬等于 chapter_words
    assert "禁止为了凑场景数或凑字数超写" in user


@pytest.mark.p1
def test_scene_max_tokens_adaptive(store):
    """逐场景生成 max_tokens 按 target_words 自适应（长场景不再被 2000 截断）。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    scenes_json = (
        '{"scenes": [{"location": "墟口", "event": "陆沉救苏妄言", "emotion": "焦灼", '
        '"target_words": 1400}], "characters_action": []}'
    )
    llm.chat.side_effect = [scenes_json, "场景正文。" * 300, "摘要"]
    _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    scene_call = llm.chat.call_args_list[1]
    kw = scene_call.kwargs if hasattr(scene_call, "kwargs") else (scene_call[2] if len(scene_call) > 2 else {})
    # 1400 字目标 → max_tokens ≥ 1400*2.2=3080，且 ≥4000 下限
    assert kw.get("max_tokens", 0) >= 4000


@pytest.mark.p1
def test_scene_done_events_injected_to_later_scene(store):
    """跨场景事件清单（事前防线）：第 1 个场景的已发生事件必须注入第 2 个场景的 prompt，
    防止"救苏妄言"被换个地点又写一遍（22:32 版空地版/坡地版双版本根因）。"""
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    llm = MagicMock()
    scenes_json = (
        '{"scenes": [{"location": "墟口空地", "event": "陆沉引开赤尉救苏妄言", "emotion": "焦灼", '
        '"target_words": 1300}, '
        '{"location": "墟口坡地", "event": "陆沉带苏妄言撤往补给站", "emotion": "紧迫", '
        '"target_words": 1100}], "characters_action": []}'
    )
    llm.chat.side_effect = [
        scenes_json,           # 拆场景
        "场景一正文。" * 200,   # 场景 1
        "场景二正文。" * 180,   # 场景 2
        "摘要",
    ]
    _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    # 场景 2 调用（call 2）的 user prompt 必须含【本章已发生事件】+ 场景 1 事件
    scene2 = llm.chat.call_args_list[2]
    user2 = scene2.args[1] if scene2.args else scene2[1]
    assert "【本章已发生事件" in user2
    assert "陆沉引开赤尉救苏妄言" in user2
    assert "禁止再次发生" in user2 or "禁止重复描写" in user2
