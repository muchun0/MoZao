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
