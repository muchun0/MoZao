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
import re
from typing import Iterable, Optional

from .. import item_state

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

#: 主体归一上限（M1-lite：只做主角 + 关键道具，别名表 ≤20 条，不做全量归一）
MAX_ALIAS = 20

#: 死亡判定的确定性词表（与 `conflict_rules._DEAD_RE` 同源；改一处必须同步另一处）
_DEAD_RE = re.compile(r"已死|身死|身亡|死亡|殒命|毙命|断气")

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
               "不信任", "威胁", "告发")),
    ("mentor", ("师", "父子", "父女", "母女", "收养", "抚养", "教", "指点",
                "传承", "护着", "依赖")),
    ("ally", ("信任", "同盟", "结盟", "合作", "共享", "托付", "并肩", "同伴",
              "搭档", "帮助", "答应")),
)


def _split_relation(raw) -> tuple[str, str]:
    """把 `"苏妄言：信任初步修复"` / `"与顾长卿：互相提防"` 拆成 (人物名, 关系原文)。"""
    if isinstance(raw, dict):
        other = _s(raw.get("name") or raw.get("target"))
        text = _s(raw.get("desc") or raw.get("text"))
    else:
        text = _s(raw)
        other = ""
        for sep in ("：", ":"):
            if sep in text:
                other, desc = text.split(sep, 1)
                other, text = _s(other), _s(desc)
                break
    # 抽取提示词历史上产出过「与X：…」，去掉关系介词再比对人物名
    other = re.sub(r"^(与|和|跟|对|同)\s*", "", other)
    return other, text


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


#: 归属表里的「给」类动词：持有者取动词之后的最后一个人（接收方）
_GIVE_WORDS = ("交给", "递给", "还给", "塞给", "送给", "归还", "交还", "弹还")
#: 归属表里的「夺」类动词：持有者取动词之前的最后一个人（施动方）
_TAKE_WORDS = ("抢走", "夺走", "收进", "收起", "揣进", "塞进", "藏进")


def _owner_from_state(text: str, chars: list[str]) -> str:
    """从章末 `items` 的归属描述里判当前持有者（`"由顾长卿弹还，陆沉收进铁匣"` → 陆沉）。

    判不出返回空，调用方计入 skipped——归属表不是自由文本，不猜。
    """
    hits = sorted([(text.find(c), c) for c in chars if c in text])
    if not hits:
        return ""
    # 1) 给类：动作之后出现的人（接收方）；句中还有别的动词也以给类为准
    for w in _GIVE_WORDS:
        pos = text.rfind(w)
        if pos >= 0:
            after = [h for h in hits if h[0] > pos]
            if after:
                return after[-1][1]
    # 2) 夺/收类：动作之前的人（施动方）
    for w in _TAKE_WORDS:
        pos = text.rfind(w)
        if pos >= 0:
            before = [h for h in hits if h[0] < pos]
            if before:
                return before[-1][1]
    # 3) 携带类固定说法：「在X怀中/手中/身上」「被X」「由X」
    for _, c in hits:
        if any(p in text for p in (f"在{c}怀", f"在{c}手", f"在{c}身",
                                   f"在{c}腰", f"被{c}", f"由{c}")):
            return c
    # 4) 兜底：只出现一个人就是他；多人且判不出 → 空
    return hits[0][1] if len(hits) == 1 else ""


def _s(value) -> str:
    return str(value or "").strip()


#: 允许进别名表的名字形态：短名词、无空白、无标点（书名/句子不会被误收）
_NAME_OK_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·]{2,12}$")


