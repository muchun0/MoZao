# -*- coding: utf-8 -*-
"""chat_json 截断重试逻辑测试。

覆盖：
1. _is_json_truncation 正确识别截断类 JSONDecodeError
2. chat_json 遇到截断错误时自动增大 max_tokens 并重试
3. 非截断类格式错误不触发 max_tokens 增大
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.llm.base import LLMClient, LLMError, _is_json_truncation


# ---------- _is_json_truncation ----------

class TestIsJsonTruncation:
    @pytest.mark.parametrize("msg", [
        "Unterminated string starting at: line 295 column 26 (char 14032)",
        "Expecting value: line 5 column 10 (char 200)",
        "Expecting ',' delimiter: line 3 column 5 (char 50)",
        "Expecting property name enclosed in double quotes: line 1 column 2",
        "Expecting ':' delimiter: line 2 column 8",
        "Unterminated array starting at: line 1 column 1",
        "Unterminated object starting at: line 1 column 1",
    ])
    def test_truncation_errors_detected(self, msg):
        # pos=100 模拟截断发生在 JSON 中途（而非开头）
        fake = json.JSONDecodeError(msg, '{"a": 1, "b": "unfinished', 100)
        assert _is_json_truncation(fake) is True

    def test_pos_zero_not_truncation(self):
        """pos==0（JSON 开头就失败，如模型输出自然语言前缀）不算截断。"""
        fake = json.JSONDecodeError("Expecting value: line 1 column 1 (char 0)", "好的，以下是大纲", 0)
        assert _is_json_truncation(fake) is False

    def test_non_truncation_error(self):
        # Extra data 不是截断（即使 pos>0）
        fake = json.JSONDecodeError("Extra data: line 2 column 1 (char 10)", '{"a": 1}{"b": 2}', 10)
        assert _is_json_truncation(fake) is False

    def test_non_json_decode_error(self):
        assert _is_json_truncation(ValueError("some other error")) is False
        assert _is_json_truncation(LLMError("network error")) is False


# ---------- chat_json 截断重试 ----------

class _FakeLLM(LLMClient):
    """记录每次 chat 调用参数的 fake client。"""

    name = "fake"

    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.calls: list[dict] = []

    def _chat_impl(self, system, user, temperature=None, max_tokens=None,
                   frequency_penalty=None, presence_penalty=None, json_mode=None):
        self.calls.append({"max_tokens": max_tokens, "temperature": temperature})
        if not self._responses:
            return '{"ok": true}'
        return self._responses.pop(0)


class TestChatJsonTruncationRetry:
    @pytest.mark.p1
    def test_truncation_triggers_max_tokens_increase(self):
        """第一次输出截断 → 第二次自动增大 max_tokens → 返回成功。"""
        truncated = '{"title": "万墟之上", "chapters": [{"no": 1, "goal": "某'  # 未闭合
        good = '{"title": "万墟之上", "chapters": []}'

        llm = _FakeLLM([truncated, good])
        result = llm.chat_json("sys", "user", temperature=0.7, retries=2)

        assert result == {"title": "万墟之上", "chapters": []}
        # 共调用 2 次：第一次用 None（走 client 默认 4096），第二次应增大到 8192
        assert len(llm.calls) == 2
        assert llm.calls[0]["max_tokens"] is None
        assert llm.calls[1]["max_tokens"] == 8192

    @pytest.mark.p1
    def test_truncation_starts_from_specified_max_tokens(self):
        """调用方指定了 max_tokens 时，截断重试在其基础上 +4096。"""
        truncated = '{"a": "broken'
        good = '{"a": "fixed"}'

        llm = _FakeLLM([truncated, good])
        result = llm.chat_json("sys", "user", max_tokens=4096, retries=2)

        assert result == {"a": "fixed"}
        assert llm.calls[0]["max_tokens"] == 4096
        assert llm.calls[1]["max_tokens"] == 8192  # 4096 + 4096

    @pytest.mark.p1
    def test_truncation_increases_up_to_ceiling(self):
        """max_tokens 增大封顶 16384，不会无限增长。"""
        truncated = '{"a": "broken'
        good = '{"a": "fixed"}'

        llm = _FakeLLM([truncated, truncated, good])
        result = llm.chat_json("sys", "user", max_tokens=12000, retries=2)

        assert result == {"a": "fixed"}
        assert llm.calls[0]["max_tokens"] == 12000
        # 12000 + 4096 = 16096，未超 16384
        assert llm.calls[1]["max_tokens"] == 16096
        # 第三次（最后一次重试）：16096 + 4096 = 20192 → 封顶 16384
        assert llm.calls[2]["max_tokens"] == 16384

    @pytest.mark.p1
    def test_non_truncation_error_does_not_increase_tokens(self):
        """普通格式错误（非截断）不增大 max_tokens，保持原参数重试。"""
        # 模型输出了一段文字但不是 JSON（如 "好的，以下是大纲：..."）
        bad = "好的，我来为你生成大纲。"
        good = '{"ok": true}'

        llm = _FakeLLM([bad, good])
        result = llm.chat_json("sys", "user", max_tokens=4096, retries=2)

        assert result == {"ok": True}
        # 两次都应保持 4096，不增大
        assert llm.calls[0]["max_tokens"] == 4096
        assert llm.calls[1]["max_tokens"] == 4096

    @pytest.mark.p1
    def test_all_retries_fail_raises_llm_error(self):
        """全部重试都截断时，抛 LLMError。"""
        truncated = '{"a": "broken'

        llm = _FakeLLM([truncated, truncated, truncated])
        with pytest.raises(LLMError, match="无法解析为 JSON"):
            llm.chat_json("sys", "user", retries=2)

        # 3 次调用（1 + 2 重试）
        assert len(llm.calls) == 3
