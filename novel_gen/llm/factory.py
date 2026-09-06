# -*- coding: utf-8 -*-
"""Provider 工厂：根据配置创建对应 LLM 客户端。"""
from __future__ import annotations

from ..config import LLMConfig, _apply_provider_defaults
from .base import LLMClient
from .providers import MockClient, OpenAICompatibleClient


def create_llm(llm_cfg: LLMConfig) -> LLMClient:
    # 统一补齐 provider 默认值（model/base_url/缺失的 api_key 从环境变量读）。
    # 若不补齐：base_url 为空会走 openai 默认端点（api.openai.com），导致请求发错地方而超时卡住。
    llm_cfg = _apply_provider_defaults(llm_cfg)
    if llm_cfg.provider == "mock":
        return MockClient(temperature=llm_cfg.temperature, max_tokens=llm_cfg.max_tokens)
    return OpenAICompatibleClient(
        provider=llm_cfg.provider,
        model=llm_cfg.model,
        api_key=llm_cfg.api_key,
        base_url=llm_cfg.base_url,
        temperature=llm_cfg.temperature,
        top_p=llm_cfg.top_p,
        max_tokens=llm_cfg.max_tokens,
    )
