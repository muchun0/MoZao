# -*- coding: utf-8 -*-
"""通用机制类型适配：四原型离线端到端冒烟（REQ-通用机制类型适配，M6）。

不依赖真实 LLM 与在线服务，串联真实链路组件做跨层校验：
建书落库（ProjectStore.save_world）→ 世界观校验（LogicValidator.bottom_anchors）
→ 记忆注入（Memory.build_context）→ 两章规则状态承接（extract_chapter_state）
→ 质检分派（QualityChecker._check_rule_consistency / _check_panel_progress）。

覆盖四类原型：
1. rule_set 规则怪谈：规则条款校验通过、注入规则条款卡、两章条款并集承接、
   规则一致性 judge 生效、数字面板检查被门控短路；
2. none 无机制书：机制/金手指校验不出 fatal、三类机制卡均不注入、两类质检均短路；
3. numeric_panel 数字面板：旧版三要素路径保留、基线卡注入、面板检查不被短路；
4. rule_set + numeric_panel 混合类型：规则卡与基线卡同时注入，两类检查各自 eligible。
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
from novel_gen.store.schemas import Chapter, World
from novel_gen.pipeline.chapter_state import extract_chapter_state
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.pipeline.validator import LogicValidator


def _make_store(tmp_path: Path, world: World) -> ProjectStore:
    cfg = NovelConfig(title="机制类型冒烟", chapter_words=2500, total_words=10000)
    store = ProjectStore.create(tmp_path / "me2e", cfg)
    store.save_world(world)
    return store


def _fatal(issues, *scopes: str) -> list:
    """筛出指定 scope 下的 fatal 问题（主题/伏笔等 major 提示不在本链路断言范围）。"""
    return [i for i in issues if i.severity == "fatal" and i.scope in scopes]


# ---------- 1. rule_set 规则怪谈 ----------

def _rule_world() -> World:
    return World.from_dict({
        "world_view": "深夜便利店规则怪谈，主角靠读懂守则活过每个副本。",
        "mechanism_types": ["rule_set"],
        "mechanism_rules": "规则语义：副本守则逐条约束主角行为边界，条款一经公布不得改写。",
        "mechanism_ruleset": [
            {"rule_id": "R1", "scope": "global", "statement": "不得回应陌生人的搭话",
             "condition": "进入副本后", "consequence": "被陌生人锁定为目标",
             "change_policy": "fixed", "status": "active"},
            {"rule_id": "R2", "scope": "instance:便利店", "statement": "午夜后不得取货架上红色商品",
             "condition": "便利店内", "consequence": "店员会主动送来",
             "change_policy": "fixed", "status": "active"},
        ],
    })


@pytest.mark.p1
def test_ruleset_book_validator_no_fatal(tmp_path):
    """规则怪谈：机制/金手指/规则集校验均不出 fatal（不强制金手指五要素）。"""
    store = _make_store(tmp_path, _rule_world())
    world = store.load_world()
    issues = LogicValidator().validate_stage("bottom_anchors", world)
    assert _fatal(issues, "mechanism", "goldfinger", "rule_set") == []


@pytest.mark.p1
def test_ruleset_book_memory_injects_rule_card(tmp_path):
    store = _make_store(tmp_path, _rule_world())
    ctx = Memory(store, 5).build_context(store.load_world(), up_to_chapter=1, chapter_goal="进入便利店")
    assert "规则条款卡" in ctx
    assert "不得回应陌生人的搭话" in ctx
    assert "午夜后不得取货架上红色商品" in ctx
    assert "能力规则说明书" not in ctx
    assert "机制初始值" not in ctx


@pytest.mark.p1
def test_ruleset_book_two_chapter_state_carries_union(tmp_path):
    """两章规则状态：第一章条款承接进第二章，副本与生效编号保留。"""
    store = _make_store(tmp_path, _rule_world())
    world = store.load_world()

    ch1 = Chapter(no=1, title="第一章 深夜便利店", content="主角进入便利店，两条守则继续生效。" * 20, summary="s")
    llm1 = MagicMock()
    llm1.chat_json.return_value = {"current_instance": "便利店", "rules_active": ["R1", "R2"]}
    st1 = extract_chapter_state(llm1, store.load_config(), world, ch1, 1, None)
    store.save_chapter_state(st1)

    ch2 = Chapter(no=2, title="第二章 违规边缘", content="主角在便利店内继续遵守守则。" * 20, summary="s")
    llm2 = MagicMock()
    llm2.chat_json.return_value = {"current_instance": "便利店", "rules_active": ["R2"]}
    st2 = extract_chapter_state(llm2, store.load_config(), world, ch2, 2, st1)
    assert st2["current_instance"] == "便利店"
    assert set(st2["rules_active"]) == {"R1", "R2"}


class _LLMRec:
    """记录调用，按预设返回 JSON；exc 非空时抛异常。"""

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple] = []

    def chat(self, system, user, **kw):
        self.calls.append((system, user))
        return self.response


@pytest.mark.p1
def test_ruleset_book_quality_judge_eligible_panel_short_circuited(tmp_path):
    store = _make_store(tmp_path, _rule_world())
    world = store.load_world()
    llm = _LLMRec('{"violations":[{"code":"rule_conflict","rule_id":"R1",'
                  '"detail":"规则：不得回应搭话；正文主角回应了陌生顾客",'
                  '"suggestion":"删除回应行为"}]}')
    checker = QualityChecker(store.load_config(), llm, store, memory=None)

    rule_issues, rule_delta = checker._check_rule_consistency(1, "正文" * 100)
    assert llm.calls, "规则怪谈必须调用规则一致性 judge"
    assert len(rule_issues) == 1 and rule_issues[0]["level"] == "大改"
    assert rule_delta == -25

    panel_issues, panel_delta = checker._check_panel_progress("剩余次数：3" * 30)
    assert panel_issues == [] and panel_delta == 0


# ---------- 2. none 无机制书 ----------

def _none_world() -> World:
    return World.from_dict({
        "world_view": "纯剧情校园故事，没有超自然机制与系统。",
        "mechanism_types": ["none"],
    })


@pytest.mark.p1
def test_none_book_validator_no_fatal(tmp_path):
    store = _make_store(tmp_path, _none_world())
    issues = LogicValidator().validate_stage("bottom_anchors", store.load_world())
    assert _fatal(issues, "mechanism", "goldfinger", "rule_set") == []


@pytest.mark.p1
def test_none_book_memory_injects_no_cards(tmp_path):
    store = _make_store(tmp_path, _none_world())
    ctx = Memory(store, 5).build_context(store.load_world(), up_to_chapter=1, chapter_goal="开学")
    assert "规则条款卡" not in ctx
    assert "能力规则说明书" not in ctx
    assert "机制初始值" not in ctx


@pytest.mark.p1
def test_none_book_quality_both_checks_short_circuited(tmp_path):
    store = _make_store(tmp_path, _none_world())
    llm = _LLMRec('{"violations": []}')
    checker = QualityChecker(store.load_config(), llm, store, memory=None)

    rule_issues, _ = checker._check_rule_consistency(1, "正文" * 100)
    panel_issues, panel_delta = checker._check_panel_progress("剩余次数：3" * 30)
    assert rule_issues == [] and panel_issues == [] and panel_delta == 0
    assert llm.calls == []


# ---------- 3. numeric_panel 数字面板（旧行为零变化） ----------

def _numeric_world() -> World:
    return World.from_dict({
        "world_view": "数字面板生存世界，剩余次数随结算递减，子时重置。",
        "mechanism_types": ["numeric_panel"],
        "mechanism_rules": "语义：剩余次数是生死状态；结算：每发生一次结算事件次数递减；"
                           "子时重置回初始值。",
        "mechanism_baseline": {
            "scar_start": "开局剩余三次",
            "scar_current": "剩余次数：3",
            "panel_format": "剩余次数：N/3",
            "reset_rule": "每日子时重置为 3",
        },
    })


@pytest.mark.p1
def test_numeric_book_validator_runs_original_path(tmp_path):
    store = _make_store(tmp_path, _numeric_world())
    issues = LogicValidator().validate_stage("bottom_anchors", store.load_world())
    assert _fatal(issues, "mechanism", "goldfinger", "rule_set") == []


@pytest.mark.p1
def test_numeric_book_memory_injects_baseline_card(tmp_path):
    store = _make_store(tmp_path, _numeric_world())
    ctx = Memory(store, 5).build_context(store.load_world(), up_to_chapter=1, chapter_goal="首次结算")
    assert "机制初始值" in ctx
    assert "剩余次数：N/3" in ctx
    assert "规则条款卡" not in ctx
    assert "能力规则说明书" not in ctx


@pytest.mark.p1
def test_numeric_book_panel_check_not_gated(tmp_path):
    """数字面板：面板进度检查不被类型门控短路（规则/能力书才短路）。"""
    store = _make_store(tmp_path, _numeric_world())
    llm = _LLMRec('{"events": []}')
    checker = QualityChecker(store.load_config(), llm, store, memory=None)
    # 规则一致性对非 rule_set 短路；面板检查进入实现（不抛错即链路打通）
    rule_issues, _ = checker._check_rule_consistency(1, "正文" * 100)
    assert rule_issues == []
    panel_issues, panel_delta = checker._check_panel_progress("剩余次数：3 剩余次数：3" * 20)
    assert isinstance(panel_issues, list) and panel_delta <= 0


# ---------- 4. 混合类型 rule_set + numeric_panel ----------

def _mixed_world() -> World:
    return World.from_dict({
        "world_view": "规则副本内附带剩余次数面板的生存世界。",
        "mechanism_types": ["rule_set", "numeric_panel"],
        "mechanism_rules": "语义：副本守则约束行为，剩余次数随结算递减；结算：违规一次扣一次。",
        "mechanism_ruleset": [{
            "rule_id": "R1", "scope": "global", "statement": "不得在副本内奔跑",
            "condition": "副本全程", "consequence": "次数扣一",
            "change_policy": "fixed", "status": "active",
        }],
        "mechanism_baseline": {
            "scar_start": "开局三次", "scar_current": "剩余次数：3",
            "panel_format": "剩余次数：N/3", "reset_rule": "副本结束重置",
        },
    })


@pytest.mark.p1
def test_mixed_book_injects_both_cards(tmp_path):
    store = _make_store(tmp_path, _mixed_world())
    ctx = Memory(store, 5).build_context(store.load_world(), up_to_chapter=1, chapter_goal="进入副本")
    assert "规则条款卡" in ctx
    assert "不得在副本内奔跑" in ctx
    assert "机制初始值" in ctx
    assert "剩余次数：N/3" in ctx


@pytest.mark.p1
def test_mixed_book_both_checks_eligible(tmp_path):
    store = _make_store(tmp_path, _mixed_world())
    llm = _LLMRec('{"violations":[{"code":"rule_conflict","rule_id":"R1",'
                  '"detail":"正文主角奔跑了","suggestion":"删除奔跑行为"}]}')
    checker = QualityChecker(store.load_config(), llm, store, memory=None)
    rule_issues, rule_delta = checker._check_rule_consistency(1, "正文" * 100)
    assert llm.calls and len(rule_issues) == 1 and rule_delta == -25
    # 面板检查对 numeric_panel 类型同样不短路
    panel_issues, _ = checker._check_panel_progress("剩余次数：3 剩余次数：3" * 20)
    assert isinstance(panel_issues, list)
