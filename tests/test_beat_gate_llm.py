# -*- coding: utf-8 -*-
"""REQ-词表换LLM 批次一：beat_gate.py LLM 语义判定测试（@pytest.mark.p1）。

成本纪律：整章只调 1 次 LLM 判所有拍，禁止每拍 1 次。
覆盖：
- ①正常：FakeLLM 返回与 planned_beats 对齐的 JSON → planned 条目直接采用 LLM 的
  covered/hit/miss（整章仅 1 次调用）；
- ②边界：LLM 返回长度与计划拍不等 → 整体回退词表逐拍判定且 logger.warning；
- ③异常：chat 抛 LLMError → 回退词表不抛；
- ④开关：use_llm=False → 纯词表（根本不调 LLM）；
- ⑤存量行为：llm=None 时输出与改造前完全一致（pass/warn/fail/skip 语义不变）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.llm.base import LLMError
from novel_gen.pipeline.beat_gate import compute_beat_coverage

#: 经典三拍（存量用例同款），用于 ⑤ 验证 llm=None 行为完全不变
_MOVES = "主角在墟口遭遇灰卒被咬死→复活后用铁锤砸碎灰卒核心→抱着小九回到铁匠铺"

#: 词表无对应动作/修饰词的两拍：_extract_slots 三类槽全空 → 词表保守判 miss。
#: 用来证明 LLM 结果被真实采用（词表下 coverage=0/fail，LLM 判覆盖则 coverage=1/pass）。
_MOVES_LLM = "他登上高塔→眺望远方灯火"
_CONTENT_LLM = "他一步步登上高塔顶端，眺望远方的万家灯火。"


class FakeLLM:
    """模拟 LLM 客户端：chat() 返回预设 JSON 字符串，可注入异常并计数调用次数。"""

    def __init__(self, reply: str | None = None, error: Exception | None = None):
        self._reply = reply
        self._error = error
        self.calls = 0
        self.last_kwargs: dict | None = None

    def chat(self, system, user, temperature=None, max_tokens=None, json_mode=None):
        self.calls += 1
        self.last_kwargs = {
            "temperature": temperature, "max_tokens": max_tokens, "json_mode": json_mode,
        }
        if self._error is not None:
            raise self._error
        return self._reply


@pytest.mark.p1
def test_llm_normal_adopts_aligned_result():
    """①正常：LLM 返回对齐 JSON → planned 采用其 covered/hit/miss，整章仅调 1 次。"""
    fake = FakeLLM(
        reply='{"beats":[{"covered":true,"hit":["登上"],"miss":[]},'
               '{"covered":true,"hit":["灯火"],"miss":[]}]}'
    )
    r = compute_beat_coverage(_CONTENT_LLM, _MOVES_LLM, llm=fake)
    # 成本纪律：整章只调 1 次 LLM，禁止每拍 1 次
    assert fake.calls == 1
    assert r["coverage"] == 1.0
    assert r["verdict"] == "pass"
    assert r["missing"] == []
    # 直接采用 LLM 的 covered/hit/miss（hit 不带词表的 "[动作]" 前缀）
    assert r["planned"][0]["covered"] is True
    assert r["planned"][0]["hit"] == ["登上"]
    assert r["planned"][1]["miss"] == []


@pytest.mark.p1
def test_llm_length_mismatch_falls_back_to_wordlist(caplog):
    """②边界：LLM 返回长度与计划拍不等 → 回退词表逐拍判定且 logger.warning。"""
    # 两拍只回了 1 条 → 对齐不符 → _llm_beat_coverage 返回 None
    fake = FakeLLM(reply='{"beats":[{"covered":true,"hit":[],"miss":[]}]}')
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.beat_gate"):
        r = compute_beat_coverage(_CONTENT_LLM, _MOVES_LLM, llm=fake)
    # 回退词表：两拍三类槽全空 → 全 miss → coverage=0.0 / fail
    assert r["coverage"] == 0.0
    assert r["verdict"] == "fail"
    assert len(r["missing"]) == 2
    assert any("回退词表" in rec.message for rec in caplog.records)


@pytest.mark.p1
def test_llm_exception_falls_back_without_raising(caplog):
    """③异常：chat 抛 LLMError → 回退词表不抛。"""
    fake = FakeLLM(error=LLMError("boom"))
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.beat_gate"):
        r = compute_beat_coverage(_CONTENT_LLM, _MOVES_LLM, llm=fake)
    assert r["coverage"] == 0.0
    assert r["verdict"] == "fail"
    assert any("回退词表" in rec.message for rec in caplog.records)


@pytest.mark.p1
def test_use_llm_false_pure_wordlist():
    """④开关：use_llm=False → 纯词表，根本不调 LLM。"""
    fake = FakeLLM(reply='{"beats":[{"covered":true,"hit":[],"miss":[]}]}')
    r = compute_beat_coverage(_CONTENT_LLM, _MOVES_LLM, llm=fake, use_llm=False)
    assert fake.calls == 0
    assert r["coverage"] == 0.0
    assert r["verdict"] == "fail"


@pytest.mark.p1
def test_llm_none_behavior_unchanged():
    """⑤存量行为：llm=None 时 pass/warn/fail/skip 语义与改造前完全一致。"""
    # pass（2/3 拍覆盖，语义槽"走回"归一到"回到"）
    content_pass = ("陆沉在墟口拾荒时突然遭遇灰卒，被咬喉致死。随后他复活过来，"
                    "拿起铁锤狠狠砸碎了灰卒的核心。之后他抱着小九一路走回铁匠铺。")
    r_pass = compute_beat_coverage(content_pass, _MOVES)
    assert r_pass["coverage"] == pytest.approx(1.0, abs=0.02)
    assert r_pass["verdict"] == "pass"
    # 词表路径 hit 带 "[动作]" 前缀（区别于 LLM 直接采用的形式）
    assert any(str(h).startswith("[动作]") for h in r_pass["planned"][0]["hit"])
    # fail（只沾第 1 拍边）
    r_fail = compute_beat_coverage("陆沉在墟口拾荒，灰卒从暗处扑出来。", _MOVES)
    assert r_fail["coverage"] < 0.4
    assert r_fail["verdict"] == "fail"
    assert len(r_fail["missing"]) >= 1
    # skip（无计划拍）
    r_skip = compute_beat_coverage("随便一段正文", "", "")
    assert r_skip["verdict"] == "skip"
    assert r_skip["coverage"] is None
