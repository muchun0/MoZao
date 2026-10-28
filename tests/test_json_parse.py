# -*- coding: utf-8 -*-
"""LLM JSON 容错解析权威模块（novel_gen/llm/json_parse.py）单元测试。"""
import pytest

from novel_gen.llm.json_parse import parse_json_obj


@pytest.mark.p2
class TestParseJsonObj:
    def test_valid_dict(self):
        assert parse_json_obj('{"a": 1}') == {"a": 1}

    def test_fenced_codeblock(self):
        assert parse_json_obj('```json\n{"b": 2}\n```') == {"b": 2}

    def test_fenced_plain(self):
        assert parse_json_obj('```\n{"c": 3}\n```') == {"c": 3}

    def test_trailing_comma_repaired(self):
        assert parse_json_obj('{"a": 1,}') == {"a": 1}

    def test_truncated_returns_empty(self):
        assert parse_json_obj('{"a": 1, "b": [1,2') == {}

    def test_non_dict_returns_empty(self):
        assert parse_json_obj('[1, 2]') == {}

    def test_invalid_returns_empty(self):
        assert parse_json_obj('not json') == {}

    def test_empty_or_none(self):
        assert parse_json_obj("") == {}
        assert parse_json_obj(None) == {}

    def test_leading_text_then_json(self):
        # 截断检测：剥离 JSON 之前的前缀文本
        assert parse_json_obj('参考：{"a":1}') == {"a": 1}

    def test_nested_dict(self):
        assert parse_json_obj('{"a": {"b": [1, 2]}}') == {"a": {"b": [1, 2]}}
