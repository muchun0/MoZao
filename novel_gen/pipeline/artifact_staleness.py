# -*- coding: utf-8 -*-
"""REQ-R13：人工改章后派生记录标脏（MVP，只读判定）。

派生记录（场景清单 / 剧情节点 / 章节摘要 / 章节要点）与正文是同一时刻产出的。
一旦正文的改动时间晚于这批记录的写入时间，这些记录就不再对应当前正文，
据此得出的体检结论可能失真。本模块只做实时比对：

- 不写盘（不新增标记文件、不改正文、不改账本）；
- 不调用 LLM、不触发生成、不产生计费；
- 不改 `consistency_report.py` 的冲突判定（避开并行需求 R11 的改动面）。

判定口径（精确到章）：
1. 正文最后改动时间 = max(该章版本历史 ts, 章节 HEAD 文件 mtime)；
2. 派生记录写入时间（基线）= chapter_commit.finished_at → projection_log ts → 最新 AI 版 ts；
3. 改动时间 > 基线 → stale；基线全无 → unknown（如实降级，不猜）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .consistency_report import chapter_numbers

logger = logging.getLogger("novel_gen.pipeline.artifact_staleness")

__all__ = ["ARTIFACT_LABELS", "GLOBAL_ARTIFACTS", "analyze"]

#: 逐章可定位的派生记录（key 与 R2 覆盖体检的 view 保持一致）
ARTIFACT_LABELS: dict[str, str] = {
    "scenes": "场景清单",
    "beat_gate": "剧情节点",
    "summaries": "章节摘要",
    "chapter_commit": "章节要点",
}

#: 全书级记录：确实会被旧正文影响，但结构上没有逐章时间戳，无法定位到章
GLOBAL_ARTIFACTS: dict[str, str] = {
    "chapter_state": "章内状态台账",
    "foreshadow_commitment": "伏笔回收记录",
    "reader_ledger": "读者认知记录",
}


def _empty() -> dict[str, Any]:
    """降级结构每次新建，避免多个请求共享同一个可变 list/dict。"""
    return {
        "checked_chapters": 0,
        "has_stale": False,
        "stale_chapters": [],
        "unknown_chapters": [],
        "items": [],
        "stale_by_view": {},
        "artifact_labels": ARTIFACT_LABELS,
        "global_artifacts": GLOBAL_ARTIFACTS,
        "notice": None,
    }


def analyze(store, chapter_no: Optional[int] = None) -> dict[str, Any]:
    """判定派生记录是否落后于正文；任何异常降级为空结构（不抛 5xx）。"""
    try:
        return _analyze(store, chapter_no)
    except Exception:  # 只读体检，异常不允许冒到接口层
        logger.exception("派生记录标脏判定失败（降级为空）")
        return _empty()


# ---------------------------------------------------------------- 内部实现

def _int(value: Any) -> int:
    """把历史数据里的时间/章号字段宽松转 int；非法值记 0。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_load(fn, default):
    try:
        return fn()
    except Exception:
        logger.warning("派生记录标脏：读取数据失败（降级）", exc_info=True)
        return default


def _chapter_mtime(store, no: int) -> int:
    try:
        return int(store.chapter_path(no).stat().st_mtime)
    except OSError:
        return 0


def _change_info(store, no: int, versions: dict) -> dict[str, Any]:
    """该章正文最后改动时间，以及是否以「人工版本」收尾。

    有版本索引时只用版本时间：HEAD 文件的 mtime 会被 AI 自身的重写（rewrite /
    auto-select）刷新，拿 mtime 比较会把正常的 AI 重写误判成人工改动。
    mtime 只在完全没有版本时间的老数据上兜底。
    """
    rec = versions.get(str(no)) if isinstance(versions, dict) else None
    history = rec.get("history") if isinstance(rec, dict) else None
    entries = [h for h in (history or []) if isinstance(h, dict)]
    user_ts = max((_int(h.get("ts")) for h in entries if h.get("source") == "user"), default=0)
    ai_ts = max((_int(h.get("ts")) for h in entries if h.get("source") != "user"), default=0)
    if user_ts or ai_ts:
        changed_at = max(user_ts, ai_ts)
        manual = user_ts > 0 and user_ts >= ai_ts
    else:
        # 老数据没有版本时间：退回 HEAD 文件 mtime，只能说明「正文变过」
        changed_at = _chapter_mtime(store, no)
        manual = False
    return {"changed_at": changed_at, "manual": manual, "ai_ts": ai_ts}


def _baseline(no: int, commits: dict, projection_ts: dict, ai_ts: int) -> tuple[int, str]:
    """该章派生记录的最后写入时间与依据；全无基线返回 (0, "")。"""
    rec = commits.get(str(no)) if isinstance(commits, dict) else None
    if isinstance(rec, dict):
        ts = _int(rec.get("finished_at"))
        if ts:
            return ts, "chapter_commit"
    ts = _int(projection_ts.get(no))
    if ts:
        return ts, "projection_log"
    if ai_ts:
        # 版本时间只是「正文写入时间」，派生记录在其后几秒内产出 → 标为近似基线
        return ai_ts, "version"
    return 0, ""


