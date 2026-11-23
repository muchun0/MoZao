# -*- coding: utf-8 -*-
"""作者视角全流程走查（2026-11-22）修复的回归用例。

覆盖：失败错误脱敏、纯文本导出、Markdown/仅标题大纲切分、老项目判定不被一次失败 run 污染。
"""
from __future__ import annotations

import json
from pathlib import Path

from novel_gen.store.project import ProjectStore, _display_name
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline
from novel_gen.config import NovelConfig
from novel_gen.utils.chapter_splitter import split_chapters
from webapp.error_text import sanitize_error
from fastapi import HTTPException
import pytest


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


# ---------- 第二轮复验（2026-11-23）遗留项 ----------

def test_unlabeled_import_fills_goal_and_core_event():
    """作者只贴梗概时，至少把事件落到 core_event，别让章纲只剩「剧情目标」一项。"""
    from webapp.routers.projects_base import _parse_chapter_plan

    one = _parse_chapter_plan(1, "雨夜来客", "主角在雨夜被人敲门惊醒。")
    assert one.goal
    assert one.core_event == one.goal

    many = _parse_chapter_plan(2, "旧信", "主角收到一封旧信。信里提到十年前的船难。")
    assert many.goal == "主角收到一封旧信。"
    assert "十年前的船难" in many.core_event


def test_import_outline_does_not_take_chapter_heading_as_book_title(tmp_path):
    """直接贴「第1章 …」时，别把章标题当书名。"""
    from webapp.routers.projects_base import _import_user_outline

    store = ProjectStore(tmp_path / "book")
    store.save_config(NovelConfig(title="雨夜灯塔", chapter_words=1000))
    text = "第1章 雨夜来客\n主角被人敲门惊醒。\n\n第2章 旧信\n主角收到一封旧信。"
    assert _import_user_outline(store, text) is True
    outline = store.load_outline()
    assert outline.title == "雨夜灯塔"
    assert [c.title for c in outline.chapters] == ["第1章 雨夜来客", "第2章 旧信"]


def test_imported_outline_field_checks_collapse_to_one_info():
    """扩写模式导入的纯梗概大纲：不逐字段报缺，只给一条可留空说明。"""
    from webapp.gates import _missing_field_checks

    chapters = [ChapterPlan(no=1, title="一", goal="主角醒来", core_event="主角醒来"),
                ChapterPlan(no=2, title="二", goal="主角出门", core_event="主角出门")]
    checks = _missing_field_checks(chapters, NovelConfig(title="t", mode="expand"))
    assert len(checks) == 1
    assert checks[0]["level"] == "info"
    assert "可留空" not in checks[0]["detail"] and "不影响生成" in checks[0]["detail"]


def test_chapter_range_out_of_bounds_returns_400(tmp_path):
    """第 9 章（大纲仅 4 章）必须 400，不能静默跑 0 章。"""
    from webapp.generate_service import guard_chapter_range

    store = ProjectStore(tmp_path / "book")
    store.save_config(NovelConfig(title="t"))
    store.save_outline(Outline(title="t", intro="", chapters=[
        ChapterPlan(no=i, title=f"第{i}章", goal="g") for i in range(1, 5)]))
    with pytest.raises(HTTPException) as exc:
        guard_chapter_range(store, "writer.chapter", {"scope": "single", "ch_no": 9})
    assert exc.value.status_code == 400
    assert "只有 4 章" in exc.value.detail

    with pytest.raises(HTTPException):
        guard_chapter_range(store, "writer.chapter",
                            {"scope": "range", "from_ch": 1, "to_ch": 9})
    # 范围内不拦
    guard_chapter_range(store, "writer.chapter", {"scope": "range", "from_ch": 2, "to_ch": 4})


def test_estimate_factor_uses_actual_chapter_words(tmp_path):
    """预估按近几章实际字数修正：目标 1000、实际 1834 → 约 1.83 倍。"""
    from webapp.generate_service import _estimate_factor, _estimated_price

    store = ProjectStore(tmp_path / "book")
    store.save_config(NovelConfig(title="t", chapter_words=1000))
    assert _estimate_factor(store, 1000) == 1.0
    store.save_chapter(Chapter(no=1, title="一", content="字" * 1834), source="ai")
    assert round(_estimate_factor(store, 1000), 2) == 1.83
    # 预估价随实际字数上浮（3 元/万字）
    price = _estimated_price("writer.chapter", {"scope": "single"},
                             store, "platform")
    assert price >= 0.5
