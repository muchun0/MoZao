# -*- coding: utf-8 -*-
"""REQ-R22 M2：状态图谱只读投影服务（库内可复用）。

**为什么是库内服务而不是独立进程**：TECH-R22 v1.1 的三条触发门槛（两个同进程消费者、
无跨语言需求、无对外只读 API）在 M2 落地时仍全部成立，判定为「暂不服务化」；因此 M2
落地形态是**一个稳定契约 + 版本指纹 + 来源标注**的库内只读服务，生成链路、webapp、
小墨 AI 共用同一入口，不再各自拼装上下文。日后真的需要独立进程时，本模块的契约可
原样搬进投影服务（见 TECH-R22 §2.3 四视图）。

四视图（与 TECH-R22 §2.3 对齐，命名沿用 M0/M1 字段）：

- ``canon``      世界事实：专有名词/永久纪律/机制基线 + 派生注册表 + 不变量
- ``characters`` 角色认知：每个角色的当前状态、所在地、目标、来源章
- ``timeline``   发展线：全部事件（章号/场景/参与人/类型/因果位/消费水位）
- ``threads``    可能分支：已埋未收伏笔（含 due/status/证据章）

只读：不写任何源账本、不修复、不落盘；消费方要写请走对应 `state/*` 模块。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from .events import EventStream

logger = logging.getLogger(__name__)

#: 视图名（新增视图必须同时改 `view()` 与 `snapshot()`，避免契约漂移）
VIEWS = ("canon", "characters", "timeline", "threads")

#: 时间线单次返回上限（服务端只读保护；消费方需要更多请按章区间分页）
TIMELINE_LIMIT_MAX = 2000


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class StateGraph:
    """单书状态图谱的只读投影入口（构造一次，可反复读；无可变状态）。"""

    def __init__(self, store, cfg=None):
        self.store = store
        if cfg is None:
            try:
                cfg = store.load_config()
            except Exception:
                logger.exception("配置读取失败（StateGraph 按无开关降级）")
                cfg = None
        self.cfg = cfg

    # ---------- 内部 ----------
    def _facts(self):
        from ..book_facts import BookFacts
        return BookFacts(self.store, self.cfg)

    def _canon_raw(self) -> dict:
        try:
            mem = self.store.load_book_memory() or {}
        except Exception:
            logger.exception("book_memory 读取失败（canon 视图降级为空）")
            return {}
        return (mem.get("canon") or {}) if isinstance(mem, dict) else {}

    def _world(self):
        try:
            return self.store.load_world()
        except Exception:
            logger.exception("world 读取失败（不变量/禁用词降级为空）")
            return None

    # ---------- 来源与版本 ----------
    def sources(self) -> dict:
        """数据来源与规模：消费方可据此判断「读到的是哪一版」。"""
        facts = self._facts()
        events = []
        try:
            events = EventStream(self.store).all()
        except Exception:
            logger.exception("events.jsonl 读取失败（来源计数降级为 0）")
        threads = []
        try:
            threads = facts.open_threads()
        except Exception:
            logger.exception("伏笔账本读取失败（来源计数降级为 0）")
        snap = {}
        try:
            snap = facts.snapshot() or {}
        except Exception:
            logger.exception("book_facts 快照读取失败（版本信息降级）")
        canon = self._canon_raw()
        return {
            "at_ch": _int(snap.get("at_ch")),
            "world_v": _int(snap.get("world_v")),
            "outline_v": _int(snap.get("outline_v")),
            "stale": bool(snap.get("stale")),
            "events": len(events),
            "threads": len(threads),
            "terms": len((canon.get("terms") or {})),
            "disciplines": len((canon.get("disciplines") or [])),
        }

    def version(self, views: Optional[list] = None) -> str:
        """内容指纹：同一份源数据 → 同一 version（消费方可据此做缓存）。"""
        payload = {"sources": self.sources(), "views": sorted(views or VIEWS)}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # ---------- 四视图 ----------
    def canon(self, at_ch: int = 0) -> dict:
        """世界事实视图：专有名词 / 永久纪律 / 机制基线 / 派生注册表 / 不变量。"""
        snap = self._facts().snapshot(at_ch=at_ch) or {}
        raw = self._canon_raw()
        terms = []
        for term, meta in (raw.get("terms") or {}).items():
            meta = meta if isinstance(meta, dict) else {}
            term = str(term or "").strip()
            if not term:
                continue
            terms.append({"term": term,
                          "canonical": str(meta.get("canonical") or "").strip() or term,
                          "category": str(meta.get("category") or "").strip()})
        baseline = raw.get("mechanism_baseline") or {}
        registry = {}
        try:
            from ..chapter_state import mechanism_field_registry
            registry = mechanism_field_registry(baseline)
        except Exception:
            logger.exception("机制注册表派生失败（canon 视图按无注册表返回）")
        world = self._world()
        forbidden = []
        for label in ("style_taboos", "setting_taboos"):
            for x in (getattr(world, label, None) or []):
                s = str(x or "").strip()
                if s and s not in forbidden:
                    forbidden.append(s)
        return {
            "at_ch": _int(snap.get("at_ch")) or _int(at_ch),
            "terms": terms,
            "disciplines": [{"rule": str(d.get("rule") or ""),
                             "example": str(d.get("example") or "")}
                            for d in (raw.get("disciplines") or []) if isinstance(d, dict)],
            "mechanism_baseline": baseline,
            "registry": registry,
            "invariants": snap.get("invariants") or {},
            "forbidden": forbidden,
        }

    def characters(self, at_ch: int = 0) -> dict:
        """角色认知视图：谁在哪、什么状态、想做什么，每条带来源章。"""
        snap = self._facts().snapshot(at_ch=at_ch) or {}
        rows = []
        for c in (snap.get("characters") or []):
            rows.append({"name": str(c.get("name") or ""),
                         "state": str(c.get("state") or ""),
                         "location": str(c.get("location") or ""),
                         "goal": str(c.get("goal") or ""),
                         "last_ch": _int(c.get("last_ch")),
                         "source": str(c.get("source") or "")})
        rows = [r for r in rows if r["name"]]
        return {"at_ch": _int(snap.get("at_ch")) or _int(at_ch), "characters": rows,
                "count": len(rows)}

    def timeline(self, at_ch: int = 0, kind: str = "", participant: str = "",
                 limit: int = TIMELINE_LIMIT_MAX) -> dict:
        """发展线视图：全量事件 + 按章分组 + 类型/参与人统计（供时间线渲染）。"""
        try:
            rows = EventStream(self.store).query(to_ch=at_ch)
        except Exception:
            logger.exception("events.jsonl 读取失败（时间线返回空）")
            rows = []
        if kind:
            rows = [r for r in rows if str(r.get("kind") or "state") == kind]
        if participant:
            rows = [r for r in rows
                    if participant in (r.get("participants") or [])]
        n = max(1, min(_int(limit, TIMELINE_LIMIT_MAX) or TIMELINE_LIMIT_MAX,
                       TIMELINE_LIMIT_MAX))
        rows = rows[-n:]
        by_ch: dict[int, list] = {}
        kinds: dict[str, int] = {}
        people: dict[str, int] = {}
        out_rows = []
        for r in rows:
            ch = _int(r.get("ch"))
            row = {"id": str(r.get("id") or ""), "ch": ch, "scene": _int(r.get("scene")),
                   "desc": str(r.get("desc") or ""),
                   "participants": list(r.get("participants") or []),
                   "kind": str(r.get("kind") or "state"),
                   "cause": str(r.get("cause") or ""),
                   "effect": list(r.get("effect") or []),
                   "consumed_ch": _int(r.get("consumed_ch"))}
            out_rows.append(row)
            by_ch.setdefault(ch, []).append(row["desc"])
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
            for p in row["participants"]:
                people[p] = people.get(p, 0) + 1
        return {"at_ch": _int(at_ch), "events": out_rows, "count": len(out_rows),
                "by_ch": [{"ch": ch, "count": len(v), "descs": v[:6]}
                          for ch, v in sorted(by_ch.items())],
                "kinds": kinds, "participants": people}

    def threads(self, at_ch: int = 0, status: str = "", level: str = "") -> dict:
        """可能分支视图：已埋未收伏笔，按 due_ch 升序（不新造承诺）。"""
        rows = []
        try:
            rows = self._facts().open_threads(at_ch=at_ch)
        except Exception:
            logger.exception("伏笔视图构造失败（返回空）")
        if status:
            want = {s.strip() for s in str(status).split(",") if s.strip()}
            rows = [r for r in rows if str(r.get("status")) in want]
        if level:
            rows = [r for r in rows if str(r.get("level") or "main") == level]
        return {"at_ch": _int(at_ch), "threads": rows, "count": len(rows)}

    # ---------- 聚合 ----------
    def view(self, name: str, at_ch: int = 0, **kwargs) -> dict:
        """按名取视图（未知名抛 ValueError，不静默给空）。"""
        name = str(name or "").strip()
        if name not in VIEWS:
            raise ValueError(f"未知视图: {name}（可选 {'/'.join(VIEWS)}）")
        return getattr(self, name)(at_ch=at_ch, **kwargs)

    def snapshot(self, at_ch: int = 0, views: Optional[list] = None) -> dict:
        """四视图聚合快照：带 version/sources/generated_at，供消费方一次性取用。"""
        want = [v for v in (views or VIEWS) if v in VIEWS]
        snap = {"version": self.version(want),
                "generated_at": datetime.now(timezone.utc).astimezone().isoformat(
                    timespec="seconds"),
                "sources": self.sources(),
                "views": {}}
        for name in want:
            try:
                snap["views"][name] = self.view(name, at_ch=at_ch)
            except Exception:
                logger.exception("视图构造失败（返回空结构并留痕）: view=%s", name)
                snap["views"][name] = {"error": "构造失败", "view": name}
        return snap
