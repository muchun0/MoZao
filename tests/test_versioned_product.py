# -*- coding: utf-8 -*-
"""`store/versioned.py` 版本化门面专项测试（TECH-Agent用户化与版本化 §3.1，阶段 1A）。

覆盖（P0 资金链路 adjacent——版本号是结算幂等键，门面正确性直接决定结算正确性）：
- commit 幂等（内容与 HEAD 相同不追加）
- 存量 legacy 初始化（旧 HEAD 存 v1(legacy)，新内容 v2）
- 版本上限淘汰跳过 selected
- promote=False 候选不顶替 HEAD/selected
- quality 透传 list()
- user_managed 免自动选分
- delete selected 拒绝
- select 回读（HEAD 切换 + user_managed 置位）
- upstream 自动读取（chapter → {outline_v, world_v}）
- run_id duck-typing（无 current_run 降级空串 / 异常降级 / 显式传参优先）
- flag 开关 parity（versioned_product=False 走 legacy 实现，行为一致）

注意：纯 store 操作，不依赖真实 LLM；占位文本避开节拍词（§11.8）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                    # noqa: E402
from novel_gen.store.project import ProjectStore             # noqa: E402
from novel_gen.store.schemas import Chapter                  # noqa: E402
from novel_gen.store.versioned import VersionedProduct       # noqa: E402


# ---------- fixtures / helpers ----------
@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "vp", cfg)


def _ch(no: int, content: str) -> Chapter:
    return Chapter(no=no, title=f"第{no}章", content=content, summary="摘要")


def _facade(store, kind: str = "chapter") -> VersionedProduct:
    return VersionedProduct(store, kind)


def _set_flag(store: ProjectStore, key: str, value) -> None:
    """改写项目 config.json 中的功能开关（轻量，不构造 NovelConfig）。"""
    d = json.loads(store.config_path().read_text(encoding="utf-8"))
    d[key] = value
    store._write_json(store.config_path(), d)


# ---------- P0 ----------
@pytest.mark.p0
def test_commit_idempotent_same_as_head(store):
    f = _facade(store)
    assert f.commit("1", _ch(1, "正文甲").to_dict()) == 1
    # 与 HEAD 相同 → 幂等不追加
    assert f.commit("1", _ch(1, "正文甲").to_dict()) is None
    assert [x["v"] for x in f.list("1")] == [1]


@pytest.mark.p0
def test_legacy_init_migrates_head(store):
    # 存量：直接写 HEAD（无索引）
    store._write_json(store.chapter_path(1), _ch(1, "旧稿").to_dict())
    f = _facade(store)
    assert f.commit("1", _ch(1, "新稿").to_dict()) == 2
    v = f.list("1")
    assert [x["v"] for x in v] == [1, 2]
    assert v[0]["source"] == "legacy"
    assert v[1]["selected"] is True
    assert f.load_version("1", 1)["content"] == "旧稿"


@pytest.mark.p0
def test_prune_skips_selected(store):
    f = _facade(store)
    # 候选提交不改 selected：v1 始终保持选中
    f.commit("1", _ch(1, "初稿").to_dict(), limit=3)
    for i in range(2, 6):  # v2..v5 候选
        f.commit("1", _ch(1, f"候选稿{i}").to_dict(), promote=False, limit=3)
    v = f.list("1")
    versions = [x["v"] for x in v]
    assert len(versions) == 3
    assert 1 in versions           # selected 跳过淘汰
    assert versions[-1] == 5       # 最新保留
    assert 2 not in versions and 3 not in versions  # 最旧非选中被淘汰
    assert v[0]["selected"] is True
    # HEAD 仍是 selected(v1) 内容
    assert store.load_chapter(1).content == "初稿"


@pytest.mark.p0
def test_promote_false_keeps_head(store):
    f = _facade(store)
    f.commit("1", _ch(1, "正式稿").to_dict())
    assert f.commit("1", _ch(1, "候选稿").to_dict(), promote=False) == 2
    assert store.load_chapter(1).content == "正式稿"  # HEAD 不变
    v = f.list("1")
    assert v[0]["selected"] is True and v[1]["selected"] is False


@pytest.mark.p0
def test_quality_passthrough(store):
    f = _facade(store)
    q = {"passed": False, "score": 55, "issue_count": 2}
    f.commit("1", _ch(1, "带质检稿").to_dict(), quality=q)
    v = f.list("1")
    assert v[0]["quality"]["score"] == 55
    assert v[0]["quality"]["issue_count"] == 2


@pytest.mark.p0
def test_user_managed_blocks_auto_select(store):
    f = _facade(store)
    f.commit("1", _ch(1, "低分稿").to_dict(), quality={"score": 40})
    f.commit("1", _ch(1, "人工稿").to_dict(), source="user")  # 置 user_managed
    # 再提高分稿：不自动切走用户所选之后的 HEAD（新稿成为 HEAD 即可）
    f.commit("1", _ch(1, "高分稿").to_dict(), quality={"score": 99})
    v = f.list("1")
    assert v[-1]["selected"] is True
    assert store.load_chapter(1).content == "高分稿"


@pytest.mark.p0
def test_delete_selected_rejected(store):
    f = _facade(store)
    f.commit("1", _ch(1, "一稿").to_dict())
    f.commit("1", _ch(1, "二稿").to_dict())
    assert f.delete("1", 2) is False      # selected 不可删
    assert f.delete("1", 1) is True
    assert [x["v"] for x in f.list("1")] == [2]
    assert f.delete("1", 99) is False


@pytest.mark.p0
def test_select_switches_head(store):
    f = _facade(store)
    f.commit("1", _ch(1, "一稿").to_dict())
    f.commit("1", _ch(1, "二稿").to_dict())
    data = f.select("1", 1)
    assert data is not None and data["content"] == "一稿"
    assert store.load_chapter(1).content == "一稿"  # HEAD 已回滚
    assert f.selected_version("1") == 1
    assert f.select("1", 99) is None  # 不存在版本


@pytest.mark.p0
def test_upstream_auto_read(store):
    """chapter 提交时 upstream=None → 自动读取当前 outline/world selected 版本号。"""
    of, wf = _facade(store, "outline"), _facade(store, "world")
    of.commit("all", {"title": "纲一", "intro": "简介"})
    wf.commit("all", {"world_view": "设定一"})
    cf = _facade(store, "chapter")
    cf.commit("1", _ch(1, "正文").to_dict())
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    entry = idx["1"]["history"][-1]
    assert entry["upstream"] == {"outline_v": 1, "world_v": 1}
    # 显式传入则透传（不自动读取）
    cf.commit("1", _ch(1, "正文改").to_dict(), upstream={"outline_v": 7})
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    assert idx["1"]["history"][-1]["upstream"] == {"outline_v": 7}


@pytest.mark.p0
def test_run_id_duck_typing(store):
    f = _facade(store)
    # 1. store 无 current_run（1B 才落地）→ 降级空串，不写 run_id 键
    f.commit("1", _ch(1, "无run").to_dict())
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    assert "run_id" not in idx["1"]["history"][-1]

    # 2. current_run 存在 → 自动读取
    store.current_run = lambda: SimpleNamespace(run_id="run-abc")
    f.commit("1", _ch(1, "有run").to_dict())
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    assert idx["1"]["history"][-1]["run_id"] == "run-abc"

    # 3. current_run 抛异常 → 降级空串不报错
    def _boom():
        raise RuntimeError("run 上下文损坏")
    store.current_run = _boom
    f.commit("1", _ch(1, "异常run").to_dict())
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    assert "run_id" not in idx["1"]["history"][-1]

    # 4. 显式传参优先于 current_run
    del store.current_run
    f.commit("1", _ch(1, "显式run").to_dict(), run_id="run-explicit")
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    assert idx["1"]["history"][-1]["run_id"] == "run-explicit"


@pytest.mark.p0
def test_flag_off_legacy_parity(store):
    """versioned_product=False → save_chapter 走 legacy 实现，对外行为与门面一致。"""
    _set_flag(store, "versioned_product", False)
    store.save_chapter(_ch(1, "一稿"))
    r = store.save_chapter(_ch(1, "二稿"))
    assert r == 2
    v = store.list_versions(1)
    assert [x["v"] for x in v] == [1, 2]
    assert v[-1]["selected"] is True
    assert store.load_chapter(1).content == "二稿"
    assert store.load_chapter_version(1, 1).content == "一稿"
    # 幂等一致
    assert store.save_chapter(_ch(1, "二稿")) is None


@pytest.mark.p0
def test_kind_rejected(store):
    with pytest.raises(ValueError):
        VersionedProduct(store, "unknown_kind")
