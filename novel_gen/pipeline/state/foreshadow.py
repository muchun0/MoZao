# -*- coding: utf-8 -*-
"""伏笔唯一写入源（REQ-R21 US-2/US-3 / TECH-R21 §2.3）。

`foreshadowing.json` 是本需求之后唯一允许写伏笔状态的文件；世界观伏笔项、
`foreshadow_commitment.json`、`setting_cards` 一律降级为派生/只读视图。

硬约束：
- **五态**：`buried|due|overdue|stalled|resolved`。`overdue` / `stalled` 由
  `due_ch` 与埋设章计算，**不允许手写**（写入直接拒绝）。
- **证据强绑定**：转 `resolved` 必须带 `evidence{ch, offset, snippet}`，缺任一项
  拒绝写入并留痕，状态保持原值（宁可留一个假未回收，也不要一个无出处的已回收）。
- **key 冻结**：`key` 由名称哈希生成，一经生成即冻结；名称改动只更新 `name`
  并把旧名写进 `aliases`，否则改名即断链。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: 五态（顺序即推进方向）
STATUSES = ("buried", "due", "overdue", "stalled", "resolved")

#: 由系统计算、禁止手写入库的状态
COMPUTED_STATUSES = {"due", "overdue", "stalled"}

_EVIDENCE_KEYS = ("ch", "offset", "snippet")


def key_for(name: str) -> str:
    """名称 → 冻结 key（`fp_` + 名称 sha1 前 10 位）。"""
    raw = str(name or "").strip()
    return "fp_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def status_for(item: dict, at_ch: int, lead: int = 3) -> str:
    """按 due_ch / 埋设章计算当前状态；resolved 为终态不重算。

    - due_ch > 0：at_ch ≥ due → overdue；at_ch ≥ due - lead → due
    - due_ch ≤ 0（无兑现章）：埋设后 ≥10 章无推进 → stalled
    """
    if str(item.get("status") or "") == "resolved":
        return "resolved"
    due = _int(item.get("due_ch"), 0)
    planted = _int(item.get("planted_ch"), 0)
    at_ch = _int(at_ch, 0)
    if due > 0:
        if at_ch >= due:
            return "overdue"
        if at_ch >= max(1, due - max(1, int(lead or 3))):
            return "due"
        return "buried"
    if planted > 0 and at_ch - planted >= 10:
        return "stalled"
    return "buried"


class ForeshadowError(ValueError):
    """伏笔写入被拒绝（调用方留痕后保持原状态）。"""


class ForeshadowLedger:
    """`foreshadowing.json` 的读写门面。"""

    def __init__(self, store, cfg=None):
        self.store = store
        self.cfg = cfg
        #: 最近一次写入被拒的原因（接口/日志观测用）
        self.last_reject = ""

    # ---------- 读 ----------
    def load(self) -> dict:
        try:
            data = self.store.load_foreshadowing()
        except Exception:
            logger.exception("foreshadowing.json 读取失败（按空账本处理）")
            return {"version": 1, "items": []}
        if not isinstance(data, dict):
            return {"version": 1, "items": []}
        items = data.get("items")
        if not isinstance(items, list):
            data["items"] = []
        data.setdefault("version", 1)
        return data

    def items(self) -> list:
        return [it for it in (self.load().get("items") or []) if isinstance(it, dict)]

    def by_key(self, key: str) -> Optional[dict]:
        for it in self.items():
            if str(it.get("key") or "") == str(key or ""):
                return it
        return None

    def by_name(self, name: str) -> Optional[dict]:
        target = str(name or "").strip()
        for it in self.items():
            if str(it.get("name") or "").strip() == target:
                return it
            if target and target in (it.get("aliases") or []):
                return it
        return None

    # ---------- 写 ----------
    def _save(self, data: dict) -> None:
        data["version"] = int(data.get("version") or 1)
        self.store.save_foreshadowing(data)

    def upsert(self, item: dict, at_ch: int = 0) -> dict:
        """新增/合并一条伏笔；返回实际落库的结构。不合规抛 ForeshadowError。"""
        if not isinstance(item, dict):
            raise ForeshadowError("伏笔条目必须是 dict")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ForeshadowError("伏笔缺名称")
        status = str(item.get("status") or "buried").strip()
        if status not in STATUSES:
            raise ForeshadowError(f"未登记的伏笔状态: {status}")
        if status in COMPUTED_STATUSES:
            self.last_reject = f"状态 {status} 由系统计算，不允许手写"
            raise ForeshadowError(self.last_reject)

        key = str(item.get("key") or "").strip() or key_for(name)
        data = self.load()
        items = data.get("items") or []
        old = None
        for it in items:
            if str(it.get("key") or "") == key:
                old = it
                break
        if old is None:
            old = self.by_name(name)
            if old is not None:
                key = str(old.get("key") or key)

        merged = dict(old or {})
        merged["key"] = key
        if old is not None and merged.get("name") and merged.get("name") != name:
            aliases = list(merged.get("aliases") or [])
            if merged["name"] not in aliases:
                aliases.append(merged["name"])
            merged["aliases"] = aliases
        merged["name"] = name
        for field in ("content", "desc", "level", "source", "planted_ch", "due_ch",
                      "reveal_ch", "inferred", "migrated", "last_touch_ch"):
            if item.get(field) is not None:
                merged[field] = item[field]
        merged.setdefault("level", "main")
        merged.setdefault("source", "world")
        merged.setdefault("planted_ch", 0)
        merged.setdefault("due_ch", 0)
        merged.setdefault("reveal_ch", 0)

        if status == "resolved":
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else merged.get("evidence")
            missing = [k for k in _EVIDENCE_KEYS if evidence is None or str((evidence or {}).get(k)).strip() == ""]
            if missing:
                self.last_reject = f"resolved 缺证据字段 {','.join(missing)}"
                logger.warning("伏笔「%s」转 resolved 被拒：缺 evidence %s（保持原状态）",
                               name, ",".join(missing))
                raise ForeshadowError(self.last_reject)
            merged["evidence"] = {
                "ch": _int(evidence.get("ch"), 0),
                "offset": _int(evidence.get("offset"), 0),
                "snippet": str(evidence.get("snippet") or "").strip(),
            }
            merged["reveal_ch"] = _int(merged["evidence"]["ch"], _int(merged.get("reveal_ch"), 0))
        merged["status"] = status

        if old is None:
            items.append(merged)
        else:
            # 就地替换（保持列表顺序稳定，迁移两次输出一致）
            for idx, it in enumerate(items):
                if str(it.get("key") or "") == key:
                    items[idx] = merged
                    break
        data["items"] = items
        self._save(data)
        return merged

    def resolve(self, key: str, evidence: dict, at_ch: int = 0) -> dict:
        """带证据回收；证据不全抛 ForeshadowError（不落盘）。"""
        item = self.by_key(key)
        if item is None:
            raise ForeshadowError(f"伏笔不存在: {key}")
        return self.upsert({**item, "status": "resolved", "evidence": evidence}, at_ch=at_ch)

    # ---------- 派生视图 ----------
    def view(self, at_ch: int = 0, lead: int = 3) -> list:
        """按 due_ch 升序返回带计算状态的列表（不落盘、不写状态）。"""
        rows = []
        for it in self.items():
            cur = status_for(it, at_ch, lead)
            row = {
                "key": str(it.get("key") or ""),
                "name": str(it.get("name") or ""),
                "desc": str(it.get("content") or it.get("desc") or "").strip(),
                "planted_ch": _int(it.get("planted_ch"), 0),
                "due_ch": _int(it.get("due_ch"), 0),
                "reveal_ch": _int(it.get("reveal_ch"), 0),
                "status": cur,
                "level": str(it.get("level") or "main"),
                "source": str(it.get("source") or "world"),
                "evidence": it.get("evidence") or None,
            }
            rows.append(row)
        rows.sort(key=lambda r: (r["due_ch"] or 10 ** 6, r["planted_ch"], r["name"]))
        return rows