def _build_alias_index(canon: dict, known: Iterable[str]) -> tuple[dict, dict]:
    """从 `book_memory.canon.terms` 提取「别名 → 标准名」，封顶 `MAX_ALIAS` 条。

    M1-lite 明确砍掉「实体别名全量归一」，只做主角 + 关键道具（canonical ≤20）。
    因此这里**只认账本已经登记过的别称**：不做字形/尊称启发式猜测，也不跨条目
    合并——猜错会把两个人并成一个节点，比不归一更坏。

    返回 `(alias_map, aliases_by_canonical)`；`known` 是设定卡与账本里出现过
    的实体名，用于给「标准名就是实体」的条目更高优先级。
    """
    known = {_s(k) for k in (known or []) if _s(k)}
    pairs: list[tuple[int, str, str]] = []
    for term, meta in (canon.get("terms") or {}).items():
        meta = meta if isinstance(meta, dict) else {}
        term = _s(term)
        canonical = _s(meta.get("canonical")) or term
        if not term or not canonical or term == canonical:
            continue
        if not (_NAME_OK_RE.match(term) and _NAME_OK_RE.match(canonical)):
            continue
        if canonical and canonical not in ("", term):
            pairs.append((0 if canonical in known else 1, term, canonical))
        for a in (meta.get("aliases") or []):
            alias = _s(a)
            if not alias or alias == canonical or not _NAME_OK_RE.match(alias):
                continue
            pairs.append((0 if canonical in known else 1, alias, canonical))
    pairs.sort(key=lambda p: (p[0], len(p[1]), p[1]))
    alias_map: dict[str, str] = {}
    for _, alias, canonical in pairs:
        if len(alias_map) >= MAX_ALIAS:
            break
        if alias == _s(canonical):
            continue
        alias_map.setdefault(alias, canonical)
    aliases_by: dict[str, list[str]] = {}
    for alias, canonical in alias_map.items():
        aliases_by.setdefault(canonical, []).append(alias)
    return alias_map, aliases_by


#: 归属表里的「放弃/失去」描述：物品碎毁、脱手、丢失时不能建持有边
_LOST_WORDS = ("砸碎", "碎裂", "碎在", "捣碎", "销毁", "焚毁", "烧毁", "丢失",
               "遗失", "不见", "消失", "滚落", "掉落", "脱手", "脱落", "散落",
               "扔在", "丢在", "抛下")


