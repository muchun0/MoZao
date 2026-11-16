# -*- coding: utf-8 -*-
"""创作阶段审阅门 M0 测试（REQ-创作阶段审阅门 §5 M0）。

覆盖：门状态机 / 审批绑版本与下游失效 / world 门依赖与跳过 / fail-open / 判断材料。
全部为纯函数级用例（不起线程、不调 LLM）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, ChapterPlan, Character, Outline, World
from webapp import gates


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="门测试", total_words=20000, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _outline(n: int = 8, fill: bool = True) -> Outline:
    chapters = []
    for i in range(1, n + 1):
        kw = dict(core_event=f"事件{i}", conflict="冲突", character_moves="主角被围堵后脱身",
                  cliffhanger="钩子", volume_no=1) if fill else {}
        chapters.append(ChapterPlan(no=i, title=f"第{i}章", **kw))
    return Outline(title="门测试", chapters=chapters)


def _world() -> World:
    return World(world_view="世界观正文", characters=[Character(name="主角")])


def _err_code(fn, *args, **kwargs) -> int:
    with pytest.raises(HTTPException) as ei:
        fn(*args, **kwargs)
    return ei.value.status_code


# ---------- 状态机 ----------

@pytest.mark.p1
def test_default_all_pending(store):
    states = gates.compute(store)
    assert set(states) == {"outline", "world", "chapter_plan"}
    assert all(s["status"] == "pending" for s in states.values())
    assert states["outline"]["current_version"] is None


@pytest.mark.p1
def test_approve_without_product_400(store):
    assert _err_code(gates.approve, store, "outline", 1) == 400


@pytest.mark.p0
def test_approve_binds_version_and_invalidates_on_change(store):
    """审批绑版本：产物一变，本门与下游全回 pending（本需求安全性地基）。"""
    store.save_outline(_outline(), commit=True, source="ai")
    states = gates.approve(store, "outline", 1)
    assert states["outline"]["status"] == "approved"
    assert states["outline"]["approved_version"] == states["outline"]["current_version"]

    # 用户编辑大纲 → 新版本 → 审批失效并落盘
    store.save_outline(_outline(n=9), commit=True, source="user")
    states = gates.compute(store)
    assert states["outline"]["status"] == "pending"
    assert states["world"]["status"] == "pending"
    assert states["chapter_plan"]["status"] == "pending"


@pytest.mark.p0
def test_downstream_requires_upstream(store):
    """门 2 未通过门 1 前不可通过（409）；通过门 1 后放行。"""
    store.save_outline(_outline(), commit=True, source="ai")
    store.save_world(_world(), commit=True, source="ai")
    assert _err_code(gates.approve, store, "world", 1) == 409
    gates.approve(store, "outline", 1)
    states = gates.approve(store, "world", 1)
    assert states["world"]["status"] == "approved"


@pytest.mark.p1
def test_world_skip_allowed_after_outline(store):
    """§8#5：允许跳过世界观门，跳过后下游可继续（status=skipped）。"""
    store.save_outline(_outline(), commit=True, source="ai")
    store.save_world(_world(), commit=True, source="ai")
    gates.approve(store, "outline", 1)
    states = gates.approve(store, "world", 1, skip=True)
    assert states["world"]["status"] == "skipped"
    # 章纲门 = 绑大纲版本，上游 skipped 视为放行
    states = gates.approve(store, "chapter_plan", 1)
    assert states["chapter_plan"]["status"] == "approved"


@pytest.mark.p1
def test_skip_only_for_world(store):
    store.save_outline(_outline(), commit=True, source="ai")
    assert _err_code(gates.approve, store, "outline", 1, skip=True) == 400


@pytest.mark.p1
def test_gates_file_corrupt_fail_open(store):
    """gates.json 损坏 → 全 pending 且不抛异常（与 current_task_progress 同口径）。"""
    gates.gate_path(store).write_text("{not json", encoding="utf-8")
    states = gates.compute(store)
    assert all(s["status"] == "pending" for s in states.values())


@pytest.mark.p1
def test_guard_agent_world_needs_outline_gate(store):
    """生成入口：门 1 未通过 → 世界观生成 409；通过后放行；其他 agent 不受影响。"""
    store.save_outline(_outline(), commit=True, source="ai")
    assert _err_code(gates.guard_agent, store, "architect.world") == 409
    gates.guard_agent(store, "architect.outline")  # 大纲自身不受门限制
    gates.approve(store, "outline", 1)
    gates.guard_agent(store, "architect.world")


@pytest.mark.p1
def test_guard_agent_legacy_project_exempt(store):
    """存量老书（已有正文、从无 gates.json）不被新门禁锁死：放行但记 warning。"""
    store.save_outline(_outline(), commit=True, source="ai")
    store.save_chapter(Chapter(no=1, title="第1章", content="正文" * 20), source="ai")
    assert gates.compute(store)["outline"]["status"] == "pending"
    gates.guard_agent(store, "architect.world")      # 存量豁免，不抛 409

    # 一旦 gates.json 出现（用户进入新流程），豁免立即失效
    gates.gate_path(store).write_text("{}", encoding="utf-8")
    assert _err_code(gates.guard_agent, store, "architect.world") == 409


# ---------- 判断材料 ----------

@pytest.mark.p1
def test_outline_review_flags_missing_fields(store):
    store.save_outline(_outline(fill=False), commit=True, source="ai")
    review = gates.outline_review(store)
    titles = " ".join(c["title"] for c in review["checks"])
    assert "核心事件" in titles
    assert review["summary"], "应有章节/分卷摘要"
    assert review["checks"][0]["anchor"].startswith("chapter:")


@pytest.mark.p1
def test_outline_review_empty(store):
    review = gates.outline_review(store)
    assert review["checks"][0]["level"] == "error"


@pytest.mark.p1
def test_world_review_empty_world(store):
    review = gates.world_review(store)
    assert review["checks"][0]["title"] == "世界观为空"


@pytest.mark.p1
def test_world_review_uncovered_chapters_locatable(store):
    """门 2 的「受影响章纲清单」：章纲没出现任何已建档人物 → 可定位到具体章。"""
    outline = _outline()
    for chapter in outline.chapters:
        chapter.character_moves = "守卫被围堵后脱身"   # 不含已建档人物「主角」
    store.save_outline(outline, commit=True, source="ai")
    store.save_world(_world(), commit=True, source="ai")   # 人物档案只有「主角」
    review = gates.world_review(store)
    checks = [c for c in review["checks"] if "未出现任何已建档人物" in c["title"]]
    assert checks and checks[0]["anchor"] == "chapter:1"


@pytest.mark.p1
def test_batch_review_counts_words_and_charge(store):
    store.save_chapter(Chapter(no=1, title="第1章", content="正文" * 30), source="ai")
    review = gates.batch_review(store, 1, 2)
    summary = {s["label"]: s["value"] for s in review["summary"]}
    assert summary["本批章数"] == "1 章"
    assert "60" in summary["本批字数"]
    assert summary["本批扣费"].startswith("¥")


@pytest.mark.p1
def test_review_unknown_gate_400(store):
    assert _err_code(gates.review, store, "unknown") == 400
