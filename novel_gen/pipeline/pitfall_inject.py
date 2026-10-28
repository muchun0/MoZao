# -*- coding: utf-8 -*-
"""踩坑账本统一事前注入（REQ-踩坑账本注入对账 · 第 2 步）。

背景（分叉的由来）
----------------
踩坑账本（`BookMemory.pitfalls`）的注入点原先分散在多条生成路径上，且**参数互不统一**：

- 场景路径：``top_n=3, min_count=2, self_ch=章号``（走 `scene_prompt` 独立形参）
- 回退整章：``top_n=5, min_count=1, self_ch=None``，且把文本**拼进 `memory_context` 走私**

其中走私通道在结构上就是不安全的：`_generate_chapter_scenes` 对第 2 个及以后的场景会
把 `mem_for_this` **整体替换**为「前情记忆已在第1个场景完整注入，本场景不再重复全文」，
拼在其中的注入块会被静默丢弃。今天回退整章只有一次调用所以侥幸可用，一旦整章路径
也接入分层注入，注入块就会无声消失。

目标形态（本模块负责）
--------------------
把「取哪些踩坑、用什么参数、渲染成什么文本」收敛为**唯一决策点**
`build_injection()`，由 `_write_body` 在分支前调用一次，向下游统一透传：

    _write_body
     ├─ inj = build_injection(cfg, memory, chapter_no)   ← 唯一决策点
     ├─ _generate_chapter_scenes(..., pitfall=inj) ── 5 处 scene_prompt(book_pitfalls=inj.block)
     │    ├─ 拆场景失败 ─→ _fallback_whole_chapter(..., pitfall=inj)
     │    └─ 重演超次 ──→ 同上
     └─ 普通整章 else ── 不注入（死路径，保留观测）

参数统一取值（架构裁定，勿改）
----------------------------
=================  ==================================  ==========================
参数               取值                                 依据
=================  ==================================  ==========================
``top_n``          ``cfg.book_memory_top_n``（默认 5）  复用已有旋钮，修「后台调节对场景路径无效」的接线缺失
``min_count``      ``cfg.pitfall_inject_min_count``(2)   与 ``escalate()`` 的 ``count>=2`` 及 ``_book_pitfalls`` docstring 同口径
``self_ch``        ``chapter_no``                        回退整章是「本章重试」场景，最需要去污染
=================  ==================================  ==========================

约束
----
- **零额外 LLM 调用**：本模块不 import / 引用 `llm`。
- **不新增第三方依赖**：仅 stdlib。
- **降级有痕**：取样异常 `logger.exception` 后返回空注入，不上抛、不阻塞生成（AGENTS.md §11.6）。
- 阈值一律走 `feature_default()`，不硬编码（AGENTS.md §11.3）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = ["PitfallInjection", "build_injection"]


@dataclass
class PitfallInjection:
    """一次注入决策的完整产物：渲染文本 + 原始 pattern 列表 + 取样参数快照。

    Attributes:
        block: 已渲染的注入块文本；空串 = 无注入，调用方不渲染该独立段落。
        patterns: 本次入选的 pattern 列表（已去空串/纯空白），供对账取 Expected。
        params: 本次取样参数快照 ``{"top_n", "min_count", "self_ch"}``，与传给
            `BookMemory.pitfall_injection` 的实参逐值相等，供对账核对「没有第二套参数」。
    """

    block: str = ""
    patterns: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def empty(self) -> bool:
        """无注入返回 True（记忆关闭 / 空账本 / 无满足 min_count 的踩坑）。"""
        return not bool(self.block)


def build_injection(cfg, memory, chapter_no: int) -> PitfallInjection:
    """生成侧踩坑注入的唯一决策点。

    「取哪些、用什么参数、渲染成什么」三件事在本函数一次性定妥，返回值由
    `_write_body` 向下游（场景路径 / 回退整章）统一透传，杜绝各路径自取参数。

    Args:
        cfg: `NovelConfig`，读 `book_memory_top_n` 与 `pitfall_inject_min_count`。
        memory: `BookMemory` 实例；None（未启用小说级记忆）时返回空注入。
        chapter_no: 当前章号，同时作为去污染的 `self_ch`。

    Returns:
        `PitfallInjection`：block 为空串表示本次无注入。任何异常都降级为空注入
        并 `logger.exception` 留痕，绝不上抛。
    """
    from ..config import feature_default

    top_n = int(getattr(cfg, "book_memory_top_n", None)
                or feature_default("book_memory_top_n") or 5)
    min_count = int(getattr(cfg, "pitfall_inject_min_count", None)
                    if getattr(cfg, "pitfall_inject_min_count", None) is not None
                    else feature_default("pitfall_inject_min_count") or 0)
    self_ch = int(chapter_no)
    params = {"top_n": top_n, "min_count": min_count, "self_ch": self_ch}

    if memory is None:
        return PitfallInjection(block="", patterns=[], params=params)
    try:
        block, patterns = memory.pitfall_injection(
            top_n=top_n, min_count=min_count, self_ch=self_ch, for_rewrite=False,
        )
    except Exception:
        logger.exception("踩坑账本注入取样失败（降级不注入）")
        return PitfallInjection(block="", patterns=[], params=params)
    return PitfallInjection(block=str(block or ""), patterns=list(patterns or []),
                            params=params)
