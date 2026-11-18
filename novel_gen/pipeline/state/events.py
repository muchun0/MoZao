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

#: id 规则：ev_ch{章号 2-3 位}_s{场景 2 位}_{章内 3 位序号}[_r{版本 2 位}]
#: 重写章用 `_r02` 后缀另立一版（同一 (ch, scene, seq) 的旧版由 supersede.json 标注），
#: 不用覆盖写——append-only 一旦允许就地改行，"重放 ≡ 旧快照" 的验收前提就没了。
ID_RE = re.compile(r"^ev_ch(\d{2,3})_s(\d{2})_(\d{3})(?:_r(\d{2}))?$")

#: 事件类型白名单（未登记的 kind 落 state，避免拼错词导致过滤失效）
KINDS = {"object", "relationship", "state", "foreshadow"}


def make_id(ch: int, scene: int, seq: int, revision: int = 1) -> str:
    """章内单调递增生成，不依赖全局计数；revision>1 时加 `_r{02d}` 后缀。"""
    base = f"ev_ch{int(ch):02d}_s{int(scene):02d}_{int(seq):03d}"
    rev = int(revision or 1)
    return base if rev <= 1 else f"{base}_r{rev:02d}"


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

    def supersede_path(self):
        return self.store.supersede_path()

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
        # 被新版本替代的行不再参与查询（侧车为空时零开销，老项目行为不变）
        sup = self.superseded()
        if sup:
            rows = [r for r in rows if str(r.get("id")) not in sup]
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
                  allow_inferred: bool = False, revision: int = 0) -> dict:
        """校验并补全一条事件；不合法抛 EventError。"""
        if not isinstance(rec, dict):
            raise EventError("事件必须是 dict")
        chapter = int(rec.get("ch") or ch or 0)
        if chapter <= 0:
            raise EventError("事件缺章号")
        desc = str(rec.get("desc") or "").strip()
        if not desc:
            raise EventError("事件缺描述")
        rev = int(rec.get("revision") or revision or 1)
        rid = str(rec.get("id") or "").strip()
        if not rid:
            rid = make_id(chapter, int(rec.get("scene") or scene or 0), int(seq or 1), rev)
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
        # 重写版本只在有值时落盘：老行保持原样，不因新契约整列变脏
        if rev > 1:
            out["revision"] = rev
        return out

    def append(self, events: Iterable[dict], *, ch: int = 0, scene: int = 0,
               allow_inferred: bool = False, revision: int = 0) -> dict:
        """追加写入；返回 {written, skipped, rejected}。已存在的 id 跳过并计数。

        批次原子性：整批先全部校验、再一次性追加（任一条不合法只拒该条，但
        **写入动作只有一次**）；调用方核对 `rejected>0` 后自行决定是否整批重来，
        半截批次不会落盘。
        """
        existing = {str(r.get("id")) for r in self.all()}
        written = skipped = rejected = 0
        lines: list[str] = []
        for i, rec in enumerate(events or [], start=1):
            try:
                item = self.normalize(rec, ch=ch, scene=scene, seq=i,
                                      allow_inferred=allow_inferred,
                                      revision=revision)
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

    # ---------- 版本（重写章另立一版） ----------
    def next_revision(self, ch: int) -> int:
        """本章下一版版本号：已有最大 revision +1（首写返回 1）。

        重写章必须换版本号，否则 `seq` 从批次位置重新计数会让新旧 id 撞车，
        新事实会被 append 当成重复静默跳过（账本停在旧版）。
        """
        chapter = int(ch or 0)
        if chapter <= 0:
            return 1
        best = 0
        for r in self._read_raw():
            if int(r.get("ch") or 0) != chapter:
                continue
            try:
                best = max(best, int(r.get("revision") or 1))
            except (TypeError, ValueError):
                continue
        return best + 1

    def supersede_revision(self, ch: int, revision: int) -> int:
        """把本章 `revision-1` 版的事件标成被新版替代，返回标注条数。

        只写 `supersede.json` 侧车（不删行、不改行）：`query()` 会隐藏旧版，
        但 `all()` 仍保留原始行，重放/回滚仍可查。
        """
        chapter = int(ch or 0)
        rev = int(revision or 0)
        if chapter <= 0 or rev < 2:
            return 0
        rows = self._read_raw()
        old_rev, new_rev = rev - 1, rev
        new_by_base = {}
        old_rows = []
        for r in rows:
            if int(r.get("ch") or 0) != chapter:
                continue
            try:
                rrev = int(r.get("revision") or 1)
            except (TypeError, ValueError):
                continue
            base = str(r.get("id") or "").split("_r")[0]
            if rrev == new_rev:
                new_by_base[base] = str(r.get("id"))
            elif rrev == old_rev:
                old_rows.append((base, str(r.get("id"))))
        pairs = [{"old": old, "new": new_by_base.get(base, ""), "ch": chapter,
                  "reason": (f"第{chapter}章重写（r{old_rev:02d}→r{new_rev:02d}）"
                             + ("" if base in new_by_base else "，新版无对应条目"))}
                 for base, old in old_rows]
        if not pairs:
            return 0
        self.supersede(pairs)
        return len(pairs)

    # ---------- 版本替代（append-only 的补丁） ----------
    def superseded(self) -> dict:
        """旧事件 id → {by, ch}；由 `supersede.json` 侧车承载，不回写事件行。"""
        try:
            data = self.store.load_supersede()
        except Exception:
            logger.exception("supersede.json 读取失败（按无替代处理）")
            return {}
        items = data.get("items") if isinstance(data, dict) else None
        out = {}
        if isinstance(items, dict):
            for old, meta in items.items():
                if isinstance(meta, dict):
                    out[str(old)] = {"by": str(meta.get("by") or ""),
                                     "ch": int(meta.get("ch") or 0)}
        return out

    def supersede(self, pairs: Iterable[dict]) -> dict:
        """标注旧事件被新版替代：`[{old, new, ch, reason}]`（写侧车，幂等）。"""
        items = {k: dict(v) for k, v in self.superseded().items()}
        for p in (pairs or []):
            old = str((p or {}).get("old") or "").strip()
            new = str((p or {}).get("new") or "").strip()
            # `new` 允许为空：重写后新版条数变少时，旧版多出来的条目也要退场（只隐藏、不删行）
            if not old:
                continue
            items[old] = {"by": new, "ch": int((p or {}).get("ch") or 0),
                          "reason": str((p or {}).get("reason") or "")}
        self.store.save_supersede({"version": 1, "items": items})
        return {"superseded": len(items)}

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