def _owner_segments(history: Iterable[dict], chars: Iterable[str]) -> dict:
    """把逐章 `items` 归属表压成「持有者变化点」序列。

    同一个人在连续多章持有同一物品只算一段（`valid_to_ch` 为空表示仍在手），
    换人则关旧段、开新段——这是 `valid_from_ch / valid_to_ch` 的唯一来源。

    **方案 B（REQ-R24）**：账本写成结构化 `{owner,state,evidence}` 时直接采信
    `owner`，`state=lost/unknown` 一律不建边——语义判断交给写入前的抽取，图不再猜。
    旧字符串形态仍走原路径，并用 `_LOST_WORDS` 兜住「碎了/丢了/滚落」这类描述。
    """
    segs: dict[str, list] = {}
    names = list(chars or [])
    for h in (history or []):
        for item, raw in ((h or {}).get("items") or {}).items():
            item = _s(item)
            if not item:
                continue
            st = item_state.normalize_item(raw)
            legacy = isinstance(raw, str)
            if not legacy and st["state"] in ("lost", "unknown"):
                continue
            desc = st["evidence"] or st["text"]
            if legacy:
                # 旧文本没有 state 信息，仅靠词表兜底；结构化输入已由抽取判过
                if not desc or any(w in desc for w in _LOST_WORDS):
                    continue
            owner = st["owner"] or _owner_from_state(desc, names)
            if not owner:
                continue
            cur = segs.setdefault(item, [])
            if cur and cur[-1][1] == owner:
                continue
            cur.append((int((h or {}).get("no") or 0), owner, desc))
    return segs


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
               valid_to_ch: Optional[int] = None, attrs: Optional[dict] = None) -> Optional[dict]:
    """建一条带证据的边；证据不全返回 None（调用方计入 skipped_edges）。

    `valid_to_ch` 只在时间维里出现：同一物品换了持有者、或人物离开某地时，旧边
    被关闭（`valid_to_ch = 新边起始章`），查询按 `at_ch` 判断是否仍在有效期内。
    """
    if not (src and dst and rel in EDGE_RELS):
        return None
    if not _s(snippet) or _int(ch) <= 0:
        return None
    edge = {"id": f"edge_{hashlib.sha1(f'{src}>{rel}>{dst}'.encode('utf-8')).hexdigest()[:8]}",
            "src": src, "dst": dst, "rel": rel,
            "valid_from_ch": int(ch),
            "valid_to_ch": int(valid_to_ch) if valid_to_ch else None,
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

    def _history(self, at_ch: int = 0) -> list:
        """逐章状态序列（时间维的数据源）：`scenes.json` 历史 + 最新章末账本。

        没有 `scenes.json` 的书退化为「只有最新一章」——此时时间维的输出与 G0'
        的单快照完全一致，不会凭空造出跨章历史。
        """
        out: list[dict] = []
        try:
            scenes = self.store.load_scenes() or {}
        except Exception:
            logger.exception("scenes.json 读取失败（时间维退化为当前快照）")
            scenes = {}
        for no, rec in (scenes.items() if isinstance(scenes, dict) else []):
            n = _int(no)
            if n <= 0 or (at_ch and n > at_ch):
                continue
            state = (rec or {}).get("state") if isinstance(rec, dict) else {}
            if not isinstance(state, dict):
                continue
            out.append({"no": n, "items": state.get("items") or {},
                        "characters": state.get("characters") or {}})
        cs = self._chapter_state()
        no = _int(cs.get("chapter"))
        if no > 0 and not (at_ch and no > at_ch) and not any(h["no"] == no for h in out):
            out.append({"no": no, "items": cs.get("items") or {},
                        "characters": cs.get("characters") or {}})
        out.sort(key=lambda h: h["no"])
        return out

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
        history = self._history(at_ch)

        # ---- 主体归一：只认账本登记过的别称，封顶 20 条 ----
        known: set[str] = set()
        for c in (getattr(world, "characters", None) or []):
            known.add(_s(_as_dict(c).get("name")))
        for it in (getattr(world, "items", None) or []):
            known.add(_s(_as_dict(it).get("name")))
        for loc in (getattr(world, "locations", None) or []):
            known.add(_s(_as_dict(loc).get("name")))
        known |= {_s(k) for k in (chapter_state.get("characters") or {})}
        known |= {_s(k) for k in (chapter_state.get("items") or {})}
        known |= {_s(k) for k in (char_states or {})}
        alias_map, aliases_by = _build_alias_index(canon, known)
        concept_alias_map: dict[str, str] = {}

        def canon_of(name) -> str:
            name = _s(name)
            return alias_map.get(name, name)

        def variants(name: str) -> list:
            """标准名的全部写法（自身 + 已登记别名），用于在散文里认出实体。"""
            return [name] + [a for a in aliases_by.get(name, []) if a != name]

        # ---- 节点：人物 / 物品 / 地点 / 势力 / 机制 ----
        char_names: list[str] = []
        for c in (getattr(world, "characters", None) or []):
            d = _as_dict(c)
            n = add_node("character", canon_of(d.get("name")),
                         aliases=aliases_by.get(canon_of(d.get("name")), []),
                         attrs={"role": _s(d.get("role"))})
            if n and canon_of(d.get("name")) not in char_names:
                char_names.append(canon_of(d.get("name")))
        for name in (chapter_state.get("characters") or {}):  # 章末账本里有、设定卡没有的人
            name = canon_of(name)
            if add_node("character", name, aliases=aliases_by.get(name, [])) and name not in char_names:
                char_names.append(name)
        for name in char_states:
            name = canon_of(name)
            if add_node("character", name, aliases=aliases_by.get(name, [])) and name not in char_names:
                char_names.append(name)

        item_names: list[str] = []
        for it in (getattr(world, "items", None) or []):
            d = _as_dict(it)
            name = canon_of(d.get("name"))
            if add_node("item", name, aliases=aliases_by.get(name, []),
                        attrs={"origin": _s(d.get("origin"))}):
                item_names.append(name)

        place_names: list[str] = []
        for loc in (getattr(world, "locations", None) or []):
            d = _as_dict(loc)
            name = canon_of(d.get("name"))
            if add_node("place", name, aliases=aliases_by.get(name, []),
                        attrs={"terrain": _s(d.get("terrain"))}):
                place_names.append(name)

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
            if nid and _s(term) != canonical:
                # 术语层自带的标准名映射只进 concept 节点别名，不占主体归一配额
                concept_alias_map[_s(term)] = canonical

        # ---- 节点：伏笔 ----
        for fs in (getattr(world, "foreshadowing", None) or []):
            d = _as_dict(fs)
            add_node("foreshadow", d.get("name"),
                     first_ch=_int(d.get("buried_ch")),
                     attrs={"due_ch": _int(d.get("reveal_ch"))})

        # ---- 边 1：present_at（人物状态 → 地点）----
        for name, st in (char_states or {}).items():
            st = st if isinstance(st, dict) else {}
            name = canon_of(name)
            loc = _s(st.get("location"))
            last = _int(st.get("last_seen"))
            if not loc or last <= 0 or (at_ch and last > at_ch):
                continue
            cid = add_node("character", name, aliases=aliases_by.get(name, []))
            pid = add_node("place", loc)
            add_edge(cid, pid, "present_at", ch=last,
                     snippet=f"{name}在{loc}", sources=["character_states"])

        # ---- 边 2：holds（时间维：逐章归属表，换人就关旧边开新边）----
        # 归属表是账本自己写下的结论（"由顾长卿弹还，陆沉收进铁匣"），比句子抽
        # 取更可靠；物品名以归属表 key 为准，没进设定卡也照样建节点（账本即事实）。
        # `_history` 给出逐章 items，`_owner_segments` 压成变化点：同一持有者跨章
        # 延续不新开边，换人时旧边 `valid_to_ch = 新段起始章`。
        segs = _owner_segments(history, char_names)
        for iname, points in segs.items():
            iname = _s(iname)
            add_node("item", iname, attrs={"origin": "章末账本"})
            if iname not in item_names:
                item_names.append(iname)
            for i, (ch, owner, desc) in enumerate(points):
                nxt = points[i + 1][0] if i + 1 < len(points) else None
                add_edge(node_id("item", iname), node_id("character", owner), "holds",
                         ch=ch, valid_to_ch=nxt, snippet=f"{iname}：{desc}",
                         sources=["timeline"])

        # ---- 边 2b：holds（章末 facts 兜底：时间轴没覆盖的物品由事实句补）----
        fch = _int(chapter_state.get("chapter"))
        facts = [f for f in (chapter_state.get("facts") or []) if _s(f)]
        covered = set(segs)
        for fact in facts:
            text = _s(fact)
            hit_items = [i for i in item_names if i and not i in covered
                         and any(v in text for v in variants(i))]
            hit_chars = [c for c in char_names if c and any(v in text for v in variants(c))]
            if not hit_items or not hit_chars:
                continue
            if len(hit_items) > 1:
                self.last_skipped += 1  # 一句里多个物品，无从判定是哪一个
                continue
            # 命中别名时把持有者归一到标准名，避免同一个人拆成两个节点
            holder = _pick_holder(text, [v for c in hit_chars for v in variants(c)])
            holder = canon_of(holder)
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
            name = canon_of(name)
            last = _int(st.get("last_seen"))
            if last <= 0 or (at_ch and last > at_ch):
                continue
            for raw in (st.get("relations") or []):
                other, text = _split_relation(raw)
                other = canon_of(other)
                if not other or not text:
                    continue
                if other not in char_names:
                    self.last_skipped += 1
                    continue
                rel = _classify_relation(text)
                if not rel:
                    self.last_skipped += 1
                    continue
                add_edge(node_id("character", name), node_id("character", other),
                         rel, ch=last, snippet=text,
                         sources=["character_states"])

        # ---- 死亡标记（供 `death_state` 规则改读图）----
        for name, st in (char_states or {}).items():
            text = st if isinstance(st, str) else (
                (st or {}).get("state") if isinstance(st, dict) else "")
            nid = node_id("character", canon_of(name))
            if nid not in nodes or not _DEAD_RE.search(str(text or "")):
                continue
            dead_ch = _int((st or {}).get("last_seen")) if isinstance(st, dict) else 0
            nodes[nid].setdefault("attrs", {})["dead_ch"] = dead_ch or _int(
                chapter_state.get("chapter"))

        node_list = sorted(nodes.values(), key=lambda n: (n["type"], n["name"]))
        edge_list = sorted(edges.values(), key=lambda e: (e["rel"], e["src"], e["dst"]))
        # at_ch 视图：只保留在该章有效的边（时间维的价值就在这——问「第 10 章时
        # 东西在谁手上」，而不是只给今天的结果）
        if at_ch:
            edge_list = [e for e in edge_list
                         if _int(e.get("valid_from_ch")) <= at_ch
                         and (not e.get("valid_to_ch") or at_ch < _int(e.get("valid_to_ch")))]
        sources = {
            "nodes": {t: sum(1 for n in node_list if n["type"] == t) for t in NODE_TYPES},
            "edges": {r: sum(1 for e in edge_list if e["rel"] == r) for r in EDGE_RELS},
            "facts": len(facts), "events": len(events), "chapters": len(history),
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

        # 7. 别名撞车（同一别称映射到两个标准名 → 归一后仍是两个人，必须人工裁决）
        seen_alias: dict[str, str] = {}
        for n in nodes:
            for a in (n.get("aliases") or []):
                alias = _s(a)
                if not alias:
                    continue
                prev = seen_alias.get(alias)
                if prev and prev != n.get("name"):
                    add("alias_conflict", "warning", alias=alias,
                        names=[prev, n.get("name")])
                else:
                    seen_alias[alias] = n.get("name")

        # 8. 同一物品在同一时点有两个持有者（时间维派生出的硬矛盾）
        holds: dict[str, list] = {}
        for e in edges:
            if e.get("rel") == "holds":
                holds.setdefault(e.get("src"), []).append(e)
        for item, rows in holds.items():
            rows.sort(key=lambda e: _int(e.get("valid_from_ch")))
            for i in range(len(rows) - 1):
                cur, nxt = rows[i], rows[i + 1]
                cur_to = _int(cur.get("valid_to_ch")) or 10 ** 6
                if cur_to > _int(nxt.get("valid_from_ch")):
                    add("overlapping_holds", "error", item=item,
                        edges=[cur.get("id"), nxt.get("id")])

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
            if n["id"] == seed or (seed and n["name"] == seed) or (
                    seed and seed in (n.get("aliases") or [])):
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

    # ---------- 影响面 ----------
    def impact(self, entity: str, ch: int = 0, at_ch: int = 0) -> dict:
        """一条实体/关系变更会影响哪些章节与伏笔（门 2 编辑 Base 前的检查入口）。

        口径（确定性、零 LLM）：
        - `chapters`：该实体参与的所有边里，证据章 > `ch` 的章号（去重升序）。
          这就是「改了这条关系，后面哪几章要重看」。
        - `foreshadows`：尚未回收（`due_ch` ≥ `ch`）的伏笔 + 与受影响章节相交的伏笔。
        - `closed_edges`：`ch` 之后被关闭的旧边（改一条边等于改写后续所有依赖它的事实）。

        找不到实体时返回空结果而不抛错——检索 miss 是正常输入。
        """
        graph = self.build(at_ch=at_ch)
        entity = _s(entity)
        target = None
        for n in graph["nodes"]:
            if n["id"] == entity or n["name"] == entity or entity in (n.get("aliases") or []):
                target = n
                break
        if not target:
            return {"entity": entity, "found": False, "chapters": [],
                    "foreshadows": [], "edges": [], "closed_edges": [],
                    "counts": {"chapters": 0, "foreshadows": 0, "edges": 0}}
        ch = _int(ch)
        touched = [e for e in graph["edges"]
                   if target["id"] in (e.get("src"), e.get("dst"))
                   and _int(e.get("evidence", {}).get("ch")) >= ch]
        chapters = sorted({_int(e.get("evidence", {}).get("ch")) for e in touched
                           if _int(e.get("evidence", {}).get("ch")) > ch})
        closed = [e for e in touched if _int(e.get("valid_to_ch")) > ch]
        fsnodes = [n for n in graph["nodes"] if n.get("type") == "foreshadow"]
        foreshadows = [{"id": n["id"], "name": n["name"],
                        "due_ch": _int((n.get("attrs") or {}).get("due_ch")),
                        "buried_ch": _int(n.get("first_ch"))}
                       for n in fsnodes
                       if _int((n.get("attrs") or {}).get("due_ch")) >= ch]
        foreshadows.sort(key=lambda f: (f["due_ch"] or 10 ** 6, f["name"]))
        return {"entity": target["name"], "entity_id": target["id"],
                "found": True, "from_ch": ch, "chapters": chapters,
                "foreshadows": foreshadows, "edges": touched, "closed_edges": closed,
                "counts": {"chapters": len(chapters), "foreshadows": len(foreshadows),
                           "edges": len(touched)}}

