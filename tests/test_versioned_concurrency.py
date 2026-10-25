# -*- coding: utf-8 -*-
"""版本化门面并发与对账测试（TECH-Agent用户化与版本化 §3.2，阶段 1A）。

覆盖（P1）：
- 并发 commit 不丢版本（per-project RLock 第二道防线）
- 并发相同内容幂等（只建 1 版本）
- 索引 ↔ 文件对账：missing_files / orphan_files / selected_mismatch
- chapter 存档目录无索引记录（orphan_dir）
- tools/version_check.py CLI 退出码

注意：纯 store 操作，不依赖真实 LLM；占位文本避开节拍词（§11.8）。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                    # noqa: E402
from novel_gen.store.project import ProjectStore             # noqa: E402
from novel_gen.store.schemas import Chapter                  # noqa: E402
from novel_gen.store.versioned import VersionedProduct       # noqa: E402
from tools import version_check                              # noqa: E402


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "cc", cfg)


def _ch(no: int, content: str) -> Chapter:
    return Chapter(no=no, title=f"第{no}章", content=content, summary="摘要")


# ---------- 并发 ----------
@pytest.mark.p1
def test_concurrent_commit_no_version_loss(store):
    """8 线程并发提交不同内容：版本号连续 1..8 不丢不重，文件与索引一致。"""
    n = 8
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            store.save_chapter(_ch(1, f"并发稿{i:02d}"))
        except BaseException as exc:  # noqa: BLE001 - 测试需收集一切异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    versions = [x["v"] for x in store.list_versions(1)]
    assert versions == list(range(1, n + 1))
    report = VersionedProduct(store, "chapter").reconcile()
    assert not any(report.values())  # 对账干净


@pytest.mark.p1
def test_concurrent_same_content_idempotent(store):
    """多线程并发提交相同内容：只建 1 个版本（幂等在锁内判定）。"""
    n = 6
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            store.save_chapter(_ch(1, "同一份稿"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert [x["v"] for x in store.list_versions(1)] == [1]


# ---------- 对账 ----------
@pytest.mark.p1
def test_reconcile_missing_files(store):
    store.save_chapter(_ch(1, "一稿"))
    store.save_chapter(_ch(1, "二稿"))
    (store.chapters_dir / "001" / "v1.json").unlink()  # 制造索引有、盘上无
    report = VersionedProduct(store, "chapter").reconcile()
    assert "1:v1" in report["missing_files"]
    assert not report["orphan_files"]


@pytest.mark.p1
def test_reconcile_orphan_files(store):
    store.save_chapter(_ch(1, "一稿"))
    # 制造盘上有、索引无
    store._write_json(store.chapters_dir / "001" / "v99.json", _ch(1, "孤儿稿").to_dict())
    report = VersionedProduct(store, "chapter").reconcile()
    assert "1:v99" in report["orphan_files"]


@pytest.mark.p1
def test_reconcile_selected_mismatch(store):
    store.save_chapter(_ch(1, "一稿"))
    idx = json.loads(store.chapter_versions_path().read_text(encoding="utf-8"))
    idx["1"]["selected"] = 99  # 篡改为不存在的版本
    store.save_chapter_versions(idx)
    report = VersionedProduct(store, "chapter").reconcile()
    assert report["selected_mismatch"] is True


@pytest.mark.p1
def test_reconcile_outline_world(store):
    VersionedProduct(store, "outline").commit("all", {"title": "纲", "intro": "介"})
    store._write_json(store.dir / "outline" / "v99.json", {"title": "孤儿纲"})
    report = VersionedProduct(store, "outline").reconcile()
    assert "all:v99" in report["orphan_files"]
    # world 干净
    assert not any(VersionedProduct(store, "world").reconcile().values())


@pytest.mark.p1
def test_chapter_orphan_dir_detected(store):
    """盘上存在 chapters/NNN/ 存档目录但索引无该章 → CLI 报 orphan_dir。"""
    store.save_chapter(_ch(1, "一稿"))
    store._write_json(store.chapters_dir / "007" / "v1.json", _ch(7, "孤儿章").to_dict())
    problems = version_check.check_project(store)
    assert any("orphan_dir" in p for p in problems)


@pytest.mark.p1
def test_version_check_cli_exit_code(store, monkeypatch, capsys):
    # CLI 默认根为 get_projects_dir()；测试项目在 tmp_path 下，替换根目录指向
    monkeypatch.setattr(version_check, "get_projects_dir", lambda: store.dir.parent)
    store.save_chapter(_ch(1, "一稿"))
    assert version_check.main(["--project", store.project_id]) == 0
    (store.chapters_dir / "001" / "v1.json").unlink()
    assert version_check.main(["--project", store.project_id]) == 1
