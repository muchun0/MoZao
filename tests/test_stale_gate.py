# -*- coding: utf-8 -*-
"""档3 生成前一致性门禁 + 上下文标注 + 人物状态过滤 —— P1/P0 混合。

为什么大多标 P1：门禁本身是「提示性拦截」，不改结算金额与幂等基线；
但插入位置在扣费/占槽之前属于资金链路边界，故「拦截发生在占槽之前」标 P0。
全部用临时 ProjectStore，不连 MySQL、不启动 HTTP、不调 LLM。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                                   # noqa: E402
from novel_gen.pipeline import chapter_staleness as st                     # noqa: E402
from novel_gen.pipeline import stale_guard as sg                           # noqa: E402
from novel_gen.pipeline.memory import Memory                               # noqa: E402
from novel_gen.store.project import ProjectStore                           # noqa: E402
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline          # noqa: E402
from novel_gen.store.versioned import VersionedProduct                     # noqa: E402
from webapp import core                                                    # noqa: E402
from webapp import db                                                      # noqa: E402
from webapp import generate_service as gs                                  # noqa: E402


def _make_book(tmp_path, chapters: int = 12) -> ProjectStore:
    cfg = NovelConfig(title="门禁测试书", total_words=20000,
                      chapter_words=2000, genre="玄幻")
    store = ProjectStore.create(tmp_path / "p", cfg)
    outline = Outline(title="门禁测试书", intro="简介",
                      chapters=[ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}")
                                for i in range(1, chapters + 1)])
    VersionedProduct(store, "world").commit("all", {"intro": "世界观v1"})
    # 先登记大纲 v1（版本库），再写完整大纲文件：
    # commit 只接受最小 payload，save_outline 负责把含章节的完整结构落到 outline.json。
    VersionedProduct(store, "outline").commit("all", {"intro": "大纲v1"})
    store.save_outline(outline)
    return store


def _write_chapters(store, nos) -> None:
    """模拟档2生成：记录前文各章依赖（v1）。"""
    for no in nos:
        prev = [{"ch": k, "v": 1} for k in range(1, no)]
        store.save_chapter(Chapter(no=no, title=f"第{no}章",
                                   content=f"第{no}章正文。"), prev_chapters=prev)


@pytest.fixture
def rewritten_book(tmp_path):
    """1–10 章写好后重写第 3 章 → 4–10 章 stale（成因 prev_chapter）。"""
    store = _make_book(tmp_path)
    _write_chapters(store, range(1, 11))
    store.save_chapter(Chapter(no=3, title="第3章", content="第3章新版正文。"),
                       prev_chapters=[])
    data = st.build_chapter_staleness(store)
    assert data["summary"]["stale_chapters"] == list(range(4, 11))
    return store


# ---------------------------------------------------------------- 门禁判定

@pytest.mark.p1
def test_gate_blocks_older_stale_not_in_targets(rewritten_book):
    """①过期章早于目标且不在本次范围内 → 409 内容（blocked + 区间/成因/出口）。"""
    verdict = sg.guard_staleness(
        rewritten_book, {"scope": "single", "ch_no": 10})

    assert verdict["blocked"] is True
    detail = verdict["detail"]
    assert detail["stale_chapters"] == list(range(4, 10))
    assert detail["ranges"] == [[4, 9]]
    assert detail["regenerate_from"] == 4
    assert detail["options"] == ["continue", "rewrite_from"]
    # 上游维度没变，causes 为空；前文重写成因走 prev_rewrites（与档2 summary 同源）
    assert detail["causes"] == []
    assert detail["prev_rewrites"][0]["ch"] == 3
    assert detail["prev_rewrites"][0]["affected"] == list(range(4, 10))
    assert detail["prev_rewrites"][0]["direction"] == "rewritten"


@pytest.mark.p1
def test_gate_allows_rewrite_from_stale_chapter(rewritten_book):
    """②从过期章起向后重写（T 覆盖 S）→ 放行。"""
    verdict = sg.guard_staleness(
        rewritten_book, {"scope": "range", "from_ch": 4, "to_ch": 10})
    assert verdict["blocked"] is False

    # 只重写第 10 章（T={10}）仍会被拦：9 及以前不在范围内
    verdict = sg.guard_staleness(
        rewritten_book, {"scope": "range", "from_ch": 10, "to_ch": 10})
    assert verdict["blocked"] is True


@pytest.mark.p1
def test_gate_never_blocks_unknown(tmp_path):
    """③unknown（存量章无依赖记录）→ 只提示不拦截。"""
    store = _make_book(tmp_path, chapters=3)
    store.save_chapter(Chapter(no=1, title="第1章", content="旧正文。"))  # 无 prev
    data = st.build_chapter_staleness(store)
    assert data["summary"]["unknown_chapters"] == [1]
    assert data["summary"]["stale_chapters"] == []

    verdict = sg.guard_staleness(store, {"scope": "single", "ch_no": 2})
    assert verdict["blocked"] is False


@pytest.mark.p1
def test_allow_inconsistent_passes_and_marks_upstream(rewritten_book):
    """④allow_inconsistent=true → 放行，并把过期章交给上游标注/落痕。"""
    verdict = sg.guard_staleness(
        rewritten_book, {"scope": "single", "ch_no": 10},
        allow_inconsistent=True)
    assert verdict["blocked"] is False
    assert verdict["unstable_chapters"] == list(range(4, 11))

    # 写章落盘：upstream.unstable=true，且 outline/world 版本仍被补齐
    rewritten_book.save_chapter(Chapter(no=11, title="第11章", content="正文。"),
                                unstable=True)
    rec = rewritten_book.load_chapter_versions()["11"]
    upstream = next(h["upstream"] for h in rec["history"]
                    if h.get("v") == rec["selected"])
    assert upstream["unstable"] is True
    assert upstream["outline_v"] == 1
    assert upstream["world_v"] == 1


@pytest.mark.p1
def test_gate_switch_off_always_passes(rewritten_book):
    """⑤stale_gate 开关关闭 → 守卫直接放行。"""
    verdict = sg.guard_staleness(
        rewritten_book, {"scope": "single", "ch_no": 10},
        cfg=SimpleNamespace(stale_gate=False))
    assert verdict["blocked"] is False
    assert verdict["unstable_chapters"] == []


# ---------------------------------------------------------------- 记忆装配

@pytest.mark.p1
def test_unstable_summary_gets_prefix(tmp_path):
    """④补充：已知不一致继续时，命中的 L4 摘要带「基于旧版」前缀。"""
    store = _make_book(tmp_path, chapters=3)
    store.append_summary(2, "主角拿到了钥匙。")
    ctx = Memory(store).build_context(None, up_to_chapter=3,
                                      unstable_chapters=[2])
    assert "（基于旧版第2章撰写，可能与最新设定不一致）" in ctx


@pytest.mark.p1
def test_character_states_filter_future(tmp_path):
    """⑥人物状态只注入 last_seen < 目标章，回写早期章不被未来事实污染。"""
    store = _make_book(tmp_path, chapters=12)
    store.save_character_states({
        "阿青": {"last_seen": 9, "location": "山门"},
        "老周": {"last_seen": 11, "location": "王城"},
    })
    ctx = Memory(store).build_context(None, up_to_chapter=10)
    assert "阿青" in ctx
    assert "老周" not in ctx


# ---------------------------------------------------------------- 接线

@pytest.fixture
def byok_db(monkeypatch):
    monkeypatch.setattr(db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(db, "get_user_llm", lambda uid: {"api_key": "k", "provider": "deepseek"})
    monkeypatch.setattr(db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(db, "get_user", lambda uid: {"id": uid, "balance": 100.0})
    monkeypatch.setattr(db, "change_balance", lambda *a, **k: None)
    monkeypatch.setattr(db, "update_project_price", lambda *a, **k: None)
    monkeypatch.setattr(db, "project_price", lambda pid: 0.0)


@pytest.fixture
def cleanup_runs():
    yield
    for pid in list(core._RUNS):
        h = core._RUNS.pop(pid, None)
        if h and h.thread and h.thread.is_alive():
            h.stop_event.set()
            h.thread.join(timeout=5)
        core._GEN_SEMAPHORE.release()


@pytest.mark.p0
def test_route_blocks_before_slot_and_billing(rewritten_book, byok_db, cleanup_runs, monkeypatch):
    """⑦门禁在计费/占槽之前：被拦截时不占槽、不落 task.json、无估算扣费。"""
    def _boom(*a, **k):
        raise AssertionError("被门禁拦截的请求不应占槽/进入计费")

    monkeypatch.setattr(core, "_acquire_slot", _boom)
    monkeypatch.setattr(gs, "_estimated_price", _boom)

    req = gs.AgentRunReq(agent="writer.chapter",
                         params={"scope": "single", "ch_no": 10})
    with pytest.raises(HTTPException) as ei:
        gs.start_agent_run(rewritten_book, req, {"id": 1, "username": "u"})

    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "stale_gate"
    assert not (rewritten_book.dir / "task.json").exists()


@pytest.mark.p1
def test_route_allow_inconsistent_propagates_run_flags(rewritten_book, byok_db,
                                                       cleanup_runs, monkeypatch):
    """④补充：allow_inconsistent 放行后，run 参数带 `_allow_inconsistent/_stale_chapters`。"""
    monkeypatch.setattr(gs, "_run_agent_task", lambda *a, **k: None)
    req = gs.AgentRunReq(agent="writer.chapter",
                         params={"scope": "single", "ch_no": 10},
                         allow_inconsistent=True)
    body = gs.start_agent_run(rewritten_book, req, {"id": 1, "username": "u"})
    assert body["started"] is True
    meta = json.loads((rewritten_book.dir / "task.json").read_text(encoding="utf-8"))
    assert meta["params"]["_allow_inconsistent"] is True
    assert meta["params"]["_stale_chapters"] == list(range(4, 11))


@pytest.mark.p1
def test_guard_source_order_before_slot():
    """⑦补充：源码顺序锁——门禁调用早于 _acquire_slot / 估算。"""
    src = Path(gs.__file__).read_text(encoding="utf-8")
    i_guard = src.index("stale_guard.guard_staleness(")
    assert i_guard < src.index("core._acquire_slot(")
    assert i_guard < src.index("_check_daily_run_limit(store)")
