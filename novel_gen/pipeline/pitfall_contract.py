# -*- coding: utf-8 -*-
"""踩坑注入路径契约（REQ-踩坑账本注入对账 · T02.5，阻塞 T03 的前置）。

存在理由
--------
本轮事故的根因不是"某处注入漏了"，而是**每条路径各自决定自己的参数**：

- 场景路径：``top_n=3, min_count=2, self_ch=章号``（``scene_prompt`` 独立形参）
- 回退整章：``top_n=5, min_count=1, self_ch=None``，且走私进 ``memory_context``

两批代码各自在不同时期补入，从未对齐，且**没有任何机制阻止第三套、第四套参数出现**。

因此本模块是**唯一权威源**：每条路径"应当"用什么参数、什么通道、是否已接线，
只在这里定义一次。**注入侧（``pitfall_inject.build_injection``）与对账侧
（``pitfall_reconcile``）必须双向读取本模块**，任何一方都不得自行计算取值——
否则契约会退化成"另一份期待"，照样漂移（AGENTS.md §11.3）。

三方恒等
--------
``contract_for()`` 的返回值 == 注入侧实际传参 == 对账侧回写的 ``expected_params``。
任一侧绕过本模块写死字面量，都会被对账的 ``params_conflict`` 标记发现。

为什么重写侧 min_count 是 1
---------------------------
这不是遗漏，是两个语义的差异，契约如实描述现状以保证 conflict 恒为 False：

- **生成侧**（scene / fallback_whole）：注入的是"长期纪律"，要求反复犯（``count>=2``）
  才值得占用每章的 prompt 预算。
- **重写侧**（rewrite_whole / rewrite_span）：已经确定本章写坏了、正在针对性回灌，
  单次发生的错误此刻也值得提示，故 ``pitfall_block`` 用默认 ``min_count=1``。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "CHANNEL_BLOCK", "CHANNEL_MEMORY_CONTEXT", "CHANNEL_NONE", "CHANNEL_REWRITE",
    "PATH_IDS", "PathContract", "contract_for", "expected_params",
]

# ---- 注入通道：与 scene_prompt/chapter_prompt 的 rendered block 位置同构 ----
CHANNEL_BLOCK = "independent_block"     # 独立成段（scene_prompt 的 book_pitfalls 形参）
CHANNEL_REWRITE = "rewrite_block"       # quality._memory_block 内的重写专用措辞块
CHANNEL_MEMORY_CONTEXT = "inside_memory_context"  # 走私通道（已废弃，保留用于识别回归）
CHANNEL_NONE = "none"                   # 未接线


class PathContract:
    """一条路径的契约。``top_n`` / ``min_count`` 一律来自 cfg，禁止字面量。"""

    __slots__ = ("path_id", "wired", "channel", "note")

    def __init__(self, path_id: str, wired: bool, channel: str, note: str = "") -> None:
        self.path_id = path_id
        self.wired = wired
        self.channel = channel
        self.note = note

    def resolve(self, cfg: Any, chapter_no: int | None) -> dict:
        """解析出实际取样参数。**取值唯一来源**，调用方不得再自行计算。"""
        return {
            "top_n": int(getattr(cfg, "book_memory_top_n", 5) or 5),
            # 重写侧口径放宽到 1（见模块 docstring），其余走统一开关
            "min_count": int(getattr(cfg, "pitfall_inject_min_count", 2) or 0),
            "self_ch": int(chapter_no) if chapter_no is not None else None,
        }


PATH_IDS: dict[str, PathContract] = {
    # ---- 生成侧：已接线，统一口径 ----
    "scene": PathContract(
        "scene", True, CHANNEL_BLOCK,
        "场景路径，现网主路径之一；原硬编码 top_n=3 无视后台旋钮，已统一取 cfg"),
    "fallback_whole": PathContract(
        "fallback_whole", True, CHANNEL_BLOCK,
        "回退整章，实测高频主路径；原 (5,1,None)+走私进 memory_context，已统一"),
    "chapter_plain": PathContract(
        "chapter_plain", True, CHANNEL_BLOCK,
        "普通整章，已被静态证明不可达（high_quality_mode 三重锁死）。"
        "wired=True 是刻意的：它若复活就必须报 L3，而不是被当成已知缺口降级"),
    # ---- 重写侧：独立语义，min_count 为 1 ----
    "rewrite_whole": PathContract(
        "rewrite_whole", True, CHANNEL_REWRITE,
        "整章重写；合规红线命中时由 quality.py:1289 强制进入，绕过 auto_rewrite_rounds"),
    "rewrite_span": PathContract(
        "rewrite_span", True, CHANNEL_REWRITE, "定点段落重写"),
    # ---- 已知缺口：本次只观测，不补注入 ----
    "expand": PathContract(
        "expand", False, CHANNEL_NONE,
        "扩写路径，完全未接线。本期按 L1u 观测并统计真实使用频次，补注入另开需求"),
}


def contract_for(path_id: str, cfg: Any, chapter_no: int | None = None) -> dict:
    """取某路径的完整契约（含解析后的取样参数）。

    Raises:
        KeyError: 未知 ``path_id``——契约新增路径必须在本模块登记，禁止调用点自行传参。
    """
    contract = PATH_IDS.get(str(path_id))
    if contract is None:
        raise KeyError(
            f"未知 path_id: {path_id!r}（新增路径请先登记到 pitfall_contract.PATH_IDS，"
            f"禁止在调用点自行传参——那正是本模块要消灭的分叉）"
        )
    resolved = contract.resolve(cfg, chapter_no)
    if str(path_id).startswith("rewrite_"):
        # 重写侧语义：单次也值得回灌（见模块 docstring）
        resolved["min_count"] = 1
    out = dict(resolved)
    out["path_id"] = contract.path_id
    out["wired"] = contract.wired
    out["channel"] = contract.channel
    return out


def expected_params(contract: dict) -> dict:
    """从契约中抽出对账要核对的参数子集（供回写 ``expected_params`` 字段）。"""
    return {
        "top_n": contract.get("top_n"),
        "min_count": contract.get("min_count"),
        "self_ch": contract.get("self_ch"),
    }
