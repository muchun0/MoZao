# -*- coding: utf-8 -*-
"""机制卡三要素 LLM 语义判定（REQ-词表换LLM 批次一）—— P1 用例。

覆盖：
1. 正常：LLM 返回 {"missing":[某固定文案]} → 只报该要素缺失，desc 与词表路径一致
2. 正常：LLM 返回 {"missing":[]} → 无 issue（不回退词表）
3. 边界：missing 含非固定文案 → 丢弃只留固定文案；dict 空 / missing 非 list → 回退词表且 warning
4. 异常：chat 抛 LLMError → 回退词表不抛
5. 开关：LogicValidator() 无参 → 纯词表（旧行为）；llm_semantic=False → 纯词表不调 LLM
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.llm.base import LLMClient, LLMError
from novel_gen.pipeline.validator import (
    LogicValidator,
    _MISSING_END,
    _MISSING_SEMANTIC,
    _MISSING_SETTLE,
)
from novel_gen.store.schemas import World

#: 词表路径下三要素全缺的最简机制卡（不含任何信号词）
_EMPTY_MECHANISM = "复活机制"
#: 词表路径下三要素全齐的机制卡（故意含全部信号词，词表兜底应报 0 缺失）
_FULL_MECHANISM = (
    "复活次数：语义上是死亡累计次数的状态面板进度。"
    "每死一次结算递减增恢复重置，减一恢复。"
    "代价成长封顶用尽永久，上限终局触发。"
)


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


def _world(mechanism):
    return World.from_dict({"world_view": "w", "mechanism_rules": mechanism})


@pytest.mark.p1
def test_llm_missing_one_desc_matches_vocab_path():
    """LLM 报「结算规则」缺失 → 只报该要素，desc 文案与词表固定文案一致。"""
    llm = FakeLLM(response='{"missing": ["结算规则（何时增减/重置/与历史衔接）"]}')
    v = LogicValidator(llm=llm)
    issues = v._check_mechanism(_world(_FULL_MECHANISM))
    assert len(llm.calls) == 1
    assert len(issues) == 1
    item = issues[0]
    assert item.severity == "fatal" and item.scope == "mechanism"
    assert _MISSING_SETTLE in item.desc
    assert _MISSING_SEMANTIC not in item.desc
    assert _MISSING_END not in item.desc


@pytest.mark.p1
def test_llm_missing_empty_list_no_issue():
    """LLM 判定三要素齐全（[]）→ 无 issue，且不回退词表。"""
    llm = FakeLLM(response='{"missing": []}')
    v = LogicValidator(llm=llm)
    # 故意用词表全缺的机制卡：若回退词表会报 3 缺失；LLM 路径下应 0 issue
    issues = v._check_mechanism(_world(_EMPTY_MECHANISM))
    assert len(llm.calls) == 1
    assert issues == []


@pytest.mark.p1
def test_llm_missing_non_fixed_text_dropped():
    """missing 含非固定文案 → 丢弃，只保留固定文案且去重。"""
    llm = FakeLLM(response=(
        '{"missing": ["随便写的要素", "结算规则（何时增减/重置/与历史衔接）",'
        ' "结算规则（何时增减/重置/与历史衔接）", " 代价与终局（代价递增/上限/触发） "]}'
    ))
    v = LogicValidator(llm=llm)
    issues = v._check_mechanism(_world(_FULL_MECHANISM))
    assert len(issues) == 1
    assert _MISSING_SETTLE in issues[0].desc
    assert _MISSING_END in issues[0].desc
    assert _MISSING_SEMANTIC not in issues[0].desc
    assert "随便写的" not in issues[0].desc


@pytest.mark.p1
def test_llm_empty_dict_falls_back_to_vocab(caplog):
    """LLM 返回空 dict → 回退词表并留 warning。"""
    llm = FakeLLM(response="{}")
    v = LogicValidator(llm=llm)
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.validator"):
        issues = v._check_mechanism(_world(_EMPTY_MECHANISM))
    assert len(issues) == 1
    assert _MISSING_SEMANTIC in issues[0].desc
    assert _MISSING_SETTLE in issues[0].desc
    assert _MISSING_END in issues[0].desc
    assert any("回退词表" in r.message for r in caplog.records)


@pytest.mark.p1
def test_llm_missing_not_list_falls_back(caplog):
    """missing 非 list（字符串）→ 回退词表并留 warning。"""
    llm = FakeLLM(response='{"missing": "结算规则"}')
    v = LogicValidator(llm=llm)
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.validator"):
        issues = v._check_mechanism(_world(_EMPTY_MECHANISM))
    assert len(issues) == 1
    assert any("回退词表" in r.message for r in caplog.records)


@pytest.mark.p1
def test_llm_chat_exception_falls_back(caplog):
    """chat 抛 LLMError → 回退词表，不向外抛。"""
    llm = FakeLLM(exc=LLMError("network down"))
    v = LogicValidator(llm=llm)
    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.validator"):
        issues = v._check_mechanism(_world(_EMPTY_MECHANISM))
    assert len(issues) == 1
    assert _MISSING_SEMANTIC in issues[0].desc


@pytest.mark.p1
def test_default_constructor_is_pure_vocab():
    """无参构造 → 纯词表（与改造前行为一致）。"""
    v = LogicValidator()
    issues = v._check_mechanism(_world(_EMPTY_MECHANISM))
    assert len(issues) == 1
    assert _MISSING_SEMANTIC in issues[0].desc


@pytest.mark.p1
def test_llm_semantic_false_is_pure_vocab_no_llm_call():
    """llm_semantic=False → 纯词表，完全不调 LLM。"""
    llm = FakeLLM(response='{"missing": []}')
    v = LogicValidator(llm=llm, llm_semantic=False)
    issues = v._check_mechanism(_world(_EMPTY_MECHANISM))
    assert llm.calls == []
    assert len(issues) == 1
    assert _MISSING_SEMANTIC in issues[0].desc


@pytest.mark.p1
def test_empty_mechanism_skips_llm():
    """空机制卡 → 直接返回，不调 LLM。"""
    llm = FakeLLM(response='{"missing": []}')
    v = LogicValidator(llm=llm)
    assert v._check_mechanism(_world("")) == []
    assert llm.calls == []
