# -*- coding: utf-8 -*-
"""R14 单章重检接口测试：写回 quality/review.json、幂等复用、force 重跑、失败不落「通过」。

全部用假 checker 注入，不调用真实模型（离线、零计费）。
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

from novel_gen.config import NovelConfig                       # noqa: E402
from novel_gen.store.project import ProjectStore               # noqa: E402
from novel_gen.store.schemas import Chapter                    # noqa: E402
from webapp import chapter_quality_service as cqs              # noqa: E402
from webapp.routers import projects_chapters as pc             # noqa: E402


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "q", cfg)


def _seed(store: ProjectStore) -> None:
    """v1 = 生成稿；v2 = 「按此改」改写稿（无 quality，正是 R14 要补检的对象）。"""
    store.save_chapter(Chapter(no=1, title="第1章", content="初稿正文" * 40, summary="摘要"))
    store.save_chapter(Chapter(no=1, title="第1章", content="改写正文" * 40, summary="摘要"),
                       source="rewrite")


class _FakeChecker:
    """真 checker + 打桩 check()：保留 recheck/_save_review 的权威落盘路径（review.json）。"""

    def __init__(self, store: ProjectStore, result: dict | None = None,
                 error: Exception | None = None, calls: list | None = None):
        pipeline = SimpleNamespace(cfg=store.load_config(), llm=object(),
                                   trace=None, guard=None)
        self.inner = cqs._build_checker(pipeline, store)
        self.result = result
        self.error = error
        self.calls = calls if calls is not None else []

    def recheck(self, *args, **kwargs):
        def _check(*a, **k):
            self.calls.append(1)
            if self.error:
                raise self.error
            return self.result
        self.inner.check = _check
        return self.inner.recheck(*args, **kwargs)


@pytest.fixture
def wired(store, monkeypatch):
    monkeypatch.setattr(pc, "_check_project_access", lambda project_id, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda project_id: store)
    monkeypatch.setattr(pc, "_open_pipeline", lambda s, trace_id="": SimpleNamespace(
        cfg=store.load_config(), llm=object(), trace=None, guard=None))
    return store


def _call(store, fake, monkeypatch, body=None) -> dict:
    monkeypatch.setattr(cqs, "_build_checker", lambda pipeline, s: fake)
    return pc.recheck_chapter_quality("pid", 1, req=body or {}, user={"id": 1})


def _quality(store: ProjectStore, v: int) -> dict | None:
    return next(x for x in store.list_versions(1) if x["v"] == v)["quality"]


@pytest.mark.p0
def test_recheck_writes_quality_and_review(wired, monkeypatch):
    _seed(wired)
    fake = _FakeChecker(wired, result={
        "passed": False, "score": 72, "hook_score": 3,
        "issues": [{"type": "读者体验", "level": "小改", "detail": "结尾略平",
                    "evidence": "改写正文", "fix_suggestion": "补一个钩子"}],
    })
    out = _call(wired, fake, monkeypatch, {"version": 2})
    assert out["reused"] is False
    assert out["score"] == 72 and out["issue_count"] == 1 and out["version"] == 2
    q = _quality(wired, 2)
    assert q["score"] == 72 and q["hook_score"] == 3 and q["issue_count"] == 1
    # 正文与 HEAD 必须保持原样（重检只判定，不改写）
    assert wired.load_chapter(1).content.startswith("改写正文")
    assert wired.load_chapter_version(1, 2).content.startswith("改写正文")
    review = json.loads((wired.dir / "review.json").read_text(encoding="utf-8"))
    rec = next(r for r in review if r["chapter"] == 1)
    assert rec["quality_check"]["score"] == 72
    assert rec["auto_revised"] is False


@pytest.mark.p0
def test_existing_quality_reused_without_model(wired, monkeypatch):
    _seed(wired)
    assert wired.update_version_quality(1, 2, {
        "passed": True, "score": 88, "issue_count": 0, "issues": []})
    fake = _FakeChecker(wired, error=AssertionError("不应重复调用模型"))
    out = _call(wired, fake, monkeypatch, {"version": 2})
    assert out["reused"] is True and len(fake.calls) == 0
    assert out["score"] == 88


def test_force_reruns_even_if_quality_exists(wired, monkeypatch):
    _seed(wired)
    wired.update_version_quality(1, 2, {"passed": True, "score": 99, "issue_count": 0})
    fake = _FakeChecker(wired, result={"passed": True, "score": 90, "issues": []})
    out = _call(wired, fake, monkeypatch, {"version": 2, "force": True})
    assert out["reused"] is False and len(fake.calls) == 1
    assert _quality(wired, 2)["score"] == 90


@pytest.mark.p0
def test_failure_never_writes_pass(wired, monkeypatch):
    _seed(wired)
    fake = _FakeChecker(wired, error=RuntimeError("模型超时"))
    with pytest.raises(HTTPException) as ei:
        _call(wired, fake, monkeypatch, {"version": 2})
    assert ei.value.status_code == 500
    assert _quality(wired, 2) is None                       # 不写任何「通过」
    assert not (wired.dir / "review.json").exists()          # 也不落半截 review


@pytest.mark.p0
def test_unknown_version_rejected(wired, monkeypatch):
    _seed(wired)
    with pytest.raises(HTTPException) as ei:
        _call(wired, _FakeChecker(wired, result={}), monkeypatch, {"version": 9})
    assert ei.value.status_code == 422


def test_default_version_uses_current_selected(wired, monkeypatch):
    _seed(wired)
    fake = _FakeChecker(wired, result={"passed": True, "score": 80, "issues": []})
    out = _call(wired, fake, monkeypatch, {})                # 不传 version → 当前选中 v2
    assert out["version"] == 2


def test_no_available_model_rejected(store):
    _seed(store)
    with pytest.raises(ValueError):
        cqs.recheck_chapter_quality(store, SimpleNamespace(
            cfg=store.load_config(), llm=None, trace=None, guard=None), 1, version=2)
