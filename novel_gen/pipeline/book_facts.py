# -*- coding: utf-8 -*-
"""单一只读状态视图（REQ-R20 / TECH-R20，M0）。

硬边界（写进模块约定，后续改动需先改需求）：
- **只读**：只调用 store 的 load_* 读取方法，本模块不 import 任何写入函数，
  也不修改传入对象；唯一写动作是把派生快照经 `store.save_book_facts()` 落盘。
- **替换而非叠加**：写章/大纲/世界观拿到的是一份按预算裁剪好的事实块，
  用来替换原有的账本类注入块，不新增提示词层。
- **单一出口**：任何人问「这本书到现在发生了什么」，只读 `book_facts.json`。

数据契约（TECH-R20）：
{ version, at_ch, world_v, outline_v, stale, invariants, characters,
  open_threads, events, terminology }

派生文件：项目目录下 `book_facts.json`（原子写，项目重置/删除需一并清理）。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: 快照结构版本（字段只增不改）
VERSION = 1

#: 硬区总上限（字符）：机制基线 / 锁定设定 / 永久纪律 / 禁用术语
HARD_LIMIT = 1200

#: 三视图预算（硬区, 活区）；M0 拍定值，验收时按实测提示词长度校准 ±20%
AGENT_BUDGETS = {
    "writer": (HARD_LIMIT, 1500),
    "architect": (HARD_LIMIT, 800),
    "world": (HARD_LIMIT, 600),
}

#: 活区确定性过滤阈值
THREAD_DUE_WINDOW = 3      # 距兑现 ≤3 章
THREAD_AGING_CH = 10       # 埋设距今 ≥10 章
EVENT_RECENT = 8           # 最近 8 条已消费事件

#: 接口 budget 参数收敛区间
BUDGET_MIN, BUDGET_MAX = 300, 3000

#: 每条可追溯记录必须至少带一个来源
SOURCE_KEYS = ("last_ch", "planted_ch", "evidence_ch", "consumed_ch")

_ARCHITECT_THREADS = 10
_ARCHITECT_CHARS = 8
_WORLD_THREADS = 6
_WORLD_CHARS = 6


def empty_snapshot(at_ch: int = 0) -> dict:
    """空结构（缺数据时给空而非 null；US-1）。"""
    return {
        "version": VERSION,
        "at_ch": int(at_ch or 0),
        "world_v": None,
        "outline_v": None,
        "stale": False,
        "invariants": {"mechanism": {}, "locked": [], "disciplines": []},
        "characters": [],
        "open_threads": [],
        "events": [],
        "terminology": {"terms": [], "forbidden": []},
    }


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _name_of(item) -> str:
    """兼容 dataclass 与 dict 两种伏笔条目。"""
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(getattr(item, "name", "") or "").strip()


class BookFacts:
    """只读聚合器：构造快照、渲染三视图注入块、查询未回收伏笔。"""

    def __init__(self, store, cfg=None):
        self.store = store
        self.cfg = cfg
        #: 无来源被丢弃的条数（构造时计数，供接口/日志观测；不进快照字段）
        self.last_dropped = 0
        #: 构造失败标记（调用方据此降级）
        self.degraded = False

    # ---------- 开关 ----------
    @property
    def enabled(self) -> bool:
        """cfg 缺省视为开启（接口层已按开关决定是否调用）。"""
        if self.cfg is None:
            return True
        return bool(getattr(self.cfg, "book_facts", False))

    # ---------- 章号与版本 ----------
    def resolve_at_ch(self, at_ch: int = 0) -> int:
        """0 = 已写最大章；显式章号原样返回（不越界纠正，只用于过滤）。

        优先读 state.json / chapter_state.json（都是小文件），避免 list_chapters
        把每章正文全量解析一遍（100 章书的性能红线来自这里）。
        """
        n = _int(at_ch, 0)
        if n > 0:
            return n
        try:
            last = _int(getattr(self.store.load_state(), "last_chapter", 0), 0)
            if last > 0:
                return last
        except Exception:
            logger.warning("state.json 读取失败（改读章末账本）", exc_info=True)
        try:
            ch = _int((self.store.load_chapter_state() or {}).get("chapter", 0), 0)
            if ch > 0:
                return ch
        except Exception:
            logger.warning("chapter_state.json 读取失败（改用章节列表）", exc_info=True)
        try:
            chapters = self.store.list_chapters() or []
            return max((_int(getattr(c, "no", 0), 0) for c in chapters), default=0)
        except Exception:
            logger.warning("读取已写章号失败（按 0 处理）", exc_info=True)
            return 0

    def _current_versions(self) -> tuple:
        """当前生效的 (outline_v, world_v)；读不到给 None（不冒充已同步）。"""
        out = {}
        for kind in ("outline", "world"):
            try:
                from ..store.versioned import VersionedProduct
                out[kind] = VersionedProduct(self.store, kind).selected_version("all")
            except Exception:
                out[kind] = None
        return out.get("outline"), out.get("world")

    def _persisted(self, at_ch: int) -> Optional[dict]:
        """同章已落盘的快照（章节内多次读取复用同一份，不重复解析九份 JSON）。"""
        try:
            d = self.store.load_book_facts()
        except Exception:
            return None
        if not isinstance(d, dict) or not d.get("version"):
            return None
        if _int(d.get("at_ch", -1), -1) != int(at_ch):
            return None
        return d

    def _mark_stale(self, snapshot: dict) -> dict:
        """快照版本与当前生效版本不一致 → stale=True（状态基于旧版设定这件事必须可见）。"""
        cur_outline, cur_world = self._current_versions()
        stored_outline = snapshot.get("outline_v")
        stored_world = snapshot.get("world_v")
        stale = False
        if cur_outline is not None and stored_outline is not None and _int(cur_outline) != _int(stored_outline):
            stale = True
        if cur_world is not None and stored_world is not None and _int(cur_world) != _int(stored_world):
            stale = True
        snapshot["stale"] = stale
        return snapshot

    # ---------- 快照 ----------
    def snapshot(self, at_ch: int = 0) -> dict:
        """构造（或复用）到 at_ch 为止的状态快照，并落盘。

        构造失败返回空结构（degraded=True），绝不抛给主链路。
        """
        at_ch = self.resolve_at_ch(at_ch)
        cached = self._persisted(at_ch)
        if cached is not None:
            return self._mark_stale(cached)
        data = self._build(at_ch)
        data = self._mark_stale(data)
        try:
            self.store.save_book_facts(data)
        except Exception:
            logger.exception("book_facts 落盘失败（降级：本次仍返回内存快照，不中断生成）")
        return data

    def _build(self, at_ch: int) -> dict:
        self.degraded = False
        self.last_dropped = 0
        dropped = 0
        snap = empty_snapshot(at_ch)
        try:
            world = self.store.load_world()
            outline = self.store.load_outline()
            mem = self.store.load_book_memory() or {}
            canon = mem.get("canon") or {}
            chapter_state = self.store.load_chapter_state() or {}
            char_states = self.store.load_character_states() or {}
            persisted_fs = self.store.load_foreshadow_commitment() or {}

            outline_v, world_v = self._current_versions()
            snap["outline_v"] = outline_v
            snap["world_v"] = world_v

            snap["invariants"] = self._build_invariants(canon, world)
            chars, d1 = self._build_characters(world, chapter_state, char_states, at_ch)
            snap["characters"] = chars
            dropped += d1
            threads, d2 = self._build_open_threads(world, persisted_fs, at_ch)
            snap["open_threads"] = threads
            dropped += d2
            snap["events"] = self._build_events(mem, at_ch)
            snap["terminology"] = self._build_terminology(canon, world)
        except Exception:
            self.degraded = True
            logger.exception("book_facts 构造失败（返回空结构 + degraded，主链路不中断）")
            return empty_snapshot(at_ch)
        self.last_dropped = dropped
        if dropped:
            logger.warning("book_facts 丢弃无来源条目 %d 条（不进注入块）", dropped)
        return snap

    # ---------- 分区构造 ----------
    def _build_invariants(self, canon: dict, world) -> dict:
        locked = []
        try:
            lock_path = getattr(self.store, "dir", None)
            if lock_path is not None:
                lp = lock_path / "lock.json"
                if lp.exists():
                    lk = json.loads(lp.read_text(encoding="utf-8")) or {}
                    if lk.get("world_view"):
                        locked.append("世界观（用户锁定，不得改写）")
                    for name, flag in (lk.get("character") or {}).items():
                        if flag:
                            locked.append(f"人物「{name}」（用户锁定，设定不得改动）")
        except Exception:
            logger.exception("读取用户锁定项失败（锁定区降级为空）")
        disciplines = []
        for d in (canon.get("disciplines") or []):
            if isinstance(d, dict):
                rule = str(d.get("rule") or "").strip()
                if rule:
                    disciplines.append(rule)
            elif str(d).strip():
                disciplines.append(str(d).strip())
        mechanism = canon.get("mechanism_baseline") or {}
        if not isinstance(mechanism, dict):
            mechanism = {"raw": str(mechanism)}
        return {"mechanism": mechanism, "locked": locked, "disciplines": disciplines}

    def _build_characters(self, world, chapter_state: dict, char_states: dict,
                          at_ch: int) -> tuple:
        """人物当前态：章末账本（生死/状态）> 人物状态快照（位置/目标）。无 last_ch 丢弃。"""
        dropped = 0
        names: list[str] = []
        for c in (getattr(world, "characters", None) or []):
            n = str(getattr(c, "name", "") or "").strip()
            if n and n not in names:
                names.append(n)
        cs_state = chapter_state.get("characters") or {}
        if isinstance(cs_state, dict):
            for n in cs_state:
                if n and n not in names:
                    names.append(str(n))
        for n in (char_states or {}):
            if n and n not in names:
                names.append(str(n))

        cs_ch = _int(chapter_state.get("chapter", 0), 0)
        out = []
        for name in names:
            st = (char_states or {}).get(name) or {}
            last = _int(st.get("last_seen", 0), 0)
            source = f"character_states:{last}" if last else ""
            state_text = ""
            if isinstance(cs_state, dict) and name in cs_state:
                state_text = str(cs_state.get(name) or "").strip()
                # 章末账本是生死/状态的权威源：同名时以其为来源（比对人物快照更权威）
                if cs_ch > 0:
                    last = max(last, cs_ch)
                    source = f"chapter_state:{cs_ch}"
            if last <= 0 or (at_ch > 0 and last > at_ch):
                dropped += 1
                continue
            if not state_text and not (st.get("location") or st.get("goal")):
                dropped += 1
                continue
            out.append({
                "name": name,
                "location": str(st.get("location") or "").strip(),
                "goal": str(st.get("goal") or "").strip(),
                "state": state_text,
                "last_ch": last,
                "source": source or f"character_states:{last}",
            })
        out.sort(key=lambda r: (-int(r["last_ch"]), r["name"]))
        return out, dropped

    def _build_open_threads(self, world, persisted_fs: dict, at_ch: int) -> tuple:
        """未回收伏笔：foreshadow_unified 开后由 `foreshadowing.json` 唯一供数，
        否则沿用 world.foreshadowing + 承诺账本（M0 行为）。"""
        if self._flag("foreshadow_unified"):
            try:
                from .state.foreshadow import ForeshadowLedger
                lead = _int(getattr(self.cfg, "foreshadow_lead", 3), 3)
                rows = ForeshadowLedger(self.store, self.cfg).view(at_ch, lead=lead)
                out = []
                for r in rows:
                    status = {"due": "due_soon", "resolved": "revealed"}.get(
                        r["status"], r["status"])
                    out.append({
                        "key": r["key"],
                        "name": r["name"],
                        "desc": r["desc"],
                        "planted_ch": r["planted_ch"],
                        "due_ch": r["due_ch"],
                        "status": status,
                        "level": r.get("level") or "main",
                        "evidence_ch": _int((r.get("evidence") or {}).get("ch"), 0) or None,
                    })
                return out, 0
            except Exception:
                logger.exception("foreshadowing.json 读取失败（退回 world 伏笔项）")
        dropped = 0
        persisted = {}
        for it in (persisted_fs.get("items") or []):
            n = _name_of(it)
            if n:
                persisted[n] = it
        lead = 3
        try:
            lead = _int(getattr(self.cfg, "foreshadow_lead", 3), 3)
        except Exception:
            lead = 3
        out = []
        for fs in (getattr(world, "foreshadowing", None) or []):
            name = _name_of(fs)
            if isinstance(fs, dict):
                reveal = fs.get("reveal_ch")
                buried = _int(fs.get("buried_ch"), 0)
                desc = str(fs.get("content") or "").strip()
            else:
                reveal = getattr(fs, "reveal_ch", None)
                buried = _int(getattr(fs, "buried_ch", 0), 0)
                desc = str(getattr(fs, "content", "") or "").strip()
            if not name or reveal is None:
                dropped += 1   # 无回收章 = 无 due_ch 来源，无法定位
                continue
            due = _int(reveal, 0)
            if due <= 0:
                dropped += 1
                continue
            prev = persisted.get(name) or {}
            revealed = prev.get("revealed_ch")
            if revealed:
                status = "revealed"
            elif at_ch >= due:
                status = "overdue"
            elif at_ch >= due - lead:
                status = "due_soon"
            else:
                status = "planted"
            out.append({
                "key": name,
                "name": name,
                "desc": desc,
                "planted_ch": buried,
                "due_ch": due,
                "status": status,
                "evidence_ch": _int(revealed, 0) if revealed else None,
            })
        out.sort(key=lambda r: (r["due_ch"], r["planted_ch"], r["name"]))
        return out, dropped

    def _build_events(self, mem: dict, at_ch: int) -> list:
        if self._flag("event_stream"):
            try:
                from .state.events import EventStream
                rows = EventStream(self.store).query_with_consumption(to_ch=at_ch)
                out = [{"ch": r["ch"], "desc": r["desc"],
                        "participants": r.get("participants") or [],
                        "kind": r.get("kind") or "state",
                        "consumed_ch": r.get("consumed_ch")}
                       for r in rows if r.get("desc")]
                return out[-EVENT_RECENT:]
            except Exception:
                logger.exception("events.jsonl 读取失败（退回 book_memory 事件）")
        consumed = ((mem.get("events") or {}).get("consumed") or [])
        rows = []
        for e in consumed:
            if not isinstance(e, dict):
                continue
            ch = _int(e.get("ch", 0), 0)
            desc = str(e.get("desc") or "").strip()
            if ch <= 0 or not desc:
                continue
            if at_ch and ch > at_ch:
                continue
            rows.append({"ch": ch, "desc": desc, "consumed_ch": ch})
        rows.sort(key=lambda r: r["consumed_ch"])
        return rows[-EVENT_RECENT:]

    def _build_terminology(self, canon: dict, world) -> dict:
        """术语统一（含标准写法）+ 禁用术语；禁用词来自世界观设定卡。"""
        terms = []
        raw_terms = canon.get("terms") if isinstance(canon.get("terms"), dict) else {}
        for k, v in (raw_terms or {}).items():
            name = str(k or "").strip()
            if not name:
                continue
            canonical = str((v or {}).get("canonical") or "").strip() if isinstance(v, dict) else ""
            terms.append(f"{name}（标准写法：{canonical}）" if canonical and canonical != name else name)
        forbidden = []
        for label in ("style_taboos", "setting_taboos"):
            for x in (getattr(world, label, None) or []):
                s = str(x or "").strip()
                if s and s not in forbidden:
                    forbidden.append(s)
        return {"terms": terms[:20], "forbidden": forbidden[:20]}

    # ---------- 开关/派生查询 ----------
    def _flag(self, name: str) -> bool:
        """M1 子开关（无 cfg 时按关闭处理：接口层另有显式判断）。"""
        if self.cfg is None:
            return False
        return bool(getattr(self.cfg, name, False))

    def violation_list(self, at_ch: int = 0, limit: int = 0) -> list:
        """确定性冲突清单（conflict_guard 关闭时返回空列表）。"""
        if not self._flag("conflict_guard"):
            return []
        try:
            from .state.conflict_rules import chapters_from_scenes, detect
            at_ch = self.resolve_at_ch(at_ch)
            scenes = {}
            try:
                scenes = self.store.load_scenes() or {}
            except Exception:
                logger.exception("scenes.json 读取失败（冲突检测跳过）")
            chapters = [c for c in chapters_from_scenes(scenes)
                        if not at_ch or c["no"] <= at_ch]
            try:
                latest = self.store.load_chapter_state() or {}
            except Exception:
                latest = {}
            if isinstance(latest, dict) and latest.get("chapter"):
                no = _int(latest.get("chapter"))
                if not any(c["no"] == no for c in chapters):
                    from .state.conflict_rules import parse_numbers
                    chapters.append({
                        "no": no,
                        "numbers": parse_numbers(latest.get("numbers")),
                        "characters": latest.get("characters") or {},
                        "items": latest.get("items") or {},
                        "time": str(latest.get("time") or ""),
                        "facts": latest.get("facts") or [],
                        "body": latest.get("body") or [],
                    })
            canonical_map = {}
            try:
                mem = self.store.load_book_memory() or {}
                canonical_map = (mem.get("canon") or {}).get("terms") or {}
            except Exception:
                logger.exception("canon 读取失败（称谓规则跳过）")
            events = []
            try:
                from .state.events import EventStream
                events = EventStream(self.store).all()
            except Exception:
                logger.exception("events.jsonl 读取失败（生死/物品规则降级为不判定）")
            rows = detect(chapters, events, canonical_map)
            return rows[:limit] if limit else rows
        except Exception:
            logger.exception("冲突规则汇总失败（不注入提醒）")
            return []

    def render_violation_block(self, at_ch: int = 0, limit: int = 5) -> str:
        """写前冲突提醒（补偿块之后、硬区之前注入）；无命中返回空串。"""
        rows = self.violation_list(at_ch, limit=limit)
        if not rows:
            return ""
        try:
            from .state.conflict_rules import render_block
            return render_block(rows, limit=limit)
        except Exception:
            logger.exception("冲突提醒渲染失败（跳过）")
            return ""

    # ---------- 未回收伏笔查询 ----------
    def open_threads(self, at_ch: int = 0, overdue_only: bool = False) -> list:
        """按 due_ch 升序（同 due 按 planted_ch）；overdue_only=只给已逾期/临期。"""
        snap = self.snapshot(at_ch)
        rows = [r for r in (snap.get("open_threads") or []) if r.get("status") != "revealed"]
        if overdue_only:
            rows = [r for r in rows if r.get("status") in ("overdue", "due_soon")]
        return rows

    # ---------- 注入块 ----------
    def block_for(self, agent: str, at_ch: int = 0, budget: Optional[int] = None) -> str:
        """按视图渲染注入块；关闭开关/构造失败返回空串（行为与改造前等价）。"""
        if not self.enabled:
            return ""
        agent = (agent or "").strip()
        if agent not in AGENT_BUDGETS:
            raise ValueError(f"未知 agent 视图: {agent}（可选 writer/architect/world）")
        snap = self.snapshot(at_ch)
        if self.degraded:
            return ""
        hard_cap, active_cap = AGENT_BUDGETS[agent]
        if budget is not None:
            try:
                active_cap = max(BUDGET_MIN, min(BUDGET_MAX, int(budget)))
            except (TypeError, ValueError):
                pass
        hard = self._render_hard(snap)
        active = self._render_active(snap, agent, active_cap, at_ch)
        parts = [p for p in (hard, active) if p]
        text = "\n\n".join(parts)
        if text and snap.get("stale"):
            text += "\n\n【提示】以下状态基于旧版设定（大纲/世界观已变更），以最新设定为准。"
        return text

    def _render_hard(self, snap: dict) -> str:
        """硬区：锁定设定 > 机制基线 > 永久纪律 > 禁用术语；永不裁剪，整条丢弃不截半句。"""
        inv = snap.get("invariants") or {}
        blocks: list[str] = []
        locked = list(inv.get("locked") or [])
        if locked:
            blocks.append("【锁定设定·不可违背】\n" + "\n".join(f"- {x}" for x in locked))
        mech = inv.get("mechanism") or {}
        if isinstance(mech, dict) and mech:
            lines = [f"- {k}：{v}" for k, v in mech.items()]
            blocks.append("【机制基线·权威口径】\n" + "\n".join(lines))
        disciplines = list(inv.get("disciplines") or [])
        if disciplines:
            blocks.append("【永久纪律·本书历史教训】\n" + "\n".join(f"- {x}" for x in disciplines))
        forbidden = list((snap.get("terminology") or {}).get("forbidden") or [])
        terms = list((snap.get("terminology") or {}).get("terms") or [])
        if terms:
            blocks.append("【术语统一·全文只能一种写法】\n" + "、".join(terms))
        if forbidden:
            blocks.append("【禁用术语·全书不得出现】\n" + "、".join(forbidden))
        out = ""
        for b in blocks:
            if len(out) + len(b) + 2 > HARD_LIMIT:
                continue   # 超预算整块丢弃（不截半句）；顺序即优先级
            out = f"{out}\n\n{b}" if out else b
        return out

    def _render_active(self, snap: dict, agent: str, cap: int, at_ch: int) -> str:
        """活区：确定性过滤 + 排序，超预算从尾部整条丢弃，不做语义打分。"""
        at_ch = self.resolve_at_ch(at_ch)
        chars = list(snap.get("characters") or [])
        threads = [r for r in (snap.get("open_threads") or []) if r.get("status") != "revealed"]
        events = list(snap.get("events") or [])

        if agent == "writer":
            focus = self._focus_characters(at_ch)
            if focus:
                chars = [c for c in chars if c.get("name") in focus]
            chars = chars[:8]
            threads = [t for t in threads
                       if t["due_ch"] <= at_ch + THREAD_DUE_WINDOW
                       or (at_ch - t["planted_ch"]) >= THREAD_AGING_CH]
            threads = threads[:8]
        elif agent == "architect":
            chars = chars[:_ARCHITECT_CHARS]
            threads = threads[:_ARCHITECT_THREADS]
            events = []
        else:  # world：机制与地理/势力现状（人物位置 + 临期伏笔，不含事件流）
            chars = chars[:_WORLD_CHARS]
            threads = threads[:_WORLD_THREADS]
            events = []

        items: list[str] = []
        if chars:
            lines = []
            for c in chars:
                bits = [c["name"]]
                if c.get("state"):
                    bits.append(c["state"])
                if c.get("location"):
                    bits.append(f"位于{c['location']}")
                if c.get("goal"):
                    bits.append(f"目标{c['goal']}")
                lines.append("- " + "；".join(bits) + f"（至第{c['last_ch']}章）")
            items.append("【人物当前态·来源可追溯】\n" + "\n".join(lines))
        if threads:
            lines = []
            for t in threads:
                tag = {"overdue": "已逾期", "due_soon": "临期"}.get(t.get("status"), "未回收")
                line = f"- {t['name']}（{tag}｜埋第{t['planted_ch']}章｜应第{t['due_ch']}章回收）"
                if t.get("desc"):
                    line += f"：{t['desc']}"
                lines.append(line)
            items.append("【未回收伏笔】\n" + "\n".join(lines))
        if events:
            items.append("【最近已发生事件】\n" + "\n".join(
                f"- 第{e['ch']}章：{e['desc']}" for e in events))

        out = ""
        for b in items:
            if len(out) + len(b) + 2 > cap:
                continue
            out = f"{out}\n\n{b}" if out else b
        return out

    def _focus_characters(self, at_ch: int) -> set:
        """在场人物：本章大纲 character_moves 命中名 + 上一章出场记录（确定性，不猜）。"""
        names: set[str] = set()
        try:
            outline = self.store.load_outline()
            chapters = getattr(outline, "chapters", None) or []
            if 0 < at_ch <= len(chapters):
                moves = str(getattr(chapters[at_ch - 1], "character_moves", "") or "")
                for c in (getattr(self.store.load_world(), "characters", None) or []):
                    n = str(getattr(c, "name", "") or "").strip()
                    if n and n in moves:
                        names.add(n)
        except Exception:
            logger.exception("读取本章人物动线失败（在场人物退化为全部人物）")
        try:
            cs = self.store.load_character_states() or {}
            for name, st in cs.items():
                if _int((st or {}).get("last_seen", 0), 0) >= at_ch - 1:
                    names.add(str(name))
        except Exception:
            pass
        return names
