# -*- coding: utf-8 -*-
"""创作阶段审阅门状态（REQ-创作阶段审阅门 M0）。

职责：
- `gates.json` 读写（原子写；缺失/损坏一律视为全 pending，不影响项目详情接口）；
- 审批绑产物版本：读取时与产物当前版本比对，不一致则本门与**全部下游门**回
  pending 并落盘留痕（本需求安全性地基，避免"改了上游却带着旧审批继续花钱"）；
- 门与 `lock.json` 语义分离：审批表示"用户看过了"，锁定表示"不许 AI 改"；
- 门 1 / 门 2 / 门 4 的判断材料（确定性体检，零 LLM、零额外计费）。

产物版本取 `VersionedProduct.selected_version()`；版本化关闭的存量项目回退为
产物文件内容指纹 —— 用户编辑（`commit=True, source="user"`）、重生成、切版本
三种情况都会让审批失效。
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from novel_gen.store.versioned import VersionedProduct

logger = logging.getLogger(__name__)

#: 门顺序（下游失效按此顺序级联）
GATES = ("outline", "world", "chapter_plan")

GATE_LABELS = {
    "outline": "大纲",
    "world": "世界观",
    "chapter_plan": "章纲对齐",
}

#: 门 → 绑定的产物类型（章纲不新造产物，绑大纲版本：章级事件就在 outline.json）
_PRODUCT_KIND = {"outline": "outline", "world": "world", "chapter_plan": "outline"}

#: 门的上游依赖（上游未通过，本门不可通过/自动失效）
_UPSTREAM = {"outline": (), "world": ("outline",), "chapter_plan": ("outline", "world")}

_STATUSES = ("pending", "approved", "skipped")

#: 章纲必需字段（缺一项即视为该章规划不完整，门 1 体检项）
PLAN_REQUIRED_FIELDS = (
    ("core_event", "核心事件"),
    ("conflict", "冲突设计"),
    ("character_moves", "人物动线"),
)

#: 质检合格线（门 4 批末卡片"合格率"口径；仅展示用，不驱动任何生成决策）
_QUALITY_PASS_SCORE = 60


# ---------- 存储 ----------

def gate_path(store) -> Path:
    """gates.json 路径（项目目录根，与 lock.json 同级、互不覆盖）。"""
    return store.dir / "gates.json"


def _pending() -> dict:
    return {"status": "pending", "approved_version": None, "approved_at": None,
            "by": None, "note": ""}


def load_raw(store) -> dict:
    """读取原始门记录（fail-open）：缺失/损坏/字段非法 → 该门按 pending 处理。"""
    try:
        data = store._read_json(gate_path(store), {})
    except Exception:
        logger.exception("gates.json 读取失败，按全 pending 降级: project=%s", store.dir.name)
        data = {}
    out: dict[str, dict] = {}
    for gate in GATES:
        rec = (data or {}).get(gate) if isinstance(data, dict) else None
        item = _pending()
        if isinstance(rec, dict):
            item.update({k: rec.get(k) for k in item if k in rec})
        if item["status"] not in _STATUSES:
            item = _pending()
        out[gate] = item
    return out


def _save(store, states: dict) -> None:
    """原子写入门记录（只落四要素，不落 current_version 等派生字段）。"""
    payload = {g: {k: (states[g] or {}).get(k) for k in _pending()} for g in GATES}
    try:
        store._write_json(gate_path(store), payload)
    except Exception:
        logger.exception("gates.json 写入失败: project=%s", store.dir.name)


# ---------- 版本比对 ----------

def _product_version(store, kind: str) -> Optional[str]:
    """产物当前版本标识：`v<n>`（版本化启用）或 `h<指纹>`（存量项目兜底）。"""
    try:
        version = VersionedProduct(store, kind).selected_version("all")
    except Exception:
        logger.exception("版本号读取失败，回退内容指纹: project=%s kind=%s",
                         store.dir.name, kind)
        version = None
    if isinstance(version, int) and version > 0:
        return f"v{version}"
    try:
        head = VersionedProduct(store, kind).head_path(store, "all")
        if head.exists():
            digest = hashlib.sha1(head.read_bytes()).hexdigest()[:10]
            return f"h{digest}"
    except Exception:
        logger.exception("产物指纹读取失败: project=%s kind=%s", store.dir.name, kind)
    return None


def current_versions(store) -> dict[str, Optional[str]]:
    """各门绑定产物的当前版本标识（章纲 = 大纲版本）。"""
    return {gate: _product_version(store, _PRODUCT_KIND[gate]) for gate in GATES}


# ---------- 状态计算（含失效级联） ----------

def compute(store) -> dict[str, dict]:
    """有效门状态：审批版本与当前版本不一致 / 上游未通过 → 本门回 pending 并落盘。

    返回每门：status / approved_version / approved_at / by / note / current_version。
    """
    raw = load_raw(store)
    versions = current_versions(store)
    states: dict[str, dict] = {}
    upstream_ok = True
    dirty = False
    for gate in GATES:
        rec, current = raw[gate], versions[gate]
        approved = rec["status"] in ("approved", "skipped")
        valid = bool(approved and rec.get("approved_version") == current and upstream_ok)
        if approved and not valid:
            logger.warning("审批失效转 pending: project=%s gate=%s approved=%s current=%s "
                           "upstream_ok=%s", store.dir.name, gate,
                           rec.get("approved_version"), current, upstream_ok)
            rec = _pending()
            dirty = True
        states[gate] = {**rec, "current_version": current}
        upstream_ok = upstream_ok and valid
    if dirty:
        _save(store, states)
    return states


def approve(store, gate: str, user_id: Optional[int], skip: bool = False,
            note: str = "") -> dict[str, dict]:
    """通过（或显式跳过）某道门，写入 approved_version = 当前产物版本。

    - 产物尚未生成 → 400；上游门未通过 → 409（门 2 也允许"跳过"但要显式传 skip）；
    - 只有 world 门允许 skip（"允许跳过门 2，但提示世界观缺失降低一致性"，§8#5）。
    """
    if gate not in GATES:
        raise HTTPException(status_code=400, detail=f"未知的门：{gate}")
    states = compute(store)
    current = states[gate]["current_version"]
    if current is None:
        raise HTTPException(status_code=400,
                            detail=f"「{GATE_LABELS[gate]}」产物尚未生成，无法通过")
    if skip and gate != "world":
        raise HTTPException(status_code=400, detail="只有世界观门允许跳过")
    for up in _UPSTREAM[gate]:
        if states[up]["status"] not in ("approved", "skipped"):
            raise HTTPException(status_code=409,
                                detail=f"请先通过「{GATE_LABELS[up]}」门，再进入「{GATE_LABELS[gate]}」")
    raw = load_raw(store)
    raw[gate] = {
        "status": "skipped" if skip else "approved",
        "approved_version": current,
        "approved_at": int(time.time()),
        "by": user_id,
        "note": note or ("已跳过（世界观缺失会降低后续一致性）" if skip else ""),
    }
    _save(store, {**states, **raw})
    logger.info("门通过: project=%s gate=%s version=%s skip=%s uid=%s",
                store.dir.name, gate, current, skip, user_id)
    return compute(store)


def guard_agent(store, agent: str) -> None:
    """生成入口门禁：世界观生成要求门 1 已通过（否则 409 明确提示）。

    存量项目豁免（重要）：门是本需求新加的，`gates.json` 从未存在且项目已经写过
    正文时，说明用户是在"旧的无门流程"下推进的老书——直接 409 会把老书的
    「重生成世界观」锁死。此时只放行 + 记 warning 留痕，门状态仍显示 pending
    （卡片照常让用户通过，新书/新流程仍是硬门禁）。
    """
    if agent != "architect.world":
        return
    states = compute(store)
    if states["outline"]["status"] == "approved":
        return
    if _is_legacy_project(store):
        logger.warning("存量项目（无 gates.json 且已有正文）跳过门 1 放行世界观生成: project=%s",
                       store.dir.name)
        return
    raise HTTPException(
        status_code=409,
        detail="请先审阅并在大纲门点击「通过，继续生成世界观」，避免未审大纲就花世界观的费用")


def _is_legacy_project(store) -> bool:
    """存量项目判定：从未生成过 gates.json，且已经写过正文。"""
    try:
        if gate_path(store).exists():
            return False
        return bool(store.list_chapters())
    except Exception:
        logger.exception("存量项目判定失败，按非存量处理: project=%s", store.dir.name)
        return False


# ---------- 判断材料（确定性体检） ----------

def outline_review(store) -> dict:
    """门 1 材料：三层摘要 + 章纲体检（只产出问题清单，不阻断）。"""
    outline = store.load_outline()
    chapters = list(getattr(outline, "chapters", []) or [])
    if not outline or not chapters:
        return {"gate": "outline", "summary": [], "total": 1, "checks": [
            {"level": "error", "title": "大纲为空", "detail": "还没有章节规划，无法审阅",
             "anchor": ""}]}
    cfg = store.load_config()
    per_ch = int(getattr(cfg, "chapter_words", 2500) or 2500)
    summary = [
        {"label": "章节", "value": f"{len(chapters)} 章"},
        {"label": "分卷", "value": f"{len(getattr(outline, 'volumes', []) or [])} 卷"},
        {"label": "预计正文", "value": f"{len(chapters) * per_ch} 字"},
    ]
    checks: list[dict] = []
    checks.extend(_missing_field_checks(chapters))
    checks.extend(_duplicate_title_checks(chapters))
    checks.extend(_numbering_checks(chapters))
    checks.extend(_cliffhanger_checks(chapters))
    return {"gate": "outline", "summary": summary, "checks": checks, "total": len(checks)}


def _missing_field_checks(chapters: list) -> list[dict]:
    """章纲字段完整度：按字段聚合，一条覆盖多章（默认卡片只展 3 条）。"""
    out: list[dict] = []
    for field_name, label in PLAN_REQUIRED_FIELDS:
        missing = [c.no for c in chapters if not str(getattr(c, field_name, "") or "").strip()]
        if not missing:
            continue
        out.append({
            "level": "warn",
            "title": f"{len(missing)} 章缺「{label}」",
            "detail": "、".join(f"第 {n} 章" for n in missing[:10])
                      + ("…" if len(missing) > 10 else ""),
            "anchor": f"chapter:{missing[0]}",
        })
    return out


def _duplicate_title_checks(chapters: list) -> list[dict]:
    seen: dict[str, list[int]] = {}
    for c in chapters:
        title = str(getattr(c, "title", "") or "").strip()
        if title:
            seen.setdefault(title, []).append(c.no)
    dups = [(_t, _nos) for _t, _nos in seen.items() if len(_nos) > 1]
    if not dups:
        return []
    title, nos = dups[0]
    return [{"level": "warn", "title": f"{len(dups)} 组章节重名",
             "detail": f"「{title}」重复于第 " + "、".join(str(n) for n in nos),
             "anchor": f"chapter:{nos[0]}"}]


def _numbering_checks(chapters: list) -> list[dict]:
    """章号连续性与分卷归属（大纲层结构体检）。"""
    out: list[dict] = []
    numbers = sorted(int(getattr(c, "no", 0) or 0) for c in chapters)
    gaps = [n for n in range(numbers[0], numbers[-1] + 1) if n not in set(numbers)]
    if gaps:
        out.append({"level": "warn", "title": f"{len(gaps)} 处章号断档",
                    "detail": "缺第 " + "、".join(str(n) for n in gaps[:10]) + " 章",
                    "anchor": f"chapter:{gaps[0]}"})
    unassigned = [c.no for c in chapters if not int(getattr(c, "volume_no", 0) or 0)]
    if unassigned:
        out.append({"level": "warn", "title": f"{len(unassigned)} 章未归属分卷",
                    "detail": "、".join(f"第 {n} 章" for n in unassigned[:10]),
                    "anchor": f"chapter:{unassigned[0]}"})
    return out


def _cliffhanger_checks(chapters: list) -> list[dict]:
    missing = [c.no for c in chapters if not str(getattr(c, "cliffhanger", "") or "").strip()]
    if not missing:
        return []
    return [{"level": "warn", "title": f"{len(missing)} 章未设计章末钩子",
             "detail": "、".join(f"第 {n} 章" for n in missing[:10]),
             "anchor": f"chapter:{missing[0]}"}]


def world_review(store) -> dict:
    """门 2 材料：世界观摘要 + 「受影响章纲清单」（人物档案未覆盖到的章）。"""
    world = store.load_world()
    outline = store.load_outline()
    if not world or not str(getattr(world, "world_view", "") or "").strip():
        return {"gate": "world", "summary": [], "total": 1, "checks": [
            {"level": "error", "title": "世界观为空",
             "detail": "世界观正文为空，建议重生成后再通过", "anchor": ""}]}
    characters = list(getattr(world, "characters", []) or [])
    summary = [
        {"label": "世界观", "value": f"{len(world.world_view)} 字"},
        {"label": "人物档案", "value": f"{len(characters)} 人"},
        {"label": "地点/势力", "value": f"{len(getattr(world, 'locations', []) or [])}/"
                                        f"{len(getattr(world, 'factions', []) or [])}"},
    ]
    checks: list[dict] = []
    if not characters:
        checks.append({"level": "warn", "title": "人物档案为空",
                       "detail": "世界观没有人物卡，写章时人物一致性缺少参照", "anchor": ""})
    else:
        checks.extend(_uncovered_chapter_checks(outline, characters))
    return {"gate": "world", "summary": summary, "checks": checks, "total": len(checks)}


def _uncovered_chapter_checks(outline, characters: list, limit: int = 60) -> list[dict]:
    """章纲人物动线/核心事件是否出现任一已建档人物（受影响章纲清单，可定位）。"""
    names = [str(getattr(c, "name", "") or "").strip() for c in characters]
    names = [n for n in names if n]
    if not names:
        return []
    chapters = list(getattr(outline, "chapters", []) or [])[:limit] if outline else []
    uncovered = []
    for ch in chapters:
        text = (str(getattr(ch, "character_moves", "") or "")
                + str(getattr(ch, "core_event", "") or "")
                + str(getattr(ch, "goal", "") or ""))
        if text and not any(name in text for name in names):
            uncovered.append(ch.no)
    if not uncovered:
        return []
    return [{"level": "warn",
             "title": f"{len(uncovered)} 章章纲未出现任何已建档人物",
             "detail": "受影响章：" + "、".join(f"第 {n} 章" for n in uncovered[:10])
                       + ("…" if len(uncovered) > 10 else ""),
             "anchor": f"chapter:{uncovered[0]}"}]


def batch_review(store, from_ch: int, to_ch: int) -> dict:
    """门 4 批末卡片：本批字数 / 质检合格率 / 本批扣费（按选中版本结算额反查）。"""
    from .settle_ledger import version_charge_map  # 延迟 import：避免 router 装配期循环

    chapters = {c.no: c for c in store.list_chapters()}
    versions = store.load_chapter_versions() or {}
    rows: list[dict] = []
    for no in range(int(from_ch), int(to_ch) + 1):
        ch = chapters.get(no)
        if ch is None:
            continue
        rec = versions.get(str(no)) or {}
        selected = rec.get("selected") or 0
        score = _selected_score(rec, selected)
        charged = (version_charge_map(store, "chapter", str(no)).get(selected) or {}).get(
            "charged", 0.0)
        rows.append({"no": no, "words": len(getattr(ch, "content", "") or ""),
                     "score": score, "charged": charged})
    passed = [r for r in rows if r["score"] is not None and r["score"] >= _QUALITY_PASS_SCORE]
    scored = [r for r in rows if r["score"] is not None]
    rate = (len(passed) / len(scored) * 100) if scored else None
    summary = [
        {"label": "本批章数", "value": f"{len(rows)} 章"},
        {"label": "本批字数", "value": f"{sum(r['words'] for r in rows)} 字"},
        {"label": "质检合格率", "value": "—" if rate is None else f"{rate:.0f}%"},
        {"label": "本批扣费", "value": f"¥{sum(r['charged'] for r in rows):.2f}"},
    ]
    failed = [r["no"] for r in rows if r["score"] is not None and r["score"] < _QUALITY_PASS_SCORE]
    checks = []
    if failed:
        checks.append({"level": "warn", "title": f"{len(failed)} 章质检未过合格线",
                       "detail": "、".join(f"第 {n} 章" for n in failed[:10]),
                       "anchor": f"chapter:{failed[0]}"})
    return {"gate": "batch", "summary": summary, "checks": checks, "total": len(checks)}


def _selected_score(rec: dict, selected: int) -> Optional[float]:
    for item in rec.get("history") or []:
        if isinstance(item, dict) and item.get("v") == selected:
            quality = item.get("quality")
            if isinstance(quality, dict) and isinstance(quality.get("score"), (int, float)):
                return float(quality["score"])
    return None


def review(store, gate: str, from_ch: int = 0, to_ch: int = 0) -> dict:
    """门材料分发（不支持的门 → 400）。"""
    if gate == "outline":
        return outline_review(store)
    if gate == "world":
        return world_review(store)
    if gate == "batch":
        if int(from_ch) < 1 or int(to_ch) < int(from_ch):
            raise HTTPException(status_code=400, detail="batch 材料需要合法的 from_ch/to_ch")
        return batch_review(store, from_ch, to_ch)
    raise HTTPException(status_code=400, detail=f"未知的门材料：{gate}")
