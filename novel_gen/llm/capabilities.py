# -*- coding: utf-8 -*-
"""Provider 能力档案（单一权威源）。

目的：把「某家 provider 支持什么参数」收敛成声明式数据表，替代散落各处的
`if provider == "xxx"` 硬编码。新增一家 provider 只需在 PROVIDER_PROFILES 加一行。

字段约定（全部可选，缺省按保守值处理）：
- protocol：协议族，当前只有 openai（OpenAI 兼容）；azure/gemini 原生需另写适配。
- default_model / default_base_url / env_key：未填时补齐的默认值。
- thinking：思考开关描述。{"field": extra_body 字段名,
  "disabled": 关闭值, "enabled": 开启值}；None 表示不支持/未知，不注入。
- json_mode：response_format 能力；"json_object" 支持，None 不支持。
- supports_temperature：是否接受 temperature 参数。
- max_output_tokens / context_window：能力上限（用于校验与提示，不做硬截断）。
- requires_base_url / requires_model：必须显式配置，否则拒绝（custom 用）。

thinking 的三种模式（LLMConfig.thinking）：
- auto：已知模型的「默认开启思考」会被自动关闭（写作流水线不需要推理过程，
  推理 token 会挤占 max_tokens 导致正文为空）；未知模型不注入。
- off / on：显式要求关闭 / 开启；profile 不支持时保持静默（不写无效参数）。
"""
from __future__ import annotations

from typing import Optional

# 各 provider 能力档案（provider 名称统一小写）
PROVIDER_PROFILES: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "protocol": "openai",
        "default_model": "deepseek-flash",
        "default_base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        # DeepSeek V4 默认开思考：显式关闭，避免推理 token 挤占正文预算
        "thinking": {
            "field": "thinking",
            "disabled": {"type": "disabled"},
            "enabled": {"type": "enabled"},
        },
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 8192,
        "context_window": 128_000,
    },
    "doubao": {  # 火山方舟（豆包 / Ark 接入点 ep-xxx）
        "label": "火山方舟（豆包）",
        "protocol": "openai",
        "default_model": "doubao-seed-1-6-250615",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "env_key": "ARK_API_KEY",
        # Ark 背后的思考型模型（如 deepseek-v4 接入点）同样用 thinking.type 开关
        "thinking": {
            "field": "thinking",
            "disabled": {"type": "disabled"},
            "enabled": {"type": "enabled"},
        },
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 16_384,
        "context_window": 256_000,
    },
    "openai": {
        "label": "OpenAI",
        "protocol": "openai",
        "default_model": "gpt-4o-mini",
        "default_base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "thinking": None,
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 16_384,
        "context_window": 128_000,
    },
    "zhipu": {
        "label": "智谱 GLM",
        "protocol": "openai",
        "default_model": "glm-4-flash",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "ZHIPU_API_KEY",
        "thinking": {
            "field": "thinking",
            "disabled": {"type": "disabled"},
            "enabled": {"type": "enabled"},
        },
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 8_192,
        "context_window": 128_000,
    },
    "qwen": {
        "label": "通义千问",
        "protocol": "openai",
        "default_model": "qwen-plus",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
        # DashScope 兼容模式用 enable_thinking 开关（布尔）
        "thinking": {
            "field": "enable_thinking",
            "disabled": False,
            "enabled": True,
        },
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 8_192,
        "context_window": 131_072,
    },
    "moonshot": {
        "label": "Moonshot",
        "protocol": "openai",
        "default_model": "moonshot-v1-8k",
        "default_base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "thinking": None,
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 8_192,
        "context_window": 32_768,
    },
    "custom": {  # 任意 OpenAI 兼容中转：base_url / model 必须显式提供
        "label": "自定义 OpenAI 兼容",
        "protocol": "openai",
        "default_model": "",
        "default_base_url": "",
        "env_key": "",
        # 未知厂商：auto 不注入；用户显式关/开时按通用 thinking.type 约定透传
        "thinking": {
            "field": "thinking",
            "disabled": {"type": "disabled"},
            "enabled": {"type": "enabled"},
        },
        "json_mode": "json_object",
        "supports_temperature": True,
        "max_output_tokens": 16_384,
        "context_window": 128_000,
        "requires_base_url": True,
        "requires_model": True,
    },
    "mock": {  # 本地假 Provider：不联网，返回占位文本，用于无 Key 冒烟测试
        "label": "本地 Mock",
        "protocol": "mock",
        "default_model": "mock-model",
        "default_base_url": "",
        "env_key": "",
        "thinking": None,
        "json_mode": None,
        "supports_temperature": True,
        "max_output_tokens": 4_096,
        "context_window": 8_192,
    },
}

# provider 别名 → 标准名（大小写/连字符/常见别称归一，避免配置了却绕过能力档案）
_PROVIDER_ALIASES: dict[str, str] = {
    "ark": "doubao",
    "volc": "doubao",
    "volcengine": "doubao",
    "volcano": "doubao",
    "byte": "doubao",
    "bytedance": "doubao",
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "openai_compat": "custom",
    "dashscope": "qwen",
    "aliyun": "qwen",
    "alibaba": "qwen",
    "glm": "zhipu",
    "bigmodel": "zhipu",
    "kimi": "moonshot",
}


def normalize_provider(provider: Optional[str]) -> str:
    """provider 名归一：去空格/小写/解别名。未知名原样返回（由校验层拒绝）。"""
    name = (provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(name, name)


def resolve_profile(provider: Optional[str]) -> Optional[dict]:
    """返回 provider 的能力档案；未知 provider 返回 None（调用方应显式报错）。"""
    return PROVIDER_PROFILES.get(normalize_provider(provider))


def is_mock(provider: Optional[str]) -> bool:
    return normalize_provider(provider) == "mock"


def provider_defaults() -> dict[str, dict]:
    """旧 PROVIDER_DEFAULTS 形状（model/base_url/env_key），供过渡期 re-export。"""
    return {
        name: {
            "model": p.get("default_model", ""),
            "base_url": p.get("default_base_url", ""),
            "env_key": p.get("env_key", ""),
        }
        for name, p in PROVIDER_PROFILES.items()
    }


def list_providers(include_mock: bool = False) -> list[dict]:
    """供 BYOK 界面 / 接口复用的 provider 清单（按档案顺序）。"""
    items = []
    for name, p in PROVIDER_PROFILES.items():
        if name == "mock" and not include_mock:
            continue
        items.append({
            "value": name,
            "label": p.get("label", name),
            "supports_thinking": p.get("thinking") is not None,
            "requires_base_url": bool(p.get("requires_base_url")),
            "requires_model": bool(p.get("requires_model")),
            "default_base_url": p.get("default_base_url", ""),
        })
    return items


def thinking_extra(profile: Optional[dict], mode: str) -> Optional[dict]:
    """按能力档案与 thinking 模式，返回需要合并进 extra_body 的字段。

    - auto：已知会默认开思考的 provider 自动关闭（写作流水线不需要推理过程）
    - on / off：显式开关；profile 不支持时返回 None（不发送无效字段）
    """
    if not profile or not profile.get("thinking"):
        return None
    cfg = profile["thinking"]
    if mode == "off":
        return {cfg["field"]: cfg.get("disabled")}
    if mode == "on":
        return {cfg["field"]: cfg.get("enabled")}
    # auto：默认关闭（只在明确支持思考开关的 provider 上执行）
    return {cfg["field"]: cfg.get("disabled")}
