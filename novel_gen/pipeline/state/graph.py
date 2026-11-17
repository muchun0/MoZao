# -*- coding: utf-8 -*-
"""每书状态图（REQ-R24 / TECH-R24 G0'）：Canon 事实图。

为什么要有这一层：M0 的 `book_facts` 是**扁平快照**——人物、伏笔、事件各一份
列表，回答得了「现在是什么样」，回答不了「林舟和铜匣是什么关系、这条关系是
第几章哪句话立的」。图把同一批账本数据整理成 **节点 + 带证据的边**，让下游
（冲突判定 / 子图检索 / 后续 Plan 图 diff）能按关系查，而不是按字符串猜。

硬边界（改动前先改需求）：
- **只读派生**：本模块只调用 `store.load_*` 与 `EventStream.all()`，不写任何源
  账本；图是每次读时按需构建的派生层，不落盘（全书 <30ms，缓存的复杂度比它贵）。
- **无证据不建边**：任何边必须带 `evidence{ch, offset, snippet}`；拿不到证据就
  不建这条边，并计入 `skipped_edges`——宁可少一条关系，也不给冲突判定提供
  无法回指的猜测（M1 实测里 `item_owner` 的误报全部来自无证据推断）。
- **单源**：节点/边的名称一律取自既有账本（世界观点、章末账本、人物状态、
  事件流），本模块不新增抽取、不调 LLM。

数据契约（TECH-R24 §2.2）：
    { version, at_ch, nodes: [...], edges: [...],
      sources: {...}, skipped_edges: int }
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: 节点类型（封顶 7 类；新增须评审并升 graph_version）
NODE_TYPES = ("character", "faction", "place", "item", "mechanism",
              "concept", "foreshadow")

#: 边类型（封顶 6 类）
EDGE_RELS = ("holds", "part_of", "ally", "enemy", "mentor", "present_at")

#: 子图默认沿哪些边扩散（多跳只走结构类关系，不走人际评价类）
SUBGRAPH_RELS = ("holds", "part_of")

#: 图结构版本（节点/边类型集合变化时递增）
VERSION = 1

#: 节点 id 前缀缩写（与 TECH-R24 的 `ent_{缩写}_{hash8}` 对齐）
_TYPE_ABBR = {"character": "char", "faction": "fac", "place": "place",
              "item": "item", "mechanism": "mech", "concept": "cpt",
              "foreshadow": "fs"}

#: 判定「持有」的确定性动词（宁缺不臆造：只认明确的拿/给/夺）
_HOLD_VERBS = ("持有", "拿着", "握着", "取出", "掏出", "拿到", "得到", "取得",
               "获得", "抢走", "夺走", "收到", "捡到", "拾起", "收进", "收起",
               "藏进", "交给", "递给", "还给", "塞给", "买了", "购得")

#: 人际关系的确定性词表（顺序即优先级：敌对 > 师承 > 结盟）
_REL_RULES = (
    ("enemy", ("仇", "敌", "杀", "通缉", "撕破", "反制", "背叛", "清算", "提防",
               "不信任", "算计", "威胁", "告发")),
    ("mentor", ("师", "父子", "父女", "母女", "收养", "抚养", "教", "指点",
                "传承", "护着", "依赖")),
    ("ally", ("信任", "同盟", "结盟", "合作", "共享", "托付", "并肩", "同伴",
              "搭档", "帮助", "答应")),
)


def _split_relation(raw) -> tuple[str, str]:
    """把 `"苏妄言：信任初步修复"` 拆成 (人物名, 关系原文)；不认识的结构返回空。"""
    if isinstance(raw, dict):
        other = _s(raw.get("name") or raw.get("target"))
        text = _s(raw.get("desc") or raw.get("text"))
        return other, text
    text = _s(raw)
    for sep in ("：", ":"):
        if sep in text:
            other, desc = text.split(sep, 1)
            return _s(other), _s(desc)
    return "", text


def _classify_relation(text: str) -> str:
    """关系句 → ally/enemy/mentor；词表都不命中返回空（不建边）。"""
    for rel, words in _REL_RULES:
        if any(w in text for w in words):
            return rel
    return ""


#: 给出类动词：物品流向动词**之后**的那个人（"他把铜匣还给甲" → 甲）
_GIVE_VERBS = ("交给", "递给", "还给", "塞给", "送给", "转交", "归还")


def _pick_holder(text: str, chars: list[str]) -> str:
    """从一句事实里判定物品持有者：给 → 动词后最近的人；取/得 → 动词前最近的人。

    判不出（无持有动词、动词两侧都没有人物）返回空，调用方计入 skipped——
    宁可少一条边，也不拿"句子里出现过的人"当持有者（那正是 M1 误报的来源）。
    """
    verbs = []
    for v in _HOLD_VERBS + _GIVE_VERBS:
        pos = text.find(v)
        if pos >= 0:
            verbs.append((pos, v))
    if not verbs:
        return ""
    pos, verb = min(verbs)
    if verb in _GIVE_VERBS:
        after = [(text.find(c), c) for c in chars if text.find(c) > pos]
        if after:
            return min(after)[1]
        # 没有接收者 → 退化为持有者，仍以动词前最近的人为准
    before = [(text.find(c), c) for c in chars if 0 <= text.find(c) < pos]
    if before:
        return max(before)[1]
    after_any = [(text.find(c), c) for c in chars if text.find(c) >= 0]
    return min(after_any)[1] if len(after_any) == 1 else ""


def _s(value) -> str:
    return str(value or "").strip()


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def node_id(kind: str, name: str) -> str:
    """稳定 id：同一类型同一规范名永远同一 id（Plan 图 diff 的前提）。"""
    abbr = _TYPE_ABBR.get(kind, kind[:4])
    raw = f"{kind}:{_s(name)}".encode("utf-8")
    return f"ent_{abbr}_{hashlib.sha1(raw).hexdigest()[:8]}"


def _make_node(kind: str, name: str, *, first_ch: int = 0, aliases: Optional[list] = None,
               attrs: Optional[dict] = None) -> dict:
    node = {"id": node_id(kind, name), "type": kind, "name": name,
            "first_ch": int(first_ch or 0), "attrs": dict(attrs or {})}
    al = [a for a in (aliases or []) if _s(a) and _s(a) != name]
    if al:
        node["aliases"] = al
    return node


def _make_edge(src: str, dst: str, rel: str, *, ch: int, snippet: str,
               confidence: str = "explicit", sources: Optional[list] = None,
               attrs: Optional[dict] = None) -> Optional[dict]:
    """建一条带证据的边；证据不全返回 None（调用方计入 skipped_edges）。"""
    if not (src and dst and rel in EDGE_RELS):
        return None
    if not _s(snippet) or _int(ch) <= 0:
        return None
    edge = {"id": f"edge_{hashlib.sha1(f'{src}>{rel}>{dst}'.encode('utf-8')).hexdigest()[:8]}",
            "src": src, "dst": dst, "rel": rel,
            "valid_from_ch": int(ch), "valid_to_ch": None,
            "evidence": {"ch": int(ch), "offset": 0, "snippet": _s(snippet)},
            "confidence": confidence,
            "sources": list(sources or [])}
    if attrs:
        edge["attrs"] = dict(attrs)
    return edge


def _as_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    for meth in ("to_dict", "model_dump", "dict"):
        fn = getattr(item, meth, None)
        if callable(fn):
            try:
                out = fn()
            except Exception:
                continue
            if isinstance(out, dict):
                return out
    return {}


class CanonGraph:
    """单书 Canon 事实图的只读构建入口（构造一次可反复查询，无可变状态）。"""

    def __init__(self, store, cfg=None):
        self.store = store
        self.cfg = cfg
        #: 最近一次构建里因缺证据被丢弃的候选边数（供接口/日志观测）
        self.last_skipped = 0

    # ---------- 数据源 ----------
    def _world(self):
        try:
            return self.store.load_world()
        except Exception:
            logger.exception("world 读取失败（图按无世界观构建）")
            return None

    def _canon(self) -> dict:
        try:
            mem = self.store.load_book_memory() or {}
        except Exception:
            logger.exception("book_memory 读取失败（术语/机制降级为空）")
            return {}
        return (mem.get("canon") or {}) if isinstance(mem, dict) else {}

    def _chapter_state(self) -> dict:
        try:
            return self.store.load_chapter_state() or {}
        except Exception:
            logger.exception("chapter_state 读取失败（facts 类边降级为空）")
            return {}

    def _char_states(self) -> dict:
        try:
            return self.store.load_character_states() or {}
        except Exception:
            logger.exception("character_states 读取失败（位置边降级为空）")
            return {}

    def _events(self, at_ch: int) -> list:
        try:
            from .events import EventStream
            return EventStream(self.store).query(to_ch=at_ch)
        except Exception:
            logger.exception("events.jsonl 读取失败（图按无事件构建）")
            return []

    # ---------- 构建 ----------
    def build(self, at_ch: int = 0) -> dict:
        """按当前账本构建 Canon 图（纯函数式：源不变则结果完全一致）。"""
        self.last_skipped = 0
        at_ch = _int(at_ch)
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        alias_map: dict[str, str] = {}

        def add_node(kind: str, name: str, **kw) -> Optional[str]:
            name = _s(name)
            if not name or kind not in NODE_TYPES:
                return None
            nid = node_id(kind, name)
            if nid not in nodes:
                nodes[nid] = _make_node(kind, name, **kw)
            else:
                # 同一节点多次出现时只补前向信息（首章取最早、属性不覆盖）
                cur = nodes[nid]
                fch = _int(kw.get("first_ch"))
                if fch and (not cur["first_ch"] or fch < cur["first_ch"]):
                    cur["first_ch"] = fch
                for a in (kw.get("aliases") or []):
                    if _s(a) and _s(a) != name and _s(a) not in cur.get("aliases", []):
                        cur.setdefault("aliases", []).append(_s(a))
            return nid

        def add_edge(*args, **kwargs) -> None:
            edge = _make_edge(*args, **kwargs)
            if edge is None:
                self.last_skipped += 1
                return
            edges.setdefault(edge["id"], edge)

        world = self._world()
        canon = self._canon()
        chapter_state = self._chapter_state()
        char_states = self._char_states()
        events = self._events(at_ch)

        # ---- 节点：人物 / 物品 / 地点 / 势力 / 机制 ----
        char_names: list[str] = []
        for c in (getattr(world, "characters", None) or []):
            d = _as_dict(c)
            n = add_node("character", d.get("name"), attrs={"role": _s(d.get("role"))})
            if n and _s(d.get("name")) not in char_names:
                char_names.append(_s(d.get("name")))
        for name in (chapter_state.get("characters") or {}):  # 章末账本里有、设定卡没有的人
            if add_node("character", name) and _s(name) not in char_names:
                char_names.append(_s(name))
        for name in char_states:
            if add_node("character", name) and _s(name) not in char_names:
                char_names.append(_s(name))

        item_names: list[str] = []
        for it in (getattr(world, "items", None) or []):
            d = _as_dict(it)
            if add_node("item", d.get("name"), attrs={"origin": _s(d.get("origin"))}):
                item_names.append(_s(d.get("name")))

        place_names: list[str] = []
        for loc in (getattr(world, "locations", None) or []):
            d = _as_dict(loc)
            if add_node("place", d.get("name"), attrs={"terrain": _s(d.get("terrain"))}):
                place_names.append(_s(d.get("name")))

        for fac in (getattr(world, "factions", None) or []):
            d = _as_dict(fac)
            add_node("faction", d.get("name"))

        for mt in (getattr(world, "mechanism_types", None) or []):
            add_node("mechanism", mt)
        for field in (canon.get("mechanism_baseline") or {}):
            add_node("mechanism", field)

        # ---- 节点：术语（concept，别名登记进节点）----
        for term, meta in (canon.get("terms") or {}).items():
            meta = meta if isinstance(meta, dict) else {}
            canonical = _s(meta.get("canonical")) or _s(term)
            aliases = list(meta.get("aliases") or [])
            if _s(term) != canonical:
                aliases.append(_s(term))
            nid = add_node("concept", canonical, aliases=aliases,
                           attrs={"category": _s(meta.get("category"))})
            if nid:
                alias_map[_s(term)] = canonical

        # ---- 节点：伏笔 ----
        for fs in (getattr(world, "foreshadowing", None) or []):
            d = _as_dict(fs)
            add_node("foreshadow", d.get("name"),
                     first_ch=_int(d.get("buried_ch")),
                     attrs={"due_ch": _int(d.get("reveal_ch"))})

        # ---- 边 1：present_at（人物状态 → 地点）----
        for name, st in (char_states or {}).items():
            st = st if isinstance(st, dict) else {}
            loc = _s(st.get("location"))
            last = _int(st.get("last_seen"))
            if not loc or last <= 0 or (at_ch and last > at_ch):
                continue
            cid = add_node("character", name)
            pid = add_node("place", loc)
            add_edge(cid, pid, "present_at", ch=last,
                     snippet=f"{_s(name)}在{loc}", sources=["character_states"])

        # ---- 边 2：holds（章末 facts → 物品归属）----
        facts = [f for f in (chapter_state.get("facts") or []) if _s(f)]
        fch = _int(chapter_state.get("chapter"))
        for fact in facts:
            text = _s(fact)
            hit_items = [i for i in item_names if i and i in text]
            hit_chars = [c for c in char_names if c and c in text]
            if not hit_items or not hit_chars:
                continue
            if len(hit_items) > 1:
                self.last_skipped += 1  # 一句里多个物品，无从判定是哪一个
                continue
            holder = _pick_holder(text, hit_chars)
            if not holder:
                self.last_skipped += 1
                continue
            add_edge(node_id("item", hit_items[0]), node_id("character", holder),
                     "holds", ch=fch, snippet=text, sources=["chapter_state"])

        # ---- 边 3：part_of（势力的上级关系由设定卡给出时才有证据）----
        for fac in (getattr(world, "factions", None) or []):
            d = _as_dict(fac)
            parent = _s(d.get("parent"))
            if parent and _s(d.get("name")) and d.get("parent_evidence_ch"):
                add_edge(node_id("faction", parent), node_id("faction", d.get("name")),
                         "part_of", ch=_int(d.get("parent_evidence_ch")),
                         snippet=_s(d.get("parent_evidence")), sources=["world"])

        # ---- 边 4：人际（character_states.relations 原文 → ally/enemy/mentor）----
        # 只认账本里已经写下的关系句，不从正文猜：evidence 就是那句话本身。
        for name, st in (char_states or {}).items():
            st = st if isinstance(st, dict) else {}
            last = _int(st.get("last_seen"))
            if last <= 0 or (at_ch and last > at_ch):
                continue
            for raw in (st.get("relations") or []):
                other, text = _split_relation(raw)
                if not other or not text:
                    continue
                if other not in char_names:
                    self.last_skipped += 1
                    continue
                rel = _classify_relation(text)
                if not rel:
                    self.last_skipped += 1
                    continue
                add_edge(node_id("character", _s(name)), node_id("character", other),
                         rel, ch=last, snippet=text,
                         sources=["character_states"])

        node_list = sorted(nodes.values(), key=lambda n: (n["type"], n["name"]))
        edge_list = sorted(edges.values(), key=lambda e: (e["rel"], e["src"], e["dst"]))
        sources = {
            "nodes": {t: sum(1 for n in node_list if n["type"] == t) for t in NODE_TYPES},
            "edges": {r: sum(1 for e in edge_list if e["rel"] == r) for r in EDGE_RELS},
            "facts": len(facts), "events": len(events),
            "skipped_edges": self.last_skipped, "aliases": len(alias_map),
        }
        return {"version": VERSION, "at_ch": at_ch, "nodes": node_list,
                "edges": edge_list, "sources": sources,
                "skipped_edges": self.last_skipped}

    # ---------- 版本 ----------
    @staticmethod
    def fingerprint(graph: dict) -> str:
        """内容指纹：同一份源数据 → 同一版本（消费方可据此判断是否刷新）。"""
        payload = {"nodes": graph.get("nodes") or [], "edges": graph.get("edges") or []}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # ---------- 体检 ----------
    def validate(self, graph: Optional[dict] = None, at_ch: int = 0) -> dict:
        """六类确定性体检：悬空引用/孤立节点/ID 冲突/时间倒挂/证据缺失/别名自指。

        只报不修：图是派生层，修应该回到源账本；这里给的是「哪条边/哪个节点
        有问题 + 为什么」，供页面与日志定位。
        """
        graph = graph if isinstance(graph, dict) else self.build(at_ch=at_ch)
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        node_ids = [n.get("id") for n in nodes]
        issues: list[dict] = []

        def add(code: str, severity: str, **kw) -> None:
            issues.append({"code": code, "severity": severity, **kw})

        # 1. ID 冲突
        dup = {i for i in node_ids if node_ids.count(i) > 1} if len(node_ids) != len(set(node_ids)) else set()
        for i in dup:
            add("duplicate_node_id", "error", node=i)

        known = set(node_ids)
        # 2. 悬空引用 + 5. 证据缺失 + 4. 时间倒挂
        for e in edges:
            for side in ("src", "dst"):
                if e.get(side) not in known:
                    add("dangling_ref", "error", edge=e.get("id"), side=side,
                        ref=e.get(side))
            ev = e.get("evidence") or {}
            if not _s(ev.get("snippet")) or _int(ev.get("ch")) <= 0:
                add("missing_evidence", "error", edge=e.get("id"))
            vf = _int(e.get("valid_from_ch"))
            vt = _int(e.get("valid_to_ch"))
            if vf <= 0:
                add("time_reversal", "error", edge=e.get("id"), reason="valid_from_ch 缺失")
            elif vt and vt < vf:
                add("time_reversal", "error", edge=e.get("id"),
                    reason="valid_to_ch 早于 valid_from_ch")

        # 3. 孤立节点（只对人物/物品报 info：地点与术语天然可能无结构边）
        linked = set()
        for e in edges:
            linked.add(e.get("src"))
            linked.add(e.get("dst"))
        for n in nodes:
            if n.get("type") not in ("character", "item"):
                continue
            if n.get("id") not in linked:
                add("isolated_node", "info", node=n.get("id"), name=n.get("name"),
                    type=n.get("type"))

        # 6. 别名自指（aliases 里出现自身名 → 术语表登记错了）
        for n in nodes:
            for a in (n.get("aliases") or []):
                if _s(a) == n.get("name"):
                    add("alias_self_ref", "warning", node=n.get("id"), alias=a)

        errors = [i for i in issues if i["severity"] == "error"]
        return {"ok": not errors, "issues": issues,
                "counts": {"nodes": len(nodes), "edges": len(edges),
                           "errors": len(errors), "warnings": len(issues) - len(errors)},
                "version": self.fingerprint(graph)}

    # ---------- 检索 ----------
    def subgraph(self, seed: str, depth: int = 1, rels: Iterable[str] = SUBGRAPH_RELS,
                 at_ch: int = 0) -> dict:
        """从 seed 出发按关系扩散 depth 跳（默认只沿 holds/part_of）。

        seed 可以是 node id，也可以是节点名；找不到时返回空子图而不抛错——
        检索 miss 是正常输入，不是程序错误。
        """
        graph = self.build(at_ch=at_ch)
        rels = tuple(r for r in (rels or SUBGRAPH_RELS) if r in EDGE_RELS)
        seed = _s(seed)
        start = None
        for n in graph["nodes"]:
            if n["id"] == seed or (seed and n["name"] == seed):
                start = n["id"]
                break
        if not start:
            return {"seed": seed, "depth": 0, "nodes": [], "edges": [],
                    "counts": {"nodes": 0, "edges": 0}, "found": False}
        keep = {start}
        frontier = {start}
        hops = max(0, _int(depth, 1))
        picked: list[dict] = []
        for _ in range(hops):
            nxt = set()
            for e in graph["edges"]:
                if e["rel"] not in rels:
                    continue
                if e["src"] in frontier and e["dst"] not in keep:
                    nxt.add(e["dst"])
                if e["dst"] in frontier and e["src"] not in keep:
                    nxt.add(e["src"])
            if not nxt:
                break
            keep |= nxt
            frontier = nxt
        picked = [e for e in graph["edges"]
                  if e["src"] in keep and e["dst"] in keep and e["rel"] in rels]
        out_nodes = [n for n in graph["nodes"] if n["id"] in keep]
        return {"seed": seed, "seed_id": start, "depth": hops, "rel": list(rels),
                "nodes": out_nodes, "edges": picked, "found": True,
                "counts": {"nodes": len(out_nodes), "edges": len(picked)}}