def _artifact_sets(store) -> dict[str, set[int]]:
    """各派生记录实际覆盖的章号集合（只读一次，避免逐章重复解析 JSON）。"""
    out: dict[str, set[int]] = {k: set() for k in ARTIFACT_LABELS}
    scenes = _safe_load(store.load_scenes, {})
    if isinstance(scenes, dict):
        out["scenes"] = {_int(k) for k in scenes if _int(k) > 0}
    beat = _safe_load(store.load_beat_gate, {})
    if isinstance(beat, dict):
        out["beat_gate"] = {_int(k) for k in beat if _int(k) > 0}
    for item in _safe_load(store.load_summaries, []) or []:
        no = _int(item.get("no")) if isinstance(item, dict) else 0
        if no > 0:
            out["summaries"].add(no)
    commits = _safe_load(store.load_chapter_commit, None) or {}
    chapters = commits.get("chapters") if isinstance(commits, dict) else None
    if isinstance(chapters, dict):
        for key, rec in chapters.items():
            if isinstance(rec, dict) and rec.get("status") != "pending" and _int(key) > 0:
                out["chapter_commit"].add(_int(key))
    return out


def _projection_ts(store) -> dict[int, int]:
    """projection_log 每章最新时间戳（派生记录写入的旁证）。"""
    out: dict[int, int] = {}
    for entry in _safe_load(store.load_projection_log, []) or []:
        if not isinstance(entry, dict):
            continue
        no = _int(entry.get("chapter"))
        ts = _int(entry.get("ts"))
        if no > 0 and ts:
            out[no] = max(out.get(no, 0), ts)
    return out


def _analyze(store, chapter_no: Optional[int]) -> dict[str, Any]:
    nos = chapter_numbers(store)
    if chapter_no is not None:
        nos = [n for n in nos if n == _int(chapter_no)]
    if not nos:
        return _empty()

    versions = _safe_load(store.load_chapter_versions, {}) or {}
    commit_data = _safe_load(store.load_chapter_commit, None) or {}
    commits = commit_data.get("chapters") if isinstance(commit_data, dict) else {}
    proj_ts = _projection_ts(store)
    artifacts = _artifact_sets(store)

    items: list[dict[str, Any]] = []
    unknown: list[int] = []
    stale_by_view: dict[str, list[int]] = {k: [] for k in ARTIFACT_LABELS}

    for no in nos:
        arts = [k for k in ARTIFACT_LABELS if no in artifacts[k]]
        if not arts:
            # 该章没有任何可比对的派生记录：谈不上「过期」（缺口由 R2 覆盖体检负责）
            continue
        info = _change_info(store, no, versions)
        base_at, basis = _baseline(no, commits, proj_ts, info["ai_ts"])
        if base_at <= 0:
            unknown.append(no)
            continue
        if info["changed_at"] <= base_at:
            continue
        items.append({
            "chapter": no,
            "manual": bool(info["manual"]),
            "confidence": "approx" if basis == "version" else "exact",
            "basis": basis,
            "changed_at": info["changed_at"],
            "record_at": base_at,
            "artifacts": arts,
        })
        for view in arts:
            stale_by_view[view].append(no)

    stale = [it["chapter"] for it in items]
    notice = _build_notice(items, unknown if len(nos) == 1 else [], single=chapter_no is not None)
    return {
        "checked_chapters": len(nos),
        "has_stale": bool(stale),
        "stale_chapters": stale,
        "unknown_chapters": unknown,
        "items": items,
        "stale_by_view": stale_by_view,
        "artifact_labels": ARTIFACT_LABELS,
        "global_artifacts": GLOBAL_ARTIFACTS,
        "notice": notice,
    }


# ---------------------------------------------------------------- 文案（单一口径）

def _fmt_chapters(nos: list[int]) -> str:
    return "、".join(str(n) for n in nos)


def _affected_labels(items: list[dict]) -> list[str]:
    """按固定顺序去重，避免同一提示里出现两种记录顺序。"""
    present: set[str] = set()
    for it in items:
        present.update(it.get("artifacts") or [])
    return [label for key, label in ARTIFACT_LABELS.items() if key in present]


def _build_notice(items: list[dict], unknown: list[int], *, single: bool) -> Optional[dict]:
    if not items:
        if unknown:
            no = unknown[0]
            return {
                "level": "info",
                "title": "无法判断记录是否过期",
                "text": f"第 {no} 章没有可比对的记录写入时间（老数据），系统无法判断这些记录是否还对应当前正文。",
                "advice": "想确认的话：重新生成这一章，让记录与正文对齐（会产生正常的生成消耗）。",
            }
        return None

    nos = [it["chapter"] for it in items]
    manual = all(it.get("manual") for it in items)
    labels = _affected_labels(items)
    subject = f"第 {_fmt_chapters(nos)} 章"
    action = "你手动改过正文" if manual else "正文有改动（比如回退到旧版本）"
    text = f"{subject}{action}，{'、'.join(labels)}还是改之前的，据此得出的体检结论可能不准。"
    advice = "想消除提示：重新生成这几章（会产生正常的生成消耗）；系统不会在你保存正文时自动重算。"
    if len(unknown) > 1:
        advice += f"另有 {len(unknown)} 章没有可比对的时间基线，无法判断是否过期。"
    return {
        "level": "warning",
        "title": "记录可能过期" if single else f"有 {len(nos)} 章的记录可能过期",
        "text": text,
        "advice": advice,
        "global_note": "另外，全书级记录（章内状态、伏笔回收、读者认知）也可能受影响，但无法精确到章。",
    }
