# -*- coding: utf-8 -*-
"""历史归并（REQ-R21 US-8 / TECH-R21 §2.4）。

把三处历史来源收敛进 `foreshadowing.json`：

| 来源 | 映射 | 缺失处理 |
|---|---|---|
| 世界观伏笔项 | key 取名称哈希；planted_ch=buried_ch，due_ch=reveal_ch | 无埋设章 → 取首次提及章并标 inferred |
| foreshadow_commitment.json | 状态与 due_ch 直接沿用，最高优先 | 与世界观冲突时以状态机为准并进报告 |
| setting_cards 命中项 | 只补 planted_ch / level，不覆盖状态 | 无对应 key → 新建并标 migrated |

**幂等**：以 key 为唯一键，已存在只补齐缺失字段，绝不覆盖 status / evidence；
连跑两次输出摘要一致。
"""
from __future__ import annotations

import logging

from .foreshadow import ForeshadowLedger, key_for

logger = logging.getLogger(__name__)


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(getattr(item, "name", "") or "").strip()


def _field(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _commitment_items(store) -> dict:
    """承诺状态机 → {name: {revealed_ch, due_ch, status}}。"""
    try:
        data = store.load_foreshadow_commitment() or {}
    except Exception:
        logger.exception("承诺状态机读取失败（本次迁移跳过该来源）")
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    out = {}
    for it in (items or []):
        n = _name(it)
        if n:
            out[n] = {
                "revealed_ch": _int(_field(it, "revealed_ch", 0), 0),
                "due_ch": _int(_field(it, "due_ch", 0), 0),
                "status": str(_field(it, "status", "") or ""),
            }
    return out


def _card_items(store) -> dict:
    """设定卡命中项 → {name: {planted_ch, level}}；只补不回写。"""
    try:
        data = store.load_setting_cards() or {}
    except Exception:
        logger.exception("设定卡读取失败（本次迁移跳过该来源）")
        return {}
    out = {}
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, dict):
        return out
    for no, rec in chapters.items():
        for card in ((rec or {}).get("cards") or []) if isinstance(rec, dict) else []:
            n = _name(card)
            if not n:
                continue
            planted = _int(no, 0)
            rec_out = out.setdefault(n, {"planted_ch": planted, "level": "sub"})
            rec_out["planted_ch"] = min(rec_out.get("planted_ch") or planted, planted)
    return out


def migrate(store, world=None) -> dict:
    """执行一次幂等归并，返回可复述的报告。"""
    ledger = ForeshadowLedger(store)
    data = ledger.load()
    items: list = data.get("items") or []
    by_key = {str(it.get("key") or ""): it for it in items}
    report = {"total": 0, "created": 0, "filled": 0, "inferred": 0,
              "conflicts": [], "sources": {"world": 0, "commitment": 0, "cards": 0}}

    try:
        world = world if world is not None else store.load_world()
    except Exception:
        logger.exception("世界观读取失败（本次迁移只处理承诺状态机）")
        world = None

    commitment = _commitment_items(store)
    cards = _card_items(store)

    def ensure(name: str, *, source: str, planted: int, due: int,
               content: str = "", level: str = "main",
               inferred: bool = False) -> dict:
        key = key_for(name)
        cur = by_key.get(key)
        if cur is None:
            cur = {"key": key, "name": name, "content": content,
                   "planted_ch": planted, "due_ch": due, "reveal_ch": 0,
                   "status": "buried", "level": level, "source": source}
            if inferred:
                cur["inferred"] = True
            items.append(cur)
            by_key[key] = cur
            report["created"] += 1
            if inferred:
                report["inferred"] += 1
            return cur
        filled = False
        if not _int(cur.get("planted_ch"), 0) and planted:
            cur["planted_ch"] = planted
            filled = True
        if not _int(cur.get("due_ch"), 0) and due:
            cur["due_ch"] = due
            filled = True
        if not str(cur.get("content") or "").strip() and content:
            cur["content"] = content
            filled = True
        if filled:
            report["filled"] += 1
        return cur

    # 1) 世界观伏笔项（最低优先）
    for fs in (getattr(world, "foreshadowing", None) or []):
        name = _name(fs)
        if not name:
            continue
        buried = _int(_field(fs, "buried_ch", 0), 0)
        due = _int(_field(fs, "reveal_ch", 0), 0)
        content = str(_field(fs, "content", "") or "").strip()
        ensure(name, source="world", planted=buried, due=due,
               content=content, inferred=buried <= 0)
        report["sources"]["world"] += 1

    # 2) 承诺状态机（最高优先：状态与 due_ch 以它为准）
    for name, info in commitment.items():
        cur = ensure(name, source="world", planted=0, due=info.get("due_ch") or 0)
        due = info.get("due_ch") or 0
        if due and _int(cur.get("due_ch"), 0) != due:
            report["conflicts"].append({"name": name, "field": "due_ch",
                                        "world": cur.get("due_ch"), "commitment": due})
            cur["due_ch"] = due
        revealed = info.get("revealed_ch") or 0
        if revealed:
            if str(cur.get("status")) != "resolved":
                report["conflicts"].append({"name": name, "field": "status",
                                            "world": cur.get("status"), "commitment": "resolved"})
                cur["status"] = "resolved"
            evidence = cur.get("evidence") if isinstance(cur.get("evidence"), dict) else {}
            if not evidence:
                # 历史数据没有证据：显式标为推定，不伪造 snippet
                cur["evidence"] = {"ch": revealed, "offset": 0,
                                   "snippet": "（历史归并：原文片段未存）"}
                cur["inferred"] = True
                report["inferred"] += 1
        report["sources"]["commitment"] += 1

    # 3) 设定卡（只补 planted_ch / level）
    for name, info in cards.items():
        cur = ensure(name, source="chapter", planted=info.get("planted_ch") or 0, due=0,
                     level=info.get("level") or "sub")
        if not cur.get("migrated") and cur.get("level") == "sub":
            cur["migrated"] = True
        report["sources"]["cards"] += 1

    data["items"] = items
    data["version"] = int(data.get("version") or 1)
    store.save_foreshadowing(data)
    report["total"] = len(items)
    return report
