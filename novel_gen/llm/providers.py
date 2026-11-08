# -*- coding: utf-8 -*-
"""
具体 Provider 实现：
- OpenAICompatibleClient：DeepSeek / 豆包火山方舟 / OpenAI 及任何 OpenAI 兼容中转
  （它们协议一致，只是 base_url / model 不同，用一份实现 + 参数区分即可）
- MockClient：本地假模型，不联网，返回带提示的占位文本。
  用途：没有 API Key 时也能端到端跑通整条流水线，验证架构与数据流正确。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from openai import OpenAI

from .base import (
    E_AUTH_FAILED, E_CONFIG_INVALID, E_CONNECTION_FAILED,
    E_EMPTY_CONTENT, E_EMPTY_CONTENT_THINKING, E_ENDPOINT_NOT_FOUND,
    E_MODEL_NOT_FOUND, E_PARAM_UNSUPPORTED, E_QUOTA_EXCEEDED,
    E_RATE_LIMITED, E_SERVER_ERROR, E_TIMEOUT,
    LLMClient, LLMError,
)
from .capabilities import resolve_profile, thinking_extra

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    """基于 openai SDK 的 OpenAI 兼容客户端。"""

    name: str = "openai-compatible"

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.8,
        top_p: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 180.0,
        max_retries: int = 3,
        connect_timeout: Optional[float] = None,
        thinking: str = "auto",
        extra_body: Optional[dict] = None,
        headers: Optional[dict] = None,
    ):
        super().__init__()
        if not api_key:
            raise LLMError(
                f"provider[{provider}] 缺少 api_key，无法创建客户端",
                code=E_AUTH_FAILED, retriable=False,
                diagnostics={"provider": provider, "base_url": base_url},
            )
        # base_url 为空时 OpenAI SDK 会静默回落到 api.openai.com：必须显式拒绝，
        # 否则「配了 provider 却打到别家」这类问题永远不会被用户看见。
        if not base_url:
            raise LLMError(
                f"provider[{provider}] 缺少 base_url（自定义兼容端点必须显式配置），"
                f"拒绝回落到 api.openai.com",
                code=E_CONFIG_INVALID, retriable=False,
                diagnostics={"provider": provider},
            )
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self._base_url = base_url or ""
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries
        self._connect_timeout = connect_timeout
        self._thinking = (thinking or "auto").strip().lower()
        self._extra_body = dict(extra_body or {})
        self._headers = dict(headers or {})
        self._profile = resolve_profile(provider) or {}
        # 每次调用的可观测状态（base._after_call 读取写进 trace）
        self.last_finish_reason = ""
        self.last_response_model = ""
        self.last_param_dropped: list[str] = []
        # R16：连接阶段与响应阶段分开限时。黑洞网络下 TCP 握手可能被系统层拖到 ~21s，
        # 只设总 timeout 无法区分「连不上」与「模型慢」；连接超时更短可快速降级。
        sdk_timeout = timeout
        if connect_timeout:
            sdk_timeout = httpx.Timeout(
                connect=float(connect_timeout), read=float(timeout),
                write=float(timeout), pool=float(timeout),
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=sdk_timeout,
            max_retries=max_retries,  # SDK 内置指数退避重试
            default_headers=dict(headers or {}) or None,
        )

    # ---------- 客户端克隆 ----------

    def clone_with(self, *, timeout: Optional[float] = None,
                   max_retries: Optional[int] = None,
                   connect_timeout: Optional[float] = None) -> "OpenAICompatibleClient":
        """复用同一 provider/model/key，仅覆盖网络策略（统一克隆入口）。

        不传的参数沿用当前客户端设置。usage / recorder / guard / json 开关共享，
        保证 token 统计、trace 与成本防护口径不变——R14/R16 的质检快速失败
        （30s/0 重试）可直接复用本入口，无需另写一套。
        """
        cloned = OpenAICompatibleClient(
            provider=self.provider, model=self.model, api_key=self._api_key,
            base_url=self._base_url, temperature=self._temperature,
            top_p=self._top_p, max_tokens=self._max_tokens,
            timeout=self._timeout if timeout is None else timeout,
            max_retries=self._max_retries if max_retries is None else max_retries,
            connect_timeout=self._connect_timeout if connect_timeout is None else connect_timeout,
            thinking=self._thinking,
            extra_body=self._extra_body,
            headers=getattr(self, "_headers", None),
        )
        cloned.usage = self.usage
        cloned.recorder = self.recorder
        cloned.guard = self.guard
        cloned.json_schema_output = self.json_schema_output
        return cloned

    def clone_limited(self, timeout: float, max_retries: int,
                      connect_timeout: Optional[float] = None) -> "OpenAICompatibleClient":
        """R16 质检专用：更短超时与更少重试（快速失败），等价于 clone_with 的固定用法。"""
        return self.clone_with(timeout=timeout, max_retries=max_retries,
                               connect_timeout=connect_timeout)

    # ---------- 请求构造（能力门控 + 参数透传） ----------

    def _build_params(self, *, temperature: Optional[float], max_tokens: Optional[int],
                      frequency_penalty: Optional[float], presence_penalty: Optional[float],
                      json_mode: Optional[bool]) -> dict:
        """按能力档案构造请求参数：只发模型支持的字段，未知私有参数走 extra_body 透传。"""
        params: dict = {
            "max_tokens": self._max_tokens if max_tokens is None else max_tokens,
        }
        if temperature is not None and self._profile.get("supports_temperature", True):
            params["temperature"] = temperature
        if self._top_p:
            params["top_p"] = self._top_p
        if frequency_penalty is not None:
            params["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            params["presence_penalty"] = presence_penalty
        # JSON 输出约束：只对声明支持 json_object 的 provider 发送；不支持时走 parse_tip 路径
        if json_mode and self._profile.get("json_mode"):
            params["response_format"] = {"type": "json_object"}
        # 厂商私有参数透传：用户显式配置的字段优先（深合并），其次按能力档案注入思考开关
        extra = dict(self._extra_body)
        injected = thinking_extra(self._profile, self._thinking)
        for key, value in (injected or {}).items():
            extra.setdefault(key, value)
        if extra:
            params["extra_body"] = extra
        return params

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
        self.last_finish_reason = ""
        self.last_response_model = ""
        self.last_param_dropped = []
        params = self._build_params(
            temperature=self._temperature if temperature is None else temperature,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            json_mode=json_mode,
        )
        # 最多两次：首次按完整参数；若参数不被接受，降参后重试一次（不重复原参数打网络）
        for attempt in range(2):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    **params,
                )
                return self._extract_content(resp)
            except LLMError:
                raise
            except Exception as exc:
                code, retriable, status = _classify_error(exc)
                if code == E_PARAM_UNSUPPORTED and attempt == 0:
                    dropped = _drop_unsupported_params(params, str(exc))
                    if dropped:
                        self.last_param_dropped = dropped
                        logger.warning(
                            "provider[%s] 不支持参数 %s，已降参重试一次（model=%s）",
                            self.provider, dropped, self.model,
                        )
                        continue
                diagnostics = self._diagnostics(exc, status=status)
                hint = _ERROR_HINTS.get(code, "模型调用失败")
                raise LLMError(
                    f"[{self.provider}] {hint}；model={self.model} "
                    f"base_url={self._base_url} status={status or '-'} "
                    f"错误：{_short_error(exc)}",
                    code=code, retriable=retriable, diagnostics=diagnostics,
                ) from exc
        raise LLMError(f"[{self.provider}] 参数降级后仍失败", code=E_PARAM_UNSUPPORTED,
                       retriable=False, diagnostics=self._diagnostics(None, status=None))

    def _extract_content(self, resp) -> str:
        """读取正文、finish_reason、实际 model、usage；空正文给可诊断的明确错误。"""
        choice = (getattr(resp, "choices", None) or [None])[0]
        message = getattr(choice, "message", None)
        content = (getattr(message, "content", None) or "")
        reasoning = getattr(message, "reasoning_content", None) or ""
        finish_reason = getattr(choice, "finish_reason", "") or ""
        response_model = getattr(resp, "model", "") or ""
        self.last_finish_reason = finish_reason
        self.last_response_model = response_model
        usage = getattr(resp, "usage", None)
        usage_info = self._note_usage(usage)
        if not content:
            thinking_hit = finish_reason == "length" or bool(reasoning)
            code = E_EMPTY_CONTENT_THINKING if thinking_hit else E_EMPTY_CONTENT
            hint = _ERROR_HINTS[code]
            raise LLMError(
                f"[{self.provider}] {hint}；model={self.model} "
                f"response_model={response_model or '-'} finish_reason={finish_reason or '-'} "
                f"usage={usage_info}",
                code=code, retriable=False,
                diagnostics=self._diagnostics(None, status=200),
            )
        if finish_reason == "length":
            # 非空但被截断：核心链路沿用「返回已生成内容」的既有语义（不硬切换成失败），
            # 但必须留痕，避免「正文少了一截却无人知道」。
            logger.warning(
                "provider[%s] 输出被 max_tokens 截断（finish_reason=length, model=%s），"
                "已返回已生成内容", self.provider, response_model or self.model,
            )
        return content

    def _note_usage(self, usage) -> dict:
        if usage is None:
            # 部分网关不返回 usage：记 0 但必须留痕（否则成本统计会静默失真）
            logger.warning("provider[%s] 响应未返回 usage，本次 token 记 0（model=%s）",
                           self.provider, self.model)
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                    "missing": True}
        info = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        self.usage.note(
            prompt_tokens=info["prompt_tokens"],
            completion_tokens=info["completion_tokens"],
            total_tokens=info["total_tokens"],
            prompt_cache_hit_tokens=getattr(usage, "prompt_cache_hit_tokens", None) or 0,
        )
        return info

    def _diagnostics(self, exc: Optional[Exception], status=None) -> dict:
        usage = {
            "prompt_tokens": getattr(self.usage, "last_prompt_tokens", 0),
            "completion_tokens": getattr(self.usage, "last_completion_tokens", 0),
        }
        url = ""
        try:
            url = str(getattr(getattr(exc, "request", None), "url", "") or "")
        except Exception:
            url = ""
        return {
            "provider": self.provider,
            "model": self.model,
            "response_model": self.last_response_model,
            "base_url": self._base_url,
            "request_url": url,
            "status": status,
            "finish_reason": self.last_finish_reason,
            "usage": usage,
            "param_dropped": list(self.last_param_dropped),
            "error_body": _short_error(exc) if exc is not None else "",
        }

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


# 错误码 → 人读提示（前端与日志共用一份文案，避免各处措辞漂移）
_ERROR_HINTS: dict[str, str] = {
    E_AUTH_FAILED: "鉴权失败：API Key 无效，或 base_url 与 Key 所属平台不匹配",
    E_MODEL_NOT_FOUND: "模型/接入点不存在：请检查 model 是否已在对应平台发布",
    E_ENDPOINT_NOT_FOUND: "端点不存在：请检查 base_url（域名/路径是否写错）",
    E_PARAM_UNSUPPORTED: "模型不接受该参数：已自动降参重试仍失败",
    E_RATE_LIMITED: "触发限流/并发上限，请稍后重试",
    E_TIMEOUT: "请求超时：检查网络或调大 timeout",
    E_CONNECTION_FAILED: "无法连接：检查 base_url 域名、网络或代理",
    E_SERVER_ERROR: "厂商服务端错误（5xx），请稍后重试",
    E_QUOTA_EXCEEDED: "额度/余额不足：请检查账户余额或配额",
    E_EMPTY_CONTENT_THINKING: "模型返回空正文，疑似思考模式占满 max_tokens 预算"
                              "（finish_reason=length/reasoning 有内容）——请把 thinking 设为 off"
                              " 或提高 max_tokens",
    E_EMPTY_CONTENT: "模型返回空正文（未返回 reasoning，finish_reason 非 length）",
    E_CONFIG_INVALID: "配置不合法",
}


def _short_error(exc: Optional[Exception]) -> str:
    if exc is None:
        return ""
    text = str(exc).replace("\n", " ").strip()
    return text[:300]


def _classify_error(exc: Exception) -> tuple[str, bool, Optional[int]]:
    """把厂商异常归到结构化错误码：区分「重试有用」与「重试必败」。"""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    # 配额/余额不足：常以 429 出现，必须先于限流判断，否则会被当成可重试
    if any(k in text for k in ("insufficient quota", "quota", "balance", "余额", "额度")):
        return E_QUOTA_EXCEEDED, False, status
    if status in (401, 403):
        return E_AUTH_FAILED, False, status
    if status == 429:
        return E_RATE_LIMITED, True, status
    if status == 404:
        if "model" in text:
            return E_MODEL_NOT_FOUND, False, status
        return E_ENDPOINT_NOT_FOUND, False, status
    if status in (400, 422):
        return E_PARAM_UNSUPPORTED, True, status
    if isinstance(status, int) and status >= 500:
        return E_SERVER_ERROR, True, status
    name = type(exc).__name__.lower()
    if "timeout" in name or "timed out" in text:
        return E_TIMEOUT, True, status
    if "connection" in name or "connect" in text:
        return E_CONNECTION_FAILED, True, status
    return E_API_ERROR, False, status


def _drop_unsupported_params(params: dict, error_text: str) -> list[str]:
    """根据错误文案定位不被支持的参数并移除（只降一次，避免无限重试）。

    命中不了具体参数名时，按「最可能不被 OpenAI 兼容端点支持」的顺序兜底降参。
    """
    text = error_text.lower()
    candidates = ("response_format", "temperature", "top_p", "extra_body",
                  "frequency_penalty", "presence_penalty")
    dropped: list[str] = []
    for key in candidates:
        if key in params and key in text:
            params.pop(key, None)
            dropped.append(key)
    if not dropped:
        for key in ("response_format", "temperature", "extra_body"):
            if key in params:
                params.pop(key, None)
                dropped.append(key)
    return dropped



class MockClient(LLMClient):
    """本地假模型：返回「按请求阶段识别出的占位内容」，用于离线冒烟测试。"""

    name: str = "mock"

    def __init__(self, temperature: float = 0.8, max_tokens: int = 4096, delay: float = 0.0):
        super().__init__()
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._delay = delay

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
        if self._delay:
            time.sleep(self._delay)
        # 按「各阶段独有的精确标记」路由，避免关键词互相包含导致误判
        # （例：大纲提示词里也含“调研报告”字样，故不能用“调研”这类宽泛词）
        if "【本章】" in user or "【单章字数】" in user:
            return _MOCK_CHAPTER
        if "world_view" in user and "characters" in user:
            return _MOCK_WORLD
        if "章节对象的数组" in user or "章节规划" in user:
            return self._mock_outline_for(user)
        if "爆款共性调研报告" in user or "共性综述" in user:
            return _MOCK_RESEARCH
        return _MOCK_GENERIC

    @staticmethod
    def _mock_outline_for(user: str) -> str:
        """按请求的章节数动态生成大纲 JSON（便于离线跑多章流程）。"""
        import re
        m = re.search(r"恰好 (\d+) 个章节", user) or re.search(r"共 (\d+) 章", user)
        n = int(m.group(1)) if m else 2
        chapters = ",\n".join(
            '    {"no": %d, "title": "第%d回 风云再起", "goal": "推进主线，制造冲突并留下悬念。"}'
            % (i + 1, i + 1)
            for i in range(n)
        )
        return (
            '{\n'
            '  "title": "剑出昆仑",\n'
            '  "intro": "少年从昆仑山巅跌落凡尘，却觉醒了上古剑魂，从此一剑破万法。",\n'
            f'  "chapters": [\n{chapters}\n  ]\n'
            '}'
        )

    def close(self) -> None:
        pass


# ---------- Mock 占位输出 ----------

_MOCK_RESEARCH = """{
  "genre": "玄幻",
  "summary": "玄幻题材以修炼升级为骨架，2026年流行『轻松向反套路』与『强开局强张力』，情绪代偿优先于单纯爽感。",
  "trends": ["题材混搭：都市高武、赛博修仙吃红利", "反套路：配角迪化、反传统退婚", "强开局：主角快速登场+强悬念"],
  "plot_patterns": ["废柴逆袭", "金手指驱动成长", "扮猪吃虎与反派打脸"],
  "styles": ["热血王道", "轻快日常", "恢弘史诗"],
  "pacing": "一章一个小反转，三章一个大爆发，每10万字设小高潮。",
  "opening_strategy": "主角3章内立住，抛出变强目标与第一场冲突/打脸。",
  "avoid": ["开局冗长铺垫", "无脑无敌开挂", "传统老套路重复"],
  "references": ["斗破苍穹（退婚流/热血）", "凡人修仙传（朴实升级）", "遮天（恢弘史诗）"]
}"""

_MOCK_OUTLINE = """{
  "title": "剑出昆仑",
  "intro": "少年从昆仑山巅跌落凡尘，却觉醒了上古剑魂，从此一剑破万法。",
  "chapters": [
    {"no": 1, "title": "昆仑之巅", "goal": "主角登场，获得上古剑魂，立下变强目标。"},
    {"no": 2, "title": "下山入世", "goal": "主角离开昆仑，初入江湖，遭遇第一场战斗。"}
  ]
}"""

_MOCK_WORLD = """{
  "world_view": "九域大陆，灵气复苏，剑修当道。",
  "characters": [
    {"name": "林夜", "role": "主角", "desc": "昆仑弃徒，性情坚韧，剑道天才。"}
  ],
  "foreshadowing": ["剑魂来历不明，暗藏惊天秘密。"]
}"""

_MOCK_CHAPTER = """这一夜，昆仑之巅风雪如刀。少年林夜跪在断崖前，指尖抚过一柄锈剑的剑脊，忽然之间，一道剑光自剑身冲出，直入眉心。他浑身剧震，仿佛听见了远古剑鸣。从这一刻起，他的命运彻底改变——他不再是那个被人看不起的杂役弟子，而是身负上古剑魂的传人。山下的江湖，正等着他。

【摘要】林夜在昆仑之巅觉醒上古剑魂，决定下山入世。"""

_MOCK_GENERIC = """（Mock 占位文本：本输出来自本地假模型，用于离线测试。请配置真实 API Key 后使用。）"""
