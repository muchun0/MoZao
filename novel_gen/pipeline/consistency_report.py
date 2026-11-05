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

import logging
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
    # REQ-R10 A：进度接口专用（未到埋点，状态机本身不返回该值）
    "not_buried": "未到埋点",
}
FS_STATE_SEVERITY: dict = {
    "overdue": "high", "stalled": "medium", "due_soon": "low",
    "planted": "info", "revealed": "info", "not_buried": "info",
}

#: 质检 issue 级别 → 严重度
LEVEL_SEVERITY: dict = {"大改": "high", "中改": "medium", "小改": "low"}

#: 一致性冲突的关键词（type/detail 命中即纳入，确定性过滤，零 LLM）
CONSISTENCY_KEYWORDS = ("一致", "矛盾", "冲突", "逻辑", "设定")

#: 全书校验记录取「问题描述」的兜底顺序（真实数据存在只有 type/detail 的文风漂移记录）
VERIFY_TEXT_FIELDS = ("problem", "detail", "title")

#: 不是设定矛盾、只算建议的类型特征（文风漂移 / 阅读节奏等）
ADVISORY_TYPE_KEYWORDS = ("情节-", "阅读障碍", "文风", "漂移", "节奏")

#: 文风漂移类特征：展示时与「阅读/节奏建议」区分，避免被误读成情节问题
STYLE_DRIFT_KEYWORDS = ("文风", "漂移")

#: 缺 problem 字段时的前端标注（不隐藏记录，但也不假装它有完整问题描述）
PROBLEM_MISSING_NOTE = "原记录未单列问题描述"

logger = logging.getLogger(__name__)

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

#: 可逐章定位缺口的视图 → 数据来源说明（生成每章时写入）
COVERAGE_LOCATABLE_META: dict = {
    "chapter_commit": "每写完一章写入的章节提交记录",
    "summaries": "每写完一章写入的章节摘要",
    "scenes": "每写完一章写入的场景清单",
    "beat_gate": "每写完一章写入的剧情节点",
}

#: 只能拿到「最后更新章号」的视图 → 无法定位具体缺哪章
COVERAGE_WATERMARK_META: dict = {
    "chapter_state": "这一项只记录最后更新到第几章，看不到具体缺哪章",
    "foreshadow_commitment": "这一项只记录最后更新到第几章，看不到具体缺哪章",
    "payoff_quota": "这一项只记录发生过爽点的章号，不代表逐章覆盖",
    "reveal_status": "这一项按伏笔回收事件记录，不代表逐章覆盖",
}

#: 记录缺口最多返回多少个章号（超出由前端折叠成区间）
MAX_MISSING_CHAPTERS = 30

#: 覆盖清单首屏最多列出的章数与每章条目数（避免把整本清单一次塞给前端）
MAX_RECORDED_CHAPTERS = 12
MAX_RECORDED_ITEMS = 3
RECORDED_TEXT_LIMIT = 46

#: 落盘记录只有编号、没有可读描述时的统一标注
NO_DESCRIPTION = "只有编号，没有描述"

#: 不可逐章定位时的空清单字段（保持接口形状一致，前端无需分支）
_EMPTY_RECORDED = {"recorded_chapters": [], "recorded_chapter_count": 0,
                   "recorded_item_count": 0, "recorded_truncated": False}


def _view_records(store, view: str) -> Optional[dict]:
    """一次性读取某视图的逐章记录（{章号: 原始值}）；读不到或异常时返回 None。"""
    try:
        if view == "chapter_commit":
            chapters = (store.load_chapter_commit() or {}).get("chapters") or {}
            return {int(k): v for k, v in chapters.items()
                    if str(k).isdigit() and isinstance(v, dict)
                    and v.get("status") != "pending"}
        if view == "summaries":
            return {int(x["no"]): x for x in (store.load_summaries() or [])
                    if isinstance(x, dict) and x.get("no")}
        if view == "scenes":
            raw = store.load_scenes() or {}
            return {int(k): v for k, v in raw.items()
                    if str(k).isdigit() and isinstance(v, dict)}
        if view == "beat_gate":
            raw = store.load_beat_gate() or {}
            return {int(k): v for k, v in raw.items()
                    if str(k).isdigit() and isinstance(v, dict)}
    except Exception:
        return None
    return None


