# -*- coding: utf-8 -*-
"""全书一致性体检与可定位索引（R2，只读、零 LLM、零写盘）。

把既有落盘信号整理成作者可判断的三类体检结论：

1. 账本覆盖：复用 `pipeline.commit.check_projection`（与 `tools/projection_check.py` 同输入同输出）；
2. 伏笔台账：复用 `reader_ledger` 的承诺状态机（`ReaderLedger.commitment_items`），
   状态含 planted/due_soon/overdue/stalled/revealed；
3. 一致性冲突：`verify_issues.json`（全书校验）+ 章节版本库当前选中版本的质检 issues
   （按一致性关键词过滤），尽量带 evidence/suggestion。

外加「提及索引」：从 world 汇总人物/地点/势力/物件/生物/术语（含 aliases），
逐章流式扫描正文（`store.iter_chapter_texts`），不在内存里保留全书正文。

约定：
- `severity ∈ high/medium/low/info`，前端统一按此排序，不在前端另判；
- 任何数据缺失都返回空列表并保留 `data_gaps` 说明，绝不输出「全部一致」这类过度结论；
- 本模块只读：不触发重写、不写库、不落盘。
"""
from __future__ import annotations

import re
from typing import Optional

from .commit import check_projection
from .reader_ledger import ReaderLedger

#: 严重度排序权重（体检页统一排序口径）
SEVERITY_RANK: dict = {"high": 3, "medium": 2, "low": 1, "info": 0}

#: 账本覆盖状态 → 展示文案 / 严重度
VIEW_STATUS_LABELS: dict = {
    "synced": "同步", "lagging": "落后", "ahead": "超前", "unknown": "未知",
}
VIEW_STATUS_SEVERITY: dict = {
    "lagging": "high", "ahead": "low", "synced": "info", "unknown": "info",
}

#: 伏笔状态 → 展示文案 / 严重度（overdue 最需要处理，revealed 只是台账记录）
FS_STATE_LABELS: dict = {
    "planted": "已埋设", "due_soon": "临期", "overdue": "逾期未回收",
    "stalled": "停滞无推进", "revealed": "已回收",
}
FS_STATE_SEVERITY: dict = {
    "overdue": "high", "stalled": "medium", "due_soon": "low",
    "planted": "info", "revealed": "info",
}

#: 质检 issue 级别 → 严重度
LEVEL_SEVERITY: dict = {"大改": "high", "中改": "medium", "小改": "low"}

#: 一致性冲突的关键词（type/detail 命中即纳入，确定性过滤，零 LLM）
CONSISTENCY_KEYWORDS = ("一致", "矛盾", "冲突", "逻辑", "设定")

#: 实体类型与展示名（唯一口径）
ENTITY_KINDS: dict = {
    "character": "人物", "location": "地点", "faction": "势力",
    "item": "物件", "enemy": "生物", "term": "术语",
}

#: 单章最多返回的原文片段数（控制响应体与前端渲染成本）
MAX_OCCURRENCES_PER_CHAPTER = 3

#: 体检页首屏返回的条目上限（其余走分页接口）
OVERVIEW_PREVIEW = 8

#: 单次实体索引最多扫描的实体数（防超大设定库拖垮请求）
MAX_INDEX_ENTITIES = 400


def chapter_numbers(store) -> list[int]:
    """返回已写章节号（升序遍历；只读文件名，不加载正文）。"""
    nos: list[int] = []
    for p in store.chapters_dir.glob("*.json"):
        try:
            nos.append(int(p.stem))
        except ValueError:
            continue
    nos.sort()
    return nos


def _sort_items(items: list[dict], key: str = "") -> list[dict]:
    """按严重度降序排序；同严重度时可按 key 再排（保持输出稳定）。"""
    return sorted(items, key=lambda x: (-SEVERITY_RANK.get(x.get("severity", "info"), 0),
                                       x.get(key, 0) if key else 0))


def _count_by(items: list[dict], field: str) -> dict:
    out: dict = {}
    for it in items:
        k = str(it.get(field, "") or "")
        if k:
            out[k] = out.get(k, 0) + 1
    return out


# ---------------- 1. 账本覆盖 ----------------

