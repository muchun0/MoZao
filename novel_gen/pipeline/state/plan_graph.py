# -*- coding: utf-8 -*-
"""Plan 计划图（REQ-R24 P0/P1）：可改的「写前约束」层。

与 Canon 的分工（TECH-R24 §2.1）：Canon 是**只追加的事实**，Plan 是**可改的计划**。
Plan 不参与冲突判定，只做两件事：

1. `compile_from_outline()`：把大纲确定性编译成分章约束（零 LLM，可重复编译）；
2. `derive_constraints(ch)`：写章前按 Base + Plan + Canon 派生该章约束块，
   由 `stages` 注入 `<chapter_constraints>`（与冲突提醒合并去重、合计 ≤300 字符）。

**为什么 Plan 不落 LLM**：约束要能被人改、能 diff、能回滚。让模型生成约束等于让
模型给自己出题，且每次编译结果不稳定——大纲是唯一权威，编译是纯函数。

数据结构（`projects/<id>/plan_graph.json`）：

```
{ "plan_version": 1, "ch_range": [1, 30],
  "constraints": [{"id":"cst_ch12_01","ch":12,"kind":"must_hold",
                   "src":"ent_item_xxx","dst":"ent_char_yyy","strength":"must",
                   "evidence":"outline#ch12-3", "text":"铜匣必须仍由陆沉持有"}],
  "intents": [{"id":"int_ch12_a","ch":12,"kind":"resolve","target":"铜匣来历",
               "by_ch":12,"strength":"should"}],
  "names": {"ent_item_xxx":"铜匣", "ent_char_yyy":"陆沉"},
  "sources": {"outline": true} }
```

硬约束：`must` 每章 ≤5 条（超出降 `should`，只进提示）；单块 ≤300 字符。
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from .graph import node_id

logger = logging.getLogger(__name__)

KINDS = ("must_present", "must_hold", "must_locate", "forbidden_present",
         "must_resolve", "must_advance")
INTENT_KINDS = ("plant", "resolve", "reveal", "transition")
STRENGTHS = ("must", "should", "hint")

MAX_MUST_PER_CH = 5
BLOCK_BUDGET = 300
_KIND_LABEL = {
    "must_present": "必须出场",
    "must_hold": "必须持有",
    "must_locate": "必须在场于",
    "forbidden_present": "禁止出现（已死亡/已离场）",
    "must_resolve": "必须回收伏笔",
    "must_advance": "机制必须推进",
}
_HOLD_VERBS = ("取出", "拿出", "掏出", "握", "持有", "收进", "塞进", "揣", "得到",
               "接过", "抢走", "夺走", "收走", "拾起", "捡起", "夺回", "抢回",
               "携带", "带上", "藏")


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------- 读 / 写 ----------
def load(store) -> Optional[dict]:
    try:
        plan = store.load_plan_graph()
    except Exception:
        logger.exception("plan_graph.json 读取失败（按无 Plan 处理）")
        return None
    if not isinstance(plan, dict):
        return None
    plan.setdefault("constraints", [])
    plan.setdefault("intents", [])
    plan.setdefault("names", {})
    plan.setdefault("plan_version", 1)
    return plan


def _content_key(plan: dict) -> str:
    """版本号比较口径：只比内容，不比 plan_version 自身。"""
    import json
    body = {k: plan.get(k) for k in ("ch_range", "constraints", "intents", "names", "sources")}
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def save(store, plan: dict) -> int:
    """内容有变化才递增 plan_version；返回写入后的版本号。"""
    old = load(store) or {}
    plan = dict(plan or {})
    plan.setdefault("constraints", [])
    plan.setdefault("intents", [])
    plan.setdefault("names", {})
    same = _content_key(old) == _content_key(plan)
    plan["plan_version"] = _int(old.get("plan_version"), 0) if same else _int(
        old.get("plan_version"), 0) + 1
    if plan["plan_version"] <= 0:
        plan["plan_version"] = 1
    store.save_plan_graph(plan)
    return plan["plan_version"]


# ---------- 编译（零 LLM，大纲 → 约束） ----------
def _chapters_of(outline) -> list:
    if outline is None:
        return []
    if isinstance(outline, dict):
        return list(outline.get("chapters") or [])
    return list(getattr(outline, "chapters", []) or [])


def _field(obj, key: str, default: str = "") -> str:
    if isinstance(obj, dict):
        return str(obj.get(key) or default)
    return str(getattr(obj, key, default) or default)


def _names_of(world, attr: str) -> list[str]:
    items = getattr(world, attr, None) if world is not None else None
    return [str(getattr(x, "name", "") or "").strip()
            for x in (items or []) if str(getattr(x, "name", "") or "").strip()]


def _move_names(moves: str, known: Iterable[str]) -> list[str]:
    """从「陆沉：…；独眼刘：…」式人物动线里取出已知人物名（按已知名单精确匹配）。"""
    known = [k for k in (known or []) if k]
    text = str(moves or "")
    return [k for k in known if k in text]


def compile_from_outline(outline, world=None, *, keep_existing: bool = True,
                         existing: Optional[dict] = None) -> dict:
    """把大纲编译成分章约束。

    - **确定性**：同一大纲 + 同一世界观重复编译结果一致（可反复跑）；
    - **不覆盖人工改动**：`keep_existing=True` 时，已有 Plan 里 `source=manual`
      的约束与 intents 原样保留（人改过的不被编译冲掉）。
    """
    chapters = _chapters_of(outline)
    chars = _names_of(world, "characters")
    locs = _names_of(world, "locations")
    items = _names_of(world, "items")
    constraints: list[dict] = []
    intents: list[dict] = []
    names: dict[str, str] = {}
    manual = {"constraints": [], "intents": []}
    if keep_existing and isinstance(existing, dict):
        for c in existing.get("constraints") or []:
            if str(c.get("source") or "") == "manual":
                manual["constraints"].append(dict(c))
        for it in existing.get("intents") or []:
            if str(it.get("source") or "") == "manual":
                manual["intents"].append(dict(it))
        names.update(existing.get("names") or {})

    for cp in chapters:
        no = _int(_field(cp, "no"), 0)
        if no <= 0:
            continue
        text = "｜".join(_field(cp, k) for k in
                        ("title", "goal", "core_event", "conflict", "character_moves"))
        seq = 0

        def add(kind: str, *, src="", dst="", text_="", evidence="", strength="must"):
            nonlocal seq
            seq += 1
            cid = f"cst_ch{no}_{seq:02d}"
            constraints.append({
                "id": cid, "ch": no, "kind": kind, "src": src, "dst": dst,
                "strength": strength, "evidence": evidence or f"outline#ch{no}",
                "text": text_, "source": "outline"})
            for nid, name in ((src, src and _name_for_id(src, chars, locs, items)),
                              (dst, dst and _name_for_id(dst, chars, locs, items))):
                if nid and name:
                    names[nid] = name

        for name in _move_names(_field(cp, "character_moves"), chars):
            nid = node_id("character", name)
            add("must_present", dst=nid, text_=f"{name}必须出场",
                evidence=f"outline#ch{no}-moves")
        for name in locs:
            if name and name in text:
                nid = node_id("place", name)
                add("must_locate", dst=nid, text_=f"必须在{name}",
                    evidence=f"outline#ch{no}-loc")
                break
        for name in items:
            if name and name in text and any(v in text for v in _HOLD_VERBS):
                nid = node_id("item", name)
                add("must_hold", src=nid, text_=f"{name}必须出现在本章并被持有/转移",
                    evidence=f"outline#ch{no}-item")
                break
        # 大纲侧显式写的伏笔回收意图（作者手填 chapter.foreshadowing 含「回收：X」）
        fs = _field(cp, "foreshadowing")
        for seg in fs.replace("；", ";").split(";"):
            seg = seg.strip()
            if seg.startswith("回收") and "：" in seg:
                target = seg.split("：", 1)[1].strip()
                if target:
                    intents.append({
                        "id": f"int_ch{no}_{len(intents) + 1:02d}", "ch": no,
                        "kind": "resolve", "target": target, "by_ch": no,
                        "strength": "should", "source": "outline"})

    constraints.extend(manual["constraints"])
    intents.extend(manual["intents"])
    constraints.sort(key=lambda c: (_int(c.get("ch")), str(c.get("id"))))
    ch_nos = [_int(_field(c, "no")) for c in chapters if _int(_field(c, "no")) > 0]
    ch_range = [min(ch_nos), max(ch_nos)] if ch_nos else [0, 0]
    return {"plan_version": 0, "ch_range": ch_range, "constraints": constraints,
            "intents": intents, "names": names,
            "sources": {"outline": bool(chapters), "world": bool(world)}}


def _name_for_id(nid: str, chars, locs, items) -> str:
    """按 id 反查名字（`ent_{type}_{sha1}`）：编译期已知名单里找，找不到留空。"""
    parts = str(nid).split("_")
    if len(parts) < 3 or parts[0] != "ent":
        return ""
    type_of = {"char": "character", "place": "place", "item": "item"}
    if parts[1] not in type_of:
        return ""
    pool = {"char": chars, "place": locs, "item": items}[parts[1]]
    for name in pool:
        if node_id(type_of[parts[1]], name) == nid:
            return name
    return ""


# ---------- 校验 ----------
def validate(plan: Optional[dict], world=None) -> dict:
    """结构校验：枚举、章号范围、id 唯一、端点可解析。只报不修。"""
    plan = plan or {}
    issues: list[dict] = []
    cmin, cmax = 0, 0
    rng = plan.get("ch_range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        cmin, cmax = _int(rng[0]), _int(rng[1])
    names = plan.get("names") or {}
    known = set(names) | {node_id("character", n) for n in _names_of(world, "characters")}

    def add(code: str, ch: int, detail: str, severity: str = "error", cid: str = ""):
        issues.append({"code": code, "severity": severity, "ch": ch,
                       "id": cid, "detail": detail})

    seen_ids: set[str] = set()
    must_per_ch: dict[int, int] = {}
    for c in plan.get("constraints") or []:
        if not isinstance(c, dict):
            add("bad_constraint", 0, "约束不是对象", cid="")
            continue
        cid = str(c.get("id") or "")
        ch = _int(c.get("ch"))
        kind = str(c.get("kind") or "")
        strength = str(c.get("strength") or "must")
        if not cid:
            add("missing_id", ch, "约束缺少 id")
        elif cid in seen_ids:
            add("duplicate_id", ch, f"id 重复：{cid}", cid=cid)
        seen_ids.add(cid)
        if kind not in KINDS:
            add("bad_kind", ch, f"未知 kind：{kind}", cid=cid)
        if strength not in STRENGTHS:
            add("bad_strength", ch, f"未知 strength：{strength}", cid=cid)
        if ch <= 0:
            add("bad_ch", ch, "约束缺章号", cid=cid)
        elif cmin and cmax and not (cmin <= ch <= cmax):
            add("ch_out_of_range", ch, f"第{ch}章超出范围 [{cmin},{cmax}]", cid=cid)
        for key, label in (("src", "src"), ("dst", "dst")):
            v = str(c.get(key) or "")
            if v and v not in known and not _name_for_id(
                    v, _names_of(world, "characters"),
                    _names_of(world, "locations"), _names_of(world, "items")):
                add("unknown_endpoint", ch, f"{label} 指向未登记实体：{v}",
                    severity="warning", cid=cid)
        if kind in ("must_present", "must_locate", "forbidden_present") and not c.get("dst"):
            add("missing_dst", ch, f"{kind} 缺少 dst", cid=cid)
        if kind == "must_hold" and not (c.get("src") or c.get("dst")):
            add("missing_endpoint", ch, "must_hold 缺少 src/dst", cid=cid)
        if strength == "must":
            must_per_ch[ch] = must_per_ch.get(ch, 0) + 1
    return {"ok": not any(i["severity"] == "error" for i in issues),
            "issues": issues,
            "counts": {"constraints": len(plan.get("constraints") or []),
                       "intents": len(plan.get("intents") or []),
                       "issues": len(issues),
                       "errors": sum(1 for i in issues if i["severity"] == "error"),
                       "warnings": sum(1 for i in issues if i["severity"] == "warning")}}


# ---------- 派生本次写章约束 ----------
def derive_constraints(ch: int, plan: Optional[dict] = None,
                       canon: Optional[dict] = None,
                       foreshadowing: Optional[dict] = None) -> list[dict]:
    """Base + Plan + Canon → 该章约束集（must 封顶 5 条，超出降 should）。

    三来源（TECH-R24 §3）：Plan 手写/编译约束；Canon 当章有效的 holds 边；
    Canon 死亡章 → forbidden_present；伏笔 `due_ch ≤ ch` 未回收 → must_resolve。
    """
    ch = _int(ch)
    out: list[dict] = []
    names: dict[str, str] = dict((plan or {}).get("names") or {})

    def add(kind: str, text_: str, *, src="", dst="", strength="must",
            evidence="", origin="plan"):
        out.append({"id": f"cst_ch{ch}_{origin}_{len(out) + 1:02d}", "ch": ch,
                    "kind": kind, "src": src, "dst": dst, "strength": strength,
                    "text": text_, "evidence": evidence, "origin": origin})

    plan_seen: set[tuple] = set()
    for c in (plan or {}).get("constraints") or []:
        if _int(c.get("ch")) != ch or str(c.get("kind")) not in KINDS:
            continue
        key = (c.get("kind"), c.get("src"), c.get("dst"), c.get("text"))
        if key in plan_seen:
            continue
        plan_seen.add(key)
        add(str(c["kind"]), str(c.get("text") or _kind_text(c, names)),
            src=str(c.get("src") or ""), dst=str(c.get("dst") or ""),
            strength=str(c.get("strength") or "must"),
            evidence=str(c.get("evidence") or ""), origin="plan")

    if canon:
        for n in canon.get("nodes") or []:
            if n.get("id") and n.get("name"):
                names.setdefault(n["id"], n["name"])
        holds: set[tuple] = set()
        for e in canon.get("edges") or []:
            if e.get("rel") != "holds":
                continue
            start = _int(e.get("valid_from_ch"))
            end = e.get("valid_to_ch")
            if not start or start > ch or (end and ch >= _int(end)):
                continue
            src, dst = str(e.get("src") or ""), str(e.get("dst") or "")
            if not src or not dst or (src, dst) in holds:
                continue
            holds.add((src, dst))
            ev = e.get("evidence") or {}
            add("must_hold", f"{names.get(dst, dst)}必须仍持有{names.get(src, src)}",
                src=src, dst=dst, strength="must",
                evidence=f"canon#ch{_int(ev.get('ch'))}", origin="canon")
        dead_now = []
        for n in canon.get("nodes") or []:
            if n.get("type") != "character":
                continue
            dead_ch = _int((n.get("attrs") or {}).get("dead_ch"))
            if dead_ch and dead_ch <= ch:
                dead_now.append((str(n.get("id") or ""), dead_ch))
        for nid, dead_ch in dead_now:
            if dead_ch == ch:
                continue  # 本章刚死，仍可出现（死亡发生在本章内）
            add("forbidden_present", f"{names.get(nid, nid)}已于第{dead_ch}章死亡，不得出场",
                dst=nid, strength="must", evidence=f"canon#ch{dead_ch}", origin="canon")

    for it in (foreshadowing or {}).get("items") or []:
        due = _int(it.get("due_ch"))
        status = str(it.get("status") or "")
        if not due or due > ch or status == "resolved":
            continue
        name = str(it.get("name") or it.get("key") or "")
        if not name:
            continue
        add("must_resolve", f"第{due}章到期的伏笔「{name}」必须回收",
            dst=str(it.get("key") or name), strength="must",
            evidence=f"foreshadow#due{due}", origin="foreshadow")

    # 持有该物品的人必然在场：去掉与 must_hold 同目标的 must_present（降噪、省预算）
    hold_dsts = {str(c.get("dst") or "") for c in out if c.get("kind") == "must_hold"}
    out = [c for c in out
           if not (c.get("kind") == "must_present" and str(c.get("dst") or "") in hold_dsts)]
    return cap_must(out)


def cap_must(constraints: list[dict], limit: int = MAX_MUST_PER_CH) -> list[dict]:
    """must 每章 ≤ limit：按**信息价值**排序后保留前 limit 条，其余降 should。

    优先级：归属/禁止出现/回收伏笔 > 机制推进 > 出场/地点。原因：人物「有没有出现」
    模型通常不会错，真正会错的是「东西现在在谁手上」「死人又说话了」——后者更该占
    must 名额。输出顺序保持不变（只改 strength），便于人读与 diff。
    """
    priority = {"must_hold": 0, "forbidden_present": 1, "must_resolve": 2,
                "must_advance": 3, "must_present": 4, "must_locate": 5}
    must_idx = [i for i, c in enumerate(constraints) if str(c.get("strength")) == "must"]
    must_idx.sort(key=lambda i: (priority.get(str(constraints[i].get("kind")), 9), i))
    keep = set(must_idx[:limit])
    out = []
    for i, c in enumerate(constraints):
        c = dict(c)
        if str(c.get("strength")) == "must" and i not in keep:
            c["strength"] = "should"
            c["downgraded"] = True
        out.append(c)
    return out


def _kind_text(c: dict, names: dict) -> str:
    kind = str(c.get("kind") or "")
    src = names.get(str(c.get("src") or ""), str(c.get("src") or ""))
    dst = names.get(str(c.get("dst") or ""), str(c.get("dst") or ""))
    return {
        "must_present": f"{dst}必须出场",
        "must_hold": f"{src}必须仍由{dst}持有",
        "must_locate": f"必须在{dst}",
        "forbidden_present": f"{dst}禁止出现",
        "must_resolve": f"必须回收「{dst}」",
        "must_advance": f"{src}的机制数值必须推进",
    }.get(kind, f"{kind} {dst}".strip())


# ---------- 渲染与合并 ----------
def render_block(constraints: Iterable[dict], budget: int = BLOCK_BUDGET) -> str:
    """`<chapter_constraints>` 的正文（不含 XML 标签，调用方包）。"""
    items = [c for c in (constraints or [])
             if str(c.get("strength")) in ("must", "should")]
    if not items:
        return ""
    header = "【本章硬约束】"
    lines: list[str] = []
    used = len(header)
    for c in items:
        mark = "" if str(c.get("strength")) == "must" else "（建议）"
        line = f"- {c.get('text') or ''}{mark}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return header + "\n" + "\n".join(lines)


def _dedupe_key(line: str) -> str:
    return "".join(ch for ch in str(line) if ch not in " \t，。；、,.;:：·-")


def merge_blocks(*blocks: str, budget: int = BLOCK_BUDGET) -> str:
    """合并约束块与冲突块：去重、保头部、合计 ≤ budget 字符。

    冲突块优先级低于约束块（硬约束是计划，冲突是历史教训）：先收约束行，
    余量再收冲突行；同一句只出现一次。
    """
    headers: list[str] = []
    lines: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        for raw in str(block or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("【") and line.endswith("】"):
                if line not in headers:
                    headers.append(line)
                continue
            key = _dedupe_key(line)
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(line)
    if not headers and not lines:
        return ""
    head = "\n".join(headers)
    used = len(head)
    kept: list[str] = []
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join([head] + kept) if kept else head


# ---------- 写章接线 ----------
def constraints_block(store, cfg, ch: int, canon: Optional[dict] = None) -> str:
    """写章前取本章约束块；未配置 Plan / 开关关闭 / 无约束时返回空串。

    `canon=None` 且 `state_graph_v2` 打开时自行建图（读时派生，不落盘）；建图失败
    只降级为「无 Canon 约束」，Plan 自身的约束照常注入。
    """
    if not getattr(cfg, "plan_graph", False):
        return ""
    plan = load(store)
    if not plan:
        return ""
    if canon is None and getattr(cfg, "state_graph_v2", False):
        try:
            from .graph import CanonGraph
            canon = CanonGraph(store, cfg).build()
        except Exception:
            logger.exception("Plan 约束派生时建图失败（降级为只用 Plan 自身约束）")
    foreshadowing = None
    try:
        from .foreshadow import ForeshadowLedger
        foreshadowing = ForeshadowLedger(store, cfg).load()
    except Exception:
        logger.exception("伏笔账本读取失败（must_resolve 约束降级为不派生）")
    constraints = derive_constraints(ch, plan=plan, canon=canon,
                                     foreshadowing=foreshadowing)
    return render_block(constraints)


# ---------- P2：卡片视图 / 编辑 / diff / impact ----------
_KIND_TO_TYPE = {"must_present": "character", "forbidden_present": "character",
                 "must_locate": "place"}
_DEATH_MARKERS = ("已死", "身死", "身亡", "死亡", "殒命", "毙命", "断气")


def cards(plan: Optional[dict], ch: int = 0) -> list[dict]:
    """门 1 的「分章约束卡」：把约束翻译成人能改的字段（名称优先，id 藏起来）。

    `ch=0` 返回全部章；卡片保持 Plan 内的顺序，便于前端原地编辑。
    """
    plan = plan or {}
    names = plan.get("names") or {}
    out: list[dict] = []
    for c in plan.get("constraints") or []:
        if not isinstance(c, dict):
            continue
        if ch and _int(c.get("ch")) != _int(ch):
            continue
        src = str(c.get("src") or "")
        dst = str(c.get("dst") or "")
        kind = str(c.get("kind") or "")
        out.append({
            "id": str(c.get("id") or ""), "ch": _int(c.get("ch")), "kind": kind,
            "kind_label": _KIND_LABEL.get(kind, kind),
            "strength": str(c.get("strength") or "must"),
            "text": str(c.get("text") or ""),
            "subject": names.get(src, src), "object": names.get(dst, dst),
            "src": src, "dst": dst,
            "evidence": str(c.get("evidence") or ""),
            "source": str(c.get("source") or ""),
        })
    return out


def _next_id(plan: dict, ch: int) -> str:
    used = {str(c.get("id") or "") for c in plan.get("constraints") or [] if isinstance(c, dict)}
    for n in range(1, 200):
        cid = f"cst_ch{ch}_m{n:02d}"
        if cid not in used:
            return cid
    return f"cst_ch{ch}_m999"


def upsert_constraint(plan: dict, payload: dict) -> dict:
    """门 1 保存单张卡：允许按 id 覆盖，也允许新建（自动分配 id）。

    字段接受 id 或人类可读名称（`src_name`/`dst_name`），语义按 kind 决定：
    must_hold 是「物品(src)→持有者(dst)」，present/locate/forbidden 只看 dst，
    must_resolve 的 dst 是伏笔名或 key。非法输入一律 ValueError（由路由转 400）。
    """
    if not isinstance(payload, dict):
        raise ValueError("约束必须是对象")
    kind = str(payload.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"未知 kind：{kind}（可选 {'/'.join(KINDS)}）")
    ch = _int(payload.get("ch"))
    if ch <= 0:
        raise ValueError("约束缺少章号 ch")
    strength = str(payload.get("strength") or "must").strip()
    if strength not in STRENGTHS:
        raise ValueError(f"未知 strength：{strength}（可选 {'/'.join(STRENGTHS)}）")

    plan = plan or {}
    plan.setdefault("constraints", [])
    plan.setdefault("intents", [])
    plan.setdefault("names", {})
    names = plan["names"]

    src = str(payload.get("src") or "")
    dst = str(payload.get("dst") or "")
    src_name = str(payload.get("src_name") or "").strip()
    dst_name = str(payload.get("dst_name") or "").strip()
    if src_name and not src:
        src = node_id("item" if kind == "must_hold" else "character", src_name)
        names.setdefault(src, src_name)
    if dst_name and not dst:
        dst = node_id(_KIND_TO_TYPE.get(kind, "character"), dst_name)
        names.setdefault(dst, dst_name)
    if kind in ("must_present", "must_locate", "forbidden_present") and not dst:
        raise ValueError(f"{kind} 需要 dst 或 dst_name")
    if kind == "must_hold" and not (src and dst):
        raise ValueError("must_hold 需要 src/dst（物品与持有者）")
    if kind == "must_resolve" and not dst:
        raise ValueError("must_resolve 需要 dst（伏笔名或 key）")

    cid = str(payload.get("id") or "").strip() or _next_id(plan, ch)
    text = str(payload.get("text") or "").strip()
    base = {"id": cid, "ch": ch, "kind": kind, "src": src, "dst": dst,
            "strength": strength, "source": "manual",
            "evidence": str(payload.get("evidence") or "manual")}
    if not text:
        text = _kind_text(base, names)
    base["text"] = text

    replaced = False
    for i, c in enumerate(plan["constraints"]):
        if isinstance(c, dict) and str(c.get("id") or "") == cid:
            plan["constraints"][i] = base
            replaced = True
            break
    if not replaced:
        plan["constraints"].append(base)
    plan["constraints"].sort(key=lambda c: (_int(c.get("ch")), str(c.get("id"))))
    return base


def remove_constraint(plan: dict, cid: str) -> bool:
    """按 id 删除一张卡；返回是否删到（没删到由路由报 404）。"""
    target = str(cid or "").strip()
    rows = (plan or {}).get("constraints") or []
    keep = [c for c in rows if not (isinstance(c, dict) and str(c.get("id") or "") == target)]
    if len(keep) == len(rows):
        return False
    plan["constraints"] = keep
    return True


def impact(plan: Optional[dict], seed: str, ch: int = 0) -> dict:
    """Plan 侧反向索引：改这个实体，会动到哪些章的约束（Canon 侧由 graph.impact 回答）。"""
    key = str(seed or "").strip()
    names = (plan or {}).get("names") or {}
    hit_ids = {nid for nid, name in names.items()
               if key and (key == name or key == nid)}
    chapters: list[int] = []
    rows: list[dict] = []
    for c in (plan or {}).get("constraints") or []:
        if not isinstance(c, dict):
            continue
        src, dst = str(c.get("src") or ""), str(c.get("dst") or "")
        text = str(c.get("text") or "")
        if not ((src in hit_ids) or (dst in hit_ids)
                or (names.get(dst, dst) == key) or (names.get(src, src) == key)
                or (key and key in text)):
            continue
        cch = _int(c.get("ch"))
        if ch and cch < ch:
            continue
        chapters.append(cch)
        rows.append({"id": str(c.get("id") or ""), "ch": cch,
                     "kind": str(c.get("kind") or ""),
                     "strength": str(c.get("strength") or ""),
                     "text": text})
    chapters = sorted({c for c in chapters if c > 0})
    return {"entity": key, "found": bool(rows), "chapters": chapters,
            "constraints": rows, "counts": {"chapters": len(chapters),
                                            "constraints": len(rows)}}


# ---------- 观察：这一章实际发生了什么 ----------
def observe(store, ch: int) -> dict:
    """取第 ch 章的实际状态（写前算不出来、写完才有）。

    数据源优先 `scenes.json` 的逐章 state（拆场景时落盘），缺失时退回最新
    `chapter_state.json`（只对最新章有效）。返回 items 已归一（三态语义）。
    """
    from ..chapter_state import _clean_items
    from ..item_state import normalize_item

    ch = _int(ch)
    state: dict = {}
    try:
        scenes = store.load_scenes() or {}
        entry = scenes.get(str(ch)) or {}
        state = (entry or {}).get("state") or {}
    except Exception:
        logger.exception("scenes.json 读取失败（diff 观察降级）")
    if not state:
        try:
            cs = store.load_chapter_state() or {}
            if _int(cs.get("chapter")) == ch:
                state = cs
        except Exception:
            logger.exception("chapter_state.json 读取失败（diff 观察降级）")
    chars = {str(k): str(v or "") for k, v in (state.get("characters") or {}).items() if k}
    items = _clean_items(state.get("items") or {}) if state.get("items") else {}
    dead = [name for name, desc in chars.items()
            if any(m in desc for m in _DEATH_MARKERS)]
    return {"available": bool(chars or items), "characters": chars,
            "items": {k: normalize_item(v) for k, v in (items or {}).items()},
            "dead": dead, "time": str(state.get("time") or ""),
            "numbers": state.get("numbers")}


def _expected_for_ch(plan: dict, ch: int, canon: Optional[dict],
                     foreshadowing: Optional[dict]) -> list[dict]:
    """「Plan 期望这一章有什么」：Plan 约束 + 到期伏笔 + 死亡禁止（不含 Canon 的持有观察）。"""
    names = dict((plan or {}).get("names") or {})
    rows: list[dict] = []
    for c in (plan or {}).get("constraints") or []:
        if _int(c.get("ch")) != ch or str(c.get("kind")) not in KINDS:
            continue
        rows.append({"id": str(c.get("id") or ""), "ch": ch,
                     "kind": str(c["kind"]), "src": str(c.get("src") or ""),
                     "dst": str(c.get("dst") or ""),
                     "strength": str(c.get("strength") or "must"),
                     "text": str(c.get("text") or _kind_text(c, names))})
    for n in (canon or {}).get("nodes") or []:
        dead_ch = _int((n.get("attrs") or {}).get("dead_ch"))
        if n.get("type") == "character" and dead_ch and dead_ch < ch:
            nid = str(n.get("id") or "")
            names.setdefault(nid, str(n.get("name") or nid))
            rows.append({"id": f"cst_ch{ch}_canon_death_{nid[-4:]}", "ch": ch,
                         "kind": "forbidden_present", "src": "", "dst": nid,
                         "strength": "must", "text": f"{names[nid]}已于第{dead_ch}章死亡，不得出场"})
    for it in (foreshadowing or {}).get("items") or []:
        due = _int(it.get("due_ch"))
        if not due or due > ch:
            continue  # 没到期的不比；已回收的仍要比（能否算满足由 diff 按 reveal_ch 判定）
        name = str(it.get("name") or it.get("key") or "")
        if name:
            rows.append({"id": f"cst_ch{ch}_fs_{str(it.get('key') or name)[-6:]}", "ch": ch,
                         "kind": "must_resolve", "src": "", "dst": name,
                         "strength": "must", "text": f"第{due}章到期的伏笔「{name}」必须回收"})
    return rows


def diff(plan: Optional[dict], ch: int, observed: dict,
         canon: Optional[dict] = None, foreshadowing: Optional[dict] = None,
         names: Optional[dict] = None) -> dict:
    """Plan 期望 vs 本章实际：missing / violation / unplanned 三档（TECH-R24 §P2）。

    - `missing`：must 未满足，**-5 分，不阻断**（漏了记分，不卡生成）；
    - `violation`：命中 forbidden（死而复生、禁止露面），**阻断**——生成前拦截，
      漏网则由调用方决定大改；
    - `unplanned`：计划外的重要变化（物品易主、计划外死亡），**仅 info 不扣分**。
    只比 `must` 与 `should` 两级；`hint` 不参与判定。
    """
    ch = _int(ch)
    names = dict(names or (plan or {}).get("names") or {})
    for n in (canon or {}).get("nodes") or []:
        if n.get("id") and n.get("name"):
            names.setdefault(str(n["id"]), str(n["name"]))
    expected = _expected_for_ch(plan or {}, ch, canon, foreshadowing)
    observed = observed or {}
    if not observed.get("available"):
        # 本章还没写（没有逐章账本状态）：不做判定，避免把「未写」误报成「没做到」
        return {"ch": ch, "has_observation": False, "skipped": "no_observation",
                "expected": len(expected), "missing": [], "violation": [],
                "unplanned": [], "satisfied": [], "unverifiable": [],
                "score": 0, "blocking": False,
                "counts": {"expected": len(expected), "satisfied": 0, "missing": 0,
                           "violation": 0, "unplanned": 0, "unverifiable": 0}}
    chars = observed.get("characters") or {}
    items = observed.get("items") or {}
    time_text = str(observed.get("time") or "")
    chars_ok = bool(chars)  # 本章没抽到人物状态时，人物类判定只能标「无法核实」
    missing: list[dict] = []
    violation: list[dict] = []
    satisfied: list[dict] = []
    unplanned: list[dict] = []
    unverifiable: list[dict] = []

    def label(nid: str) -> str:
        return names.get(nid, nid)

    for c in expected:
        kind = c["kind"]
        name = label(c["dst"]) if c["dst"] else label(c["src"])
        if kind == "must_present":
            ok = name in chars
        elif kind == "must_hold":
            st = items.get(label(c["src"]))
            ok = bool(st) and item_state_state(st) == "held" and (
                str(st.get("owner") or "") == label(c["dst"])
                or str(st.get("owner") or "") == c["dst"])
            if st and item_state_state(st) == "lost":
                ok = False
        elif kind == "must_locate":
            ok = bool(name) and (name in time_text or any(name in d for d in chars.values()))
        elif kind == "forbidden_present":
            ok = name not in chars
        elif kind == "must_resolve":
            ok = False
            for it in (foreshadowing or {}).get("items") or []:
                if str(it.get("name") or "") != name and str(it.get("key") or "") != name:
                    continue
                ok = str(it.get("status") or "") == "resolved" and _int(it.get("reveal_ch")) <= ch
        else:
            unverifiable.append({**c, "label": name, "kind_label": _KIND_LABEL.get(kind, kind)})
            continue
        row = {**c, "label": name, "kind_label": _KIND_LABEL.get(kind, kind)}
        if not chars_ok and kind in ("must_present", "forbidden_present"):
            unverifiable.append({**row, "reason": "本章未抽取到人物状态，无法核实"})
            continue
        if kind == "must_locate" and not (chars or time_text):
            unverifiable.append({**row, "reason": "本章没有地点/时间文本，无法核实地点了"})
            continue
        if ok:
            satisfied.append(row)
        elif kind == "forbidden_present":
            violation.append({**row, "detail": f"{name}在第{ch}章出现，与禁止项冲突"})
        else:
            missing.append({**row, "penalty": -5 if c["strength"] == "must" else 0})

    # 计划外：本章实际持有的物品没被 Plan 声明（易主/新增），或计划外死亡
    planned_holds = {(c["src"], c["dst"]) for c in expected if c["kind"] == "must_hold"}
    for iname, st in items.items():
        if item_state_state(st) != "held":
            continue
        owner = str(st.get("owner") or "")
        if not owner:
            continue
        pair = (node_id("item", iname), node_id("character", owner))
        if pair in planned_holds:
            continue
        unplanned.append({"kind": "item_transfer", "text": f"{iname}在第{ch}章由{owner}持有（Plan 未声明）",
                          "item": iname, "owner": owner})
    for name in observed.get("dead") or []:
        if not any(c["kind"] == "forbidden_present" and label(c["dst"]) == name for c in expected):
            unplanned.append({"kind": "character_death", "text": f"{name}在第{ch}章死亡（Plan 未标注）",
                              "name": name})

    penalised = [m for m in missing if m.get("penalty")]
    score = -5 * len(penalised)
    return {"ch": ch, "has_observation": bool(observed.get("available")),
            "expected": len(expected), "missing": missing, "violation": violation,
            "unplanned": unplanned, "satisfied": satisfied, "unverifiable": unverifiable,
            "score": score, "blocking": bool(violation),
            "counts": {"expected": len(expected), "satisfied": len(satisfied),
                       "missing": len(missing), "violation": len(violation),
                       "unplanned": len(unplanned), "unverifiable": len(unverifiable)}}


def item_state_state(st: dict) -> str:
    """`normalize_item()` 结果的 state（避免在 diff 里重复读键）。"""
    return str((st or {}).get("state") or "unknown")
