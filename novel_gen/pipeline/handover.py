# -*- coding: utf-8 -*-
"""交接工单（REQ-R30 M1）：编排层判定「该不该叫人」，不把判定写进 Prompt。

五类触发器（TECH-R30-M0M1 §6）：
- rule_hit      硬红线命中（大改 + 合规/设定冲突/规则冲突/自相矛盾）→ blocking
- low_confidence 无硬红线但中改/小改偏多或质检证据不足 → 稳定哈希抽检，抽中 blocking
- proposal      模型新增设定（只阻塞合并，不阻正文；由 ledger_proposal 产生）
- budget        余额耗尽 → blocking
- human_stop    人工暂停 → blocking

落盘：`handovers/<id>.json` + `handovers/index.json`（只追加），审计写
`ledger_audit.jsonl`。所有写盘原子；判定失败只降级留痕，不阻断生成。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TRIGGERS = ("rule_hit", "low_confidence", "proposal", "budget", "human_stop",
            "repair_exhausted")
SEVERITY = {"rule_hit": "high", "low_confidence": "mid", "proposal": "mid",
            "budget": "high", "human_stop": "mid", "repair_exhausted": "mid"}

# 硬红线关键词：与质检 issue 的 type 对齐（level=大改 才计入）
HARD_KEYWORDS = ("合规", "违禁", "设定冲突", "规则冲突", "自相矛盾", "事件重复")

OPEN = "open"
RESOLVED = "resolved"
CLOSED = "closed"
AUTO_PASSED = "auto_passed"

OPTIONS = ["approve", "reject", "revise", "abort", "repair"]


# ---------- 路径与审计 ----------

def handovers_dir(store) -> Path:
    return Path(store.dir) / "handovers"


def handover_path(store, hid: str) -> Path:
    return handovers_dir(store) / f"{hid}.json"


def index_path(store) -> Path:
    return handovers_dir(store) / "index.json"


def append_audit(store, row: dict) -> None:
    """追加审计（append-only JSONL）；失败仅日志，不影响主流程。"""
    try:
        path = Path(store.dir) / "ledger_audit.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), **row}, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("审计写入失败: project=%s row=%s", store.dir.name, row.get("kind"))


# ---------- 判定 ----------

def _norm_rule(value: str) -> str:
    return " ".join(str(value or "").replace("：", ":").split())


def _issue_level(issue: dict) -> str:
    return str((issue or {}).get("level") or "")


def _is_hard(issue: dict) -> bool:
    text = f"{issue.get('type', '')}{issue.get('check_type', '')}{issue.get('detail', '')}"
    return any(k in text for k in HARD_KEYWORDS)


def _sample_hit(task_id: str, chapter_no: int, rate: int) -> bool:
    """稳定哈希抽检（同一任务同一章结果恒定，测试可复现）。"""
    if rate <= 0:
        return False
    if rate >= 100:
        return True
    seed = f"{task_id}:{chapter_no}".encode("utf-8")
    return int(hashlib.md5(seed).hexdigest()[:8], 16) % 100 < rate


def _quote(issue: dict, chapter_no: int) -> dict:
    snippet = str(issue.get("detail") or issue.get("evidence") or "").strip()
    return {"ch": int(chapter_no), "offset": int(issue.get("offset") or 0),
            "snippet": snippet[:160]}


def _next_id(store) -> str:
    today = datetime.now().strftime("%Y%m%d")
    d = handovers_dir(store)
    seq = 1
    while handover_path(store, f"h-{today}-{seq:04d}").exists():
        seq += 1
    return f"h-{today}-{seq:04d}"


def create_handover(store, *, task_id: str, trigger: str, severity: str,
                    blocking: bool, evidence: dict, snapshot_ref: str = "",
                    dedupe_key: str, status: str = OPEN,
                    options: list | None = None) -> tuple[dict, bool]:
    """建工单（同 dedupe_key 且未关闭时复用，防止重复刷屏）。返回 (工单, 是否新建)。"""
    if trigger not in TRIGGERS:
        raise ValueError(f"未知触发器: {trigger}")
    existing = _open_by_key(store, dedupe_key)
    if existing is not None:
        return existing, False
    now = time.time()
    data = {
        "handover_id": _next_id(store),
        "task_id": str(task_id),
        "trigger": trigger,
        "severity": severity,
        "blocking": bool(blocking),
        "evidence": dict(evidence or {}),
        "snapshot_ref": str(snapshot_ref or ""),
        "options": list(options or OPTIONS),
        "status": status,
        "dedupe_key": str(dedupe_key),
        "created_at": now,
        "resolved_at": None,
        "resolved_by": None,
        "resolution": None,
        "reason": "",
    }
    try:
        store._write_json(handover_path(store, data["handover_id"]), data)
        _index_upsert(store, data)
        append_audit(store, {"kind": "handover_create", "handover_id": data["handover_id"],
                             "task_id": task_id, "trigger": trigger,
                             "severity": severity, "blocking": bool(blocking),
                             "dedupe_key": dedupe_key, "status": status})
        return data, True
    except Exception:
        logger.exception("工单落盘失败（降级）: project=%s trigger=%s", store.dir.name, trigger)
        return data, False


def _open_by_key(store, dedupe_key: str) -> dict | None:
    for row in list_handovers(store, status="", limit=500):
        if row.get("dedupe_key") == dedupe_key and row.get("status") in (OPEN, AUTO_PASSED):
            return row
    return None


def evaluate_after_chapter(store, cfg, task_id: str, chapter_no: int,
                           snapshot_ref: str = "") -> list[dict]:
    """章后判定（rule_hit / low_confidence）。返回本轮新建/命中的工单列表。"""
    if not bool(getattr(cfg, "handover_v1", False)):
        return []
    try:
        review = next((r for r in store.load_review()
                       if isinstance(r, dict) and int(r.get("chapter") or 0) == int(chapter_no)),
                      None)
    except Exception:
        logger.exception("review 读取失败（交接判定跳过）: project=%s ch=%s",
                         store.dir.name, chapter_no)
        return []
    if not review:
        return []
    issues = [i for i in (review.get("issues") or []) if isinstance(i, dict)]
    qc = review.get("quality_check") or {}
    out: list[dict] = []

    hard = [i for i in issues if _issue_level(i) == "大改" and _is_hard(i)]
    if hard or (bool(qc.get("evidence_insufficient")) and
                any(_issue_level(i) == "大改" for i in issues)):
        first = hard[0] if hard else {"type": "质检证据不足", "detail": "关键评委失败，证据不足"}
        rule = _norm_rule(first.get("type") or first.get("check_type") or "hard_rule")
        h, _ = create_handover(
            store, task_id=task_id, trigger="rule_hit", severity="high", blocking=True,
            evidence={"issue_ids": [f"ch{chapter_no}:{i}" for i in range(len(issues))],
                      "rule": rule, "quote": _quote(first, chapter_no),
                      "issues": issues[:5]},
            snapshot_ref=snapshot_ref, dedupe_key=f"ch:{chapter_no}:rule:{rule}")
        out.append(h)
        return out

    threshold = int(getattr(cfg, "handover_low_confidence_count", 3) or 0)
    soft = [i for i in issues if _issue_level(i) in ("中改", "小改")]
    low = (threshold > 0 and len(soft) >= threshold) or bool(qc.get("evidence_insufficient"))
    if low:
        rate = int(getattr(cfg, "handover_sample_rate", 20) or 0)
        blocking = _sample_hit(task_id, chapter_no, rate)
        rule = "low_confidence"
        h, _ = create_handover(
            store, task_id=task_id, trigger="low_confidence", severity="mid",
            blocking=blocking,
            evidence={"issue_ids": [f"ch{chapter_no}:{i}" for i in range(len(issues))],
                      "rule": rule,
                      "quote": _quote(soft[0] if soft else {}, chapter_no),
                      "issue_count": len(soft),
                      "sample_rate": rate,
                      "sampled": blocking},
            snapshot_ref=snapshot_ref,
            dedupe_key=f"ch:{chapter_no}:rule:{rule}",
            status=OPEN if blocking else AUTO_PASSED)
        out.append(h)
    return out


def create_budget_handover(store, task_id: str, chapter_no: int,
                           snapshot_ref: str = "") -> dict:
    h, _ = create_handover(
        store, task_id=task_id, trigger="budget", severity="high", blocking=True,
        evidence={"issue_ids": [], "rule": "budget_exhausted",
                  "quote": {"ch": int(chapter_no), "offset": 0,
                            "snippet": "余额已耗尽，需充值后放行"}},
        snapshot_ref=snapshot_ref, dedupe_key=f"budget:task:{task_id}")
    return h


def create_human_stop_handover(store, task_id: str, chapter_no: int,
                               snapshot_ref: str = "") -> dict:
    h, _ = create_handover(
        store, task_id=task_id, trigger="human_stop", severity="mid", blocking=True,
        evidence={"issue_ids": [], "rule": "human_pause",
                  "quote": {"ch": int(chapter_no), "offset": 0,
                            "snippet": "人工暂停，任务停在交接点"}},
        snapshot_ref=snapshot_ref, dedupe_key=f"human_stop:{task_id}:{chapter_no}")
    return h


def create_repair_exhausted_handover(store, task_id: str, chapter_no: int,
                                     reason: str = "", snapshot_ref: str = "") -> dict:
    """修复预算耗尽（REQ-R32 F5）：同章修复达上限仍不通过 → blocking 转人工。"""
    h, _ = create_handover(
        store, task_id=task_id, trigger="repair_exhausted", severity="high", blocking=True,
        evidence={"issue_ids": [], "rule": "repair_exhausted",
                  "quote": {"ch": int(chapter_no), "offset": 0,
                            "snippet": reason or "自动修复达上限，需人工处理"}},
        snapshot_ref=snapshot_ref,
        dedupe_key=f"repair_exhausted:{task_id}:{chapter_no}")
    return h


# ---------- 读取 / 处理 ----------

def _index_rows(store) -> list[dict]:
    path = index_path(store)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except Exception:
        logger.warning("handovers/index.json 损坏，按目录重建: project=%s", store.dir.name,
                       exc_info=True)
        return []


def _index_upsert(store, data: dict) -> None:
    rows = _index_rows(store)
    row = {k: data.get(k) for k in
           ("handover_id", "task_id", "trigger", "severity", "blocking",
            "status", "dedupe_key", "created_at")}
    rows = [r for r in rows if r.get("handover_id") != row["handover_id"]]
    rows.append(row)
    store._write_json(index_path(store), rows)


def _load_handover(store, hid: str) -> dict | None:
    path = handover_path(store, hid)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("工单读取失败: project=%s id=%s", store.dir.name, hid, exc_info=True)
        return None


def list_handovers(store, *, status: str = OPEN, severity: str = "",
                   limit: int = 50, offset: int = 0) -> list[dict]:
    """只读列表（零写盘、零 LLM）；index 缺失时按目录重建。"""
    rows = _index_rows(store)
    if not rows:
        rows = []
        d = handovers_dir(store)
        if d.is_dir():
            for p in sorted(d.glob("h-*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(data, dict):
                    rows.append({k: data.get(k) for k in
                                 ("handover_id", "task_id", "trigger", "severity",
                                  "blocking", "status", "dedupe_key", "created_at")})
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if severity:
        rows = [r for r in rows if r.get("severity") == severity]
    rows.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    start = max(0, int(offset))
    end = start + max(1, min(200, int(limit or 50)))
    out: list[dict] = []
    for row in rows[start:end]:
        full = _load_handover(store, str(row.get("handover_id") or ""))
        out.append(full or row)
    return out


def pending_count(store, *, blocking_only: bool = False) -> int:
    rows = [r for r in list_handovers(store, status=OPEN, limit=200)]
    if blocking_only:
        rows = [r for r in rows if r.get("blocking")]
    return len(rows)


def pending_blocking_for_task(store, task_id: str) -> list[dict]:
    return [h for h in list_handovers(store, status=OPEN, limit=200)
            if h.get("task_id") == task_id and h.get("blocking")]


def resolve(store, hid: str, action: str, *, by: str = "", reason: str = "") -> dict:
    """处理工单：approve/reject/revise/abort → resolved/closed；写审计。"""
    action = str(action or "").strip()
    if action not in OPTIONS:
        raise ValueError(f"未知处理动作: {action}")
    data = _load_handover(store, hid)
    if data is None:
        raise KeyError(f"工单不存在: {hid}")
    if data.get("status") not in (OPEN, AUTO_PASSED):
        raise ValueError(f"工单已处理: {data.get('status')}")
    now = time.time()
    data["status"] = RESOLVED if action in ("approve", "revise", "repair") else CLOSED
    data["resolution"] = action
    data["resolved_at"] = now
    data["resolved_by"] = str(by or "")
    data["reason"] = str(reason or "")
    store._write_json(handover_path(store, hid), data)
    _index_upsert(store, data)
    append_audit(store, {"kind": "handover_resolve", "handover_id": hid,
                         "task_id": data.get("task_id"), "action": action,
                         "by": str(by or ""), "reason": str(reason or ""),
                         "status": data["status"]})
    return data
