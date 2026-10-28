# -*- coding: utf-8 -*-
"""REQ-词表换LLM 批次一 scene_event_utils.py：词表归一表/因果正则/救援复写换 LLM 语义（P1）。

覆盖：
- 正常：LLM derive 一次产出语义槽 + 因果合并决策（事件带 LLM conflict/action，因果拍合并生效）；
- 正常：LLM rewrite 命中已发生事件下标 → 返回复写事件原文；
- 边界：events 长度与 beats 不符回退词表 + warning；action 不在规范集合置空；merge 越界丢弃；
- 异常：llm.chat 抛异常 → 回退词表不抛；
- 开关：use_llm=False / llm=None → 与改造前词表输出一致。
词表（_SCENE_ACTION_NORM / _CAUSAL_BEAT_RE / _SCENE_RESCUE_VERBS / _event_conflict）保留为兜底。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.scene_event_utils import (
    _derive_events_from_moves,
    _scene_text_rewrites_event,
)


class FakeLLM:
    """最小假 LLM：chat 返回预置 raw 或抛预置异常，记录调用次数。"""

    def __init__(self, raw=None, exc=None):
        self.raw = raw
        self.exc = exc
        self.calls = 0

    def chat(self, system, user, temperature=0.0, max_tokens=0):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.raw


# 4 拍动线：n_target=3（chapter_words=3000），允许 1 次因果合并后恰好 3 事件
MOVES = "→".join([
    "沈砚救出苏妄言",
    "老更夫沿河巡查",
    "苏妄言昏迷不醒",
    "苏妄言缓缓苏醒",
])
CHAPTER_WORDS = 3000  # _scene_count_for_words → n_target=3


def _derive_raw(slots, merge):
    return json.dumps({"events": slots, "merge": merge}, ensure_ascii=False)


# ---------- 正常：LLM derive 语义槽 + 因果合并 ----------

@pytest.mark.p1
def test_llm_derive_slots_and_merge():
    """LLM 返回规范 JSON → 事件带 LLM conflict/action；merge=[4] 使第4拍并入第3拍（4→3 事件）。"""
    raw = _derive_raw(
        [
            {"conflict": "苏妄言", "action": "救援"},
            {"conflict": "老更夫", "action": "侦查"},
            {"conflict": "苏妄言", "action": ""},
            {"conflict": "苏妄言", "action": "救援"},
        ],
        [4],
    )
    llm = FakeLLM(raw=raw)
    events = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=llm)
    assert llm.calls == 1
    assert len(events) == 3  # 因果合并生效：4 拍 → 3 事件
    assert events[0]["conflict"] == "苏妄言" and events[0]["action"] == "救援"
    assert events[1]["conflict"] == "老更夫" and events[1]["action"] == "侦查"
    # 第3拍合并第4拍，描述拼接，继承前一拍槽位
    assert "昏迷不醒" in events[2]["desc"] and "苏醒" in events[2]["desc"]
    assert events[2]["conflict"] == "苏妄言" and events[2]["action"] == ""


# ---------- 正常：LLM rewrite 命中 ----------

@pytest.mark.p1
def test_llm_rewrite_returns_matched_event():
    """LLM 返回 {"rewrite_of": 0} → 返回复写的已发生事件原文。"""
    done = ["救出苏妄言", "老更夫巡河"]
    llm = FakeLLM(raw='{"rewrite_of": 0}')
    out = _scene_text_rewrites_event("把苏妄言从废墟里拉出来", done, llm=llm)
    assert out == "救出苏妄言"
    assert llm.calls == 1


@pytest.mark.p1
def test_llm_rewrite_no_match_returns_empty():
    """LLM 返回 rewrite_of=-1 → ""（不误杀正常推进）。"""
    done = ["救出苏妄言"]
    llm = FakeLLM(raw='{"rewrite_of": -1}')
    out = _scene_text_rewrites_event("次日众人启程翻过山岭", done, llm=llm)
    assert out == ""


# ---------- 边界：回退词表 / 字段规范化 ----------

@pytest.mark.p1
def test_events_length_mismatch_falls_back_to_wordlist(caplog):
    """events 长度与 beats 不符 → derive 返回 None，回退词表路径并记 warning。"""
    raw = _derive_raw(
        [{"conflict": "苏妄言", "action": "救援"}],  # 仅 1 条，beats 有 4 条
        [],
    )
    llm = FakeLLM(raw=raw)
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.scene_event_utils"):
        llm_events = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=llm)
    # 与纯词表输出（llm=None）逐字段一致
    wordlist_events = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=None)
    assert llm_events == wordlist_events
    assert any("回退词表路径" in r.message for r in caplog.records)


@pytest.mark.p1
def test_action_not_in_set_blanked():
    """action 不在规范动作集合 → 置空串（宁缺毋滥）。"""
    raw = _derive_raw(
        [
            {"conflict": "苏妄言", "action": "瞬移大法"},  # 非规范动作
            {"conflict": "老更夫", "action": "侦查"},
            {"conflict": "苏妄言", "action": ""},
            {"conflict": "苏妄言", "action": "逃离"},
        ],
        [],
    )
    llm = FakeLLM(raw=raw)
    # chapter_words=0 → n_target=4，不发生合并，逐拍对齐
    events = _derive_events_from_moves(MOVES, chapter_words=0, llm=llm)
    assert len(events) == 4
    assert events[0]["action"] == ""          # 非法动作被清空
    assert events[1]["action"] == "侦查"
    assert events[3]["action"] == "逃离"


@pytest.mark.p1
def test_merge_out_of_range_dropped():
    """merge 越界下标（99/100）被丢弃 → 不发生额外合并。"""
    raw = _derive_raw(
        [
            {"conflict": "苏妄言", "action": "救援"},
            {"conflict": "老更夫", "action": "侦查"},
            {"conflict": "苏妄言", "action": ""},
            {"conflict": "苏妄言", "action": "逃离"},
        ],
        [99, 100, 0],  # 全部越界/非法
    )
    llm = FakeLLM(raw=raw)
    events = _derive_events_from_moves(MOVES, chapter_words=0, llm=llm)
    assert len(events) == 4  # 未合并


# ---------- 异常：chat 抛错回退词表 ----------

@pytest.mark.p1
def test_llm_exception_falls_back_silently():
    """llm.chat 抛异常 → 回退词表，不向外抛。"""
    llm = FakeLLM(exc=RuntimeError("boom"))
    out = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=llm)
    expected = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=None)
    assert out == expected


@pytest.mark.p1
def test_rewrite_llm_exception_falls_back_to_wordlist():
    """rewrite 的 llm.chat 抛异常 → 回退词表救援复写逻辑。"""
    done = ["救出苏妄言"]
    llm = FakeLLM(exc=RuntimeError("boom"))
    # 词表路径：names 含苏妄言，文本含冲突对象且含救援动词 → 命中 done[0]
    out = _scene_text_rewrites_event("苏妄言被救了出来", done, names=("苏妄言",), llm=llm)
    assert out == "救出苏妄言"


@pytest.mark.p1
def test_rewrite_llm_non_structured_falls_back_to_wordlist(caplog):
    """LLM 输出非结构化 JSON（无 rewrite_of 键）→ warning 留痕 + 回退词表逻辑。"""
    done = ["救出苏妄言"]
    llm = FakeLLM(raw="这不是JSON，模型在自由发挥")
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.scene_event_utils"):
        out = _scene_text_rewrites_event("苏妄言被救了出来", done, names=("苏妄言",), llm=llm)
    # 词表兜底命中，而非静默 return ""
    assert out == "救出苏妄言"
    assert any("非结构化" in r.message for r in caplog.records)


# ---------- 开关：use_llm / llm=None ----------

@pytest.mark.p1
def test_use_llm_false_skips_llm_and_keeps_wordlist():
    """use_llm=False → 不调用 LLM，输出与词表一致。"""
    llm = FakeLLM(raw=_derive_raw(
        [{"conflict": "X", "action": "击杀"}] * 4, [4]))
    out = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=llm, use_llm=False)
    expected = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS, llm=None)
    assert out == expected
    assert llm.calls == 0


@pytest.mark.p1
def test_llm_none_backward_compatible():
    """llm=None（改造前调用方式）→ 纯词表路径正常工作。"""
    out = _derive_events_from_moves(MOVES, chapter_words=CHAPTER_WORDS)
    assert isinstance(out, list) and len(out) >= 1
    out2 = _scene_text_rewrites_event("无关正文", ["已发生事件"])
    assert out2 == ""
