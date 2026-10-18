# -*- coding: utf-8 -*-
"""
LLM 抽象基类：所有 Provider 统一实现 chat / chat_json 两个能力。

为什么要抽象：
- 生成流水线只依赖这两个方法，不关心底层是 DeepSeek 还是豆包
- chat_json 负责「让模型输出结构化 JSON」（人物卡/世界观用），统一在这里做解析与失败重试

生产级加固（阶段1）：
- chat 改为模板方法：自动为**每一次** LLM 调用埋点（Prompt/返回/Token/耗时/错误栈），
  写入 TraceRecorder（模块1）；调用方零改动。
- 挂载 AgentGuard（模块2）：每次调用前检查循环上限，调用后累计 Token 并检查阈值。
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Optional

from .usage import Usage


class LLMError(Exception):
    """LLM 调用失败（网络、鉴权、限流、格式错误等）。"""


class LLMClient(ABC):
    """大模型客户端抽象基类。"""

    name: str = "base"

    def __init__(self):
        # 阶段1：调用统计（Token 防护与监控用）
        self.usage = Usage()
        self.call_count = 0
        # 阶段1：埋点与防护挂载点（由 NovelPipeline 注入）
        self.recorder = None   # TraceRecorder | None
        self.guard = None      # AgentGuard | None
        # REQ-超长篇攻坚 S4.3：JSON 输出约束开关（factory/管线注入，provider 支持时生效）
        self.json_schema_output = False

    def chat(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        json_mode: Optional[bool] = None,
    ) -> str:
        """单轮对话，返回纯文本。模板方法：包装 _chat_impl 做埋点与成本防护。

        json_mode：为 True 时向 provider 传 response_format json_object（provider 支持则
        强制结构化 JSON 输出，降低 chat_json 解析重试；不支持时降级回 parse_tip 路径）。
        """
        # 模块2：循环上限检查（调用前）
        if self.guard is not None:
            self.guard.before_call()
        start = time.monotonic()
        try:
            text = self._chat_impl(
                system, user,
                temperature=temperature,
                max_tokens=max_tokens,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
                json_mode=json_mode,
            )
        except Exception as exc:
            self._after_call(system, user, None, exc, start)
            raise
        self._after_call(system, user, text, None, start)
        return text

    @abstractmethod
    def _chat_impl(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        json_mode: Optional[bool] = None,
    ) -> str:
        """子类实现：真正调用 Provider。json_mode 由子类按自身能力决定是否启用 response_format。"""

    # ---------- 阶段1：埋点 + Token 统计 ----------
    def _after_call(self, system: str, user: str, text: Optional[str],
                    exc: Optional[Exception], start: float) -> None:
        duration_ms = int((time.monotonic() - start) * 1000)
        self.call_count += 1
        tok_in = self.usage.last_prompt_tokens
        tok_out = self.usage.last_completion_tokens
        # 模块2：Token 阈值检查（调用后，按真实消耗累计）
        if self.guard is not None and text is not None:
            self.guard.after_call(self.usage)
        # 模块1：LLM 调用级埋点
        if self.recorder is not None:
            try:
                self.recorder.record(
                    step_type="llm_call",
                    step_name=f"{self.name} {self.model_label()}",
                    system=system,
                    user=user,
                    output=text or "",
                    token_in=tok_in,
                    token_out=tok_out,
                    duration_ms=duration_ms,
                    success=exc is None,
                    error=f"{type(exc).__name__}: {exc}" if exc else "",
                    model=self.model_label(),
                    extra={
                        "call_index": self.call_count,
                        # REQ-超长篇攻坚 S4.1：前缀缓存命中 token（DeepSeek 返回），供命中率验证
                        "prompt_cache_hit_tokens": self.usage.last_prompt_cache_hit_tokens,
                    },
                )
            except Exception:
                pass

    def model_label(self) -> str:
        return getattr(self, "model", self.name)

    def close(self) -> None:
        """子类按需关闭底层客户端。"""

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retries: int = 2,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        json_mode: Optional[bool] = None,
    ) -> dict:
        """让模型输出 JSON，并解析为 dict。解析失败时自动重试（模型偶发输出残缺 JSON）。

        实现方式：向模型强调输出为合法 JSON，收到文本后用 json.loads 解析；
        失败则把「解析错误信息」回传给模型要求修正，最多重试 retries 次。
        若判定错误是输出被 max_tokens 截断（Unterminated string 等），
        重试时自动增大 token 上限（+4096，封顶 16384），避免反复在同一上限处截断。

        json_mode：S4.3 约束——None 时按 self.json_schema_output 决定；为 True 时向
        provider 传 response_format json_object（支持则消除解析重试），不支持自动降级。
        """
        parse_tip = (
            "你的回复必须是单个合法的 JSON 对象，不要输出任何解释、代码块标记或额外文字。"
        )
        last_err: Optional[Exception] = None
        # 记录本次调用实际使用的 max_tokens，截断时自动增大
        cur_max_tokens = max_tokens
        use_json_mode = self.json_schema_output if json_mode is None else json_mode
        for attempt in range(retries + 1):
            try:
                text = self.chat(
                    system=f"{system}\n\n{parse_tip}",
                    user=user,
                    temperature=temperature,
                    max_tokens=cur_max_tokens,
                    frequency_penalty=frequency_penalty,
                    presence_penalty=presence_penalty,
                    json_mode=use_json_mode,
                )
                return _parse_json_strict(text)
            except (json.JSONDecodeError, LLMError) as exc:
                last_err = exc
                if attempt < retries:
                    if _is_json_truncation(exc):
                        # 输出被 max_tokens 截断：自动增大上限重试
                        if cur_max_tokens is None:
                            cur_max_tokens = 8192
                        else:
                            cur_max_tokens = min(cur_max_tokens + 4096, 16384)
                        user = (
                            f"{user}\n\n上一次输出在中途被截断了（JSON 不完整），"
                            f"错误：{exc}\n"
                            f"请输出完整的 JSON 对象，确保所有字段都填写完整，不要省略任何内容。"
                        )
                    else:
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


def _is_json_truncation(exc: Exception) -> bool:
    """判断 JSONDecodeError 是否由输出被 max_tokens 截断导致（而非格式错误）。

    截断类错误的典型特征：JSON 在中途断开，字符串/数组/对象未闭合。
    pos == 0 说明输出根本不是以 JSON 开头（如模型输出了自然语言前缀），
    属于格式问题而非 token 截断。
    """
    if not isinstance(exc, json.JSONDecodeError):
        return False
    if exc.pos == 0:
        return False
    msg = str(exc)
    truncation_signals = (
        "Unterminated string",
        "Unterminated array",
        "Unterminated object",
        "Expecting value",
        "Expecting ',' delimiter",
        "Expecting property name",
        "Expecting ':' delimiter",
    )
    return any(sig in msg for sig in truncation_signals)
