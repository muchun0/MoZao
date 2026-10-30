# -*- coding: utf-8 -*-
"""面板结算事件锚点 LLM 语义判定（REQ-词表换LLM 批次二 quality.py）—— P1 用例。

把 _panel_progress_issues 中写死的 _PANEL_EVENT_ANCHORS/_PANEL_EVENT_RECALL 锚点正则
换成 LLM 语义判定，正则保留为确定性兜底。覆盖：
1. seg 含真实结算事件（"他死了"），LLM 判 true → major（机制数字未结算推进）
2. seg 为回忆转述（"她看见他死了"），LLM 判 false → minor（面板数字重复显示）
3. LLM 返回非结构化 → 回退词表正则 + caplog 有 warning
4. llm.chat 抛异常 → 回退词表不向外抛
5. use_llm=False → 纯词表、LLM 零调用
6. llm=None → 与改造前词表行为一致
7. 换措辞结算（"他咽了气"，词表漏检），LLM 判 true → major（证明 LLM 增益）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.llm.base import LLMClient, LLMError
from novel_gen.pipeline.quality import QualityChecker

#: filler：两次面板数字之间的无关叙事（不含任何面板词 / 结算锚点，词表兜底可预期）
#: 总长需 > _PANEL_REPEAT_SPAN*2(=300) 越过入口守卫，且两次出现 gap > _PANEL_REPEAT_SPAN(150)
_FILLER = "他一路向前走，穿过林间的小道，天色渐渐暗了下来。" * 16

_MAJOR = "写作-机制数字未结算推进"
_MINOR = "写作-面板数字重复显示（冗余）"


def _content(mid: str) -> str:
    """构造两次同值面板数字「剩余次数：2/3」跨段复现，中间夹 mid 段叙事。

    gap = 第二次出现起点 - 第一次起点；filler 保证 gap > _PANEL_REPEAT_SPAN(150)。
    """
    head = "剩余次数：2/3。"
    tail = "剩余次数：2/3。"
    return head + _FILLER + mid + tail


class FakeLLM(LLMClient):
    """离线假 LLM：按预设返回文本或抛异常。"""

    def __init__(self, response=None, exc=None):
        super().__init__()
        self.response = response
        self.exc = exc
        self.calls = []

    def _chat_impl(self, system, user, **kw):
        self.calls.append((system, user, kw))
        if self.exc is not None:
            raise self.exc
        return self.response


def _checker(llm=None) -> QualityChecker:
    """_panel_progress_issues 不触碰 self.cfg/self.llm，给占位 cfg/store 即可。"""
    return QualityChecker(cfg=object(), llm=None, store=None)


def _types(issues) -> list[str]:
    return [it["type"] for it in issues]


@pytest.mark.p1
def test_llm_true_real_settle_event_is_major():
    """seg 真实结算事件（"他死了"，无回忆词），LLM 判 true → major。"""
    llm = FakeLLM(response='{"has_settle_event": true, "reason": "主角死亡结算"}')
    issues = _checker(llm)._panel_progress_issues(_content("他死了。"), llm=llm, use_llm=True)
    assert len(llm.calls) == 1
    assert _MAJOR in _types(issues)
    assert _MINOR not in _types(issues)


@pytest.mark.p1
def test_llm_false_recalled_death_is_minor():
    """seg 为回忆转述（"她看见他死了"），LLM 判 false → minor。"""
    llm = FakeLLM(response='{"has_settle_event": false, "reason": "仅回忆他人死亡"}')
    issues = _checker(llm)._panel_progress_issues(_content("她看见他死了。"), llm=llm, use_llm=True)
    assert len(llm.calls) == 1
    assert _MINOR in _types(issues)
    assert _MAJOR not in _types(issues)


@pytest.mark.p1
def test_llm_unstructured_falls_back_to_regex(caplog):
    """LLM 返回非结构化 → 回退词表正则（seg 含"他死了"无回忆词 → major）+ warning。"""
    llm = FakeLLM(response="这段文字看起来发生了一些事情，不好判断。")
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.quality"):
        issues = _checker(llm)._panel_progress_issues(_content("他死了。"), llm=llm, use_llm=True)
    assert _MAJOR in _types(issues)
    assert any("回退词表" in r.message for r in caplog.records)


@pytest.mark.p1
def test_llm_chat_exception_falls_back_no_throw(caplog):
    """llm.chat 抛异常 → 回退词表，不向外抛（seg 无锚点 → minor）。"""
    llm = FakeLLM(exc=LLMError("network down"))
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.quality"):
        issues = _checker(llm)._panel_progress_issues(_content("他继续赶路。"), llm=llm, use_llm=True)
    # seg 无任何结算锚点 → 词表兜底 event=False → minor
    assert _MINOR in _types(issues)
    assert _MAJOR not in _types(issues)
    assert any("回退词表" in r.message for r in caplog.records)


@pytest.mark.p1
def test_use_llm_false_pure_vocab_zero_llm_call():
    """use_llm=False → 纯词表，LLM 零调用。"他咽了气"词表无锚点 → minor。"""
    llm = FakeLLM(response='{"has_settle_event": true, "reason": "咽气=死亡结算"}')
    issues = _checker(llm)._panel_progress_issues(_content("他咽了气。"), llm=llm, use_llm=False)
    assert llm.calls == []
    assert _MINOR in _types(issues)
    assert _MAJOR not in _types(issues)


@pytest.mark.p1
def test_llm_none_matches_legacy_vocab_behavior():
    """llm=None → 与改造前词表行为一致："他死了"无回忆词 → major。"""
    issues = _checker(None)._panel_progress_issues(_content("他死了。"), llm=None, use_llm=True)
    assert _MAJOR in _types(issues)


@pytest.mark.p1
def test_llm_catches_reworded_settle_that_vocab_misses():
    """换措辞结算（"他咽了气"，词表锚表漏检），LLM 判 true → major（证明 LLM 增益）。"""
    llm = FakeLLM(response='{"has_settle_event": true, "reason": "咽气=真实死亡结算"}')
    issues = _checker(llm)._panel_progress_issues(_content("他咽了气，再没了声息。"), llm=llm, use_llm=True)
    assert len(llm.calls) == 1
    assert _MAJOR in _types(issues)
    assert _MINOR not in _types(issues)
