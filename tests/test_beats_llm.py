# -*- coding: utf-8 -*-
"""REQ-词表换LLM 批次一：beats.py 节拍抽取 LLM 化测试（P0-14 重演防线）。

验收重点：换措辞重演仍能检出、正常章不误报；LLM 失败/非结构化/违标签/开关关闭
时确定性词表兜底，行为与旧版一致。

覆盖：
- 正常：FakeLLM 返回规范 JSON → extract_beats 消费 LLM 结果（不回退词表）
- 换措辞重演检出：LLM 序列 vs done 序列 LCS≥4 占比≥0.7 → _beats_rewrite_detected 命中
- 边界：LLM 返回非规范标签 → 回退词表且 logger.warning 留痕
- 异常：FakeLLM.chat 抛 LLMError → 回退词表，不向上抛
- 开关：use_llm=False → 纯词表（LLM 不被调用）
- llm=None → 纯词表（与旧行为一致）
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.llm.base import LLMError
from novel_gen.pipeline.beats import (
    _BEAT_LABELS,
    _beats_rewrite_detected,
    _extract_beat_sequence,
    extract_beats,
)


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


#: 正文里没有任何词表行为词——词表必抽为空，以此证明走的是 LLM 结果
_PLAIN_TEXT = "一段没有任何固定行为词的占位正文，用来隔离词表兜底。"
#: 词表可抽的正文：逼问→盘问，咽气→死亡，睁开眼→复活
_DICT_TEXT = "他逼问对方半天，那人咽气了，半晌才睁开眼。"


@pytest.mark.p0
def test_extract_beats_uses_llm_result():
    """正常：LLM 返回规范 JSON → 直接采用 LLM 序列（词表对该正文抽空，证非兜底）。"""
    llm = _FakeLLM(reply='{"beats": ["盘问", "戳穿", "死亡", "复活"]}')
    seq = extract_beats(_PLAIN_TEXT, llm=llm)
    assert llm.calls == 1
    assert seq == ["盘问", "戳穿", "死亡", "复活"]
    assert _extract_beat_sequence(_PLAIN_TEXT) == []  # 词表兜底确为空


@pytest.mark.p0
def test_paraphrase_rewrite_still_detected():
    """换措辞重演检出：LLM 语义抽出的序列与 done 序列 LCS≥4 占比≥0.7 → 命中。"""
    llm = _FakeLLM(reply='{"beats": ["盘问", "戳穿", "失控", "死亡", "复活"]}')
    seq = extract_beats(_PLAIN_TEXT, llm=llm)
    done = [["盘问", "戳穿", "失控", "死亡", "复活"]]  # 上一版完整循环
    assert _beats_rewrite_detected(seq, done) is True
    # 反向：新序列只有 2 拍单事件 → 不误报
    short = [b for b in seq[:2]]
    assert _beats_rewrite_detected(short, done) is False


@pytest.mark.p0
def test_llm_noncanonical_labels_fallback_with_warning(caplog):
    """边界：LLM 自造标签不在白名单 → logger.warning + 回退词表。"""
    assert {"审问", "暴毙", "苏醒"}.isdisjoint(_BEAT_LABELS)
    llm = _FakeLLM(reply='{"beats": ["审问", "暴毙", "苏醒"]}')
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.beats"):
        seq = extract_beats(_DICT_TEXT, llm=llm)
    assert seq == _extract_beat_sequence(_DICT_TEXT)  # = ["盘问","死亡","复活"]
    assert seq == ["盘问", "死亡", "复活"]
    assert any("whitelist" in r.getMessage() for r in caplog.records)


@pytest.mark.p0
def test_llm_chat_exception_fallback_not_raised():
    """异常：chat 抛 LLMError → 捕获回退词表，不向上抛。"""
    llm = _FakeLLM(error=True)
    seq = extract_beats(_DICT_TEXT, llm=llm)
    assert seq == ["盘问", "死亡", "复活"]


@pytest.mark.p0
def test_use_llm_false_pure_dict():
    """开关：use_llm=False → 纯词表，LLM 一次都不被调用。"""
    llm = _FakeLLM(error=True)
    seq = extract_beats(_DICT_TEXT, llm=llm, use_llm=False)
    assert llm.calls == 0
    assert seq == _extract_beat_sequence(_DICT_TEXT)


@pytest.mark.p0
def test_llm_none_pure_dict_backward_compat():
    """llm=None → 纯词表，与旧版 `_extract_beat_sequence` 行为完全一致。"""
    assert extract_beats(_DICT_TEXT) == _extract_beat_sequence(_DICT_TEXT)
    assert extract_beats("") == []


@pytest.mark.p0
def test_llm_empty_beats_no_fallback():
    """LLM 判定正文无节拍（空列表）→ 返回 [] 且不回退词表（模型语义结论被尊重）。"""
    llm = _FakeLLM(reply='{"beats": []}')
    assert extract_beats(_DICT_TEXT, llm=llm) == []
    assert llm.calls == 1
