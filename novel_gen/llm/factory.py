# -*- coding: utf-8 -*-
"""Provider 工厂：根据配置创建对应 LLM 客户端。"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from ..config import LLMConfig, _apply_provider_defaults, validate_llm_config
from .base import E_CONFIG_INVALID, LLMClient, LLMError
from .capabilities import is_mock
from .providers import MockClient, OpenAICompatibleClient


def create_llm(llm_cfg: LLMConfig) -> LLMClient:
    # 统一补齐 provider 默认值（model/base_url/缺失的 api_key 从环境变量读）。
    # 若不补齐：base_url 为空会走 openai 默认端点（api.openai.com），导致请求发错地方而超时卡住。
    llm_cfg = _apply_provider_defaults(llm_cfg)
    # 配置不可用必须立刻报错：把「配了不生效」变成「配错在哪里」，而不是发一次必败请求
    issues = validate_llm_config(llm_cfg)
    if issues:
        raise LLMError(
            "模型配置不可用：" + "；".join(issues),
            code=E_CONFIG_INVALID, retriable=False,
            diagnostics={"provider": llm_cfg.provider, "model": llm_cfg.model,
                         "base_url": llm_cfg.base_url},
        )
    if is_mock(llm_cfg.provider):
        return MockClient(temperature=llm_cfg.temperature, max_tokens=llm_cfg.max_tokens)
    return OpenAICompatibleClient(
        provider=llm_cfg.provider,
        model=llm_cfg.model,
        api_key=llm_cfg.api_key,
        base_url=llm_cfg.base_url,
        temperature=llm_cfg.temperature,
        top_p=llm_cfg.top_p,
        max_tokens=llm_cfg.max_tokens,
        timeout=llm_cfg.timeout,
        max_retries=llm_cfg.max_retries,
        thinking=llm_cfg.thinking,
        extra_body=llm_cfg.extra_body,
        headers=llm_cfg.headers,
    )


def clone_with(llm_cfg: LLMConfig, *, timeout: Optional[float] = None,
               max_retries: Optional[int] = None) -> LLMClient:
    """按同一份 LLM 配置重建客户端，只覆盖网络策略（统一克隆入口）。

    R14/R16 的质检快速失败（30s / 0 重试）可直接调用本函数，避免各处重复拼配置；
    token 统计 / trace / 成本防护由调用方注入（与 create_llm 一致）。
    """
    if timeout is not None or max_retries is not None:
        llm_cfg = replace(
            llm_cfg,
            timeout=llm_cfg.timeout if timeout is None else timeout,
            max_retries=llm_cfg.max_retries if max_retries is None else max_retries,
        )
    return create_llm(llm_cfg)
