# -*- coding: utf-8 -*-
"""作者视角全流程走查（2026-11-22）修复的回归用例。

覆盖：失败错误脱敏、纯文本导出、Markdown/仅标题大纲切分、老项目判定不被一次失败 run 污染。
"""
from __future__ import annotations

import json
from pathlib import Path

from novel_gen.store.project import ProjectStore, _display_name
from novel_gen.store.schemas import Chapter
from novel_gen.config import NovelConfig
from novel_gen.utils.chapter_splitter import split_chapters
from webapp.error_text import sanitize_error


def test_sanitize_error_hides_provider_and_key():
    raw = "openai.AuthenticationError: Incorrect API key sk-abc123 (provider=deepseek, base_url=http://x)"
    out = sanitize_error(raw)
    assert "sk-abc123" not in out
    assert "openai" not in out.lower()
    assert "模型" in out


def test_sanitize_error_keeps_actionable_reason():
    assert "余额不足" in sanitize_error("余额不足：还差 1.20 元")


def test_sanitize_error_strips_exception_prefix():
    assert sanitize_error("RuntimeError: 调研未产出（扩写模式下调研不适用）") == "调研未产出（扩写模式下调研不适用）"


def test_display_name_keeps_chinese_and_strips_illegal_chars():
    assert _display_name("万墟之上：第一部/开篇") == "万墟之上：第一部_开篇"
    assert _display_name('  bad<>:"|name  ') == "bad_name"


def test_split_markdown_heading_and_title_only_chapters():
    text = "## 第1章 雨夜\n\n正文一\n\n## 第2章 未写\n\n## 第3章 决战\n正文三"
    chapters = split_chapters(text)
    assert [c["title"] for c in chapters] == ["第1章 雨夜", "第2章 未写", "第3章 决战"]
    assert chapters[0]["content"] == "正文一"
    # 只有标题没有正文也要保留为待写章节
    assert chapters[1]["content"] == ""


def test_plain_text_export_reads_chapters_without_model(tmp_path):
    store = ProjectStore(tmp_path / "book")
    store.save_config(NovelConfig(title="万墟之上", premise="一句话简介"))
    store.save_chapter(Chapter(no=1, title="雨夜", content="第一段。"))
    store.save_chapter(Chapter(no=2, title="决战", content="第二段。"))
    from webapp.routers.projects_base import _build_plain_export

    txt = _build_plain_export(store, store.load_config(), "txt")
    assert "万墟之上" in txt and "第1章 雨夜" in txt and "第一段。" in txt
    md = _build_plain_export(store, store.load_config(), "md")
    assert md.startswith("# 万墟之上")
    assert "## 第2章 决战" in md


def test_failed_run_does_not_mark_project_legacy(tmp_path):
    store = ProjectStore(tmp_path / "book")
    store.save_config(NovelConfig(title="新书"))
    runs = Path(store.dir) / "agent_runs"
    runs.mkdir()
    (runs / "r1.json").write_text(json.dumps({"status": "failed", "outputs": []}), encoding="utf-8")
    from webapp import generation_flow

    assert generation_flow._is_legacy(store) is False

    (runs / "r2.json").write_text(json.dumps({"status": "ok", "outputs": [{"kind": "outline"}]}),
                                  encoding="utf-8")
    assert generation_flow._is_legacy(store) is True