#: 物品转移/处置动词（判定 `kind="object"` 的确定性线索，零 LLM）
_OBJECT_VERBS = (
    "交给", "递给", "还给", "扔给", "塞给", "交给", "送给", "卖给", "押给",
    "抢", "夺", "偷", "顺走", "失窃", "丢了", "丢失", "收进", "收起", "藏进",
    "取出", "掏出", "典当", "典了", "购得", "买到", "捡到", "拾起", "吞下", "吃光",
)


def name_hits(text: str, names: Iterable[str]) -> list:
    """正文里出现的已知人物名（按首次出现位置排序，去重）。"""
    src = str(text or "")
    hits = [(src.find(str(n).strip()), str(n).strip())
            for n in (names or []) if str(n).strip()]
    hits = [(pos, name) for pos, name in hits if pos >= 0]
    hits.sort()
    return list(dict.fromkeys(name for _pos, name in hits))


def classify_fact(text: str, names: Iterable[str] = (),
                  items: Iterable[str] = ()) -> tuple:
    """一条章节事实 → (participants, kind)。

    零 LLM 确定性判定：出现的人物名做参与人；出现物品名且带转移/处置动词 →
    `object`（物品规则的事件豁免），否则 `state`。命名/措辞不匹配时退化为空参与人
    与 `state`（宁缺不臆造），但变量本身必须存在——否则事件流永远没有参与人与
    物品事件，生死/物品规则拿不到豁免证据。
    """
    participants = name_hits(text, names)
    kind = "state"
    item_names = [str(i).strip() for i in (items or []) if str(i).strip()]
    if item_names and any(v in str(text) for v in _OBJECT_VERBS):
        hit_items = [i for i in item_names if i in str(text)]
        if hit_items:
            kind = "object"
    return participants, kind


def derive_fact_events(facts: Iterable[str], ch: int, names: Iterable[str] = (),
                       items: Iterable[str] = (), scene: int = 0) -> list:
    """把章末 facts 转成事件行（参与人/类型/因果位按确定性规则补齐）。"""
    rows = []
    for desc in (facts or []):
        desc = str(desc or "").strip()
        if not desc:
            continue
        participants, kind = classify_fact(desc, names, items)
        rows.append({"ch": int(ch), "scene": scene, "desc": desc,
                     "participants": participants, "cause": "", "effect": [],
                     "kind": kind, "inferred": True})
    return rows


def resolve_at_ch_from_rows(rows: list, default: int = 0) -> int:
    return max((int(r.get("ch") or 0) for r in (rows or [])), default=default)


#: 摘要切句（摘要天然是分句概述，按句末标点切）
_SUMMARY_SPLIT_RE = re.compile(r"[。！？!?；;\n]")

