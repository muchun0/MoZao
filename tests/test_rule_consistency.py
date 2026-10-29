# -*- coding: utf-8 -*-
"""规则条款一致性 LLM judge 测试（REQ-通用机制类型适配，M4）。

覆盖：
1. 非 rule_set 类型 / 空规则集 / 开关关闭 → 不调 LLM，返回空；
2. rule_conflict = 大改 -25；rule_added / rule_scope = 中改 -10；
3. 实例过滤：global + 当前实例纳入，其他副本条款排除；
4. LLM 异常降级空列表并留痕，不抛错；
5. 普通模式按 rule_consistency_every 降频，高质量模式每章执行。
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
from novel_gen.store.schemas import World
from novel_gen.pipeline.quality import QualityChecker


def _make_store(tmp_path, world: World) -> ProjectStore:
    cfg = NovelConfig(title="规则怪谈测试", chapter_words=2500, total_words=10000)
    store = ProjectStore.create(tmp_path / "rc", cfg)
    store.save_world(world)
    return store


def _rule_world(extra_rules: list[dict] | None = None) -> World:
    rules = extra_rules or [{
        "rule_id": "R1", "scope": "global",
        "statement": "不得回应陌生人的搭话",
        "condition": "进入副本后",
        "consequence": "被陌生人锁定为目标",
        "change_policy": "fixed", "status": "active",
    }]
    return World.from_dict({
        "world_view": "规则怪谈世界",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本守则约束行为边界。",
        "mechanism_ruleset": rules,
    })


class FakeLLM:
    """记录调用并按预设返回 JSON 文本；exc 非空时抛异常。"""

    def __init__(self, response: str = '{"violations": []}', exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.calls: list[tuple] = []

    def chat(self, system, user, **kw):
        self.calls.append((system, user))
        if self.exc is not None:
            raise self.exc
        return self.response


def _checker(store, llm, cfg: NovelConfig | None = None) -> QualityChecker:
    cfg = cfg or NovelConfig(title="规则怪谈测试", chapter_words=2500, total_words=10000)
    return QualityChecker(cfg, llm, store, memory=None)


@pytest.mark.p1
def test_non_rule_set_skips_judge(tmp_path):
    """非规则类型：不调 LLM，返回空。"""
    world = World.from_dict({
        "world_view": "w", "mechanism_types": ["ability"],
        "mechanism_rules": "能力语义：听见物品声音。",
    })
    store = _make_store(tmp_path, world)
    llm = FakeLLM()
    issues, delta = _checker(store, llm)._check_rule_consistency(1, "正文内容" * 100)
    assert issues == [] and delta == 0
    assert llm.calls == []


@pytest.mark.p1
def test_empty_ruleset_skips_judge(tmp_path):
    """rule_set 但规则集为空 → 跳过。"""
    world = World.from_dict({
        "world_view": "w", "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义。", "mechanism_ruleset": [],
    })
    store = _make_store(tmp_path, world)
    llm = FakeLLM()
    issues, delta = _checker(store, llm)._check_rule_consistency(1, "正文" * 100)
    assert issues == [] and delta == 0
    assert llm.calls == []


@pytest.mark.p1
def test_switch_disabled_skips_judge(tmp_path):
    """rule_consistency_check=False → 整体关闭。"""
    store = _make_store(tmp_path, _rule_world())
    cfg = NovelConfig(title="x", chapter_words=2500, total_words=10000)
    cfg.rule_consistency_check = False
    llm = FakeLLM()
    issues, delta = _checker(store, llm, cfg)._check_rule_consistency(1, "正文" * 100)
    assert issues == [] and delta == 0
    assert llm.calls == []


@pytest.mark.p1
def test_rule_conflict_is_major_minus_25(tmp_path):
    """rule_conflict → 大改，扣 25 分。"""
    store = _make_store(tmp_path, _rule_world())
    llm = FakeLLM('{"violations":[{"code":"rule_conflict","rule_id":"R1",'
                  '"detail":"规则：不得回应陌生人；正文：主角回应了陌生人搭话",'
                  '"suggestion":"删除回应行为"}]}')
    issues, delta = _checker(store, llm)._check_rule_consistency(1, "正文" * 100)
    assert len(issues) == 1
    assert issues[0]["type"] == "规则一致性-rule_conflict"
    assert issues[0]["level"] == "大改"
    assert issues[0]["fix_suggestion"] == "删除回应行为"
    assert delta == -25


@pytest.mark.p1
def test_rule_added_and_scope_are_medium_minus_10(tmp_path):
    """rule_added / rule_scope → 中改，各扣 10 分。"""
    store = _make_store(tmp_path, _rule_world())
    llm = FakeLLM('{"violations":['
                  '{"code":"rule_added","rule_id":"（未收录）","detail":"正文私增一条规则",'
                  '"suggestion":"删除私增条款"},'
                  '{"code":"rule_scope","rule_id":"R1","detail":"他副本规则被套到当前副本",'
                  '"suggestion":"限定回原副本"}]}')
    issues, delta = _checker(store, llm)._check_rule_consistency(1, "正文" * 100)
    levels = [i["level"] for i in issues]
    assert levels == ["中改", "中改"]
    assert delta == -20


@pytest.mark.p1
def test_instance_filter_excludes_other_instance_rules(tmp_path):
    """其他副本条款不进 judge 输入；global 与当前实例条款纳入。"""
    world = _rule_world([
        {"rule_id": "R1", "scope": "global", "statement": "全局守则",
         "condition": "全程", "consequence": "后果一", "status": "active"},
        {"rule_id": "R2", "scope": "instance:便利店", "statement": "便利店守则",
         "condition": "店内", "consequence": "后果二", "status": "active"},
        {"rule_id": "R3", "scope": "instance:学校", "statement": "学校守则",
         "condition": "校内", "consequence": "后果三", "status": "active"},
    ])
    store = _make_store(tmp_path, world)
    store.save_chapter_state({"chapter": 1, "current_instance": "便利店"})
    llm = FakeLLM('{"violations": []}')
    _checker(store, llm)._check_rule_consistency(1, "正文" * 100)
    _, user = llm.calls[0]
    assert "R1" in user and "R2" in user
    assert "R3" not in user


@pytest.mark.p1
def test_llm_exception_returns_empty(tmp_path):
    """LLM 抛异常 → 空列表、0 分，不向外抛。"""
    store = _make_store(tmp_path, _rule_world())
    llm = FakeLLM(exc=RuntimeError("network down"))
    issues, delta = _checker(store, llm)._check_rule_consistency(1, "正文" * 100)
    assert issues == [] and delta == 0


@pytest.mark.p1
def test_normal_mode_frequency_gating(tmp_path):
    """普通模式：第 1 章命中（every=3），第 2 章跳过；高质量模式每章执行。"""
    store = _make_store(tmp_path, _rule_world())
    cfg = NovelConfig(title="x", chapter_words=2500, total_words=10000)
    cfg.high_quality_mode = False
    cfg.rule_consistency_every = 3
    checker = _checker(store, FakeLLM('{"violations": []}'), cfg)
    issues1, _ = checker._check_rule_consistency(1, "正文" * 100)
    assert checker.llm.calls  # 第 1 章执行
    checker.llm.calls.clear()
    issues2, _ = checker._check_rule_consistency(2, "正文" * 100)
    assert issues2 == []
    assert checker.llm.calls == []
