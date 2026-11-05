# -*- coding: utf-8 -*-
"""REQ-R10 伏笔进度与入账修复专项测试。

覆盖：
- D1：网页写章章后确定性入账（正常推进 / 同章幂等 / 账本缺失自动初始化 / 异常降级 / 开关关闭）
- D2：世界观保存伏笔字段往返不丢（结构化 → PUT → GET 逐项相等）
- D3：到期当天边界（n == reveal 即逾期）
- A：进度接口 summary/items/unstructured（老字符串兜底、未到埋点）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig  # noqa: E402
from novel_gen.pipeline import reader_ledger as rl  # noqa: E402
from novel_gen.pipeline.reader_ledger import ReaderLedger, commit_chapter_account  # noqa: E402
from novel_gen.store.project import ProjectStore  # noqa: E402
from novel_gen.store.schemas import Chapter, ForeshadowItem, World  # noqa: E402
from webapp.routers import projects_world as pw  # noqa: E402


@pytest.fixture
def cfg():
    return NovelConfig(title="R10 测试书", genre="玄幻", total_words=10000, chapter_words=2500)


@pytest.fixture
def store(tmp_path, cfg):
    s = ProjectStore.create(tmp_path / "p", cfg)
    w = World()
    w.foreshadowing = [
        ForeshadowItem(id="f1", name="守则孤本", buried_ch=1, reveal_ch=3, level="large",
                       content="守则孤本藏在档案室"),
        ForeshadowItem(id="f2", name="黑石片", buried_ch=1, reveal_ch=8, level="small",
                       content="半块黑石片"),
        ForeshadowItem(id="f3", name="母本去向", buried_ch=10, reveal_ch=12, level="medium",
                       content="母本流向"),
    ]
    s.save_world(w)
    return s


@pytest.fixture
def routed(store, monkeypatch):
    """路由依赖打桩：鉴权 noop + _find_project 返回临时 store。"""
    monkeypatch.setattr(pw, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pw, "_find_project", lambda pid: store)
    return store


def _write_chapters(store, upto: int, contents: dict | None = None):
    contents = contents or {}
    for no in range(1, upto + 1):
        store.save_chapter(Chapter(no=no, title=f"第{no}章",
                                   content=contents.get(no, f"第{no}章的正文内容。")))


# ---------- D3：到期当天边界 ----------
@pytest.mark.p1
def test_boundary_reveal_day_counts_overdue(store, cfg):
    """n == reveal-1 临期；n == reveal 当天即逾期；n == reveal+1 仍逾期。"""
    ledger = ReaderLedger(store, cfg)
    states = {n: {i["name"]: i["state"] for i in ledger.commitment_items(n)}["守则孤本"]
              for n in (2, 3, 4)}
    assert states[2] == "due_soon"    # reveal-1（lead=3 已进入临期窗口）
    assert states[3] == "overdue"     # 计划回收当天必须兑现
    assert states[4] == "overdue"
    # 到期当章的展示文案不该是「已逾期 0 章」
    assert pw._fs_distance("overdue", {"reveal_ch": 3}, 3)[1] == "第 3 章到期未收"
    assert pw._fs_distance("overdue", {"reveal_ch": 3}, 5)[1] == "已逾期 2 章"


# ---------- D1：章后确定性入账 ----------
@pytest.mark.p0
def test_commit_chapter_account_initializes_ledger(store, cfg):
    """账本缺失时首次入账自动初始化并推进状态。"""
    assert store.load_foreshadow_commitment() is None
    _write_chapters(store, 3, {3: "他终于打开守则孤本，真相浮现。"})
    commit_chapter_account(store, cfg, 3)
    pc = store.load_foreshadow_commitment()
    assert pc is not None and pc["updated_ch"] == 3
    item = [i for i in pc["items"] if i["name"] == "守则孤本"][0]
    assert item["state"] == "revealed" and item["revealed_ch"] == 3


@pytest.mark.p0
def test_commit_chapter_account_idempotent(store, cfg):
    """同一章重复提交：账本内容不变，不重复计数。"""
    _write_chapters(store, 3, {3: "他终于打开守则孤本，真相浮现。"})
    commit_chapter_account(store, cfg, 3)
    first = store.load_foreshadow_commitment()
    commit_chapter_account(store, cfg, 3)
    second = store.load_foreshadow_commitment()
    assert first == second
    pq = store.load_payoff_quota() or {}
    assert sum(1 for r in pq.get("log", []) if r.get("ch") == 3) <= 1


@pytest.mark.p1
def test_commit_chapter_account_degrades_on_error(store, cfg, monkeypatch):
    """入账异常只留痕：不抛错、不阻断写章。"""
    def boom(self, n):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(ReaderLedger, "update_post_chapter", boom)
    commit_chapter_account(store, cfg, 3)  # 不应抛异常


@pytest.mark.p1
def test_commit_chapter_account_respects_switch(store, cfg):
    """总开关关闭：零写入。"""
    cfg.reader_ledger = False
    _write_chapters(store, 3, {3: "他终于打开守则孤本。"})
    commit_chapter_account(store, cfg, 3)
    assert store.load_foreshadow_commitment() is None


@pytest.mark.p1
def test_commit_helper_exported_for_web_paths():
    """两条网页写章链路共用的入口必须存在（防止误删接线）。"""
    assert callable(rl.commit_chapter_account)


# ---------- D2：世界观保存字段往返 ----------
@pytest.mark.p0
def test_world_put_keeps_foreshadow_fields(store, routed):
    """GET → PUT → GET 往返后伏笔字段逐项相等（不再被 str(dict) 压扁）。"""
    body = pw.project_world(project_id="x", user={})
    raw = [x.to_dict() if hasattr(x, "to_dict") else x for x in body["foreshadowing"]]
    before = sorted(raw, key=lambda x: x["name"])
    pw.update_world(project_id="x", payload={"foreshadowing": raw, "world_view": "测试世界"},
                    user={})
    after_body = pw.project_world(project_id="x", user={})
    after = sorted([x.to_dict() if hasattr(x, "to_dict") else x
                    for x in after_body["foreshadowing"]], key=lambda x: x["name"])
    assert after == before
    assert all(isinstance(x, dict) for x in after)
    assert after[0]["reveal_ch"] == 3 and after[0]["buried_ch"] == 1


@pytest.mark.p1
def test_parse_foreshadowing_compat_string_and_dict():
    """老字符串与结构化 dict 都能解析，非法项丢弃。"""
    items = pw._parse_foreshadowing(["老伏笔A", {"name": "结构化B", "buried_ch": 2,
                                                "reveal_ch": 5}, 123, ""])
    assert [i.name for i in items] == ["老伏笔A", "结构化B"]
    assert items[1].reveal_ch == 5


# ---------- A：进度接口 ----------
@pytest.mark.p1
def test_foreshadowing_progress_api(store, routed):
    """总览 + 逐条状态 + 老字符串兜底 + 未到埋点。"""
    _write_chapters(store, 9)
    store.save_chapter(Chapter(no=8, title="第8章", content="他握着半块黑石片。"))
    commit_chapter_account(store, store.load_config(), 8)
    # 追加一条老格式纯字符串伏笔（模拟历史数据）
    world = store.load_world()
    world.foreshadowing.append("老格式伏笔")
    store.save_world(world)

    body = pw.list_foreshadowing(project_id="x", user={})
    summary = body["summary"]
    assert summary["total"] == 4
    assert summary["tracked"] == 3
    assert summary["unstructured"] == 1
    assert summary["baseline_ch"] == 9
    assert summary["revealed"] == 1      # 黑石片 ch8 已收
    assert summary["overdue"] == 1       # 守则孤本 reveal=3 < 9
    assert summary["not_buried"] == 1    # 母本去向 buried=10 > 9

    items = {i["name"]: i for i in body["items"]}
    assert items["黑石片"]["distance_label"].startswith("第 8 章已收")
    assert items["守则孤本"]["distance_label"] == "已逾期 6 章"
    assert items["母本去向"]["state_label"] == "未到埋点"
    assert body["unstructured"][0]["name"] == "老格式伏笔"
    assert "未登记章号" in body["unstructured"][0]["note"]
    # 向后兼容：名称数组仍返回
    assert body["foreshadowing"] == ["守则孤本", "黑石片", "母本去向", "老格式伏笔"]


@pytest.mark.p2
def test_foreshadowing_progress_empty(tmp_path, cfg, monkeypatch):
    """无世界观：空结构 + 全零 summary，前端显示空状态。"""
    s = ProjectStore.create(tmp_path / "empty", cfg)
    monkeypatch.setattr(pw, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pw, "_find_project", lambda pid: s)
    body = pw.list_foreshadowing(project_id="x", user={})
    assert body["items"] == [] and body["unstructured"] == []
    assert body["summary"]["total"] == 0