#: 单章摘要事件上限（再多就是复述整章，时间线不可扫读）
SUMMARY_EVENT_LIMIT = 4


def derive_summary_events(summary: str, ch: int, names: Iterable[str] = (),
                          items: Iterable[str] = (), limit: int = SUMMARY_EVENT_LIMIT) -> list:
    """确定性兜底事件：章末账本缺本章时，用**本章摘要**切句生成事件。

    章号永远写成传入的 ch（调用方保证是本章），来源标 `inferred=true`——
    它不是因果抽取结果，只是"本章发生了什么"的确定性投影。
    """
    parts = [p.strip() for p in _SUMMARY_SPLIT_RE.split(str(summary or "")) if p.strip()]
    rows = []
    for part in parts[:max(1, int(limit or SUMMARY_EVENT_LIMIT))]:
        if len(part) < 4:   # 过短片段（"走了""他"）不是事件
            continue
        participants, kind = classify_fact(part, names, items)
        rows.append({"ch": int(ch), "scene": 0, "desc": part,
                     "participants": participants, "cause": "", "effect": [],
                     "kind": kind, "inferred": True})
    return rows


def derive_chapter_events(no: int, state: Optional[dict], chapter,
                          names: Iterable[str] = (), items: Iterable[str] = ()) -> list:
    """本章事件：账本属于本章 → 用 facts；账本停在旧章 → 用本章摘要兜底。

    硬约束：事件章号永远等于本章章号。旧实现直接读"最新章末快照"，账本因降频
    （`state_extract_frequency`）停在旧章时，会把旧章事实反复按旧章号写进事件流，
    表现为时间线里"事件全挂第 1 章、后面章节没有事件"。
    """
    no = int(no or 0)
    state_ch = int((state or {}).get("chapter") or 0)
    if state_ch == no:
        facts = [str(x).strip() for x in ((state or {}).get("facts") or []) if str(x).strip()]
        if facts:
            return derive_fact_events(facts, no, names, items)
    return derive_summary_events(getattr(chapter, "summary", "") or "", no, names, items)


def _world_names(store) -> tuple[list, list]:
    """世界观里的人物/物品名（补齐参与人与物品事件判定，失败降级空）。"""
    try:
        world = store.load_world()
        names = [str(getattr(c, "name", "") or "") for c in
                 (getattr(world, "characters", None) or [])]
        items = [str(getattr(i, "name", "") or "") for i in
                 (getattr(world, "items", None) or [])]
        return [n for n in names if n], [i for i in items if i]
    except Exception:
        logger.exception("世界观读取失败（事件参与人降级为空）")
        return [], []


def backfill_chapter_events(store, upto: int = 0) -> dict:
    """给"没有事件"的历史章补本章摘要事件（append-only，幂等）。

    用途：修复账本降频/抽取失败导致后续章零事件、时间线全挂第 1 章的存量书。
    只补空章：已有事件的章一条不动；同一 id 重复写入由 append 去重，重复跑无副作用。
    """
    stream = EventStream(store)
    taken = {int(r.get("ch") or 0) for r in stream.all()}
    names, items = _world_names(store)
    chapters = written = 0
    for ch in store.list_chapters():
        no = int(getattr(ch, "no", 0) or 0)
        if no <= 0 or no in taken or (upto and no > int(upto)):
            continue
        rows = derive_summary_events(getattr(ch, "summary", "") or "", no, names, items)
        if not rows:
            continue
        res = stream.append(rows, ch=no, allow_inferred=True)
        if res.get("written"):
            chapters += 1
            written += res["written"]
    if written:
        # 快照缓存（book_facts.json）不感知 events.jsonl 的追加，必须显式失效，
        # 否则时间线接口会继续返回补齐前的旧快照（实测过一次）。
        try:
            store.invalidate_book_facts()
        except Exception:
            logger.exception("事件补齐后 book_facts 缓存失效失败（下次生成会重建）")
    return {"chapters": chapters, "events": written}


def _main(argv: list) -> int:
    """维护入口：补齐某本书缺失章号的事件。

    用法：python -m novel_gen.pipeline.state.events <项目目录> [--upto N]
    """
    if not argv:
        print("用法: python -m novel_gen.pipeline.state.events <项目目录> [--upto N]")
        return 2
    from ...store.project import ProjectStore
    project_dir = argv[0]
    upto = 0
    if "--upto" in argv:
        idx = argv.index("--upto")
        upto = int(argv[idx + 1]) if idx + 1 < len(argv) else 0
    store = ProjectStore.open(project_dir)
    res = backfill_chapter_events(store, upto=upto)
    print(json.dumps({"project": store.dir.name, **res}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