def build_coverage(store, up_to_chapter: Optional[int] = None) -> dict:
    """账本覆盖体检：与 `tools/projection_check.py` 同源（复用 check_projection）。

    `up_to_chapter` 缺省取当前已写最大章号；显式传入基准章号可做「写到第 N 章时」的假设体检。
    """
    baseline = int(up_to_chapter) if up_to_chapter is not None else 0
    if up_to_chapter is None:
        nos = chapter_numbers(store)
        baseline = nos[-1] if nos else 0
    rows = check_projection(store, up_to_chapter=baseline)
    for r in rows:
        r["label"] = VIEW_STATUS_LABELS.get(r["status"], r["status"])
        r["severity"] = VIEW_STATUS_SEVERITY.get(r["status"], "info")
    return {"baseline_ch": baseline, "views": rows,
            "lagging_views": [r["view"] for r in rows if r["status"] == "lagging"]}


# ---------------- 2. 伏笔台账 ----------------

def build_foreshadow(store, cfg=None, chapter_no: Optional[int] = None) -> list[dict]:
    """伏笔台账：复用 reader_ledger 状态机，输出含原文摘录（有则显示）。"""
    baseline = int(chapter_no) if chapter_no is not None else 0
    if chapter_no is None:
        nos = chapter_numbers(store)
        baseline = nos[-1] if nos else 0
    if baseline <= 0:
        return []
    items = ReaderLedger(store, cfg).commitment_items(baseline)
    out: list[dict] = []
    for it in items:
        state = str(it.get("state") or "planted")
        out.append({
            "name": it.get("name", ""),
            "state": state,
            "state_label": FS_STATE_LABELS.get(state, state),
            "severity": FS_STATE_SEVERITY.get(state, "info"),
            "buried_ch": int(it.get("buried_ch") or 0),
            "reveal_ch": int(it.get("reveal_ch") or 0),
            "revealed_ch": it.get("revealed_ch"),
            "level": it.get("level", "medium"),
            "excerpt": (it.get("original_excerpt") or "").strip(),
        })
    return _sort_items(out, key="reveal_ch")


# ---------------- 3. 一致性冲突 ----------------

def _is_consistency_issue(issue: dict) -> bool:
    """质检 issue 是否属于一致性/逻辑类（确定性关键词过滤）。"""
    text = f"{issue.get('type', '')} {issue.get('detail', '')}"
    return any(k in text for k in CONSISTENCY_KEYWORDS)


def _verify_severity(problem: str, suggestion: str) -> str:
    """全书校验问题的严重度：出现强词归 high，其余按 major 处理。"""
    blob = f"{problem} {suggestion}"
    if any(k in blob for k in ("致命", "严重", "崩坏", "崩塌", "硬伤")):
        return "high"
    return "medium"


def _selected_quality(rec: dict) -> dict:
    """章节版本索引 → 当前选中版本的质检结果（无则空 dict）。"""
    if not isinstance(rec, dict):
        return {}
    selected = rec.get("selected")
    for h in rec.get("history") or []:
        if isinstance(h, dict) and h.get("v") == selected:
            q = h.get("quality")
            return q if isinstance(q, dict) else {}
    return {}


def build_conflicts(store) -> list[dict]:
    """一致性冲突汇总（只读）：全书校验问题 + 章节选中版本的质检一致性问题。"""
    out: list[dict] = []
    seen: set = set()

    def _add(item: dict) -> None:
        # 同一章同类型同细节只保留一条（版本重生成会产生重复质检记录）
        key = (item["source"], item["chapter"], item["type"], item["detail"][:60])
        if key in seen:
            return
        seen.add(key)
        out.append(item)

    for it in store.load_verify_issues():
        chapter = int(it.get("chapter") or 0)
        problem = str(it.get("problem") or "").strip()
        suggestion = str(it.get("suggestion") or "").strip()
        if not chapter or not problem:
            continue
        _add({"source": "verify", "chapter": chapter, "type": "全书一致性校验",
              "detail": problem, "evidence": "", "suggestion": suggestion,
              "severity": _verify_severity(problem, suggestion)})

    for ch_key, rec in (store.load_chapter_versions() or {}).items():
        try:
            chapter = int(ch_key)
        except (TypeError, ValueError):
            continue
        quality = _selected_quality(rec)
        for issue in quality.get("issues") or []:
            if not isinstance(issue, dict) or not _is_consistency_issue(issue):
                continue
            detail = str(issue.get("detail") or "").strip()
            _add({"source": "chapter_quality", "chapter": chapter,
                  "type": str(issue.get("type") or "一致性"),
                  "detail": detail,
                  "evidence": str(issue.get("evidence") or "").strip(),
                  "suggestion": str(issue.get("fix_suggestion") or "").strip(),
                  "severity": LEVEL_SEVERITY.get(issue.get("level"), "medium")})
    return _sort_items(out, key="chapter")


