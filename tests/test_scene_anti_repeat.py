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
from novel_gen.pipeline.scene_event_utils import (
    _split_moves_beats, _derive_events_from_moves,
    _event_summary, _event_anchor, _remaining_events,
    _scene_check_descs, _scene_text_repeats_generated,
    _anchor_cover, _scene_text_rewrites_event,
)


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
        '{"duplicate_pairs": []}',   # 派生路径通用 LLM 语义去重评审：无重复（2026-10-28 接入）
        _SPLIT_JSON,
        _NORMAL_SCENE,
        _NORMAL_SCENE,
        "场景链路摘要",
        "扩写正文。" * 300,   # 2 场景合计 < 2500 → 自动触发扩写
    ]
    cfg = store.load_config()
    # 词表换LLM 批次一：本测试断言固定 mock 序列中的场景 2 prompt 位置，关新开关保持原调用布局
    cfg.llm_semantic_beats = False
    cfg.llm_semantic_scene_events = False
    cfg.llm_semantic_beat_gate = False
    _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    scene2_user = llm.chat.call_args_list[3][0][1]
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


# ---------- 事件派生：引号/单字对内的箭头不得作为 beat 分隔符（L2 ch3 实证） ----------

#: L2 ch3 大纲原文：『死→活』里的箭头是语义，不是 beat 分隔符
_L2_CH3_MOVES = (
    "陆沉：推林半夏时喊『跑啊！』——这是他全书第一次主动为别人冒死。"
    "林半夏：跑出五十步后停住，本能地用共情去『看』陆沉的状态，感知到『死→活』的断层，"
    "她脸色变了，但没跑回去。赤尉蚀：追林半夏到第四层边界被碎晶屏障弹回。"
)


@pytest.mark.p1
def test_split_moves_beats_ignores_arrow_inside_cn_quotes():
    """中文引号内的 → 不切分，整段保留一个 beat。"""
    beats = _split_moves_beats(_L2_CH3_MOVES)
    assert len(beats) == 1
    assert "死→活" in beats[0]


@pytest.mark.p1
def test_split_moves_beats_ignores_arrow_inside_ascii_and_title_marks():
    """英文引号 / 书名号内的 → 同样不切分。"""
    for text in (
        '他问"死→活是什么意思"，她没答。',
        "他反复念《死→活》三个字。",
        "她只说了句「生→灭」就走了。",
    ):
        assert len(_split_moves_beats(text)) == 1


@pytest.mark.p1
def test_split_moves_beats_keeps_unquoted_short_cjk_pair():
    """未加引号的单字/短词对 → 视为词义箭头，不切。"""
    for text in ("死→活", "失败→成功", "死→活。然后离开"):
        assert len(_split_moves_beats(text)) == 1


@pytest.mark.p1
def test_split_moves_beats_splits_latin_placeholder_beats():
    """A→B 作为两个 beat，仍需切分（与单字对区分）。"""
    assert _split_moves_beats("A→B") == ["A", "B"]


@pytest.mark.p1
def test_split_moves_beats_splits_clause_arrows():
    """完整分句之间的 → 仍是 beat 分隔符。"""
    beats = _split_moves_beats("陆沉进铁匠铺→苏妄言盘问→陆沉装傻")
    assert len(beats) == 3


@pytest.mark.p1
def test_derive_events_no_residual_fragment():
    """修复前 str.split('→') 会产生 '活』…' 残句；修复后事件不再残句开头。"""
    legacy = [b.strip() for b in _L2_CH3_MOVES.split("→")]
    assert legacy[1].startswith("活』")  # 旧行为可复现
    events = _derive_events_from_moves(_L2_CH3_MOVES, chapter_words=2200, use_llm=False)
    assert events
    assert any("死→活" in ev["desc"] for ev in events)
    for ev in events:
        assert not ev["desc"].startswith("活』")  # 旧实现残句
        assert not ev["desc"].startswith("』")


@pytest.mark.p1
def test_derive_events_top_level_arrow_still_splits():
    """顶层 → 仍按 beat 派生事件。"""
    events = _derive_events_from_moves(
        "陆沉进铁匠铺→苏妄言盘问→陆沉装傻", chapter_words=10000, use_llm=False,
    )
    assert len(events) == 3


