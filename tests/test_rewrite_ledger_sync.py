# -*- coding: utf-8 -*-
"""缺陷修复：R1「按此改」局部改写后同步章后账本（P0）。

覆盖：
- 改写→结算之后，摘要重生成、场景记录标脏、章末账本 force 重跑；
- 账本同步失败不回滚已结算版本、不影响返回，但必须留日志；
- 章节/场景缺失时安全降级（不抛错、不误调用）。
扣费口径不在此断言（由 test_issue_rewrite.py / test_settle_per_version.py 覆盖）。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                       # noqa: E402
from novel_gen.pipeline.chapter_ledger import sync_after_rewrite  # noqa: E402
from novel_gen.store.project import ProjectStore               # noqa: E402
from novel_gen.store.schemas import Chapter                    # noqa: E402
from webapp import settle_ledger                                # noqa: E402
from webapp.chapter_issue_service import apply_issue_rewrite    # noqa: E402

PID = "p2"


def _cfg(**kw) -> NovelConfig:
    cfg = NovelConfig(title="账本测试书", genre="玄幻", total_words=10000,
                      chapter_words=2500)
    cfg.pitfall_reconcile = False
    cfg.book_memory = False
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def store(tmp_path):
    return ProjectStore.create(tmp_path / PID, _cfg())


@pytest.fixture
def wallet(monkeypatch):
    """内存钱包：不连 MySQL。"""
    state = {"balance": 100.0}

    def _charge(uid, delta, note):
        state["balance"] = round(state["balance"] + delta, 4)

    monkeypatch.setattr(settle_ledger.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(settle_ledger.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(settle_ledger.db, "get_user",
                        lambda uid: {"id": uid, "balance": state["balance"]})
    monkeypatch.setattr(settle_ledger.db, "change_balance", _charge)
    monkeypatch.setattr(settle_ledger.db, "update_project_price", lambda pid, price: None)
    monkeypatch.setattr(settle_ledger, "_grant_commission", lambda *a, **k: None)
    return state


def _setup(store) -> None:
    content = "天还没亮，墟口外的风就带着一股潮气。\n\n陆沉在界碑下蹲了一夜。"
    store.save_chapter(Chapter(no=1, title="第1章", content=content, summary="旧摘要"))
    store.save_scenes(1, [{"summary": "陆沉守碑"}], {})
    (store.dir / "review.json").write_text(json.dumps(
        [{"chapter": 1, "issues": [
            {"level": "中改", "type": "模糊", "detail": "意象重复",
             "evidence": "陆沉在界碑下蹲了一夜。", "fix_suggestion": "补环境细节"}]}],
        ensure_ascii=False), encoding="utf-8")


def _pipeline(reply: str) -> MagicMock:
    pipeline = MagicMock()
    pipeline.cfg = _cfg()
    pipeline.llm = MagicMock()
    pipeline.llm.chat.return_value = reply
    pipeline.trace = None
    pipeline.guard = None
    return pipeline


@pytest.mark.p0
def test_rewrite_syncs_summary_scenes_and_commit(store, wallet):
    _setup(store)
    pipeline = _pipeline("陆沉蹲了一夜，膝盖僵硬，界碑的雾更重了。")
    pipeline.chapter_writer._gen_summary.return_value = "陆沉守碑至天明，雾中界碑显露异样。"
    pipeline.commit_chapter_ledgers.return_value = {"status": "committed",
                                                   "done": ["chapter_state"]}

    res = apply_issue_rewrite(
        store, pipeline, store.load_chapter(1), 1,
        {"type": "模糊", "detail": "意象重复",
         "evidence": "陆沉在界碑下蹲了一夜。", "version": 1},
        billing_mode="platform", project_id=PID)

    assert res["version"] == 2
    summaries = {int(s["no"]): s["summary"] for s in store.load_summaries()}
    assert summaries[1].startswith("陆沉守碑至天明")
    scenes = store.load_scenes()["1"]
    assert scenes["stale"]["reason"] == "issue_rewrite"
    assert scenes["scenes"] == [{"summary": "陆沉守碑"}]
    pipeline.commit_chapter_ledgers.assert_called_once_with(1, force=True)
    assert res["ledger"]["commit"]["status"] == "committed"


@pytest.mark.p0
def test_rewrite_ledger_failure_keeps_result_and_logs(store, wallet, caplog):
    """账本同步失败：不抛错、不回滚版本、不影响返回，但必须留日志。"""
    _setup(store)
    pipeline = _pipeline("陆沉蹲了一夜，界碑上的字被雾磨得发白。")
    pipeline.chapter_writer._gen_summary.side_effect = RuntimeError("摘要服务不可用")
    pipeline.commit_chapter_ledgers.side_effect = RuntimeError("账本写入失败")

    with caplog.at_level(logging.WARNING):
        res = apply_issue_rewrite(
            store, pipeline, store.load_chapter(1), 1,
            {"type": "模糊", "detail": "意象重复",
             "evidence": "陆沉在界碑下蹲了一夜。", "version": 1},
            billing_mode="platform", project_id=PID)

    assert res["version"] == 2 and res["charged"] > 0
    assert store.load_chapter(1).content.endswith("碑上的字被雾磨得发白。")
    assert res["ledger"]["summary"] is False and res["ledger"]["commit"] is None
    assert "摘要同步失败" in caplog.text or "账本" in caplog.text


@pytest.mark.p2
def test_sync_skips_when_chapter_missing(store):
    pipeline = MagicMock()
    pipeline.cfg = _cfg()
    result = sync_after_rewrite(store, pipeline, 99, version=1)
    assert result == {"summary": False, "scenes_stale": False,
                      "book_memory": False, "commit": None}
    pipeline.commit_chapter_ledgers.assert_not_called()


@pytest.mark.p2
def test_mark_scenes_stale_without_record(store):
    assert store.mark_scenes_stale(7, reason="issue_rewrite") is False