def _recorded_chapters(store, view: str) -> Optional[set]:
    """读取某视图「已记录的章号」集合；读取失败或该视图无逐章记录时返回 None。"""
    records = _view_records(store, view)
    return None if records is None else set(records)


def _clip_text(value) -> str:
    """压成单行并截断，避免一条记录把卡片撑满。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= RECORDED_TEXT_LIMIT:
        return text
    return text[: RECORDED_TEXT_LIMIT - 1] + "…"


def _chapter_entries(view: str, record) -> tuple[list[str], int]:
    """把一章的落盘记录压成可读条目，返回（摘要列表, 条目总数）。"""
    if view == "scenes":
        scenes = record.get("scenes") or []
        texts: list[str] = []
        for sc in scenes[:MAX_RECORDED_ITEMS]:
            if not isinstance(sc, dict):
                texts.append(NO_DESCRIPTION)
                continue
            text = sc.get("event") or sc.get("location") or sc.get("scene_event_ref")
            if text and sc.get("location") and str(sc["location"]) not in str(text):
                text = f"{sc['location']} · {text}"
            texts.append(_clip_text(text) if text else NO_DESCRIPTION)
        return texts, len(scenes)
    if view == "beat_gate":
        planned = record.get("planned") or []
        texts = []
        for b in planned[:MAX_RECORDED_ITEMS]:
            if not isinstance(b, dict):
                texts.append(_clip_text(b) or NO_DESCRIPTION)
                continue
            mark = "已覆盖" if b.get("covered") else "未覆盖"
            beat = b.get("beat")
            texts.append(_clip_text(f"{mark}：{beat}") if beat else mark)
        return texts, len(planned)
    if view == "summaries":
        return [_clip_text(record.get("summary")) or NO_DESCRIPTION], 1
    if view == "chapter_commit":
        done = record.get("done") or []
        failed = record.get("failed") or []
        texts = [t for t in (_clip_text(x) for x in done[:MAX_RECORDED_ITEMS]) if t]
        if failed and len(texts) < MAX_RECORDED_ITEMS:
            texts.append(_clip_text(f"失败 {len(failed)} 项"))
        return texts or [NO_DESCRIPTION], max(len(done) + len(failed), 1)
    return [], 0


def _coverage_detail(store, view: str, covered_ch: Optional[int],
                     written: list[int]) -> dict:
    """覆盖项的缺口事实：能逐章定位就给具体章号与已记条目，不能就老实说只看得到落后多少。

    只统计「已写章节里没有记录」的章，不把未写的未来章节算成缺口。
    """
    if view in COVERAGE_LOCATABLE_META:
        records = _view_records(store, view)
        if records is None:
            return {"locatable": False, "missing_chapters": [], "missing_count": 0,
                    "record_source": COVERAGE_LOCATABLE_META[view] + "（本次读取失败）",
                    **_EMPTY_RECORDED}
        recorded = set(records)
        missing = sorted(n for n in written if n not in recorded)
        listed = sorted(n for n in written if n in recorded)
        entries: list[dict] = []
        total_items = 0
        for n in listed:
            texts, count = _chapter_entries(view, records[n])
            total_items += count
            if len(entries) < MAX_RECORDED_CHAPTERS:
                entries.append({"ch": n, "count": count, "items": texts})
        return {"locatable": True,
                "missing_chapters": missing[:MAX_MISSING_CHAPTERS],
                "missing_count": len(missing),
                "record_source": COVERAGE_LOCATABLE_META[view],
                "recorded_chapters": entries,
                "recorded_chapter_count": len(listed),
                "recorded_item_count": total_items,
                "recorded_truncated": len(listed) > MAX_RECORDED_CHAPTERS}
    if view in COVERAGE_WATERMARK_META:
        if covered_ch is None:
            # 连最后更新章号都没有：不能推算落后几章，只能说明查不到
            return {"locatable": False, "missing_chapters": [], "missing_count": 0,
                    "record_source": "这一项没有记录更新到第几章，无法判断缺口",
                    **_EMPTY_RECORDED}
        baseline = written[-1] if written else 0
        gap = max(0, baseline - int(covered_ch))
        return {"locatable": False, "missing_chapters": [], "missing_count": gap,
                "record_source": COVERAGE_WATERMARK_META[view], **_EMPTY_RECORDED}
    return {"locatable": False, "missing_chapters": [], "missing_count": 0,
            "record_source": "这一项没有记录覆盖到第几章，无法判断缺口", **_EMPTY_RECORDED}


def build_coverage(store, up_to_chapter: Optional[int] = None) -> dict:
    """账本覆盖体检：与 `tools/projection_check.py` 同源（复用 check_projection）。

    `up_to_chapter` 缺省取当前已写最大章号；显式传入基准章号可做「写到第 N 章时」的假设体检。
    """
    baseline = int(up_to_chapter) if up_to_chapter is not None else 0
    if up_to_chapter is None:
        nos = chapter_numbers(store)
        baseline = nos[-1] if nos else 0
    rows = check_projection(store, up_to_chapter=baseline)
    # 缺口只统计到本次基准章为止，避免把尚未写的章节算成「缺记录」
    written = [n for n in chapter_numbers(store) if n <= baseline]
    for r in rows:
        r["label"] = VIEW_STATUS_LABELS.get(r["status"], r["status"])
        r["severity"] = VIEW_STATUS_SEVERITY.get(r["status"], "info")
        r.update(_coverage_detail(store, r["view"], r.get("covered_ch"), written))
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


#: 判为「雷同描写」而非「前后矛盾」的类型特征（这类不是设定矛盾）
REPETITION_TYPE_KEYWORDS = ("事件重复", "内容重复", "重复")

#: 质检 detail 的两处对照（提示词要求引用「前一处…后一处…」）
_TWO_SIDE_RE = re.compile(r"前一处[：:]\s*(.*?)[；;，。\n』”\"]+后一处[：:]\s*(.*)$", re.S)


def _match_entities(text: str, entities: list[dict]) -> list[dict]:
    """在问题描述里匹配已知实体（名字或别名），用于标明「涉及人物/设定」。"""
    hits: list[dict] = []
    for e in entities:
        for alias in [e.get("name", "")] + list(e.get("aliases") or []):
            if len(alias) >= 2 and alias in text:
                hits.append(e)
                break
    return hits[:5]


#: 动作词：用于从「前一处：二舅公走到桌前…」这类原文里截出人物主语
_ACTION_WORDS = ("走", "站", "坐", "抓", "把", "抬", "转", "看", "说", "笑",
                 "跑", "冲", "握", "伸", "跨", "拿", "提", "推", "拉", "低", "回", "作", "用")
_SUBJECT_RE = re.compile(
    r"^([\u4e00-\u9fa5]{2,4})(?:[一二三四五六七八九十两几]步)?(?="
    + "|".join(_ACTION_WORDS) + r")"
)


def _subject_from_side(text: str) -> str:
    """从一句动作描写里截出开头的称呼/人名；截不出来就返回空串（不硬猜）。"""
    text = (text or "").strip()
    m = _SUBJECT_RE.match(text)
    return m.group(1) if m else ""


def _setting_excerpt(world, names: set) -> Optional[dict]:
    """取设定原文（人物/地点/势力/物件/生物）作为「预期」依据；无则 None。"""
    if world is None or not names:
        return None
    for attr in ("characters", "locations", "factions", "items", "enemies"):
        for obj in getattr(world, attr, None) or []:
            name = str(getattr(obj, "name", "") or "").strip()
            if name not in names:
                continue
            for field in ("description", "desc", "summary", "setting"):
                text = str(getattr(obj, field, "") or "").strip()
                if text:
                    return {"name": name, "excerpt": text[:160]}
    return None


#: 质检 detail 里引用的设定原文（如「与他‘表面温和…’的设定有冲突」）
_SETTING_QUOTE_RE = re.compile(r"[‘“\"]([^’”\"]{4,160})[’”\"]\s*(?:的)?(?:人物)?设定")


def _setting_quote(detail: str) -> str:
    """取质检记录自己引用的设定原文；没有写就不返回，绝不替作者猜。"""
    m = _SETTING_QUOTE_RE.search(detail or "")
    return m.group(1).strip() if m else ""


def _setting_by_text(world, text: str) -> Optional[dict]:
    """把质检引用的设定原文对回世界观条目；对不上就返回 None（展示时标为「记录引用」）。"""
    if world is None or not text:
        return None
    for attr in ("characters", "locations", "factions", "items", "enemies"):
        for obj in getattr(world, attr, None) or []:
            name = str(getattr(obj, "name", "") or "").strip()
            blob = " ".join(str(getattr(obj, f, "") or "")
                            for f in ("description", "desc", "summary", "setting"))
            if name and text in blob:
                return {"name": name, "excerpt": text}
    return None


def _enrich_conflict(item: dict, entities: list[dict], world) -> dict:
    """给每条记录补齐「依据 / 预期 vs 实际 / 为什么判」三要素。

    分类口径：
    - repetition：不同人物的同类动作/句式重复（不是设定矛盾，降为 low）；
    - duplicate_event：同一人物、同一事件被写成两个版本（是重复叙述，不是设定矛盾）；
    - insufficient：质检只给结论、没引用原文也没写对照设定，无法核对（降为 low）；
    - conflict：其余，按原严重度，并尽量挂上质检引用的设定原文。
    """
    detail = item["detail"]
    evidence = item.get("evidence") or ""
    matched = _match_entities(f"{detail} {evidence} {item.get('suggestion', '')}", entities)
    pair = _TWO_SIDE_RE.search(detail)
    sides = [pair.group(1).strip(), pair.group(2).strip()] if pair else []
    subjects = [s for s in (_subject_from_side(x) for x in sides) if s]
    side_entities = [{e["name"] for e in _match_entities(x, entities)} for x in sides]
    same_subject = (
        (len(subjects) == 2 and subjects[0] == subjects[1])
        or (len(side_entities) == 2 and side_entities[0] and side_entities[0] == side_entities[1])
        or ("同一人物" in detail) or ("同一个人" in detail) or ("同一事件" in detail)
    )
    item["entities"] = [e["name"] for e in matched]
    item["check_type"] = item["type"]
    # 缺 problem 字段时如实标注，前端据此提示「原记录未单列问题描述」
    if item.get("problem_missing"):
        item["note"] = PROBLEM_MISSING_NOTE

    is_repetition = any(k in item["type"] for k in REPETITION_TYPE_KEYWORDS)
    if is_repetition:
        if same_subject:
            item.update({
                "category": "duplicate_event", "category_label": "同一事件重复",
                "expected": "", "actual": detail,
                "reason": "同一个人在同一冲突下做出同一行为、结果也相同，却被写成了两个版本；这是重复叙述，不是设定矛盾。",
                "basis": [{"kind": "check", "name": "单章质检 · 一致性评审记录", "excerpt": ""}],
            })
            return item
        # 世界观里没登记的人物：从两处原文开头截出称呼，至少能说清「涉及谁」
        if pair and not item["entities"]:
            for side in sides:
                name = _subject_from_side(side)
                if name and name not in item["entities"]:
                    item["entities"].append(name)
        item.update({
            "category": "repetition", "category_label": "雷同描写",
            "expected": sides[0] if pair else "两处相似的动作描写",
            "actual": sides[1] if pair else detail,
            "reason": "两处是不同人物的同类动作，不是同一事件的第二个版本；按一致性评审规则（同人物+同冲突+同结果）不构成矛盾，属于动作/句式雷同。",
            "severity": "low",
            "basis": [{"kind": "check", "name": "单章质检 · 一致性评审记录", "excerpt": ""}],
        })
        return item

    # 阅读障碍 / 情节推进 / 文风漂移这类是建议，不是「前后对不上」，不冒充冲突
    if any(k in item["type"] for k in ADVISORY_TYPE_KEYWORDS):
        is_style = any(k in item["type"] for k in STYLE_DRIFT_KEYWORDS)
        basis_name = ("单章质检 · 一致性评审记录" if item["source"] == "chapter_quality"
                      else "全书通读检查记录")
        item.update({
            "category": "advisory",
            "category_label": "文风/表达提醒" if is_style else "阅读/节奏提醒",
            "expected": "", "actual": detail,
            "reason": ("这条不是设定矛盾，是检查记录给出的文风提醒：只描述了写法变化，没有对照设定条目。"
                       if is_style else
                       "这条不是设定矛盾，是单章质检给出的阅读或节奏建议，没有对照设定条目。"),
            # 文风漂移按原记录的改稿级别给严重度，其余建议保持 low（不冒充必须处理）
            "severity": (LEVEL_SEVERITY.get(str(item.get("level") or ""), "low")
                         if is_style else "low"),
            "basis": [{"kind": "check", "name": basis_name, "excerpt": ""}],
        })
        return item

    # 依据优先用质检自己引用的设定原文；只有类型明确是「设定」冲突时才对世界观条目
    quoted = _setting_quote(detail)
    setting = _setting_by_text(world, quoted) if quoted else None
    if quoted and not setting:
        name = _subject_from_side(detail) or "质检引用的设定"
        setting = {"name": name, "excerpt": quoted}
    elif not quoted and "设定" in item["type"]:
        setting = _setting_excerpt(world, {e["name"] for e in matched})
    if setting and setting["name"] != "质检引用的设定" and setting["name"] not in item["entities"]:
        item["entities"].append(setting["name"])

    # detail 里已经引用了正文原句（“…”/‘…’）时，不能算「没给原文」
    has_quote = bool(re.search(r"[‘“\"][^’”\"]{4,}", detail))

    if item["source"] == "chapter_quality" and not evidence and not pair and not setting and not has_quote:
        item.update({
            "category": "insufficient", "category_label": "依据不足",
            "expected": "", "actual": detail,
            "reason": "质检只写了一句判断，没有引用原文，也没有写对照的设定；无法核对，先不当作冲突。",
            "severity": "low",
            "basis": [{"kind": "check", "name": "单章质检 · 一致性评审记录", "excerpt": ""}],
        })
        return item

    basis = [{"kind": "check", "name": "单章质检 · 一致性评审记录" if item["source"] == "chapter_quality" else "全书通读检查记录", "excerpt": ""}]
    if setting:
        basis.append({"kind": "setting", "name": setting["name"], "excerpt": setting["excerpt"]})
    item.update({
        "category": "conflict", "category_label": "前后矛盾",
        "expected": setting["excerpt"] if setting else "",
        "actual": evidence or detail,
        "reason": (f"与「{setting['name']}」的设定冲突：记录引用的设定原文见依据，正文写法与其不一致。"
                   if setting else
                   ("记录里引用了正文原句，但没有写对照的设定条目；系统只能按原句指出前后不一致，需要打开这一章核对。"
                    if has_quote else
                    "检查记录判定前后不一致；但记录里没有引用任何设定条目，系统无法指出冲突对象，需要打开这一章核对原文。")),
        "basis": basis,
    })
    return item


def _verify_detail(it: dict) -> tuple[str, bool]:
    """取全书校验记录的问题描述（problem → detail → title 兜底）。

    返回（描述文本, 是否缺 problem 字段）。真实数据里的文风漂移记录只有 type/detail，
    旧实现直接跳过，导致「工具能看到 21 条、体检只剩 19 条」。
    """
    problem = str(it.get("problem") or "").strip()
    if problem:
        return problem, False
    for field in VERIFY_TEXT_FIELDS[1:]:
        text = str(it.get(field) or "").strip()
        if text:
            return text, True
    return "", False


def build_conflicts_with_counts(store) -> tuple[list[dict], dict]:
    """一致性冲突汇总 + 计数口径（只读、零 LLM、零写盘）。

    每条都带 category / basis / expected / actual / reason，缺依据的降级为「依据不足」。

    计数口径（同一轮遍历内产生，前端按 sources 自证差异）：
    - verify.records：verify_issues.json 原始条数（工具「全书校验」看到的数）；
    - verify.included：进入体检的条数（缺 problem 时用 detail/title 兜底）；
    - verify.skipped：连问题描述都没有、无法计入的条数；
    - verify.fallback：缺 problem、走兜底的条数；
    - chapter_quality.candidates / included：命中一致性关键词的质检条数 / 计入条数；
    - merged.total / merged.deduped：最终条数 / 去重掉的条数。
    """
    out: list[dict] = []
    seen: set = set()
    world = store.load_world()
    entities = collect_entities(world)
    stats: dict = {
        "verify": {"records": 0, "included": 0, "skipped": 0, "fallback": 0},
        "chapter_quality": {"candidates": 0, "included": 0},
        "merged": {"total": 0, "deduped": 0},
    }

    def _add(item: dict) -> None:
        # 同一章同类型同细节只保留一条（版本重生成会产生重复质检记录）
        key = (item["source"], item["chapter"], item["type"], item["detail"][:60])
        if key in seen:
            stats["merged"]["deduped"] += 1
            return
        seen.add(key)
        out.append(_enrich_conflict(item, entities, world))

    try:
        verify_records = store.load_verify_issues()
    except Exception:
        logger.warning("体检读取 verify_issues 失败，按空清单降级", exc_info=True)
        verify_records = []
    stats["verify"]["records"] = len(verify_records)
    for raw in verify_records:
        if not isinstance(raw, dict):
            stats["verify"]["skipped"] += 1
            continue
        detail, fallback = _verify_detail(raw)
        if not detail:
            stats["verify"]["skipped"] += 1
            continue
        try:
            chapter = int(raw.get("chapter") or 0)
        except (TypeError, ValueError):
            chapter = 0  # 不能定位也保留（会计入计数，前端不提供跳章）
        stats["verify"]["included"] += 1
        if fallback:
            stats["verify"]["fallback"] += 1
        _add({
            "source": "verify", "chapter": chapter,
            # 保留记录自带类型（如「文风漂移-L3」），既有的「全书一致性校验」仅作兜底
            "type": str(raw.get("type") or "全书一致性校验").strip(),
            "detail": detail,
            "evidence": str(raw.get("evidence") or "").strip(),
            "suggestion": str(raw.get("suggestion") or "").strip(),
            "level": str(raw.get("level") or ""),
            "problem_missing": fallback,
            "severity": _verify_severity(detail, str(raw.get("suggestion") or "")),
        })

    try:
        versions = store.load_chapter_versions() or {}
    except Exception:
        logger.warning("体检读取 chapter_versions 失败，按空清单降级", exc_info=True)
        versions = {}
    for ch_key, rec in versions.items():
        try:
            chapter = int(ch_key)
        except (TypeError, ValueError):
            continue
        for issue in (_selected_quality(rec).get("issues") or []):
            if not isinstance(issue, dict) or not _is_consistency_issue(issue):
                continue
            detail = str(issue.get("detail") or "").strip()
            if not detail:
                continue
            stats["chapter_quality"]["candidates"] += 1
            stats["chapter_quality"]["included"] += 1
            _add({"source": "chapter_quality", "chapter": chapter,
                  "type": str(issue.get("type") or "一致性"),
                  "detail": detail,
                  "evidence": str(issue.get("evidence") or "").strip(),
                  "suggestion": str(issue.get("fix_suggestion") or issue.get("suggestion") or "").strip(),
                  "level": str(issue.get("level") or ""),
                  "severity": LEVEL_SEVERITY.get(issue.get("level"), "medium")})

    stats["merged"]["total"] = len(out)
    return _sort_items(out, key="chapter"), stats


def build_conflicts(store) -> list[dict]:
    """一致性冲突清单（向后兼容入口）：只返回条目，不返回计数口径。"""
    items, _ = build_conflicts_with_counts(store)
    return items


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
    conflicts, conflict_sources = build_conflicts_with_counts(store)
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
            # 计数口径：全书校验 / 单章质检各多少条、合并去重后多少条，供前端自证差异
            "sources": conflict_sources,
            "items": conflicts[:OVERVIEW_PREVIEW],
        },
        "entities": {"candidates": len(entity_candidates)},
        "data_gaps": [
            "未覆盖语义级跨章冲突全量检测：一致性结论来自已有全书校验与章节质检记录。",
            "账本覆盖/伏笔状态来自已落盘产物，缺失项显示「未记录」，不代表全部一致。",
        ],
    }
