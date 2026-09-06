# -*- coding: utf-8 -*-
"""
LLM 抽象基类：所有 Provider 统一实现 chat / chat_json 两个能力。

为什么要抽象：
- 生成流水线只依赖这两个方法，不关心底层是 DeepSeek 还是豆包
- chat_json 负责「让模型输出结构化 JSON」（人物卡/世界观用），统一在这里做解析与失败重试
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Optional


class LLMError(Exception):
    """LLM 调用失败（网络、鉴权、限流、格式错误等）。"""


class LLMClient(ABC):
    """大模型客户端抽象基类。"""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """单轮对话，返回纯文本。temperature/max_tokens 不传时用配置默认值。"""

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retries: int = 2,
    ) -> dict:
        """让模型输出 JSON，并解析为 dict。解析失败时自动重试（模型偶发输出残缺 JSON）。

        实现方式：向模型强调输出为合法 JSON，收到文本后用 json.loads 解析；
        失败则把「解析错误信息」回传给模型要求修正，最多重试 retries 次。
        """
        parse_tip = (
            "你的回复必须是单个合法的 JSON 对象，不要输出任何解释、代码块标记或额外文字。"
        )
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                text = self.chat(
                    system=f"{system}\n\n{parse_tip}",
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return _parse_json_strict(text)
            except (json.JSONDecodeError, LLMError) as exc:
                last_err = exc
                if attempt < retries:
                    # 把错误回传给模型，要求它只输出修正后的 JSON
                    user = (
                        f"{user}\n\n上一次输出无法解析为 JSON，错误：{exc}\n"
                        f"请重新输出，只返回一个合法的 JSON 对象。"
                    )
        raise LLMError(f"模型输出无法解析为 JSON，已重试 {retries} 次。原因：{last_err}")


def _parse_json_strict(text: str) -> dict:
    """严格解析模型返回的 JSON：容忍外层被 ```json ... ``` 包裹。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉 ```json / ``` 包裹
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise json.JSONDecodeError("期望 JSON 对象", t, 0)
    return obj
