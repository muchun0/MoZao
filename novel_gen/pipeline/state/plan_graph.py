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
    """按 id 前缀反查名字（编译期已知名单里找，找不到留空）。"""
    prefix, _, _h = str(nid).partition("_")
    type_of = {"char": "character", "place": "place", "item": "item"}
    if prefix not in type_of:
        return ""
    pool = {"char": chars, "place": locs, "item": items}[prefix]
    for name in pool:
        if node_id(type_of[prefix], name) == nid:
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
            if v and v not in known and _name_for_id(v, _names_of(world, "characters"),
                                                     _names_of(world, "locations"),
                                                     _names_of(world, "items")):
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