# ---------- 事件锚点账本（2026-11-08）：id + 摘要 + 原文锚点；剩余未消费事件清单 ----------

@pytest.mark.p1
def test_event_summary_truncates_and_collapses():
    """摘要压空白并截断到 40 字，超长以省略号收尾；短描述原样返回。"""
    assert _event_summary("短事件") == "短事件"
    assert _event_summary("陆沉  推林半夏") == "陆沉 推林半夏"
    long = "陆沉" * 30
    assert len(_event_summary(long)) == 41  # 40 字 + 省略号
    assert _event_summary(long).endswith("…")


@pytest.mark.p1
def test_event_anchor_picks_original_sentence():
    """原文锚点取与事件描述字面重合最高的整句，零 LLM。"""
    piece = ("林半夏站在巷口，布包带子勒着肩。她跑出五十步，停住。"
             "三息后，陆沉从酸液坑里爬出来，半边脸还是烂的。他哑着嗓子问墟苔采到没有。")
    desc = "陆沉：从酸液坑里爬出，问林半夏墟苔采到了吗"
    anchor = _event_anchor(piece, desc)
    assert "陆沉从酸液坑里爬出来" in anchor
    assert "林半夏站在巷口" not in anchor


@pytest.mark.p1
def test_event_anchor_empty_piece():
    assert _event_anchor("", "事件描述") == ""


@pytest.mark.p1
def test_anchor_cover_detects_non_rescue_repeat():
    """修复②：兜底判据换成锚点 bigram 命中，不再只认救援动词。

    「破门」不含 救/引开/砸/挡 等救援词，旧词表必然漏检；锚点判据能命中。
    """
    anchor = "他冲向包间门，肩膀撞上去，门板纹丝不动"
    text = "卫衣男骂了一声。他冲向包间门，肩膀撞上去，门板纹丝不动，发出一声闷响。"
    ratio, hit = _anchor_cover(text, anchor)
    assert ratio >= 0.5 and hit >= 5
    out = _scene_text_rewrites_event(
        text, ["相亲对象B强行破门失败"],
        anchors=[["他冲向包间门，肩膀撞上去，门板纹丝不动"]])
    assert out == "相亲对象B强行破门失败"


@pytest.mark.p1
def test_anchor_cover_not_triggered_by_normal_progression():
    """误伤评估：正常推进只会沿用人物/物件名，不会把锚点句照写一半以上。"""
    anchor = "他冲向包间门，肩膀撞上去，门板纹丝不动"
    text = "沈决走到门边，侧耳贴上门板听了两秒。门外很静，走廊灯亮着。"
    ratio, hit = _anchor_cover(text, anchor)
    assert not (ratio >= 0.5 and hit >= 5)
    assert _scene_text_rewrites_event(
        text, ["相亲对象B强行破门失败"],
        anchors=[["他冲向包间门，肩膀撞上去，门板纹丝不动"]]) == ""


@pytest.mark.p1
def test_remaining_events_uses_zero_based_current_index():
    """current_idx 是当前场景 0 基下标：只返回其后未消费事件；末场景切空才是对的。

    旧实现调用方传 1 基 idx，切片靠"错位刚好"才对上前一场景之后的清单，
    最后一场景必然得到空清单，掩盖了"末场景没有比对对象"的缺陷。
    """
    plan = [("事件1", "推人冒死"), ("事件2", "复活问墟苔"), ("事件3", "赤尉追击")]
    assert _remaining_events(plan, set(), 0) == [("事件2", "复活问墟苔"), ("事件3", "赤尉追击")]
    # 事件2 已被前序场景越权消费 → 不再作为「剩余未消费」列出
    assert _remaining_events(plan, {"事件1", "事件2"}, 0) == [("事件3", "赤尉追击")]
    assert _remaining_events(plan, set(), 1) == [("事件3", "赤尉追击")]
    assert _remaining_events(plan, set(), 2) == []


