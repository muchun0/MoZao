# -*- coding: utf-8 -*-
"""多 Provider 兼容性单测（零真实网络调用：用 FakeOpenAI 替换底层 SDK）。

覆盖：能力档案归一 / 配置默认值与校验 / 参数能力门控与透传 / 结构化错误分类 /
降参重试 / chat_json 不再对必败参数重试 / 客户端克隆共享统计。
"""
from types import SimpleNamespace

import pytest

from novel_gen.config import LLMConfig, _apply_provider_defaults, validate_llm_config
from novel_gen.llm import providers as providers_mod
from novel_gen.llm.base import (
    E_AUTH_FAILED, E_CONFIG_INVALID, E_EMPTY_CONTENT_THINKING, E_MODEL_NOT_FOUND,
    E_PARAM_UNSUPPORTED, LLMError,
)
from novel_gen.llm.capabilities import (
    list_providers, normalize_provider, resolve_profile, thinking_extra,
)
from novel_gen.llm.factory import create_llm
from novel_gen.llm.providers import OpenAICompatibleClient


# ---------------- Fake OpenAI SDK ----------------

def _resp(content="正文", finish_reason="stop", model="deepseek-flash", usage=True):
    message = SimpleNamespace(content=content, reasoning_content=None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    u = (SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                         prompt_cache_hit_tokens=0) if usage else None)
    return SimpleNamespace(choices=[choice], model=model, usage=u)


class _ApiError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class _FakeOpenAI:
    """记录请求参数，按 responder 返回结果或抛错。"""

    def __init__(self, responder):
        self.requests: list[dict] = []
        self.init_kwargs = {}
        fake = self

        class _Completions:
            def create(self, **kwargs):
                fake.requests.append(kwargs)
                return responder(kwargs)

        self.chat = SimpleNamespace(completions=_Completions())

    def close(self):
        pass


@pytest.fixture
def fake_openai(monkeypatch):
    """返回安装器：install(responder) 后构造的客户端都走 FakeOpenAI。"""
    def install(responder):
        holder = {}

        def _factory(**kwargs):
            client = _FakeOpenAI(responder)
            client.init_kwargs = kwargs
            holder["client"] = client
            return client

        monkeypatch.setattr(providers_mod, "OpenAI", _factory)
        return holder
    return install


def _make_client(**overrides):
    params = dict(provider="deepseek", model="deepseek-flash", api_key="sk-test",
                  base_url="https://api.deepseek.com")
    params.update(overrides)
    return OpenAICompatibleClient(**params)


# ---------------- 能力档案与配置 ----------------

def test_provider_aliases_and_list():
    assert normalize_provider("Ark") == "doubao"
    assert normalize_provider(" OpenAI-Compatible ") == "custom"
    assert normalize_provider("GLM") == "zhipu"
    assert resolve_profile("unknown") is None
    values = [p["value"] for p in list_providers()]
    assert "doubao" in values and "custom" in values and "mock" not in values
    doubao = next(p for p in list_providers() if p["value"] == "doubao")
    assert doubao["supports_thinking"] is True


