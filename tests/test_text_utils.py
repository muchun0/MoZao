# -*- coding: utf-8 -*-
"""纯文本算法权威模块（novel_gen/utils/text.py）单元测试。"""
import pytest

from novel_gen.utils.text import longest_common_substr_len


@pytest.mark.p2
class TestLongestCommonSubstrLen:
    def test_contained(self):
        assert longest_common_substr_len("忘忧阁", "xx忘忧阁yy") == 3

    def test_partial_overlap(self):
        assert longest_common_substr_len("忘忧阁密报", "阁主密报进正文") == 2

    def test_no_common(self):
        assert longest_common_substr_len("abc", "xyz") == 0

    def test_single_char_not_counted(self):
        # 窗口从 2 起，单字符公共不算覆盖
        assert longest_common_substr_len("a", "ab") == 0

    def test_empty(self):
        assert longest_common_substr_len("", "x") == 0
        assert longest_common_substr_len("x", "") == 0

    def test_identical(self):
        s = "你好世界abc"
        assert longest_common_substr_len(s, s) == len(s)

    def test_order_insensitive(self):
        assert longest_common_substr_len("长串母串abcdef", "cde") == 3
        assert longest_common_substr_len("cde", "长串母串abcdef") == 3