# ---------------- 4. 提及索引 ----------------

def collect_entities(world) -> list[dict]:
    """从 world 汇总可索引实体：人物/地点/势力/物件/生物/术语，含 aliases。

    同名实体只保留一条（类型优先级按遍历顺序：人物 > 地点 > 势力 > 物件 > 生物），
    术语卡的 aliases 合并到同名实体上（术语卡是「同一专有名词的别名权威来源」）。
    """
    if world is None:
        return []
    entities: dict = {}

    def _push(name: str, kind: str) -> None:
        name = (name or "").strip()
        if len(name) < 2:
            return
        if name not in entities:
            entities[name] = {"name": name, "kind": kind, "aliases": []}

    for c in getattr(world, "characters", None) or []:
        _push(getattr(c, "name", ""), "character")
    for loc in getattr(world, "locations", None) or []:
        _push(getattr(loc, "name", ""), "location")
    for f in getattr(world, "factions", None) or []:
        _push(getattr(f, "name", ""), "faction")
    for it in getattr(world, "items", None) or []:
        _push(getattr(it, "name", ""), "item")
    for e in getattr(world, "enemies", None) or []:
        _push(getattr(e, "name", ""), "enemy")
    for t in getattr(world, "terminology", None) or []:
        _push(getattr(t, "name", ""), "term")
        target = entities.get((getattr(t, "name", "") or "").strip())
        if target is not None:
            for a in getattr(t, "aliases", None) or []:
                a = (a or "").strip()
                if len(a) >= 2 and a not in target["aliases"]:
                    target["aliases"].append(a)
    return list(entities.values())


def _snippet(content: str, idx: int, matched: str) -> str:
    """抽取命中位置前后文（紧凑化空白），供前端展示「定位到章节内」的依据。"""
    lo = max(0, idx - 24)
    hi = min(len(content), idx + max(len(matched), 1) + 40)
    text = re.sub(r"\s+", " ", content[lo:hi]).strip()
    return f"{'…' if lo > 0 else ''}{text}{'…' if hi < len(content) else ''}"


def _scan_mentions(store, alias_map: dict, with_snippet: bool = False,
                   max_per_chapter: int = MAX_OCCURRENCES_PER_CHAPTER) -> dict:
    """逐章流式扫描正文，返回 {实体key: [{no,title,count,occurrences?}]}。

    全书正文一次只载入一章（store.iter_chapter_texts），扫描完即丢弃。
    """
    result: dict = {k: [] for k in alias_map}
    for no, title, content in store.iter_chapter_texts():
        if not content:
            continue
        for key, aliases in alias_map.items():
            count = 0
            occ: list[dict] = []
            for alias in aliases:
                if len(alias) < 2:
                    continue
                hit = content.count(alias)
                if not hit:
                    continue
                count += hit
                if not with_snippet:
                    continue
                pos = content.find(alias)
                while pos >= 0 and len(occ) < max_per_chapter:
                    occ.append({"offset": pos, "snippet": _snippet(content, pos, alias)})
                    pos = content.find(alias, pos + len(alias))
            if not count:
                continue
            rec = {"no": no, "title": title, "count": count}
            if with_snippet:
                rec["occurrences"] = occ
            result[key].append(rec)
    return result


def _match_entity(entity: dict, query: str) -> bool:
    if not query:
        return True
    q = query.strip().lower()
    return q in entity["name"].lower() or any(q in a.lower() for a in entity["aliases"])


