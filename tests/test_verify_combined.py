# -*- coding: utf-8 -*-
"""P1-5 一致性校验合并测试（P1，REQ-token优化）。

覆盖：verify_combined_prompt 三类检查同一次输出、_parse_json_obj 围栏解析、
manager._run_verify 合并路径只调 1 次 LLM、verify_every 频率配置。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.templates import verify_combined_prompt
from novel_gen.pipeline.manager import NovelPipeline, _parse_json_obj


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "v", cfg)


@pytest.mark.p1
def test_verify_combined_prompt_three_checks(store):
    cfg = store.load_config()
    sys_s, user_s = verify_combined_prompt(cfg, 5, ["s1", "s2", "s3"], "世界", "正文")
    assert "1. consistency" in user_s
    assert "2. reader" in user_s
    assert "3. editor" in user_s
    assert '"issues"' in user_s and '"reader"' in user_s and '"editor"' in user_s
    assert "【最近几章摘要】" in user_s


@pytest.mark.p2
def test_parse_json_obj_fence(store):
    raw = '```json\n{"issues": [], "editor": {"score": 6}}\n```'
    assert _parse_json_obj(raw).get("editor", {}).get("score") == 6
    assert _parse_json_obj("不是JSON") == {}
    assert _parse_json_obj("[1,2]") == {}


@pytest.mark.p1
def test_run_verify_combined_single_call(store):
    """verify_combined=True：_run_verify 只调 1 次 LLM，输出三类结果并落 issues。"""
    cfg = store.load_config()
    cfg.high_quality_mode = True
    store.save_config(cfg)
    store.save_chapter(Chapter(no=5, title="第五章", content="正文" * 200, summary="s5"))
    store.append_summary(5, "第五章摘要")
    llm = MagicMock()
    llm.chat.return_value = (
        '{"issues": [{"problem": "时间线矛盾", "suggestion": "调整"}], '
        '"reader": {"confused": true, "issues": [{"problem": "人物分不清", "suggestion": "加称呼"}]}, '
        '"editor": {"score": 6, "reader_churn_risk": "中", "issues": []}}'
    )
    pipe = NovelPipeline(cfg, store, llm)
    pipe._run_verify(5, silent=True)
    assert llm.chat.call_count == 1
    issues = store.load_verify_issues()
    assert any("时间线矛盾" in i.get("problem", "") for i in issues)
    assert any("人物分不清" in i.get("problem", "") for i in issues)


@pytest.mark.p2
def test_verify_every_config(store):
    cfg = store.load_config()
    assert cfg.verify_every == 5
    assert cfg.verify_combined is True
