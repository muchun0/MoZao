# -*- coding: utf-8 -*-
"""事件流（REQ-R21 US-1 / TECH-R21 §2.2）。

契约：
{ "id": "ev_ch12_s03_001", "ch": 12, "scene": 3,
  "participants": ["林舟", "二舅公"],
  "cause": "ev_ch09_s02_004", "effect": ["ev_ch13_s01_002"],
  "desc": "林舟在祠堂取出铜匣，被二舅公撞见",
  "kind": "object|relationship|state|foreshadow",
  "inferred": false }

硬约束（写进模块约定）：
- **append-only**：写路径只有 `open(mode="a")`，本模块不提供任何回写/删除接口；
  同一 id 重复写入直接忽略并计数（重跑与迁移不会撞号）。
- **消费状态不进事件行**：已消费会随时间变化，回写等于就地改行；消费水位另存
  `consumption.json`（整文件原子覆盖），查询时按 id 左连接。
- **孤立事件**：`cause` 与 `effect` 不得同时为空；仅迁移期的推定事件允许，
  且必须带 `inferred=true`。抽取失败时写一条 `inferred=true` 的孤立事件并
  `logger.warning`，时间线照实显示缺口，不静默跳过。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: id 规则：ev_ch{章号 2-3 位}_s{场景 2 位}_{章内 3 位序号}
ID_RE = re.compile(r"^ev_ch(\d{2,3})_s(\d{2})_(\d{3})$")

#: 事件类型白名单（未登记的 kind 落 state，避免拼错词导致过滤失效）
KINDS = {"object", "relationship", "state", "foreshadow"}


def make_id(ch: int, scene: int, seq: int) -> str:
    """章内单调递增生成，不依赖全局计数。"""
    return f"ev_ch{int(ch):02d}_s{int(scene):02d}_{int(seq):03d}"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


class EventError(ValueError):
    """事件不合法（调用方捕获后跳过该条，不中断写章）。"""


class EventStream:
    """事件流读写。读路径容忍坏行（跳过并计数），写路径只追加。"""

    def __init__(self, store):
        self.store = store
        #: 最近一次读取/写入发现的重复或有问题的行数（供接口/日志观测）
        self.last_duplicates = 0
        self.last_bad_lines = 0

    # ---------- 路径 ----------
    def path(self):
        return self.store.events_path()

    def consumption_path(self):
        return self.store.consumption_path()

    # ---------- 读 ----------
    def _read_raw(self) -> list:
        path = self.path()
        if not path.exists():
            return []
        rows = []
        bad = 0
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    bad += 1
                    continue
                if isinstance(rec, dict) and rec.get("id"):
                    rows.append(rec)
                else:
                    bad += 1
        self.last_bad_lines = bad
        if bad:
            logger.warning("events.jsonl 有 %d 行无法解析（跳过，不回写）", bad)
        return rows

    def all(self) -> list:
        """按 id 去重（先写者为准），按 ch 升序返回。"""
        seen: dict[str, dict] = {}
        dup = 0
        for rec in self._read_raw():
            rid = str(rec.get("id"))
            if rid in seen:
                dup += 1
                continue
            seen[rid] = rec
        self.last_duplicates = dup
        if dup:
            logger.warning("events.jsonl 发现重复 id %d 条（保留首条）", dup)
        rows = list(seen.values())
        rows.sort(key=lambda r: (int(r.get("ch") or 0), str(r.get("id"))))
        return rows

    def query(self, from_ch: int = 0, to_ch: int = 0, participant: str = "",
              cause: str = "", thread: str = "", limit: int = 0) -> list:
        """按章区间/参与者/因果/线索过滤；limit>0 取最近 limit 条。"""
        rows = self.all()
        lo = int(from_ch or 0)
        hi = int(to_ch or 0)
        if lo > 0:
            rows = [r for r in rows if int(r.get("ch") or 0) >= lo]
        if hi > 0:
            rows = [r for r in rows if int(r.get("ch") or 0) <= hi]
        if participant:
            who = participant.strip()
            rows = [r for r in rows if who in _as_list(r.get("participants"))]
        if cause:
            rows = [r for r in rows if str(r.get("cause") or "") == cause
                    or cause in _as_list(r.get("effect"))]
        if thread:
            key = thread.strip()
            rows = [r for r in rows
                    if str(r.get("kind") or "") == "foreshadow"
                    and (key in str(r.get("desc") or "") or key in _as_list(r.get("threads")))]
        if limit and int(limit) > 0:
            rows = rows[-int(limit):]
        return rows

    def consumption(self) -> dict:
        try:
            data = self.store.load_consumption()
        except Exception:
            logger.exception("消费水位读取失败（按未消费处理）")
            return {}
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            return {}
        out = {}
        for rid, ch in items.items():
            try:
                out[str(rid)] = int(ch)
            except (TypeError, ValueError):
                continue
        return out

    def query_with_consumption(self, **kwargs) -> list:
        """查询结果补 consumed_ch（左连接 consumption.json，不改事件行）。"""
        cons = self.consumption()
        out = []
        for r in self.query(**kwargs):
            item = dict(r)
            item["consumed_ch"] = cons.get(str(item.get("id") or ""))
            out.append(item)
        return out

    # ---------- 写 ----------
    @staticmethod
    def normalize(rec: dict, *, ch: int = 0, scene: int = 0, seq: int = 0,
                  allow_inferred: bool = False) -> dict:
        """校验并补全一条事件；不合法抛 EventError。"""
        if not isinstance(rec, dict):
            raise EventError("事件必须是 dict")
        chapter = int(rec.get("ch") or ch or 0)
        if chapter <= 0:
            raise EventError("事件缺章号")
        desc = str(rec.get("desc") or "").strip()
        if not desc:
            raise EventError("事件缺描述")
        rid = str(rec.get("id") or "").strip()
        if not rid:
            rid = make_id(chapter, int(rec.get("scene") or scene or 0), int(seq or 1))
        if not ID_RE.match(rid):
            raise EventError(f"事件 id 不合规: {rid}")
        kind = str(rec.get("kind") or "state").strip()
        if kind not in KINDS:
            kind = "state"
        cause = str(rec.get("cause") or "").strip()
        effect = _as_list(rec.get("effect"))
        inferred = bool(rec.get("inferred"))
        if not cause and not effect and not (inferred or allow_inferred):
            raise EventError("事件 cause/effect 同时为空（孤立事件仅迁移期允许）")
        out = {
            "id": rid,
            "ch": chapter,
            "scene": int(rec.get("scene") or scene or 0),
            "participants": _as_list(rec.get("participants")),
            "cause": cause,
            "effect": effect,
            "desc": desc,
            "kind": kind,
        }
        threads = _as_list(rec.get("threads"))
        if threads:
            out["threads"] = threads
        if inferred:
            out["inferred"] = True
        return out

    def append(self, events: Iterable[dict], *, ch: int = 0, scene: int = 0,
               allow_inferred: bool = False) -> dict:
        """追加写入；返回 {written, skipped, rejected}。已存在的 id 跳过并计数。"""
        existing = {str(r.get("id")) for r in self.all()}
        written = skipped = rejected = 0
        lines: list[str] = []
        for i, rec in enumerate(events or [], start=1):
            try:
                item = self.normalize(rec, ch=ch, scene=scene, seq=i,
                                      allow_inferred=allow_inferred)
            except EventError as exc:
                rejected += 1
                logger.warning("事件被拒绝：%s（%s）", exc, rec)
                continue
            if item["id"] in existing:
                skipped += 1
                continue
            existing.add(item["id"])
            lines.append(json.dumps(item, ensure_ascii=False))
            written += 1
        if lines:
            path = self.path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(line + "\n")
        return {"written": written, "skipped": skipped, "rejected": rejected}

    def mark_consumed(self, event_ids: Iterable[str], ch: int) -> dict:
        """整文件原子覆盖写消费水位（走 store._write_json），返回最新水位条数。"""
        cons = self.consumption()
        for rid in event_ids or []:
            rid = str(rid or "").strip()
            if rid:
                cons[rid] = int(ch or 0)
        self.store.save_consumption({"version": 1, "items": cons})
        return {"consumed": len(cons)}


def build_event_block(rows: list, limit: int = 8) -> str:
    """最近事件渲染（供 book_facts 复用，保持单一措辞）。"""
    rows = [r for r in (rows or []) if r.get("desc")][-limit:]
    if not rows:
        return ""
    return "【最近已发生事件】\n" + "\n".join(
        f"- 第{r['ch']}章：{r['desc']}" for r in rows)


def resolve_at_ch_from_rows(rows: list, default: int = 0) -> int:
    return max((int(r.get("ch") or 0) for r in (rows or [])), default=default)
