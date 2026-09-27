# -*- coding: utf-8 -*-
"""
LLM Token 用量统计（模块1/2 共用）。

- Usage 累计 prompt/completion/total token，并保留最近一次调用用量（last_*），
  供埋点（模块1）与成本防护（模块2）读取。
- Provider 在 _chat_impl 内拿到响应后调用 note() 上报；拿不到 usage 时不上报（保持 0）。
"""
from __future__ import annotations


class Usage:
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0

    def note(self, prompt_tokens: int = 0, completion_tokens: int = 0,
             total_tokens: int = 0) -> None:
        """上报一次调用用量（幂等；为 0 时按前两者推算）。"""
        total = int(total_tokens or 0)
        if total <= 0:
            total = int(prompt_tokens or 0) + int(completion_tokens or 0)
        self.prompt_tokens += int(prompt_tokens or 0)
        self.completion_tokens += int(completion_tokens or 0)
        self.total_tokens += total
        self.last_prompt_tokens = int(prompt_tokens or 0)
        self.last_completion_tokens = int(completion_tokens or 0)
        self.last_total_tokens = total

    def reset(self) -> None:
        """重置累计（一般用于单任务粒度重新计数）。"""
        self.__init__()
