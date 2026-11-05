# -*- coding: utf-8 -*-
"""R2 体检接口层测试：只读契约、分页/筛选、错误分支。

直接调用路由函数（与既有 API 测试一致），monkeypatch 掉 DB 项目定位与权限校验，
不连 MySQL、不启动 HTTP 服务；重点验证接口产出形状与参数边界。
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
from novel_gen.store.schemas import Chapter, Character, ForeshadowItem, World
from webapp.routers import projects_consistency as pc

USER = {"id": 1, "username": "tester"}


@pytest.fixture
def book(tmp_path):
    cfg = NovelConfig(title="接口书", total_words=10000, chapter_words=2500, genre="玄幻")
    store = ProjectStore.create(tmp_path / "p", cfg)
    store.save_world(World(
        characters=[Character(name="林舟")],
        foreshadowing=[
            ForeshadowItem(name="密令", content="半张密令", buried_ch=1, reveal_ch=2),
            ForeshadowItem(name="旧信", content="旧信", buried_ch=2, reveal_ch=20),
        ],
    ))
    for no in range(1, 6):
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content=f"林舟在第{no}章行动。"))
    store.save_foreshadow_commitment({"version": 1, "updated_ch": 5, "items": [
        {"name": "密令", "revealed_ch": 3},
    ]})
    store.append_verify_issues(4, [{"problem": "时间线矛盾：第2章是白天，第4章说昨夜",
                                    "suggestion": "统一时间线"}])
    return store


@pytest.fixture
def routed(monkeypatch, book):
    """把路由内的 DB 定位/权限替换为内存 store。"""
    monkeypatch.setattr(pc, "_check_project_access", lambda pid, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda pid: book)
    return book


def test_all_routes_registered():
    paths = {getattr(r, "path", "") for r in pc.app.routes}
    for suffix in ("overview", "coverage", "foreshadow", "conflicts", "entities", "mentions"):
        assert f"/api/projects/{{project_id}}/consistency/{suffix}" in paths


def test_overview_shape(routed):
    data = pc.consistency_overview("p1", USER)
    assert data["project_id"] == routed.project_id
    assert data["baseline_ch"] == 5
    assert data["foreshadow"]["counts"]["planted"] >= 1
    assert data["conflicts"]["total"] >= 1
    assert data["coverage"]["views"]


def test_coverage_up_to_param(routed):
    data = pc.consistency_coverage("p1", up_to=2, user=USER)
    assert data["baseline_ch"] == 2


def test_foreshadow_state_filter_and_pagination(routed):
    all_items = pc.consistency_foreshadow("p1", user=USER)
    assert all_items["total"] >= 2
    page = pc.consistency_foreshadow("p1", limit=1, offset=0, user=USER)
    assert len(page["items"]) == 1
    revealed = pc.consistency_foreshadow("p1", state="revealed", user=USER)
    assert [i["name"] for i in revealed["items"]] == ["密令"]


def test_conflicts_severity_filter(routed):
    data = pc.consistency_conflicts("p1", user=USER)
    assert data["items"][0]["chapter"] == 4
    high = pc.consistency_conflicts("p1", severity="high", user=USER)
    assert all(i["severity"] == "high" for i in high["items"])


def test_entities_filter_and_pagination(routed):
    data = pc.consistency_entities("p1", q="林", user=USER)
    assert [i["name"] for i in data["items"]] == ["林舟"]
    assert data["items"][0]["total_mentions"] == 5
    empty = pc.consistency_entities("p1", kind="location", user=USER)
    assert empty["items"] == [] and empty["total"] == 0


def test_mentions_returns_chapter_locations(routed):
    data = pc.consistency_mentions("p1", "林舟", alias=[], user=USER)
    assert data["total_chapters"] == 5
    assert data["chapters"][0]["occurrences"][0]["snippet"].startswith("林舟")


def test_mentions_requires_name(routed):
    with pytest.raises(HTTPException) as exc:
        pc.consistency_mentions("p1", "  ", user=USER)
    assert exc.value.status_code == 400


def test_limit_is_clamped(routed):
    data = pc.consistency_entities("p1", limit=9999, user=USER)
    assert data["limit"] == 200


def test_http_contract_with_query_params(routed):
    """真实 HTTP 路径校验：路径参数 / 重复 alias 查询参数 / 鉴权依赖覆盖。"""
    import asyncio

    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=pc.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/api/projects/p1/consistency/mentions",
                params=[("name", "林舟"), ("alias", "阿舟")],
                headers={"Authorization": "Bearer test-token"},
            )

    pc.app.dependency_overrides[pc._require_user] = lambda: USER
    try:
        resp = asyncio.run(_run())
    finally:
        pc.app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "林舟" and "阿舟" in body["aliases"]
    assert body["total_chapters"] == 5
