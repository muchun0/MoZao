# -*- coding: utf-8 -*-
"""三期 F2 场景体检 测试（2026-10-14）。

用 tmp_path 构造最小 ProjectStore，验证：
- 字数维度：过短→bad、达标→ok
- 存在性：未生成章节→bad
- 重复度：复用 _beat_cycle_issues，含循环→bad
- 无产物降级：空目录调用不报错
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.scene_inspector import inspect_chapter


@pytest.fixture
def fake_store(tmp_path):
    proj = ProjectStore(tmp_path / "p1")
    proj.dir.mkdir(parents=True, exist_ok=True)
    return proj


@pytest.mark.p1
def test_inspect_missing_chapter(fake_store):
    r = inspect_chapter(fake_store, 99, target_words=2500)
    assert r["chapter"] == 99
    dim0 = r["dimensions"][0]
    assert dim0["level"] == "bad" and "尚未生成" in dim0["detail"]


@pytest.mark.p1
def test_inspect_short_content_bad(fake_store):
    # 仅 100 字，目标 2500 → 字数维度 bad
    fake_store.save_chapter(Chapter(no=1, title="c1", content="这是一段非常短的开头。" * 5))
    r = inspect_chapter(fake_store, 1, target_words=2500)
    words_dim = next(d for d in r["dimensions"] if d["dim"] == "字数")
    assert words_dim["level"] == "bad"
    assert words_dim["suggestion"]  # 有建议文案


@pytest.mark.p2
def test_inspect_ok_when_no_target(fake_store):
    fake_store.save_chapter(Chapter(no=1, title="c1", content="正常正文内容。"))
    r = inspect_chapter(fake_store, 1, target_words=0)
    words_dim = next(d for d in r["dimensions"] if d["dim"] == "字数")
    assert words_dim["level"] == "ok"


@pytest.mark.p1
def test_inspect_repetition_detected(fake_store):
    # 构造一个有情节循环重演的正文（同一序列重复多遍）
    loop = "他闭上眼，面板弹出，提示死亡次数加一，随即他猛地睁开眼又回到清晨。"
    content = loop + loop + loop + loop + loop + loop
    fake_store.save_chapter(Chapter(no=1, title="c1", content=content))
    r = inspect_chapter(fake_store, 1, target_words=2500)
    rep_dim = next(d for d in r["dimensions"] if d["dim"] == "重复度")
    assert rep_dim["level"] == "bad"
    assert rep_dim["suggestion"]
