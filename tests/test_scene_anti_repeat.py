# -*- coding: utf-8 -*-
"""正文层防重演修复测试（P1，REQ-scene-plan-align §7.3 措施1/2/3/5）。

覆盖：
- _extract_beat_sequence：节拍序列提取（空/单拍/顺序/去连续重复/长词优先）
- _beats_rewrite_detected：完整循环重演判定（换措辞循环→True；短序列/正常事件→False）
- _beat_cycle_issues：整章情节级重演检测（质检 2.12）
- scene_prompt 事件进度硬约束注入（event_progress 参数）
- 集成：场景正文节拍序列与已发生场景重合 → 重试；连续 2 次重试仍重演 → 回退整章
- 集成：场景2+ 行动表措辞不再"写手必须逐条落实"（防每场景落实全章行动）
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
from novel_gen.store.schemas import ChapterPlan, Outline
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.beats import (
    _extract_beat_sequence, _beats_rewrite_detected, _beats_local_rewrite_detected,
    _beat_cycle_issues,
)
from novel_gen.pipeline.templates import scene_prompt
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _writer(cfg, llm, store):
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


# 真实《万墟之上》第2章 character_moves（骨架派生源）
_CH2_MOVES = (
    "苏妄言进入铁匠铺，老陈头回避→苏妄言用术法扫描陆沉，确认他没有执能→"
    "陆沉装傻，苏妄言不耐烦，说'你这种人我见多了'→苏妄言执能意外触发（言出法随），"
    "陆沉替她挡下失控的术法碎片，死了一次→复活后陆沉继续装晕，苏妄言以为他只是被震晕→"
    "苏妄言留下一枚铜牌，说'三天后到执刑司报到，算你临时征召'→陆沉捏着铜牌，心里骂娘。"
)

# 2 事件 2 场景（2500 字/章 → 场景粒度优化 N=2；场景逐字引用事件1..2），characters_action 非空
_SPLIT_JSON = (
    '{"events": [{"id": "事件1", "desc": "占位", "conflict": "苏妄言", "action": "试探",'
    ' "final_state": ""},'
    ' {"id": "事件2", "desc": "占位", "conflict": "苏妄言", "action": "抵挡",'
    ' "final_state": ""}],'
    ' "scenes": [{"location": "铁匠铺", "scene_event_ref": "事件1", "emotion": "平静",'
    ' "anchors": ["火炉"], "time_marker": "清晨", "target_words": 1250},'
    ' {"location": "铁匠铺", "scene_event_ref": "事件2", "emotion": "惊险",'
    ' "anchors": ["术法碎片"], "time_marker": "片刻后", "target_words": 1250}],'
    ' "characters_action": [{"name": "陆沉", "goal": "蒙混过关",'
    ' "key_actions": ["装傻", "挡死", "装晕"], "emotion": "警惕", "dialogue_intent": "隐瞒执能"}]}'
)

# 同一完整循环的两个换措辞版本（第2章实证重演模式：盘问→戳穿→失控→挡死→死亡→复活→面板→留牌→离场）
_CYCLE_A = (
    "苏妄言盘问陆沉，陆沉装傻。苏妄言看穿他的把戏：别装了。"
    "苏妄言术法失控，白光炸开。陆沉扑上去替她挡死，被贯穿，咽气倒下。"
    "陆沉复活，睁眼看到面板，可死次数2。苏妄言递出一枚铜牌，陆沉收下。"
    "陆沉转身离开，跟上她离去。"
)
_CYCLE_B = (
    "她再逼问一次，陆沉继续试探。她冷笑：识破了。"
    "执能再次失控，爆裂开来。陆沉冲上去护在身前，断气。"
    "他回神爬起来，蚀痕爬了一分，面板显示2。她留牌给他，令牌落在掌心。"
    "他跟上她的背影，消失在外头。"
)
# 正常单事件场景（短序列，不应误判）
_NORMAL_SCENE = "陆沉在铁匠铺打铁，苏妄言推门进来盘问了两句。陆沉装傻糊弄过去。"


def _save_outline(store, moves: str):
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    outline = Outline(
        chapters=[ChapterPlan(
            no=1, title="第一章", goal="苏妄言来铁匠铺试探陆沉",
            core_event="苏妄言盘问陆沉执能，执能意外触发，陆沉挡死一次，被临时征召",
            character_moves=moves,
        )],
    )
    store.save_outline(outline)


# ---------- _extract_beat_sequence ----------

@pytest.mark.p1
def test_extract_beat_sequence_cycle_order():
    """完整循环 → 归一化节拍序列（顺序保持、长词优先、去连续重复；书级拍已移除）。"""
    seq = _extract_beat_sequence(_CYCLE_A)
    assert seq == ["盘问", "戳穿", "失控", "死亡", "复活", "离场"]


@pytest.mark.p1
def test_extract_beat_sequence_empty_and_single():
    """空文本 / 单拍 → []（单拍无循环意义）。"""
    assert _extract_beat_sequence("") == []
    assert _extract_beat_sequence(None) == []
    assert _extract_beat_sequence("陆沉离开了铁匠铺。") == []


@pytest.mark.p1
def test_extract_beat_sequence_dedupe_consecutive():
    """同一节拍连续出现只记一次（'盘问…又问…再盘问' = 1 拍盘问，去重后仍有离场拍）。"""
    assert _extract_beat_sequence("她盘问他，又问了一次，再盘问，然后离开了。") == ["盘问", "离场"]


# ---------- _beats_rewrite_detected ----------

@pytest.mark.p1
def test_beats_rewrite_detected_cycle_rewrite():
    """换措辞完整循环（CYCLE_B vs CYCLE_A）→ 判重演 True。"""
    a = _extract_beat_sequence(_CYCLE_A)
    b = _extract_beat_sequence(_CYCLE_B)
    assert len(a) >= 4 and len(b) >= 4
    assert _beats_rewrite_detected(b, [a]) is True


@pytest.mark.p1
def test_beats_rewrite_detected_no_false_positive_short():
    """正常单事件短序列（<4 拍）不得误判重演。"""
    seq = _extract_beat_sequence(_NORMAL_SCENE)
    assert len(seq) < 4
    assert _beats_rewrite_detected(seq, [["盘问", "挡死", "死亡", "复活", "面板"]]) is False


@pytest.mark.p1
def test_beats_rewrite_detected_no_false_positive_different():
    """完全不同事件序列（长但无重合节拍）→ False。"""
    other = ["结拜", "约定", "离场", "诊脉", "药包", "暗格"]
    assert _beats_rewrite_detected(other, [["盘问", "戳穿", "失控", "挡死", "死亡", "复活"]]) is False


@pytest.mark.p1
def test_beats_rewrite_detected_empty_done_seqs():
    """无已登记序列 → False。"""
    seq = _extract_beat_sequence(_CYCLE_A)
    assert _beats_rewrite_detected(seq, []) is False


# ---------- _beat_cycle_issues（质检 2.12） ----------

@pytest.mark.p1
def test_beat_cycle_issues_detects_two_versions():
    """整章内含同一节拍序列两个换措辞版本（非重叠）→ 报大改。"""
    content = _CYCLE_A + "\n\n" + _CYCLE_B
    issues = _beat_cycle_issues(content)
    assert len(issues) == 1
    assert issues[0]["type"] == "情节-同事件循环重演"
    assert issues[0]["level"] == "大改"


@pytest.mark.p1
def test_beat_cycle_issues_clean():
    """正常章节（无循环重演）→ 不报。"""
    clean = _CYCLE_A + "\n\n" + _NORMAL_SCENE + "\n\n" + "陆沉收下铜牌离开。"
    # CYCLE_A 只出现一次，后续片段节拍不构成完整循环重复
    assert _beat_cycle_issues(clean) == []


# ---------- 2026-10-15 全书复测误报回归（强情节拍共享 ≥2 约束） ----------

@pytest.mark.p1
def test_beat_cycle_issues_no_false_positive_high_freq_beats():
    """高频骨架拍循环（对峙→留牌→对峙→留牌，0 种强情节拍）→ 不报。
    ch2 实证误报：反复对视/摸铜牌凑出的假循环，正文无复读。"""
    content = (
        "陆沉盯着她，她收下铜牌。陆沉又盯着她，她把铜牌拍回桌上。"
        "她离开前回头盯着他，他捏着铜牌没吭声。"
    )
    assert _beat_cycle_issues(content) == []


@pytest.mark.p1
def test_beat_cycle_issues_no_false_positive_env_beats():
    """环境+骨架拍循环（石阶→对峙→石阶→面板→石阶，0 种强情节拍）→ 不报。
    ch9 实证误报：走石阶/对视/面板结算的环境骨架重复。"""
    content = (
        "他踏上石阶，盯着前面的赤尉。又走完一段石阶，面板亮出可死次数。"
        "最后一阶石阶，他停下，回头盯着来路，面板数字没变。"
    )
    assert _beat_cycle_issues(content) == []


@pytest.mark.p1
def test_beat_cycle_issues_no_false_positive_one_strong_shared():
    """仅 1 种强情节拍共享（失控→面板→失控→面板，不同事件共享行为词）→ 不报。
    ch11 实证误报：两次不同失控事件共享'失控'拍，非同一事件换措辞重演。"""
    content = (
        "他失控撞向岩壁，面板显示可死次数。她执能失控出手，面板又亮了一次。"
    )
    assert _beat_cycle_issues(content) == []


@pytest.mark.p1
def test_beat_cycle_issues_no_false_positive_unequal_windows():
    """窗口长度差大的跨事件匹配（6 拍 vs 10 拍，骨架拍拼窗）→ 不报。
    ch11 实证误报：两个不同失控事件被面板/对峙骨架拍拼进一个 6 拍窗口，
    与后续 10 拍窗口 LCS 达标但长度差 4，非同一事件链等长重演。"""
    content = (
        "玄将失控扑来，陆沉对峙，玄将死亡，面板显示可死次数。"
        "他又失控蜕皮，面板再亮。玄将再死，面板又亮，他失控对峙，"
        "面板，他包扎伤口，转身离去。"
    )
    assert _beat_cycle_issues(content) == []


@pytest.mark.p1
def test_beat_cycle_issues_detects_strong_shared_two_versions():
    """同一事件两版本共享 ≥2 种强情节拍且窗口等长
    （盘问→失控→死亡→面板 ×2）→ 仍报大改。"""
    content = (
        "她盘问他，他失控，咽气倒下，又睁开眼爬了起来。"
        "她又逼问，执能失控，断气，再次睁眼站起。"
    )
    issues = _beat_cycle_issues(content)
    assert len(issues) == 1
    assert issues[0]["type"] == "情节-同事件循环重演"
    assert issues[0]["level"] == "大改"


# ---------- scene_prompt 事件进度硬约束 ----------

def _scene(cfg, event="苏妄言盘问陆沉", idx=2, n=4, progress=True):
    sc = {"location": "铁匠铺", "event": event, "emotion": "警惕",
          "anchors": [], "target_words": 620, "time_marker": "紧随其后"}
    ep = f"本章共 {n} 个事件/场景，本场景为第 {idx}/{n} 个。" if progress else ""
    _, user = scene_prompt(
        cfg, 1, "第一章", idx, sc, "前情", "上一场景结尾", "",
        core_event="核心事件", scene_state_hint="", event_progress=ep,
    )
    return user


@pytest.mark.p1
def test_scene_prompt_event_progress_injected():
    """event_progress 非空 → user 注入【事件进度（硬约束）】，含'禁止把后续事件的完整流程提前写完'。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500)
    user = _scene(cfg)
    assert "事件进度（硬约束" in user
    assert "本章共 4 个事件/场景，本场景为第 2/4 个" in user
    assert "禁止把后续事件的完整流程提前写完" in user


