# -*- coding: utf-8 -*-
"""统一错误脱敏（P0 走查修复：生成失败不再静默，也不再向作者暴露技术细节）。

原则：
- 保留「能指导下一步动作」的信息（如余额不足、模型未配置、章节号）；
- 去掉异常类名、调用栈、文件路径、provider / api_key / base_url 等内部线索；
- 无法判断时给一句可操作的中性提示，而不是空串。
"""

import re

_TECH_MARKERS = (
    "traceback", "exception", "stack", "api_key", "api key", "apikey",
    "base_url", "provider", "httpx", "openai.", "requests.",
)
# 已知可展示的失败原因（命中即原样返回，只截断长度）
_KEEP_HINTS = (
    "余额不足", "未配置模型", "没有配置模型", "模型未配置", "未识别到", "权限",
    "审核", "内容不合规", "超时", "限流", "每日", "锁定", "已停止",
)

_MAX_LEN = 200


def sanitize_error(value, fallback: str = "生成没有完成，请重试") -> str:
    """把异常对象/文本转成作者可读的一句话；绝不返回技术栈或密钥线索。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # 只取第一行，去掉异常类名前缀
    first = text.splitlines()[0].strip()
    first = re.sub(r"^(HTTPException|Exception|Error|RuntimeError|ValueError|KeyError)[:：]\s*", "", first)
    # 形如 "openai.AuthenticationError: Incorrect API key..." → 统一改成配置提示
    lowered = first.lower()
    if any(m in lowered for m in _TECH_MARKERS):
        return "模型服务调用失败：请检查「模型配置」中的服务商与 Key，或改回平台默认模型后重试"
    if any(h in first for h in _KEEP_HINTS):
        return first[:_MAX_LEN]
    # 带文件路径/堆栈特征的一律不外露
    if re.search(r"[A-Za-z]:\\|/[\w.-]+/[\w.-]+\.py|line \d+", first):
        return fallback
    return first[:_MAX_LEN] if len(first) <= _MAX_LEN else first[:_MAX_LEN] + "…"
