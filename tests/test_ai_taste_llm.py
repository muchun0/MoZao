# -*- coding: utf-8 -*-
"""REQ-词表换LLM 批次二 ai_taste.py：AI 痕迹四维度 LLM 语义判定测试（P1）。

验收重点：LLM 判中 conn/zero_direct 等维度正确扣分并产出 issue；正常正文不误报；
LLM 失败/非结构化/异常/开关关闭/未接 llm 时确定性词表兜底，行为与旧版一致，且整章
仅 1 次 LLM 调用（score 与 issues 共享一次 judge）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.llm.base import LLMError
from novel_gen.pipeline.ai_taste import ai_taste_issues, score_ai_taste


class _FakeLLM:
    """最小 LLM 桩：reply 为返回文本；error=True 时 chat 抛 LLMError。"""

    def __init__(self, reply=None, error=False):
        self.reply = reply
        self.error = error
        self.calls = 0

    def chat(self, *a, **k):
        self.calls += 1
        if self.error:
            raise LLMError("boom")
        return self.reply


#: 自然正文：含'他怕了'点破（fallback zero_direct=False）、无连接词/情绪词/明喻/排比/雷同，
#: → fallback judge 全 false、score=100、无 issue；以此隔离兜底，证明 LLM 命中是语义来源。
_CLEAN = (
    "他怕了，攥紧拳头。她把药瓶推过来，说先吃了再说。"
    "他低头看一眼，没接，只把外套拢了拢。窗外有人喊他，他应了一声。"
) * 2

_ALL_FALSE = '{"connective_excess": false, "emotion_excess": false, "filler_overuse": false, "zero_direct": false}'


@pytest.mark.p1
def test_llm_conn_excess_deducts_25_and_issues():
    """LLM 判中连接词堆砌 → score 扣 25、issues 含该维度（fallback 对 _CLEAN 本为 100）。"""
    assert score_ai_taste(_CLEAN) == 100  # 纯词表：无连接词 → 不扣分（对照基线）
    llm = _FakeLLM(reply='{"connective_excess": true, "emotion_excess": false, "filler_overuse": false, "zero_direct": false}')
    assert score_ai_taste(_CLEAN, llm=llm) == 75
    assert llm.calls == 1
    types = {it["type"] for it in ai_taste_issues(_CLEAN, threshold=100, llm=llm)}
    assert "AI痕迹-连接词堆砌" in types


@pytest.mark.p1
def test_llm_zero_direct_deducts_10_and_issues():
    """LLM 判零直白 → 扣 10、issues 含该维度（_CLEAN 本有'他怕了'，fallback 判非零直白）。"""
    llm = _FakeLLM(reply='{"connective_excess": false, "emotion_excess": false, "filler_overuse": false, "zero_direct": true}')
    assert score_ai_taste(_CLEAN, llm=llm) == 90
    types = {it["type"] for it in ai_taste_issues(_CLEAN, threshold=100, llm=llm)}
    assert "AI痕迹-全程演过头（零直白）" in types


@pytest.mark.p1
def test_llm_all_false_natural_no_deduct():
    """LLM 全部 false（自然正文）→ score 不减分、issues 为空。"""
    llm = _FakeLLM(reply=_ALL_FALSE)
    assert score_ai_taste(_CLEAN, llm=llm) == 100
    assert ai_taste_issues(_CLEAN, threshold=60, llm=llm) == []


@pytest.mark.p1
def test_llm_unstructured_fallback_with_warning(caplog):
    """LLM 返回非 JSON → 回退词表统计 + caplog 有 warning；_CLEAN 兜底仍 100 分。"""
    llm = _FakeLLM(reply="这段文字我觉得挺好，没什么问题。")
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.ai_taste"):
        assert score_ai_taste(_CLEAN, llm=llm) == 100
    assert any("fallback" in r.getMessage() for r in caplog.records)
    assert llm.calls == 1


@pytest.mark.p1
def test_llm_chat_exception_fallback_not_raised():
    """llm.chat 抛异常 → 捕获回退词表，不向上抛。"""
    llm = _FakeLLM(error=True)
    assert score_ai_taste(_CLEAN, llm=llm) == 100  # 回退兜底，无异常
    assert llm.calls == 1


@pytest.mark.p1
def test_use_llm_false_pure_dict_zero_call():
    """use_llm=False → 纯词表统计，LLM 一次都不被调用。"""
    llm = _FakeLLM(error=True)
    assert score_ai_taste(_CLEAN, llm=llm, use_llm=False) == 100
    assert llm.calls == 0


@pytest.mark.p1
def test_llm_none_backward_compat():
    """llm=None → 与旧版纯词表行为一致（_CLEAN=100；密集连接词样本仍被词表判中）。"""
    assert score_ai_taste(_CLEAN) == 100
    heavy_conn = "然而，然而，然而，然而，然而，然而。" * 2  # 词表连接词密度远超阈值
    # 词表判 conn=true(-25) 且无点破 zero_direct=true(-10) → 65
    assert score_ai_taste(heavy_conn) == 65
    assert any(it["type"] == "AI痕迹-连接词堆砌" for it in ai_taste_issues(heavy_conn, threshold=100))


@pytest.mark.p1
def test_empty_text_no_llm():
    """空文本 → 100 分、无 issue、不调 LLM。"""
    llm = _FakeLLM(error=True)
    assert score_ai_taste("", llm=llm) == 100
    assert ai_taste_issues("", llm=llm) == []
    assert llm.calls == 0


@pytest.mark.p1
def test_issues_reuse_single_judge_call():
    """score 与 issues 共享一次 judge：单次 ai_taste_issues 仅触发 1 次 LLM 调用。"""
    llm = _FakeLLM(reply='{"connective_excess": true, "emotion_excess": false, "filler_overuse": false, "zero_direct": false}')
    issues = ai_taste_issues(_CLEAN, threshold=100, llm=llm)
    assert any(it["type"] == "AI痕迹-连接词堆砌" for it in issues)
    assert llm.calls == 1