@pytest.mark.p1
def test_scene_prompt_event_progress_absent_when_empty():
    """event_progress 为空 → 不注入该块（行为不变）。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500)
    user = _scene(cfg, progress=False)
    assert "事件进度（硬约束" not in user


@pytest.mark.p1
def test_scene2_plus_action_plan_no_full_implementation(store):
    """场景2+ 行动表措辞：不再'写手必须逐条落实'全章行动，
    改为'本场景只落实与本场景唯一事件直接相关的行动'（防每场景落实全章行动序列）。"""
    _save_outline(store, _CH2_MOVES)
    llm = MagicMock()
    llm.chat.side_effect = [
        _SPLIT_JSON,
        _NORMAL_SCENE,
        _NORMAL_SCENE,
        "场景链路摘要",
        "扩写正文。" * 300,   # 2 场景合计 < 2500 → 自动触发扩写
    ]
    _writer(store.load_config(), llm, store).write(1, "第一章", "目标", None, silent=True)
    scene2_user = llm.chat.call_args_list[2][0][1]
    assert "本场景只落实与『本场景唯一事件』直接相关的行动" in scene2_user
    assert "禁止跨场景重复落实同一行动/同一节拍" in scene2_user
    assert "写手必须逐条落实" not in scene2_user


# ---------- 生成端修复（2026-10-09 第二轮：局部子序列重合 / done_beats 事前注入 / 内部循环 / 整章回退重试） ----------

#: 重试稿"压缩重演段+稀释"逃逸案例：done 为 9 拍完整循环，seq 只复现前 6 拍
#: （整序列 LCS/min = 6/9 = 0.667 < 0.7 逃逸整序列检测，但 6 拍子序列被完整复现 → 局部命中）
_DONE_9 = ["盘问", "戳穿", "失控", "挡死", "死亡", "复活", "面板", "留牌", "离场"]
_SEQ_ESCAPED = ["盘问", "戳穿", "失控", "挡死", "死亡", "复活", "诊脉", "药包", "暗格", "地图", "结拜", "约定"]


@pytest.mark.p1
def test_beats_local_rewrite_detected_compressed_escape():
    """重试稿压缩重演段使整序列 LCS 占比 <0.7（逃逸整序列检测）→ 局部子序列重合仍命中。"""
    assert _beats_rewrite_detected(_SEQ_ESCAPED, [_DONE_9]) is False  # 整序列逃逸
    assert _beats_local_rewrite_detected(_SEQ_ESCAPED, [_DONE_9]) is True  # 局部兜底命中


@pytest.mark.p1
def test_beats_local_rewrite_detected_different():
    """完全不同事件（无重合节拍）→ 局部检测不误报。"""
    other = ["结拜", "约定", "离场", "诊脉", "药包", "暗格"]
    assert _beats_local_rewrite_detected(other, [_DONE_9]) is False


@pytest.mark.p1
def test_beats_local_rewrite_detected_short():
    """新序列 <4 拍 → 局部检测不触发。"""
    short = ["盘问", "离场"]
    assert _beats_local_rewrite_detected(short, [_DONE_9]) is False


@pytest.mark.p1
def test_beats_local_rewrite_detected_empty_done():
    """无已登记序列 → False。"""
    assert _beats_local_rewrite_detected(_SEQ_ESCAPED, []) is False


# ---------- scene_prompt done_beats 事前约束注入 ----------

@pytest.mark.p1
def test_scene_prompt_done_beats_injected():
    """done_beats 非空 → user 注入【本章已发生节拍序列（硬约束）】，含'禁止再次出现相同顺序'。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500)
    sc = {"location": "铁匠铺", "event": "陆沉装晕", "emotion": "警惕",
          "anchors": [], "target_words": 620, "time_marker": "紧随其后"}
    _, user = scene_prompt(
        cfg, 1, "第一章", 2, sc, "前情", "上一场景结尾", "",
        core_event="核心事件", scene_state_hint="", event_progress="本章共 4 个事件/场景，本场景为第 2/4 个。",
        done_beats="- 盘问→戳穿→失控→挡死\n- 死亡→复活→面板→留牌",
    )
    assert "本章已发生节拍序列" in user
    assert "禁止再次出现相同顺序的节拍组合" in user
    assert "盘问→戳穿→失控→挡死" in user


@pytest.mark.p1
def test_scene_prompt_done_beats_absent_when_empty():
    """done_beats 为空 → 不注入该块（行为不变）。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500)
    sc = {"location": "铁匠铺", "event": "陆沉装晕", "emotion": "警惕",
          "anchors": [], "target_words": 620, "time_marker": "紧随其后"}
    _, user = scene_prompt(
        cfg, 1, "第一章", 2, sc, "前情", "上一场景结尾", "",
        core_event="核心事件", scene_state_hint="", event_progress="",
    )
    assert "本章已发生节拍序列" not in user


# ---------- 集成：场景1内部两遍 → 重试（堵 done_beat_seqs 为空时的盲区） ----------

