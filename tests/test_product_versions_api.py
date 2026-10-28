"""阶段 1C 新增 API 测试（P1）。

覆盖：产物版本列表（金额反查）/受控回滚/回滚影响范围（TECH §11.3）、
agent-prompts 读写（§3.4）、agent-run 停止薄封装、rewrite 槽位收敛（§11.2 缺口）。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.versioned import VersionedProduct
from webapp import core
from webapp import settle_ledger
from webapp.routers import projects_chapters as pc
from webapp.routers import projects_generation as pg
from webapp.routers import projects_versions as pv


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def routed(store, monkeypatch):
    """路由依赖打桩：鉴权 noop + _find_project 返回临时 store。"""
    for mod in (pv, pg, pc):
        monkeypatch.setattr(mod, "_check_project_access", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_find_project", lambda pid: store)
    return store


@pytest.fixture
def cleanup_runs():
    yield
    for pid in list(core._RUNS):
        h = core._RUNS.pop(pid, None)
        if h and h.thread and h.thread.is_alive():
            h.stop_event.set()
            h.thread.join(timeout=5)
        core._GEN_SEMAPHORE.release()


def _outline_dict(tag: str) -> dict:
    return {"title": "测试书", "intro": f"简介{tag}",
            "chapters": [{"no": 1, "title": "第1章", "goal": "目标"}]}


# ---------- settle_ledger.version_charge_map ----------

@pytest.mark.p1
def test_version_charge_map_empty(store):
    assert settle_ledger.version_charge_map(store, "outline", "all") == {}


@pytest.mark.p1
def test_version_charge_map_hit(store):
    ledger = settle_ledger.load_ledger(store)
    ledger["entries"] = {
        "outline:all:v1": {"words": 100, "due": 0.03, "charged": 0.03, "mode": "platform"},
        "outline:all:v2": {"words": 200, "due": 0.06, "charged": 0.0, "mode": "user_llm"},
        "outline:all:vX": {"words": 1, "due": 0.01, "charged": 0.01, "mode": "platform"},
        "chapter:1:v1": {"words": 2500, "due": 0.75, "charged": 0.75, "mode": "platform"},
    }
    settle_ledger._write_ledger(store, ledger)
    got = settle_ledger.version_charge_map(store, "outline", "all")
    assert got[1]["charged"] == 0.03
    assert got[2]["mode"] == "user_llm"
    assert 0 not in got  # 解析失败（vX → 0）跳过
    assert all(v >= 1 for v in got)  # chapter 条目不串入


# ---------- 产物版本列表 / 回滚 ----------

@pytest.mark.p1
def test_list_product_versions_with_charge(store, routed):
    vp = VersionedProduct(store, "outline")
    assert vp.commit("all", _outline_dict("甲"), source="ai") == 1
    assert vp.commit("all", _outline_dict("乙"), source="ai") == 2
    ledger = settle_ledger.load_ledger(store)
    ledger["entries"] = {
        "outline:all:v2": {"words": 50, "due": 0.02, "charged": 0.02, "mode": "platform"},
    }
    settle_ledger._write_ledger(store, ledger)

    body = pv.list_product_versions(store.dir.name, "outline", {"id": 1})
    versions = body["versions"]
    assert [v["v"] for v in versions] == [1, 2]
    assert versions[1]["selected"] is True
    assert versions[1]["words"] > 0
    assert versions[1]["charged"] == 0.02  # 有账本条目 → 带金额
    assert "charged" not in versions[0]    # 无账本条目 → 不带金额键（降级）


@pytest.mark.p1
def test_list_product_versions_bad_kind(store, routed):
    with pytest.raises(HTTPException) as ei:
        pv.list_product_versions(store.dir.name, "chapter", {"id": 1})
    assert ei.value.status_code == 400


@pytest.mark.p1
def test_select_product_version_rollback(store, routed):
    vp = VersionedProduct(store, "outline")
    vp.commit("all", _outline_dict("甲"), source="ai")
    vp.commit("all", _outline_dict("乙"), source="ai")
    body = pv.select_product_version(store.dir.name, "outline", 1, {"id": 1})
    assert body["selected"] == 1
    assert vp.selected_version("all") == 1
    head = vp.list("all")
    assert next(v for v in head if v["v"] == 1)["selected"] is True
    with pytest.raises(HTTPException) as ei:
        pv.select_product_version(store.dir.name, "outline", 99, {"id": 1})
    assert ei.value.status_code == 404


# ---------- 回滚影响范围（§11.3） ----------

@pytest.mark.p1
def test_rollback_impact_by_upstream(store, routed):
    """章节选中版本的 upstream.outline_v > 目标 v → 计入受影响章，regenerate_from 取最小。"""
    ch = VersionedProduct(store, "chapter")
    ch.commit("1", {"no": 1, "title": "一", "content": "正文一" * 100},
              source="ai", upstream={"outline_v": 2, "world_v": 1})
    ch.commit("2", {"no": 2, "title": "二", "content": "正文二" * 100},
              source="ai", upstream={"outline_v": 3, "world_v": 1})
    ch.commit("3", {"no": 3, "title": "三", "content": "正文三" * 100},
              source="ai", upstream={"outline_v": 1, "world_v": 1})
    body = pv.rollback_impact(store.dir.name, "outline", 1, {"id": 1})
    assert body["affected_chapters"] == [1, 2]  # outline_v 2/3 > 1；章 3 的 1 不 >
    assert body["regenerate_from"] == 1
    assert body["target_version"] == 1
    assert "stale_views" in body


@pytest.mark.p1
def test_rollback_impact_no_upstream(store, routed):
    """存量章无 upstream 记录 → 不误判为受影响（降级只报视图状态）。"""
    ch = VersionedProduct(store, "chapter")
    ch.commit("1", {"no": 1, "title": "一", "content": "正文一" * 100}, source="ai")
    body = pv.rollback_impact(store.dir.name, "outline", 1, {"id": 1})
    assert body["affected_chapters"] == []
    assert body["regenerate_from"] is None


# ---------- agent-prompts 读写 ----------

@pytest.mark.p1
def test_agent_prompts_get_lazy_snapshot(store, routed):
    body = pg.list_agent_prompts(store.dir.name, {"id": 1})
    prompts = body["prompts"]
    assert set(prompts) == {"researcher", "architect.outline", "architect.volume",
                            "architect.world", "writer.chapter"}
    assert prompts["writer.chapter"]["version"] == "writer.chapter@v1"  # 懒快照 v1
    assert prompts["writer.chapter"]["text"]


@pytest.mark.p1
def test_agent_prompts_save_new_version(store, routed):
    # 全新 store 直接保存：首次快照即 v1（author=user；懒快照只在读取时触发）
    body = pg.save_agent_prompt(store.dir.name, "writer.chapter",
                                pg._AgentPromptReq(text="你是高冷写手"), {"id": 1})
    assert body["version"] == "writer.chapter@v1"
    got = pg.list_agent_prompts(store.dir.name, {"id": 1})
    assert got["prompts"]["writer.chapter"]["text"] == "你是高冷写手"
    # 再次保存递增为 v2（不可变版本链）
    body2 = pg.save_agent_prompt(store.dir.name, "writer.chapter",
                                 pg._AgentPromptReq(text="你是热血写手"), {"id": 1})
    assert body2["version"] == "writer.chapter@v2"
    got2 = pg.list_agent_prompts(store.dir.name, {"id": 1})
    assert got2["prompts"]["writer.chapter"]["text"] == "你是热血写手"
    with pytest.raises(HTTPException) as ei:
        pg.save_agent_prompt(store.dir.name, "editor.review",
                             pg._AgentPromptReq(text="x"), {"id": 1})
    assert ei.value.status_code == 400


# ---------- agent-run 停止 ----------

@pytest.mark.p1
def test_stop_agent_run(store, routed, cleanup_runs):
    pid = store.dir.name
    body = pg.stop_agent_run(pid, "t_none", {"id": 1})
    assert body["stopped"] is False  # 未运行

    handle = core._RunHandle()
    handle.task_id = "t_run"
    handle.thread = threading.Thread(target=threading.Event().wait, args=(30,), daemon=True)
    handle.thread.start()
    core._RUNS[pid] = handle
    with pytest.raises(HTTPException) as ei:
        pg.stop_agent_run(pid, "t_other", {"id": 1})
    assert ei.value.status_code == 409  # task_id 不匹配
    body = pg.stop_agent_run(pid, "t_run", {"id": 1})
    assert body["stopped"] is True
    assert handle.stop_event.is_set()
    core._RUNS.pop(pid, None)