def build_entity_index(store, world=None, query: str = "", kind: str = "",
                       limit: int = 50, offset: int = 0) -> dict:
    """人物/设定提及索引：每个实体的出现章节、总次数、首末章号（分页）。

    全量实体扫描一次后统一排序再分页，保证「被引最多的核心角色」稳定排在最前；
    实体数超过 MAX_INDEX_ENTITIES 时截断扫描量，避免超大设定库拖垮单次请求。
    """
    world = world if world is not None else store.load_world()
    matched = [e for e in collect_entities(world)
               if (not kind or e["kind"] == kind) and _match_entity(e, query)]
    total = len(matched)
    scan_list = matched[:MAX_INDEX_ENTITIES]
    alias_map = {e["name"]: [e["name"]] + list(e["aliases"]) for e in scan_list}
    stats = _scan_mentions(store, alias_map, with_snippet=False)

    items: list[dict] = []
    for e in scan_list:
        chapters = stats.get(e["name"], [])
        items.append({
            "name": e["name"], "kind": e["kind"],
            "kind_label": ENTITY_KINDS.get(e["kind"], e["kind"]),
            "aliases": list(e["aliases"]),
            "chapters": [{"no": c["no"], "title": c["title"], "count": c["count"]}
                         for c in chapters],
            "total_mentions": sum(c["count"] for c in chapters),
            "first_ch": chapters[0]["no"] if chapters else None,
            "last_ch": chapters[-1]["no"] if chapters else None,
        })
    # 被引次数多的实体优先（本书核心角色/设定一眼可见）；零命中实体保留在末尾作覆盖缺口
    items.sort(key=lambda x: (-x["total_mentions"], x["name"]))
    start = max(0, offset)
    return {"items": items[start: start + max(1, limit)], "total": total,
            "offset": offset, "limit": limit,
            "truncated": total > MAX_INDEX_ENTITIES}


def search_mentions(store, name: str, aliases: Optional[list[str]] = None,
                    limit_chapters: int = 50) -> dict:
    """单个实体的章节内定位：返回每章命中次数与前若干条原文片段（含字符偏移）。"""
    name = (name or "").strip()
    alias_list: list[str] = [name] + [a for a in (aliases or []) if (a or "").strip()]
    alias_list = list(dict.fromkeys([a for a in alias_list if len(a) >= 2]))[:8]
    stats = _scan_mentions(store, {name: alias_list}, with_snippet=True)
    chapters = stats.get(name, [])
    truncated = len(chapters) > limit_chapters
    return {
        "name": name, "aliases": alias_list,
        "chapters": chapters[:limit_chapters],
        "total_chapters": len(chapters), "truncated": truncated,
    }


# ---------------- 汇总（首屏） ----------------

def build_overview(store, cfg=None) -> dict:
    """体检首屏：概览计数 + 各分类前 N 条 + 数据缺口声明（缺数据不做过度结论）。"""
    nos = chapter_numbers(store)
    baseline = nos[-1] if nos else 0
    world = store.load_world()
    coverage = build_coverage(store, up_to_chapter=baseline)
    foreshadow = build_foreshadow(store, cfg, baseline)
    conflicts = build_conflicts(store)
    entity_candidates = collect_entities(world)

    return {
        "project_id": store.project_id,
        "baseline_ch": baseline,
        "chapters_written": len(nos),
        "has_world": world is not None,
        "has_outline": store.load_outline() is not None,
        "coverage": {
            "baseline_ch": coverage["baseline_ch"],
            "lagging_views": coverage["lagging_views"],
            "views": coverage["views"],
        },
        "foreshadow": {
            "total": len(foreshadow),
            "counts": _count_by(foreshadow, "state"),
            "items": foreshadow[:OVERVIEW_PREVIEW],
        },
        "conflicts": {
            "total": len(conflicts),
            "counts": _count_by(conflicts, "severity"),
            "items": conflicts[:OVERVIEW_PREVIEW],
        },
        "entities": {"candidates": len(entity_candidates)},
        "data_gaps": [
            "未覆盖语义级跨章冲突全量检测：一致性结论来自已有全书校验与章节质检记录。",
            "账本覆盖/伏笔状态来自已落盘产物，缺失项显示「未记录」，不代表全部一致。",
        ],
    }
