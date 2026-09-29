# -*- coding: utf-8 -*-
"""架构总控 Orchestrator · 阶段门禁（StageGate）—— P1 用例。

覆盖（REQ-多Agent协作分层优化，阶段C）：
1. 产出 → 准出校验 → 不合格自动重生成（≤ retries）
2. 门禁关闭时直接通过
3. 始终不合格 → 超限仲裁放行（记录 arbitrations，不抛错）
4. mock provider 默认跳过门禁
5. 门禁通过时记录非致命问题
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.orchestrator import Orchestrator
from novel_gen.store.schemas import World


class FakeTrace:
    def __init__(self):
        self.steps = []

    def record_agent_step(self, name, extra=None):
        self.steps.append((name, extra))


def _cfg(gate=True, retries=2, provider="deepseek"):
    return SimpleNamespace(stage_gate=gate, stage_gate_retries=retries,
                           llm=SimpleNamespace(provider=provider))


def _bad_world():
    return World.from_dict({"world_view": "w", "mechanism_rules": "机制",
                            "foreshadowing": [{"name": "谜", "buried_ch": 1, "reveal_ch": None}]})


def _good_world():
    return World.from_dict({"world_view": "w",
                            "mechanism_rules": "机制语义：可死次数=今日剩余复活次数；"
                                               "结算规则：每死一次减1，每日子时重置为3；"
                                               "代价终局：次数用尽即死，蚀痕进度随累计死亡递增",
                            "theme_proposition": "t",
                            "goldfinger_rules": {"trigger": "t", "upper_limit": "u",
                                                 "lower_limit": "l", "cost": "c", "growth_path": "g"},
                            "foreshadowing": [{"name": "谜", "buried_ch": 1, "reveal_ch": 50, "level": "large"}]})


@pytest.mark.p1
def test_stage_gate_regenerate_then_pass():
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        return _bad_world() if calls["n"] == 1 else _good_world()

    invalidated = []
    o = Orchestrator(_cfg(retries=2), None, None, trace=FakeTrace())
    res = o.run_stage("bottom_anchors", produce, invalidate=lambda: invalidated.append(1))
    assert res.goldfinger_rules.is_complete()
    assert calls["n"] == 2
    assert len(invalidated) == 1


@pytest.mark.p1
def test_stage_gate_disabled_passes_immediately():
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        return _bad_world()

    o = Orchestrator(_cfg(gate=False), None, None)
    res = o.run_stage("bottom_anchors", produce, invalidate=lambda: None)
    assert calls["n"] == 1
    assert res is not None


@pytest.mark.p1
def test_stage_gate_arbitrate_after_retries():
    o = Orchestrator(_cfg(retries=2), None, None, trace=FakeTrace())
    res = o.run_stage("bottom_anchors", _bad_world, invalidate=lambda: None)
    assert res is not None
    assert o.arbitrations, "超限应记录仲裁"
    assert o.arbitrations[0]["stage"] == "bottom_anchors"
    assert o.arbitrations[0]["fatal"]


@pytest.mark.p1
def test_stage_gate_skipped_for_mock_provider():
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        return _bad_world()

    o = Orchestrator(_cfg(provider="mock"), None, None)
    o.run_stage("bottom_anchors", produce, invalidate=lambda: None)
    assert calls["n"] == 1, "mock provider 应跳过门禁（保持离线生成行为一致）"


@pytest.mark.p1
def test_stage_gate_records_non_fatal_on_pass():
    o = Orchestrator(_cfg(retries=2), None, None, trace=FakeTrace())
    res = o.run_stage("bottom_anchors", _good_world, invalidate=lambda: None)
    assert res.goldfinger_rules.is_complete()
    assert o.arbitrations == []
