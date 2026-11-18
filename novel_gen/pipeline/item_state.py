# -*- coding: utf-8 -*-
"""物品归属字段的唯一读写口径（REQ-R24 方案 B，TECH-R24 §7）。

`chapter_state["items"]` 允许两种形态：

- **旧形态（存量书）**：`{"铜匣": "在陆沉怀中"}`——自由文本，靠规则猜持有者。
- **新形态（方案 B）**：`{"铜匣": {"owner": "陆沉", "state": "held",
  "evidence": "陆沉把铜匣塞进铁匣"}}`——持有者与「是否仍持有」由抽取阶段
  （LLM，写入前）判好，图与规则只读不再猜。

为什么结构化：`被老陈头砸碎在地`/`从袖口滚落` 这类「失去」语义是开放集合，
确定性词表永远补不全；把语义判断放在**写账本时**（已有的一次 LLM 抽取里扩字段），
图仍然是纯确定性投影——指纹可复现、diff 可回指、读接口零 LLM。

`state` 三值：`held`（仍持有）/ `lost`（已失去：碎、丢、被毁、脱手）/
`unknown`（正文没写清）。`lost` 与 `unknown` 都不建 holds 边——宁可少一条边，
不编造持有者。
"""
from __future__ import annotations

STATES = ("held", "lost", "unknown")


def normalize_item(value) -> dict:
    """把任意形态的 items 值归一成 `{owner, state, evidence, text}`。

    不抛异常、不改语义：旧字符串只做包装（`state=unknown`），结构化输入按白名单
    取值，非法 `state` 一律降为 `unknown`。
    """
    if isinstance(value, dict):
        owner = str(value.get("owner") or "").strip()
        state = str(value.get("state") or "").strip().lower()
        if state not in STATES:
            state = "held" if owner else "unknown"
        if state == "held" and not owner:
            state = "unknown"   # 没有持有者的 held 等于没说清
        evidence = str(value.get("evidence") or "").strip()
        return {"owner": owner, "state": state, "evidence": evidence,
                "text": str(value.get("text") or evidence or "").strip()}
    text = str(value or "").strip()
    return {"owner": "", "state": "unknown", "evidence": "", "text": text}


def owner_of(value) -> str:
    """结构化字段给出的持有者姓名；旧文本返回空串（交由规则层解析）。"""
    return normalize_item(value)["owner"]


def is_lost(value) -> bool:
    """显式「已失去」。旧文本没有这个信息，返回 False。"""
    return normalize_item(value)["state"] == "lost"


def is_unknown(value) -> bool:
    return normalize_item(value)["state"] == "unknown"


def evidence_of(value) -> str:
    st = normalize_item(value)
    return st["evidence"] or st["text"]


def label(value) -> str:
    """注入提示词用的一行描述，保持旧文本原样。"""
    st = normalize_item(value)
    if st["state"] == "held" and st["owner"]:
        return f"{st['owner']}持有" + (f"（{st['evidence']}）" if st["evidence"] else "")
    if st["state"] == "lost":
        return "已失去" + (f"（{st['evidence']}）" if st["evidence"] else "")
    if st["text"]:
        return st["text"]
    return "归属不明"
