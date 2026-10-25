# -*- coding: utf-8 -*-
"""产物字数统计：结算计费的**唯一事实来源**（AGENTS §11.3 代码与数据分离）。

按产物类型统计「AI 实际生成的文本字符数」，供 `webapp/settle_ledger.py` 按版本结算。
纯函数、无外部依赖、可离线单测；调用方只需传产物对象或其 `to_dict()` 结果。

计费口径约定（与 TECH-Agent用户化与版本化 §4.4 一致）：
- 章节：正文 `content` 字符数（与现网 `len(c.content)` 一致）；
- 大纲：总纲 + 各卷 + 各章的标题/简介/目标字符数；
- 分卷：卷简介 + 该卷章节标题/目标字符数（调用方组装 payload）；
- 世界观：递归累加所有字符串值长度（字段漂移免疫，新增卡片自动计入）。
"""
from __future__ import annotations

from typing import Any

__all__ = ["count_words"]

# 大纲计费字段（唯一事实来源；新增字段需同步此处并有单测覆盖）
_OUTLINE_SCALAR_FIELDS = ("title", "intro", "core_conflict", "main_line")
_VOLUME_FIELDS = ("name", "intro")
_CHAPTER_FIELDS = ("title", "goal")


def count_words(kind: str, payload: Any) -> int:
    """统计指定产物的 AI 生成字数。

    Args:
        kind: 产物类型，`chapter` / `outline` / `volume` / `world`。
        payload: 产物对象（带 `to_dict()`）或其 dict 形式。

    Returns:
        非负字符数。

    Raises:
        ValueError: `kind` 不在支持范围内（显式失败，避免静默按 0 计费）。
    """
    kind = (kind or "").strip().lower()
    if kind == "chapter":
        return _count_chapter(payload)
    if kind == "outline":
        return _count_outline(payload)
    if kind == "volume":
        return _count_volume(payload)
    if kind == "world":
        return _count_text_deep(payload)
    raise ValueError(f"不支持的产物类型: {kind}")


def _as_dict(payload: Any) -> dict:
    """统一成 dict：兼容 dataclass schema 对象与已序列化 dict。"""
    if isinstance(payload, dict):
        return payload
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        return to_dict() or {}
    if isinstance(payload, str):
        return {"content": payload}
    return {}


def _text_len(value: Any) -> int:
    """单个字段的字符数（非字符串按空串处理，避免 None/数字污染统计）。"""
    return len(value) if isinstance(value, str) else 0


def _sum_fields(item: Any, fields: tuple[str, ...]) -> int:
    d = _as_dict(item)
    return sum(_text_len(d.get(f)) for f in fields)


def _count_chapter(payload: Any) -> int:
    return _text_len(_as_dict(payload).get("content"))


def _count_outline(payload: Any) -> int:
    d = _as_dict(payload)
    total = sum(_text_len(d.get(f)) for f in _OUTLINE_SCALAR_FIELDS)
    for vol in d.get("volumes") or []:
        total += _sum_fields(vol, _VOLUME_FIELDS)
    for ch in d.get("chapters") or []:
        total += _sum_fields(ch, _CHAPTER_FIELDS)
    return total


def _count_volume(payload: Any) -> int:
    """分卷：卷简介 + 卷内章节标题/目标。payload 需含 `intro` 与 `chapters` 列表。"""
    d = _as_dict(payload)
    total = _text_len(d.get("intro")) + _text_len(d.get("name"))
    for ch in d.get("chapters") or []:
        total += _sum_fields(ch, _CHAPTER_FIELDS)
    return total


def _count_text_deep(payload: Any) -> int:
    """递归累加 dict/list 中所有字符串值的长度（世界观等多卡片结构）。

    兼容 dataclass schema 对象：先经 `to_dict()` 转成 dict 再递归（字段漂移免疫）。
    """
    if isinstance(payload, str):
        return len(payload)
    if isinstance(payload, dict):
        return sum(_count_text_deep(v) for v in payload.values())
    if isinstance(payload, (list, tuple)):
        return sum(_count_text_deep(v) for v in payload)
    if hasattr(payload, "to_dict"):
        return _count_text_deep(_as_dict(payload))
    return 0