def test_apply_defaults_and_validate(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
    cfg = _apply_provider_defaults(LLMConfig(provider="Ark", model="ep-2026", base_url=""))
    assert cfg.provider == "doubao"
    assert cfg.base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg.api_key == "ark-test-key"
    assert validate_llm_config(cfg) == []

    # custom 不提供默认端点：缺 base_url/model 必须报错，绝不静默回落 api.openai.com
    custom = LLMConfig(provider="custom", api_key="sk-x")
    issues = validate_llm_config(_apply_provider_defaults(custom))
    assert any("base_url" in i for i in issues) and any("model" in i for i in issues)

    unknown_issues = validate_llm_config(LLMConfig(provider="nope", api_key="x", model="m"))
    assert any("未知 provider" in i for i in unknown_issues)


def test_thinking_gating():
    assert thinking_extra(resolve_profile("deepseek"), "auto") == {"thinking": {"type": "disabled"}}
    assert thinking_extra(resolve_profile("deepseek"), "on") == {"thinking": {"type": "enabled"}}
    assert thinking_extra(resolve_profile("openai"), "off") is None
    assert thinking_extra(resolve_profile("qwen"), "off") == {"enable_thinking": False}


# ---------------- 参数能力门控与透传 ----------------

def test_build_params_gating_and_passthrough():
    client = _make_client(thinking="auto", extra_body={"top_k": 20})
    params = client._build_params(temperature=0.7, max_tokens=1000,
                                  frequency_penalty=None, presence_penalty=None,
                                  json_mode=True)
    assert params["temperature"] == 0.7
    assert params["response_format"] == {"type": "json_object"}
    assert params["extra_body"] == {"top_k": 20, "thinking": {"type": "disabled"}}

    # 用户显式配置的 thinking 优先于能力档案注入（深合并，不覆盖用户值）
    client2 = _make_client(thinking="auto", extra_body={"thinking": {"type": "enabled"}})
    params2 = client2._build_params(temperature=0.7, max_tokens=1000,
                                    frequency_penalty=None, presence_penalty=None,
                                    json_mode=False)
    assert params2["extra_body"]["thinking"] == {"type": "enabled"}
    assert "response_format" not in params2

    # openai 无思考能力：auto/off 都不注入无效字段
    client3 = _make_client(provider="openai", base_url="https://api.openai.com/v1")
    params3 = client3._build_params(temperature=0.7, max_tokens=1000,
                                    frequency_penalty=None, presence_penalty=None,
                                    json_mode=False)
    assert "extra_body" not in params3


# ---------------- 结构化错误 ----------------

def test_empty_content_with_thinking_is_not_retriable(fake_openai):
    fake_openai(lambda kw: _resp(content="", finish_reason="length"))
    client = _make_client(thinking="on")
    with pytest.raises(LLMError) as ei:
        client.chat("sys", "user")
    err = ei.value
    assert err.code == E_EMPTY_CONTENT_THINKING and err.retriable is False
    assert err.diagnostics["finish_reason"] == "length"
    assert err.diagnostics["base_url"] == "https://api.deepseek.com"


def test_auth_and_model_not_found_codes(fake_openai):
    fake_openai(lambda kw: (_ for _ in ()).throw(_ApiError(401, "invalid api key")))
    with pytest.raises(LLMError) as ei:
        _make_client().chat("sys", "user")
    assert ei.value.code == E_AUTH_FAILED and ei.value.retriable is False

    fake_openai(lambda kw: (_ for _ in ()).throw(_ApiError(404, "model not found")))
    with pytest.raises(LLMError) as ei2:
        _make_client().chat("sys", "user")
    assert ei2.value.code == E_MODEL_NOT_FOUND and ei2.value.retriable is False


def test_param_unsupported_drops_and_retries_once(fake_openai):
    calls = {"n": 0}
    holder = fake_openai(lambda kw: _resp())
    # 第一次带 response_format 报 400，降参后第二次成功
    def responder(kw):
        calls["n"] += 1
        if "response_format" in kw:
            raise _ApiError(400, "response_format is not supported for this model")
        return _resp()
    holder2 = fake_openai(responder)
    client = _make_client()
    text = client.chat("sys", "user", json_mode=True)
    assert text == "正文"
    assert calls["n"] == 2
    assert client.last_param_dropped == ["response_format"]
    assert "response_format" not in holder2["client"].requests[-1]


def test_finish_reason_length_with_content_keeps_content(fake_openai):
    fake_openai(lambda kw: _resp(content="半截正文", finish_reason="length"))
    client = _make_client()
    assert client.chat("sys", "user") == "半截正文"
    assert client.last_finish_reason == "length"


def test_chat_json_does_not_retry_auth_failure(fake_openai):
    holder = fake_openai(lambda kw: (_ for _ in ()).throw(_ApiError(401, "bad key")))
    client = _make_client()
    with pytest.raises(LLMError) as ei:
        client.chat_json("sys", "user")
    assert ei.value.code == E_AUTH_FAILED
    assert len(holder["client"].requests) == 1  # 必败参数只打一次网络


def test_chat_json_degrade_on_param_unsupported(fake_openai):
    calls = {"n": 0}
    def responder(kw):
        calls["n"] += 1
        if kw.get("response_format"):
            raise _ApiError(400, "response_format unsupported")
        return _resp(content='{"ok": true}')
    holder = fake_openai(responder)
    client = _make_client()
    data = client.chat_json("sys", "user", json_mode=True)
    assert data == {"ok": True}
    # 客户端内部降参一次 + JSON 模式关闭后重试，最终成功
    assert calls["n"] >= 2
    assert "response_format" not in holder["client"].requests[-1]


# ---------------- 工厂与克隆 ----------------

def test_factory_rejects_custom_without_base_url():
    with pytest.raises(LLMError) as ei:
        create_llm(LLMConfig(provider="custom", model="m", api_key="sk-x", base_url=""))
    assert ei.value.code == E_CONFIG_INVALID


def test_factory_passes_compat_fields(fake_openai):
    holder = fake_openai(lambda kw: _resp())
    cfg = LLMConfig(provider="doubao", model="ep-2026", api_key="ark-test",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    timeout=45, max_retries=0, thinking="off",
                    headers={"X-Env": "test"})
    client = create_llm(cfg)
    init_kw = holder["client"].init_kwargs
    assert init_kw["timeout"] == 45 and init_kw["max_retries"] == 0
    assert init_kw["default_headers"] == {"X-Env": "test"}


def test_clone_with_keeps_config_and_shares_usage(fake_openai):
    fake_openai(lambda kw: _resp())
    client = _make_client(timeout=180, max_retries=3, thinking="off")
    clone = client.clone_with(timeout=30, max_retries=0)
    assert clone._timeout == 30 and clone._max_retries == 0
    assert clone._thinking == "off" and clone.model == client.model
    assert clone.usage is client.usage  # token 统计口径共享