@pytest.mark.p1
def test_scene_check_descs_includes_own_event_only_when_consumed():
    """自身事件仅在已被前序场景越权消费时纳入比对，正常推进不误判复写。"""
    assert _scene_check_descs(["事件1"], "事件2", False, []) == ["事件1"]
    assert _scene_check_descs(["事件1"], "事件2", True, []) == ["事件1", "事件2"]
    # 保序去重：自身事件与后续清单重复时只留一条
    assert _scene_check_descs(["事件1"], "事件2", True, ["事件2", "事件3"]) == [
        "事件1", "事件2", "事件3"]


@pytest.mark.p1
def test_scene_text_repeats_generated_detects_restart_opening():
    """拼接缝"同一段落重新起头"：开头句照抄即判重复（ch1「包间门合上」形态）。"""
    scene1 = "包间门合上的那一声，很轻。陈婉把茶盏推回来，指腹在杯沿停了一停。"
    scene2 = ("包间门合上的那一声，很轻。陈婉把茶盏推回来，指腹在杯沿停了一停。"
              "她抬头看向窗外，雨声压住了后半句话。")
    assert _scene_text_repeats_generated(scene2, [scene1])
    # 正常承接（开场不同）不误判
    scene3 = "雨在檐口连成线。陈婉没有回头，只把账本翻到最后一页，指尖压在墨迹上。"
    assert _scene_text_repeats_generated(scene3, [scene1]) == ""


@pytest.mark.p1
def test_scene1_overwrite_future_event_triggers_retry(store, monkeypatch):
    """Q1 ch3 病根回归：场景1写出后续事件 → 比对集合含「剩余未消费事件」并触发重试。

    此前比对集合只有 done_events（场景1为空），越权消费全程无人拦截；修复后场景1
    的比对清单必须包含事件2，命中即重试，重试稿干净则采用。
    """
    _save_outline(store, _CH2_MOVES)
    import novel_gen.pipeline.stages as _stages
    seen: list[list[str]] = []

    def _fake_check(text, descs, names=(), llm=None, use_llm=True, anchors=None):
        seen.append(list(descs))
        # 只有越权稿带「硬接」；命中时返回最后一个清单项（= 剩余未消费事件2）
        return descs[-1] if "硬接" in (text or "") else ""

    monkeypatch.setattr(_stages, "_scene_text_rewrites_event", _fake_check)
    offending = "陆沉硬接失控术法碎片，挡在苏妄言身前，死了一次。"
    llm = MagicMock()
    llm.chat.side_effect = [
        '{"duplicate_pairs": []}',   # 派生事件语义去重评审
        _SPLIT_JSON,                 # 拆场景
        offending,                   # 场景1（越权写完事件2）
        _NORMAL_SCENE,               # 场景1重试（干净）
        _NORMAL_SCENE,               # 场景2
        "场景链路摘要",
        "扩写正文。" * 300,
    ]
    cfg = store.load_config()
    cfg.llm_semantic_beats = False
    cfg.llm_semantic_scene_events = False
    cfg.llm_semantic_beat_gate = False
    _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    # 场景1 的比对清单必须含后续事件（原实现只含 done_events=空 → 不调用检测）
    assert seen and seen[0], "场景1未把剩余未消费事件纳入比对"
    assert any("挡" in d for d in seen[0])
    retry_user = llm.chat.call_args_list[3][0][1]
    assert "本章剩余未消费事件（硬约束·禁越权消费" in retry_user
    assert "重试强制规则" in retry_user


@pytest.mark.p1
def test_scene_prompt_unconsumed_events_hard_constraint():
    """upcoming_events 注入【本章剩余未消费事件（硬约束·禁越权消费）】及编号事件。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500)
    sc = {"location": "裂隙口", "event": "推人冒死", "emotion": "紧张",
          "anchors": [], "target_words": 1100, "time_marker": "午后"}
    _, user = scene_prompt(
        cfg, 1, "第三章", 1, sc, "前情", "上一场景结尾", "",
        upcoming_events="- 事件2（尚未发生）：复活问墟苔\n- 事件3（尚未发生）：赤尉追击",
    )
    assert "本章剩余未消费事件（硬约束·禁越权消费" in user
    assert "禁止写出它们的完整流程" in user
    assert "事件2（尚未发生）：复活问墟苔" in user
    assert "本章后续事件预览" not in user


