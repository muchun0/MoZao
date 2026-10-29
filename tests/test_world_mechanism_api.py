"""世界观接口 · 机制类型数值呈现测试（fix/world-mechanism-initial-display，P1）。

背景：世界观编辑器只能按 mechanism_baseline（数字面板专用字段）渲染，
`GET /api/projects/{id}/world` 组装的字段清单里既没有 `mechanism_types` 也没有
`mechanism_ruleset`，导致 rule_set / power_level 等类型的书「机制初始值」永远为空，
界面只剩旧文案占位提示（如"如：约一指宽""如：今日可死次数：N/3"）。

覆盖：
- GET 下发七类机制各自的数值承载字段（ old / new 数据都不报错）
- PUT 增量更新类型标签与条款（非法/空项不落库），回读一致
- 空壳子卡（全空 gy / power_system）不写回，避免污染既有设定
- world 缺失时返回含新字段的空骨架
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig  # noqa: E402
from novel_gen.store.project import ProjectStore  # noqa: E402
from webapp.routers import projects_world as pw  # noqa: E402


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="机制测试书", total_words=10000, chapter_words=2500, genre="规则怪谈")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def routed(store, monkeypatch):
    """路由依赖打桩：鉴权 noop + _find_project 返回临时 store（沿用 stage1C 测试口径）。"""
    monkeypatch.setattr(pw, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pw, "_find_project", lambda pid: store)
    return store


def _rule_set_payload() -> dict:
    return {
        "world_view": "规则怪谈世界",
        "mechanism_types": ["rule_set", "未知类型"],
        "mechanism_ruleset": [
            {"rule_id": "R1", "scope": "instance:旧城", "statement": "夜间不得回应敲门声",
             "condition": "天黑后", "consequence": "被替换", "change_policy": "fixed",
             "first_ch": 1, "status": "active"},
            {"rule_id": "R2", "scope": "global", "statement": "   ", },  # 空条款应被丢弃
        ],
    }


@pytest.mark.p1
def test_get_world_returns_mechanism_fields_when_empty(store, routed):
    """world 尚不存在：返回含机制新字段的空骨架，前端不会因 undefined 白屏。"""
    store.save_world.__self__  # noqa: B018  确认 store 可用
    body = pw.project_world(project_id="x", user={})
    for key in ("mechanism_types", "mechanism_ruleset", "goldfinger_rules",
                "power_system", "mechanism_rules"):
        assert key in body
    assert body["mechanism_types"] == [] and body["mechanism_ruleset"] == []


@pytest.mark.p1
def test_rule_set_world_persists_and_reads_back(store, routed):
    """规则约束型书：类型标签 + 条款清单经 PUT 落盘，GET 回读一致（不再是占位文案）。"""
    res = pw.update_world(project_id="x", payload=_rule_set_payload(), user={})
    # 非法类型被剔除，合法类型保留
    assert res["mechanism_types"] == ["rule_set"]
    assert len(res["mechanism_ruleset"]) == 1
    rule = res["mechanism_ruleset"][0]
    assert rule["rule_id"] == "R1" and rule["statement"].startswith("夜间")

    listed = pw.project_world(project_id="x", user={})
    assert listed["mechanism_types"] == ["rule_set"]
    assert listed["mechanism_ruleset"][0]["scope"] == "instance:旧城"
    assert listed["mechanism_ruleset"][0]["consequence"] == "被替换"

    world = store.load_world()
    assert world.mechanism_types == ["rule_set"]
    assert len(world.mechanism_ruleset) == 1


@pytest.mark.p1
def test_legacy_numeric_panel_world_fallback(store, routed):
    """旧书（无 mechanism_types、只有数字面板字段）：数值照常下发，接口不报错。"""
    pw.update_world(project_id="x", payload={
        "world_view": "旧结构书",
        "mechanism_baseline": {"scar_start": "手腕", "scar_current": "累计约二十次",
                              "panel_format": "今日可死次数：N/3", "reset_rule": "每日子时重置为3"},
    }, user={})
    listed = pw.project_world(project_id="x", user={})
    assert listed["mechanism_types"] == []           # 未显式声明：为空，由前端推断
    assert listed["mechanism_baseline"]["scar_current"] == "累计约二十次"
    assert listed["mechanism_ruleset"] == []


@pytest.mark.p1
def test_empty_sub_card_not_written(store, routed):
    """全空的子卡不回写：防止空壳 goldfinger/power_system 触发"五要素缺失"误报。"""
    res = pw.update_world(project_id="x", payload={
        "world_view": "空壳测试", "goldfinger_rules": {"trigger": "", "upper_limit": ""},
        "power_system": {"levels": [], "protagonist_level": ""},
    }, user={})
    # 未写入本体，只下发全空的字段骨架（前端 v-model 绑定不缺键）
    assert not any(res["goldfinger_rules"].values())
    assert res["power_system"]["levels"] == [] and res["power_system"]["protagonist_level"] == ""
    assert store.load_world().goldfinger_rules is None
    assert store.load_world().power_system is None

    # 有内容则正常写入并按字典回读
    res2 = pw.update_world(project_id="x", payload={
        "world_view": "空壳测试",
        "power_system": {"levels": [{"name": "炼气", "range": "1-9 重"}],
                         "protagonist_level": "炼气三层"},
    }, user={})
    assert res2["power_system"]["protagonist_level"] == "炼气三层"
    assert res2["power_system"]["levels"][0]["name"] == "炼气"


@pytest.mark.p1
def test_empty_types_entity_keeps_declared_types(store, routed):
    """前端未携带 mechanism_types（如旧前端）时不清空已声明类型。"""
    pw.update_world(project_id="x", payload=_rule_set_payload(), user={})
    pw.update_world(project_id="x", payload={"world_view": "再次保存"}, user={})
    assert pw.project_world(project_id="x", user={})["mechanism_types"] == ["rule_set"]


# ---------- 类型判定口径：后端下发权威结果，前端不得自行推断（QA 分叉回归） ----------

@pytest.mark.p1
def test_resolved_types_matches_resolve_types(store, routed):
    """`mechanism_types_resolved` 必须恒等于 `resolve_types(world)`。

    守护口径唯一：前端一律以该字段分派，不再在前端重算判定（含中文别名归一与
    旧书推断），避免前后端两套词表/两套推断规则漂移。
    """
    from novel_gen.pipeline.mechanism_types import resolve_types

    cases = [
        {},                                                              # 空 world → none
        {"mechanism_types": ["规则怪谈"]},                                # 中文别名 → rule_set
        {"mechanism_types": ["火星机制"]},                                # 全非法 → 推断 → none
        {"mechanism_baseline": {"panel_format": "今日可死次数：N/3"}},     # 推断 numeric_panel
        {"mechanism_baseline": {"scar_end": "蔓延至心脉"}},                # 只填非判定字段 → none
        {"mechanism_types": ["ability"],
         "mechanism_baseline": {"panel_format": "x"}},                    # 显式声明优先
        {"goldfinger_rules": {"trigger": "接触", "upper_limit": "三次"}},  # 推断 ability
        {"power_system": {"levels": [{"name": "炼气"}], "protagonist_level": "炼气三层"}},
    ]
    for i, payload in enumerate(cases):
        pw.update_world(project_id="x", payload={"world_view": f"用例{i}", **payload}, user={})
        body = pw.project_world(project_id="x", user={})
        expected = resolve_types(store.load_world())
        assert body["mechanism_types_resolved"] == expected, f"用例{i} 口径分叉: {payload}"
        # 下发的 resolved 必须是合法枚举值，且 none 不与其他类型叠加
        assert body["mechanism_types_resolved"]
        assert not ("none" in body["mechanism_types_resolved"]
                    and len(body["mechanism_types_resolved"]) > 1)


@pytest.mark.p1
def test_resolved_types_sent_on_put_too(store, routed):
    """PUT 响应同样下发 resolved：保存后前端能立刻按权威口径重渲染，不用等下次 GET。"""
    res = pw.update_world(project_id="x", payload={"world_view": "别名书",
                                                   "mechanism_types": ["规则怪谈"]}, user={})
    assert res["mechanism_types_resolved"] == ["rule_set"]
    # 落库即归一化：中文别名经 normalize_types 清洗后写回，物与 resolved 一致，
    # 不再出现"库里存别名、运行时归一"的两套数据
    assert res["mechanism_types"] == ["rule_set"]


@pytest.mark.p1
def test_empty_type_list_means_undeclared_not_clear(store, routed):
    """显式传空数组 / 全非法值 / null = 「未声明」，不得清空已声明类型。

    守护 `update_world` 里的 `if declared:` 守卫：一旦把空列表当成「清空」，旧前端
    （不带机制字段、或回传全非法值）每次保存都会把已声明类型抹掉，运行时分派退回
    「按字段推断」，与用户/LLM 的显式声明不一致。上一版用例只覆盖了「不带该键」，
    覆盖不到这条守卫，故补本例。
    """
    pw.update_world(project_id="x", payload=_rule_set_payload(), user={})
    for raw in ([], ["火星机制"], None):
        res = pw.update_world(project_id="x", payload={"world_view": "再次保存",
                                                       "mechanism_types": raw}, user={})
        assert res["mechanism_types"] == ["rule_set"], f"入参 {raw!r} 不应清空已声明类型"
    assert store.load_world().mechanism_types == ["rule_set"]
