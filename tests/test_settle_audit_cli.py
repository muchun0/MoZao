# -*- coding: utf-8 -*-
"""结算对账 CLI 测试（tools/settle_audit.py）。

验证三方对账的判定与退出码：账本内部一致、MySQL 项目累计金额一致、
章节已计费版本全部入账（缺条目只告警不补扣）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from tools import settle_audit
from webapp import settle_ledger

PID = "audit_book"


@pytest.fixture
def project(tmp_path, monkeypatch):
    # 直接用 PID 作为目录名（ProjectStore.create 会再拼一层自动 ID，不便于按 ID 定位）
    store = ProjectStore(tmp_path / PID)
    store.save_config(NovelConfig(title="对账书", total_words=10000))
    settle_ledger.settle_version(store, "chapter", "1", 1, 2000, mode="user_llm",
                                 hq=False, project_id=PID)  # 0 元条目，避免依赖钱包
    monkeypatch.setattr(settle_audit, "get_projects_dir", lambda: tmp_path)
    return store


def _run(monkeypatch, db_price: float) -> int:
    monkeypatch.setattr(settle_audit, "_audit_project_price",
                        lambda pid, billed: [] if db_price == billed else ["mismatch"])
    monkeypatch.setattr(sys, "argv", ["settle_audit", "--project", PID])
    return settle_audit.main()


def test_audit_consistent_returns_zero(project, monkeypatch):
    assert _run(monkeypatch, db_price=0.0) == 0


def test_audit_price_mismatch_returns_one(project, monkeypatch):
    assert _run(monkeypatch, db_price=9.9) == 1


def test_audit_missing_chapter_entry_warns(project, monkeypatch, capsys):
    """章节已生成 AI 版本但账本缺条目 → 告警并返回 1（不自动补扣）。"""
    before = settle_ledger.snapshot_chapter_versions(project, 2)
    project.save_chapter(Chapter(no=2, title="第二章", content="正文" * 500))
    assert settle_ledger.snapshot_chapter_versions(project, 2) - before == {1}
    assert _run(monkeypatch, db_price=0.0) == 1
    out = capsys.readouterr().out
    assert "chapter:2:v1" in out
    assert "不自动补扣" in out


def test_audit_no_ledger_returns_zero(tmp_path, monkeypatch):
    """未结算过的项目：无账本直接返回 0（不误报）。"""
    ProjectStore(tmp_path / PID).save_config(NovelConfig(title="空书"))
    monkeypatch.setattr(settle_audit, "get_projects_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["settle_audit", "--project", PID])
    assert settle_audit.main() == 0


def test_audit_missing_project_returns_one(tmp_path, monkeypatch):
    monkeypatch.setattr(settle_audit, "get_projects_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["settle_audit", "--project", "not_exist"])
    assert settle_audit.main() == 1


def test_ledger_entry_shape(project):
    """对账依赖的字段：charged / words 必须齐全。"""
    entry = json.loads((project.dir / "settle.json").read_text(encoding="utf-8"))["entries"]["chapter:1:v1"]
    assert entry["words"] == 2000
    assert entry["charged"] == 0.0
