# -*- coding: utf-8 -*-
"""
具体 Provider 实现：
- OpenAICompatibleClient：DeepSeek / 豆包火山方舟 / OpenAI 及任何 OpenAI 兼容中转
  （它们协议一致，只是 base_url / model 不同，用一份实现 + 参数区分即可）
- MockClient：本地假模型，不联网，返回带提示的占位文本。
  用途：没有 API Key 时也能端到端跑通整条流水线，验证架构与数据流正确。
"""
from __future__ import annotations

import time
from typing import Optional

from openai import OpenAI

from .base import LLMClient, LLMError


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
    ):
        super().__init__()
        if not api_key:
            raise LLMError(f"provider[{provider}] 缺少 api_key，无法创建客户端")
        self.provider = provider
        self.model = model
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout,
            max_retries=3,  # SDK 内置指数退避重试
        )

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
        try:
            kwargs: dict = {}
            if self._top_p:
                kwargs["top_p"] = self._top_p
            if frequency_penalty is not None:
                kwargs["frequency_penalty"] = frequency_penalty
            if presence_penalty is not None:
                kwargs["presence_penalty"] = presence_penalty
            # REQ-超长篇攻坚 S4.3：JSON 输出约束——provider 支持 response_format 时强制 json_object
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            # 2026-10-18：DeepSeek V4 系列思考模式默认开启，推理 token 按输出价计费且挤占
            # max_tokens 预算（正文可能被截短）；写作流水线无需推理过程，显式关闭。
            if self.provider == "deepseek":
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self._temperature if temperature is None else temperature,
                max_tokens=self._max_tokens if max_tokens is None else max_tokens,
                **kwargs,
            )
            content = resp.choices[0].message.content
            if not content:
                raise LLMError("模型返回空内容")
            # 阶段1：Token 用量统计（部分网关不返回 usage，缺省 0 不误统计）
            usage = getattr(resp, "usage", None)
            if usage is not None:
                # REQ-超长篇攻坚 S4.1：DeepSeek 等返回前缀缓存命中 token，供 cache_hit 埋点
                cache_hit = getattr(usage, "prompt_cache_hit_tokens", None) or 0
                self.usage.note(
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    prompt_cache_hit_tokens=cache_hit,
                )
            return content
        except LLMError:
            raise
        except Exception as exc:  # 网络/鉴权/限流等，统一包装
            raise LLMError(f"[{self.provider}] 调用失败: {exc}") from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


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
