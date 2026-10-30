# -*- coding: utf-8 -*-
"""大纲 / 世界观版本化测试（TECH-Agent用户化与版本化 §3.5/§3.10，阶段 1A）。

覆盖（P1）：
- 全新项目首次提交得 v1（无 legacy）
- 存量项目 legacy 迁移（旧 HEAD 存 v1(legacy)，新内容 v2）
- select 回读（HEAD 回滚 + user_managed 置位）
- 一次 run 仅 1 版本：`commit=False` 中间落盘多次 + `commit_head` 收尾一次
- `commit_head` 幂等（HEAD 与最新存档一致不追加）
- flag 关闭（versioned_product=False）：不建版本库、恢复 outline_snapshots 自动快照

注意：纯 store 操作，不依赖真实 LLM；占位文本避开节拍词（§11.8）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                    # noqa: E402
from novel_gen.store.project import ProjectStore             # noqa: E402
from novel_gen.store.schemas import ChapterPlan, Outline, World  # noqa: E402
from novel_gen.store.versioned import VersionedProduct       # noqa: E402


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "ow", cfg)


def _outline(title: str, intro: str = "简介") -> Outline:
    return Outline(title=title, intro=intro,
                   chapters=[ChapterPlan(no=1, title="开篇", goal="目标")])


def _world(view: str) -> World:
    return World(world_view=view)


def _set_flag(store: ProjectStore, key: str, value) -> None:
    d = json.loads(store.config_path().read_text(encoding="utf-8"))
    d[key] = value
    store._write_json(store.config_path(), d)


# ---------- 大纲 ----------
@pytest.mark.p1
def test_outline_first_commit_is_v1(store):
    assert store.save_outline(_outline("纲一"), commit=True) == 1
    f = VersionedProduct(store, "outline")
    v = f.list("all")
    assert [x["v"] for x in v] == [1]
    assert v[0]["source"] == "ai" and v[0]["selected"] is True
    assert store.load_outline().title == "纲一"


@pytest.mark.p1
def test_outline_legacy_migration(store):
    # 存量：直接写 outline.json（无版本库）
    store._write_json(store.outline_path(), _outline("旧纲").to_dict())
    assert store.save_outline(_outline("新纲"), commit=True) == 2
    f = VersionedProduct(store, "outline")
    v = f.list("all")
    assert [x["v"] for x in v] == [1, 2]
    assert v[0]["source"] == "legacy"
    assert f.load_version("all", 1)["title"] == "旧纲"
    assert store.load_outline().title == "新纲"


@pytest.mark.p1
def test_outline_select_older_version(store):
    store.save_outline(_outline("纲一"), commit=True)
    store.save_outline(_outline("纲二"), commit=True, source="user")
    f = VersionedProduct(store, "outline")
    data = f.select("all", 1)
    assert data is not None and data["title"] == "纲一"
    assert store.load_outline().title == "纲一"   # HEAD 已切到所选版本
    assert f.selected_version("all") == 1
    # select 置 user_managed：后续 commit 不自动选分覆盖（无 quality 时本就无候选，验证标记）
    idx = json.loads((store.dir / "outline" / "versions.json").read_text(encoding="utf-8"))
    assert idx["all"]["user_managed"] is True


@pytest.mark.p1
def test_one_run_one_version_via_commit_head(store):
    """管线中间多次落盘（commit=False）只写 HEAD；run 收尾 commit_head 一次 → 仅 1 版本。"""
    store.save_outline(_outline("中间稿一"))          # commit=False：只写 HEAD
    store.save_outline(_outline("中间稿二"))
    store.save_outline(_outline("终稿"))
    assert not (store.dir / "outline" / "versions.json").exists()  # 中间落盘不建版本
    f = VersionedProduct(store, "outline")
    assert f.commit_head("all", source="ai") == 1
    assert [x["v"] for x in f.list("all")] == [1]
    assert f.load_version("all", 1)["title"] == "终稿"
    assert store.load_outline().title == "终稿"


@pytest.mark.p1
def test_commit_head_idempotent(store):
    store.save_outline(_outline("稿一"))
    f = VersionedProduct(store, "outline")
    assert f.commit_head("all") == 1
    assert f.commit_head("all") is None            # HEAD 未变 → 幂等
    store.save_outline(_outline("稿二"))            # 中间落盘改 HEAD
    assert f.commit_head("all") == 2               # 与最新存档不同 → 追加
    assert [x["v"] for x in f.list("all")] == [1, 2]


@pytest.mark.p1
def test_commit_head_no_head_returns_none(store):
    f = VersionedProduct(store, "outline")
    assert f.commit_head("all") is None            # 无 HEAD 不建空版本


# ---------- 世界观 ----------
@pytest.mark.p1
def test_world_first_commit_and_select(store):
    assert store.save_world(_world("设定一"), commit=True) == 1
    store.save_world(_world("设定二"), commit=True, source="user")
    f = VersionedProduct(store, "world")
    v = f.list("all")
    assert [x["v"] for x in v] == [1, 2]
    assert f.select("all", 1)["world_view"] == "设定一"
    assert store.load_world().world_view == "设定一"


@pytest.mark.p1
def test_world_commit_head_flow(store):
    store.save_world(_world("中间设定"))
    store.save_world(_world("终设定"))
    f = VersionedProduct(store, "world")
    assert f.commit_head("all") == 1
    assert f.commit_head("all") is None
    assert [x["v"] for x in f.list("all")] == [1]


# ---------- flag 关闭回退 ----------
@pytest.mark.p1
def test_flag_off_no_versions_and_snapshots(store):
    """versioned_product=False：save_outline 不建版本库，恢复 outline_snapshots 快照。"""
    _set_flag(store, "versioned_product", False)
    assert store.save_outline(_outline("纲一"), commit=True) is None   # 不建版本
    assert not (store.dir / "outline" / "versions.json").exists()
    assert store.load_outline().title == "纲一"                        # HEAD 正常
    snaps = list((store.dir / "outline_snapshots").glob("snap_*.json"))
    assert len(snaps) >= 1                                             # 旧快照机制恢复
    # world 同样不建版本库
    assert store.save_world(_world("设定一"), commit=True) is None
    assert not (store.dir / "world" / "versions.json").exists()


@pytest.mark.p1
def test_flag_on_no_snapshots(store):
    """versioned_product=True（默认）：不再写 outline_snapshots（两套版本体系不并存）。"""
    store.save_outline(_outline("纲一"), commit=True)
    assert not (store.dir / "outline_snapshots").exists()
