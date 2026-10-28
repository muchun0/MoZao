# -*- coding: utf-8 -*-
"""version_diff.py 文本/结构化 diff 纯函数测试（P1）。

占位文本避开节拍词（§11.8）：不用"救/杀/转身/救人"等生成特征词，用中性叙述句。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.version_diff import (  # noqa: E402
    diff_chapter_text,
    diff_structured,
    MAX_DIFF_BLOCKS,
)


class TestChapterTextDiff:
    def test_identical_shortcut(self):
        r = diff_chapter_text("甲\n乙", "甲\n乙")
        assert r["identical"] is True
        assert r["blocks"] == []

    def test_add_and_del_paragraph(self):
        a = "第一段。\n第三段。"
        b = "第一段。\n新增段。\n第三段。"
        r = diff_chapter_text(a, b)
        assert r["identical"] is False
        types = [x["type"] for x in r["blocks"]]
        assert "add" in types
        add = next(x for x in r["blocks"] if x["type"] == "add")
        assert add["b_text"] == "新增段。"

    def test_replace_refines_to_mod(self):
        a = "天色慢慢暗下来，街市亮起灯。"
        b = "天色渐暗，沿街灯火次第亮起。"
        r = diff_chapter_text(a, b)
        types = [x["type"] for x in r["blocks"]]
        assert "mod" in types or ("del" in types and "add" in types)

    def test_blank_lines_ignored(self):
        r = diff_chapter_text("甲\n\n\n乙", "甲\n乙")
        assert r["identical"] is True

    def test_truncation_collapse(self):
        # 250 段全新增 → 250 个 add 块，必超 MAX_DIFF_BLOCKS
        a = ""
        b = "\n".join(f"全新第{i}段文字内容。" for i in range(250))
        r = diff_chapter_text(a, b)
        assert r["truncated"] is True
        collapse = [x for x in r["blocks"] if x["type"] == "collapse"]
        assert collapse and collapse[0]["count"] > 0
        # 块数（含折叠块）不超过上限 + 1
        assert len(r["blocks"]) <= MAX_DIFF_BLOCKS + 1


class TestStructuredDiff:
    def test_dict_key_added_removed_modified(self):
        a = {"name": "甲", "age": 18}
        b = {"name": "甲", "age": 20, "city": "京"}
        changes = diff_structured(a, b)
        types = {(c["path"], c["type"]) for c in changes}
        assert ("age", "modified") in types
        assert ("city", "added") in types

    def test_list_of_dict_aligned_by_name(self):
        a = {"chars": [{"name": "甲", "x": 1}, {"name": "乙", "x": 2}]}
        b = {"chars": [{"name": "乙", "x": 9}, {"name": "丙", "x": 3}]}
        changes = diff_structured(a, b)
        paths = {c["path"] for c in changes}
        # 丙 added、甲 removed、乙.x modified
        assert any("chars" in p and "丙" in p for p in paths)
        assert any("chars" in p and "甲" in p for p in paths)
        assert any("x" in p for p in paths)

    def test_group_mapping(self):
        a = {"foreshadowing": [{"name": "铜片"}]}
        b = {"foreshadowing": []}
        changes = diff_structured(a, b)
        assert changes[0]["group"] == "伏笔"

    def test_missing_field_tolerated(self):
        a = {"name": "甲"}  # 旧数据缺 age
        b = {"name": "甲", "age": 30}
        changes = diff_structured(a, b)
        assert any(c["type"] == "added" and c["path"] == "age" for c in changes)

    def test_truncate_long_text(self):
        a = {"bio": "字" * 1000}
        b = {"bio": "字" * 1000 + "新"}
        changes = diff_structured(a, b)
        old = changes[0]["old"]
        assert "…(截断)" in old

    def test_primitive_list_set_diff(self):
        changes = diff_structured(["a", "b"], ["b", "c"])
        types = {(c["name"], c["type"]) for c in changes}
        assert ("a", "removed") in types
        assert ("c", "added") in types
