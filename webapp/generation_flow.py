# -*- coding: utf-8 -*-
"""REQ-R27 分步确认式生成流：阶段编排层（唯一状态源 `generation_stage.json`）。

为什么单独一层而不改生成内核：现有 `agent-runs` 已经把每个阶段做成独立 run、独立结算，
缺的只是「阶段间的可持久化暂停点」。本模块只做这件事——记录阶段状态、在用户确认时
调用同一个 `generate_service.start_agent_run` 起下一阶段，不新增第二条生成通路。

约束（见 TECH-R27 v1.2）：
1. 编排状态只写 `generation_stage.json`（项目根、原子写）；`task.json` 只表示当前 run。
2. 起 run 前失败（402/409/429）不改阶段状态、不写产物、不扣费。
3. 恢复按 `agent_runs/<run_id>.json` 侧写 + 产物 digest 对账，绝不因「run 不存在」判失败。
4. 唯一主链路钩子在 `_run_agent_task` 的 finally 末尾（`on_run_finished`），异常只留日志。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from novel_gen.store.run_context import read_run_file

from .logger import get_logger

logger = get_logger("generation_flow")

FLOW_FILE = "generation_stage.json"
FLOW_VERSION = 1

STAGE_KEYS = ("research", "outline", "world", "volume", "chapters")
STAGE_AGENT = {
    "research": "researcher",
    "outline": "architect.outline",
    "world": "architect.world",
    "volume": "architect.volume",
    "chapters": "writer.chapter",
}
AGENT_STAGE = {v: k for k, v in STAGE_AGENT.items()}
STAGE_LABELS = {
    "research": "题材调研",
    "outline": "大纲",
    "world": "人物世界观",
    "volume": "卷纲",
    "chapters": "正文",
}
#: 中间阶段在 run 成功后进入 done（等确认）；chapters 成功后直接 confirmed（终态）
_MIDDLE_STAGES = ("research", "outline", "world", "volume")

_MAX_IDEMPOTENCY = 50
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

# 进程内项目级写锁：覆盖「校验状态 → 变更 → 起 run」全段（第一道防线是 _acquire_slot）
_FLOW_LOCKS: dict[str, threading.RLock] = {}
_FLOW_LOCKS_GUARD = threading.Lock()


def _lock(store) -> threading.RLock:
    key = str(store.dir)
    with _FLOW_LOCKS_GUARD:
        lk = _FLOW_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _FLOW_LOCKS[key] = lk
        return lk


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def flow_path(store) -> Path:
    return Path(store.dir) / FLOW_FILE


# ---------- 读写 ----------

def read_flow(store) -> tuple[dict | None, bool]:
    """读流程状态。返回 (flow, corrupt)；损坏时 flow=None 且 corrupt=True（允许重建）。"""
    p = flow_path(store)
    if not p.exists():
        return None, False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("stages"), list):
            raise ValueError("结构非法")
        return data, False
    except Exception:
        logger.exception("generation_stage.json 损坏: project=%s", store.dir.name)
        return None, True


def _save(store, flow: dict) -> None:
    flow["updated_at"] = _now()
    try:
        store._write_json(flow_path(store), flow)
    except Exception:
        logger.exception("generation_stage.json 写入失败: project=%s", store.dir.name)


def _stage(flow: dict, key: str) -> dict | None:
    for item in flow.get("stages") or []:
        if item.get("key") == key:
            return item
    return None


def _current(flow: dict) -> dict | None:
    """当前阶段 = 第一个未到终态的阶段（confirmed/skipped 视为完成）。"""
    for item in flow.get("stages") or []:
        if item.get("status") not in ("confirmed", "skipped"):
            return item
    return None


# ---------- 模式 ----------

def supported(store, cfg=None) -> bool:
    """本期仅对 NovelConfig.mode == "auto" 生效；expand 项目不支持分步流。"""
    cfg = cfg or store.load_config()
    return str(getattr(cfg, "mode", "auto")) != "expand"


def _is_legacy(store) -> bool:
    """老项目判定：存在历史 run 侧写或已写章节（决定默认模式，不改任何数据）。"""
    runs = Path(store.dir) / "agent_runs"
    try:
        if runs.is_dir() and any(p.suffix == ".json" for p in runs.iterdir()):
            return True
    except OSError:
        pass
    try:
        return bool(store.list_chapters())
    except Exception:
        logger.exception("老项目判定失败，按新项目处理: project=%s", store.dir.name)
        return False


def resolve_mode(store, cfg=None) -> tuple[str, str]:
    """模式优先级：项目显式设置 > 老项目默认 auto > 新项目默认 stepwise。"""
    cfg = cfg or store.load_config()
    explicit = str(getattr(cfg, "generation_mode", "") or "")
    if explicit in ("auto", "stepwise"):
        return explicit, "project"
    return ("auto", "default_old") if _is_legacy(store) else ("stepwise", "default_new")


def _write_mode(store, mode: str) -> None:
    """只改 config.json 的 generation_mode，整文件回写其余字段（不重建 NovelConfig）。"""
    path = store.config_path()
    data = store._read_json(path, {}) or {}
    data["generation_mode"] = mode
    store._write_json(path, data)


def _assert_supported(store, cfg=None) -> None:
    if not supported(store, cfg):
        raise HTTPException(status_code=400, detail={
            "code": "MODE_UNSUPPORTED", "message": "扩写项目不支持分步确认式生成流"})


# ---------- digest ----------

def _sha1(obj) -> str:
    text = json.dumps(obj or {}, ensure_ascii=False, sort_keys=True)
    return "sha1:" + hashlib.sha1(text.encode("utf-8")).hexdigest()


def compute_digest(store, key: str) -> str | None:
    """阶段产物 digest：中间阶段为产物 canonical JSON 的 sha1；正文为「分卷+章计划摘要」。

    只读产物文件，不读正文（毫秒级），与 GET /flow 的回写口径一致。
    """
    try:
        if key == "research":
            data = store._read_json(store.research_path(), None)
            return _sha1({"research": data}) if data else None
        if key == "world":
            data = store._read_json(store.world_path(), None)
            return _sha1({"world": data}) if data else None
        if key in ("outline", "volume", "chapters"):
            outline = store._read_json(store.outline_path(), None)
            if not outline:
                return None
            if key == "outline":
                return _sha1({"outline": outline})
            volumes = outline.get("volumes") or []
            if key == "volume":
                return _sha1({"volumes": volumes})
            fields = ("no", "title", "goal", "volume_no", "core_event", "conflict",
                      "character_moves", "cliffhanger")
            plans = [{f: (ch or {}).get(f) for f in fields}
                     for ch in (outline.get("chapters") or [])]
            return _sha1({"volumes": volumes, "chapters": plans})
    except Exception:
        logger.exception("产物 digest 计算失败（降级为空）: project=%s stage=%s",
                         store.dir.name, key)
    return None


# ---------- 初始化 ----------

def _initial_stages(store, cfg) -> list[dict]:
    """按配置与既有产物生成初始阶段清单；已有产物视为已确认，避免重复花钱。"""
    research_done = bool(store._read_json(store.research_path(), None))
    outline_done = bool(store._read_json(store.outline_path(), None))
    world_done = bool(store._read_json(store.world_path(), None))
    chapters_done = bool(store.list_chapters())

    stages: list[dict] = []
    for key in STAGE_KEYS:
        item = {"key": key, "status": "pending", "run_id": "", "task_id": "",
                "digest": "", "edited_at": None, "last_error": {}}
        if key == "volume":
            # 卷纲已并入大纲一次产出，本期不单独自动跑（REQ v0.3 §5.5）
            item.update(status="skipped", reason="merged_into_outline")
        stages.append(item)

    # 已有产物 → confirmed（含向上推断：大纲存在即认为调研已过）
    produced = {
        "research": research_done or outline_done,
        "outline": outline_done,
        "world": world_done,
        "chapters": chapters_done,
    }
    for item in stages:
        key = item["key"]
        if item["status"] != "pending" or not produced.get(key):
            continue
        dg = compute_digest(store, key)
        if dg:
            item.update(status="confirmed", digest=dg, confirmed_at=_now(),
                        confirmed_by="existing_product")
    # 调研未开启：显式 skipped，流程从大纲开始
    if not bool(getattr(cfg, "with_research", False)):
        st = _stage({"stages": stages}, "research")
        if st and st["status"] == "pending":
            st.update(status="skipped", reason="with_research_false")
    return stages


def _new_flow(store, mode: str, source: str, cfg) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return {
        "version": FLOW_VERSION,
        "flow_id": f"gf_{stamp}_{hashlib.sha1(str(store.dir).encode()).hexdigest()[:4]}",
        "mode": mode,
        "mode_source": source,
        "current": "",
        "stages": _initial_stages(store, cfg),
        "idempotency": {},
        "created_at": _now(),
        "updated_at": _now(),
    }


# ---------- 运行中判定与对账 ----------

def _active_run(store) -> bool:
    from . import core  # 延迟 import：避免 core ↔ flow 循环引用

    handle = core._RUNS.get(store.dir.name)
    if handle is not None and handle.thread is not None and handle.thread.is_alive():
        return True
    return (Path(store.dir) / "task.json").exists()


def _latest_run_for(store, agent: str) -> dict | None:
    runs_dir = Path(store.dir) / "agent_runs"
    if not runs_dir.is_dir():
        return None
    files = sorted((p for p in runs_dir.glob("*.json") if not p.name.endswith(".tmp")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        data = read_run_file(store.dir, p.stem)
        if data and data.get("agent") == agent:
            return data
    return None


def _apply_run_status(st: dict, run: dict) -> None:
    """run 侧写 → 阶段状态。ok 但 error 非空（如余额耗尽中途停）按 stopped 处理。"""
    status = str(run.get("status") or "failed")
    st["finished_at"] = _now()
    if status == "ok":
        if str(run.get("error") or "").strip():
            st["status"] = "stopped"
            st["last_error"] = {"code": "partial", "message": str(run.get("error"))}
        else:
            st["status"] = "confirmed" if st["key"] == "chapters" else "done"
    elif status == "stopped":
        st["status"] = "stopped"
    else:
        st["status"] = "failed"
        st["last_error"] = {"code": "run_failed",
                            "message": str(run.get("error") or "生成失败，可重试（再次计费）")}


def _reconcile(store, flow: dict) -> bool:
    """把 running 阶段按 run 侧写收敛（重启/关页恢复的唯一入口）。返回是否有变更。"""
    changed = False
    task = store._read_json(Path(store.dir) / "task.json", {}) or {}
    for st in flow.get("stages") or []:
        if st.get("status") != "running":
            continue
        run_id = str(st.get("run_id") or "")
        run = read_run_file(store.dir, run_id) if run_id else None
        if run:
            _apply_run_status(st, run)
            changed = True
            continue
        if task.get("kind") == "agent_run" and (not run_id or task.get("run_id") == run_id):
            continue  # 线程仍在跑
        latest = _latest_run_for(store, STAGE_AGENT[st["key"]])
        if latest and (not run_id or latest.get("run_id") == run_id
                       or float(latest.get("started_at") or 0) >= float(st.get("started_ts") or 0)):
            st["run_id"] = str(latest.get("run_id") or "")
            st["task_id"] = str(latest.get("task_id") or "")
            _apply_run_status(st, latest)
        else:
            st["status"] = "failed"
            st["last_error"] = {"code": "interrupted",
                                "message": "上次执行中断，可重试（再次计费）"}
        changed = True
    return changed


def _refresh_digests(store, flow: dict) -> bool:
    """GET 时重算已完成阶段产物 digest；变化即回写 edited_at（编辑后确认的依据）。"""
    changed = False
    for st in flow.get("stages") or []:
        if st.get("status") not in ("done", "confirmed"):
            continue
        dg = compute_digest(store, st["key"])
        if dg and dg != st.get("digest"):
            st["digest"] = dg
            st["edited_at"] = _now()
            changed = True
    return changed


# ---------- 价格提示 ----------

def _price_hint(store, key: str) -> float:
    """只读预估价（BYOK 恒 0；失败降级为 0，不影响卡片渲染）。"""
    try:
        from . import db, generate_service

        uid = db.project_owner(store.dir.name)
        mode = generate_service.resolve_billing_mode(uid)
        agent = STAGE_AGENT[key]
        params = {"scope": "all"} if key == "chapters" else (
            {"vol_no": 1} if key == "volume" else {})
        if key == "volume":
            return 0.0
        return float(generate_service._estimated_price(agent, params, store, mode))
    except Exception:
        logger.exception("阶段预估价计算失败（降级为 0）: project=%s stage=%s",
                         store.dir.name, key)
        return 0.0


# ---------- 确认卡 ----------

def _clip(text, limit: int = 90) -> str:
    s = str(text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def _build_card(store, st: dict, cfg) -> dict | None:
    """后端从产物只读生成卡片摘要；失败时 lines=[] + error，卡片仍可操作。"""
    key, status = st["key"], st["status"]
    if status not in ("done", "ready", "failed", "stopped", "skipped", "confirmed"):
        return None
    lines: list[str] = []
    error = ""
    try:
        if key == "research" and status in ("done", "confirmed", "skipped"):
            report = store.load_research()
            if report is not None:
                lines = [f"题材：{report.genre or cfg.genre}",
                         f"共性：{_clip(report.summary, 100)}"]
                lines += [f"流行方向：{_clip(x, 50)}" for x in (report.trends or [])[:3]]
                lines += [f"可混合文风：{_clip(x, 50)}" for x in (report.styles or [])[:3]]
        elif key == "outline" and status in ("done", "confirmed"):
            outline = store.load_outline()
            if outline is not None:
                lines = [f"一句话卖点：{_clip(outline.intro, 100)}",
                         f"主线：{_clip(outline.main_line or outline.core_conflict, 100)}",
                         f"结构：{len(outline.chapters)} 章 / {len(outline.volumes)} 卷"]
                for v in (outline.volumes or [])[:3]:
                    lines.append(f"{v.name or ('第%s卷' % v.no)}：{_clip(v.intro, 50)}")
        elif key == "world" and status in ("done", "confirmed"):
            world = store.load_world()
            if world is not None:
                names = [c.name for c in (world.characters or []) if getattr(c, "name", "")]
                lines = [f"世界观：{_clip(world.world_view, 100)}",
                         f"人物卡：{len(names)} 人" + (f"（{'、'.join(names[:5])}）" if names else ""),
                         f"机制类型：{'、'.join(world.mechanism_types or []) or '未声明'}"]
                lines += [f"核心设定：{_clip(x, 60)}" for x in (world.locked_settings or [])[:3]]
        elif key == "volume":
            lines = ["卷纲已并入大纲一次生成（大纲阶段已产出分卷与逐章计划）"]
        elif key == "chapters" and status in ("ready",):
            outline = store.load_outline()
            total = len(outline.chapters) if outline else 0
            words = int(getattr(cfg, "chapter_words", 2500) or 2500)
            done = len(store.list_chapters())
            lines = [f"计划：全书 {total} 章，待写 {max(0, total - done)} 章",
                     f"目标字数：约 {total * words} 字",
                     f"预估费用：¥{_price_hint(store, 'chapters'):.2f}（按实际生成字数结算）"]
    except Exception as exc:  # noqa: BLE001 — 摘要失败不能挡住确认
        logger.exception("确认卡摘要生成失败: project=%s stage=%s", store.dir.name, key)
        lines, error = [], f"{type(exc).__name__}: 摘要暂不可用"
    actions = _card_actions(key, status)
    card = {"stage": key, "title": STAGE_LABELS[key], "status": status,
            "lines": lines, "actions": actions, "digest": st.get("digest") or "",
            "edited": bool(st.get("edited_at")), "error": error,
            "reason": st.get("reason") or "",
            "message": (st.get("last_error") or {}).get("message", "")}
    if actions and "regenerate" in actions and key != "chapters":
        card["regenerate_estimate"] = _price_hint(store, key)
    return card


def _card_actions(key: str, status: str) -> list[str]:
    if key == "chapters":
        if status == "ready":
            return ["continue", "back_outline"]
        if status in ("failed", "stopped"):
            return ["continue"]
        return []
    if status == "ready":
        return ["continue", "back_outline"] if key == "chapters" else ["continue"]
    if status == "done":
        return ["continue", "edit", "regenerate"] if key in ("outline", "world") \
            else ["continue", "regenerate"]
    if status == "skipped" and key == "volume":
        return []
    if status in ("failed", "stopped"):
        return ["regenerate"]
    return []


# ---------- 对外的读取 ----------

def _public_flow(store, flow: dict, cfg, with_card: bool) -> dict:
    stages = []
    for st in flow.get("stages") or []:
        stages.append({
            "key": st["key"], "label": STAGE_LABELS[st["key"]],
            "status": st.get("status"), "reason": st.get("reason") or "",
            "digest": st.get("digest") or "", "edited": bool(st.get("edited_at")),
            "run_id": st.get("run_id") or "", "task_id": st.get("task_id") or "",
            "started_at": st.get("started_at"), "finished_at": st.get("finished_at"),
            "last_error": st.get("last_error") or {},
        })
    cur = _current(flow)
    flow["current"] = cur["key"] if cur else ""
    cards = []
    if with_card and cur:
        card = _build_card(store, cur, cfg)
        if card:
            cards.append(card)
    return {"flow_id": flow.get("flow_id"), "mode": flow.get("mode"),
            "mode_source": flow.get("mode_source"), "current": flow.get("current", ""),
            "stages": stages, "cards": cards, "updated_at": flow.get("updated_at"),
            "created_at": flow.get("created_at")}


def get_flow(store, with_card: bool = True) -> dict:
    """GET /generation/flow：只读 + 对账，不主动起 run。"""
    cfg = store.load_config()
    if not supported(store, cfg):
        return {"supported": False, "mode": None, "mode_source": "unsupported", "flow": None}
    mode, source = resolve_mode(store, cfg)
    with _lock(store):
        flow, corrupt = read_flow(store)
        if corrupt:
            return {"supported": True, "mode": mode, "mode_source": source,
                    "flow": None, "corrupt": True}
        if not flow:
            return {"supported": True, "mode": mode, "mode_source": source, "flow": None}
        changed = _reconcile(store, flow)
        changed = _refresh_digests(store, flow) or changed
        if changed:
            _save(store, flow)
    return {"supported": True, "mode": mode, "mode_source": source,
            "flow": _public_flow(store, flow, cfg, with_card)}


# ---------- 起 run ----------

def _launch(store, flow: dict, key: str, user: dict, params: dict | None = None) -> None:
    """起某阶段 run；失败回滚阶段状态并原样抛出（402/409/429 不产生任何副作用）。"""
    from . import generate_service

    st = _stage(flow, key)
    prev = {"status": st.get("status"), "last_error": st.get("last_error") or {}}
    st.update(status="running", started_at=_now(), started_ts=time.time(),
              finished_at=None, last_error={}, reason="")
    _save(store, flow)  # 先落 running 再起线程：run 结束钩子不会读到旧状态
    req = generate_service.AgentRunReq(agent=STAGE_AGENT[key], params=params or {})
    try:
        body = generate_service.start_agent_run(store, req, user or {})
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        st.update(status=_rollback_status(prev.get("status"), key),
                  last_error={"code": detail.get("code") or f"http_{exc.status_code}",
                              **detail})
        _save(store, flow)  # 回滚也要落盘：否则磁盘停在 running，刷新后误显示在跑
        raise
    except Exception as exc:
        st.update(status=_rollback_status(prev.get("status"), key),
                  last_error={"code": "start_failed", "message": f"{type(exc).__name__}: {exc}"})
        _save(store, flow)
        logger.exception("阶段 run 启动失败: project=%s stage=%s", store.dir.name, key)
        raise
    st["run_id"] = str(body.get("run_id") or "")
    st["task_id"] = str(body.get("task_id") or "")


def _rollback_status(prev: str | None, key: str) -> str:
    """起 run 失败后的阶段状态：重生成/重试失败保留原状态；首次启动失败落 ready 供重试。"""
    if prev in ("done", "confirmed", "failed", "stopped", "ready"):
        return prev
    return "ready"


def _next_key(key: str) -> str | None:
    idx = STAGE_KEYS.index(key)
    for nxt in STAGE_KEYS[idx + 1:]:
        return nxt
    return None


def _approve_gate(store, key: str, user: dict) -> None:
    """分步确认即「用户看过」：放行对应审阅门。门不满足条件时原样 409，不吞。"""
    from . import gates

    gate = {"outline": "outline", "world": "world"}.get(key)
    if not gate:
        return
    states = gates.compute(store)
    if states.get(gate, {}).get("status") in ("approved", "skipped"):
        return
    uid = user.get("id") if isinstance(user, dict) else None
    gates.approve(store, gate, uid, note="R27 分步确认放行")


def _continue_locked(store, flow: dict, key: str, user: dict) -> dict:
    """确认当前阶段并放行下一阶段（调用方已持锁）。"""
    st = _stage(flow, key)
    if st.get("status") == "ready":
        # 首次启动失败后的重试：本阶段还没有产物，直接补起 run，不做「确认通过」
        _launch(store, flow, key, user, {"scope": "all"} if key == "chapters" else None)
        return {"stage": key, "action": "continue", "stage_status": "running",
                "run_id": st.get("run_id", ""), "task_id": st.get("task_id", "")}
    if key == "chapters":
        # ready → 起正文 run（scope=all，等价既有「一键全部」但先出卡与预估）
        _launch(store, flow, "chapters", user, {"scope": "all"})
        return {"stage": key, "action": "continue", "status": st["status"],
                "run_id": st.get("run_id", ""), "task_id": st.get("task_id", "")}

    _approve_gate(store, key, user)  # 门不通过 → HTTPException 原样上抛
    st.update(status="confirmed", confirmed_at=_now(),
              confirmed_by=str((user or {}).get("id") or ""))
    nxt = _next_key(key)
    while nxt:
        nst = _stage(flow, nxt)
        if nst.get("status") == "skipped":
            nxt = _next_key(nxt)
            continue
        if nxt == "chapters" and flow.get("mode") == "stepwise":
            # 正文阶段开跑前确认：只置 ready，不起 run
            nst.update(status="ready")
            break
        _launch(store, flow, nxt, user,
                {"scope": "all"} if nxt == "chapters" else None)
        break
    return {"stage": key, "action": "continue", "stage_status": "confirmed",
            "next": nxt or ""}


def _record_idempotent(flow: dict, key: str, result: dict) -> None:
    cache = flow.setdefault("idempotency", {})
    cache[key] = {"ts": time.time(), "result": result}
    if len(cache) > _MAX_IDEMPOTENCY:
        for old in sorted(cache, key=lambda k: cache[k].get("ts") or 0)[:len(cache) - _MAX_IDEMPOTENCY]:
            cache.pop(old, None)


def start_flow(store, user: dict, mode: str | None = None) -> dict:
    """POST /flow/start：建流程并起首阶段（已存在流程 → 幂等返回现状）。"""
    cfg = store.load_config()
    _assert_supported(store, cfg)
    resolved, source = resolve_mode(store, cfg)
    if mode in ("auto", "stepwise"):
        _write_mode(store, mode)
        resolved, source = mode, "project"
    with _lock(store):
        flow, corrupt = read_flow(store)
        if flow and not corrupt:
            return get_flow(store, with_card=True)  # 双击幂等
        if _active_run(store):
            raise HTTPException(status_code=409, detail={
                "code": "STAGE_RUNNING", "message": "已有生成任务在运行，请等待结束后再启动分步流程"})
        flow = _new_flow(store, resolved, source, cfg)
        st = _current(flow)
        if st is None:
            _save(store, flow)
            return get_flow(store, with_card=True)
        if st["key"] == "chapters" and flow["mode"] == "stepwise":
            st.update(status="ready")
            _save(store, flow)
            return get_flow(store, with_card=True)
        _save(store, flow)
        _launch(store, flow, st["key"], user,
                {"scope": "all"} if st["key"] == "chapters" else None)
        _save(store, flow)
    return get_flow(store, with_card=True)


def confirm(store, user: dict, stage_key: str, action: str, digest: str,
            client_token: str = "", force: bool = False) -> dict:
    """POST /flow/confirm：continue / regenerate / skip / back_outline，带 digest 与幂等键。"""
    cfg = store.load_config()
    _assert_supported(store, cfg)
    if stage_key not in STAGE_KEYS:
        raise HTTPException(status_code=400, detail={"code": "BAD_STAGE", "message": "未知阶段"})
    if action not in ("continue", "regenerate", "skip", "back_outline"):
        raise HTTPException(status_code=400, detail={"code": "BAD_ACTION", "message": "未知动作"})
    with _lock(store):
        flow, corrupt = read_flow(store)
        if corrupt or not flow:
            raise HTTPException(status_code=409, detail={
                "code": "FLOW_CORRUPT" if corrupt else "FLOW_NOT_FOUND",
                "message": "阶段流程不存在或已损坏，请重新启动"})
        _reconcile(store, flow)
        st = _stage(flow, stage_key)
        assert st is not None
        # 幂等键先于状态校验：双击/重放同一请求要返回首次结果，而不是因为状态已变而 409
        idem_key = f"{flow.get('flow_id')}:{stage_key}:{action}:{digest}:{force and action == 'regenerate'}"
        cached = (flow.get("idempotency") or {}).get(idem_key)
        if cached:
            return {**cached.get("result", {}), "idempotent": True,
                    "flow": _public_flow(store, flow, cfg, True)}
        allowed = _allowed_status(stage_key, action)
        if st.get("status") not in allowed:
            _save(store, flow)
            raise HTTPException(status_code=409, detail={
                "code": "STAGE_NOT_READY", "stage": stage_key,
                "status": st.get("status"), "message": "当前阶段状态不允许该操作"})
        # digest 校验：编辑后用旧 digest 确认必须 409（防止卡片与产物错位）
        current_digest = compute_digest(store, stage_key)
        if action in ("continue", "regenerate") and digest and current_digest \
                and digest != current_digest and st.get("status") in ("done", "ready", "confirmed"):
            raise HTTPException(status_code=409, detail={
                "code": "STAGE_CHANGED", "stage": stage_key,
                "current_digest": current_digest,
                "message": "产物已更新，请刷新后确认最新版本"})
        result = _apply_action(store, flow, stage_key, action, digest, user, force)
        result["idempotent"] = False
        _record_idempotent(flow, idem_key, result)
        _save(store, flow)
        return {**result, "flow": _public_flow(store, flow, cfg, True)}


def _allowed_status(key: str, action: str) -> set[str]:
    if action == "continue":
        return {"ready", "failed", "stopped"} if key == "chapters" else {"done", "ready"}
    if action == "regenerate":
        return {"done", "confirmed", "failed", "stopped"}
    if action == "skip":
        return {"pending", "failed", "stopped"} if key == "research" else set()
    if action == "back_outline":
        return {"ready", "failed", "stopped"} if key == "chapters" else set()
    return set()


def _apply_action(store, flow: dict, key: str, action: str, digest: str,
                  user: dict, force: bool) -> dict:
    st = _stage(flow, key)
    if action == "skip":
        st.update(status="skipped", reason="user_skip")
        nxt = _next_key(key)
        while nxt and _stage(flow, nxt).get("status") == "skipped":
            nxt = _next_key(nxt)
        if nxt:
            _launch(store, flow, nxt, user,
                    {"scope": "all"} if nxt == "chapters" else None)
        return {"stage": key, "action": "skip", "stage_status": "skipped", "next": nxt or ""}
    if action == "back_outline":
        outline_st = _stage(flow, "outline")
        outline_st.update(status="done")
        volume_st = _stage(flow, "volume")
        volume_st.update(status="skipped", reason="merged_into_outline")
        st.update(status="pending", last_error={})
        return {"stage": key, "action": "back_outline", "stage_status": "pending",
                "next": "outline"}
    if action == "regenerate":
        # 重复重生成保护：10 分钟内第二次起需 force=true（UI 二次确认）
        win = float(st.get("regenerated_ts") or 0)
        if not force and time.time() - win < 600:
            raise HTTPException(status_code=409, detail={
                "code": "REGENERATE_CONFIRM", "stage": key,
                "message": "刚重生成过本步，继续会再次扣费，请确认 force=true"})
        st["regenerated_ts"] = time.time()
        _reset_downstream(flow, key)
        st.update(digest="", edited_at=None)
        _launch(store, flow, key, user)
        return {"stage": key, "action": "regenerate", "status": st["status"],
                "run_id": st.get("run_id", ""), "task_id": st.get("task_id", "")}
    return _continue_locked(store, flow, key, user)


def _reset_downstream(flow: dict, key: str) -> None:
    """重生成上游后，下游产物将失效：回到 pending（卷纲仍为并入大纲的 skipped）。"""
    idx = STAGE_KEYS.index(key)
    for nxt in STAGE_KEYS[idx + 1:]:
        nst = _stage(flow, nxt)
        if nxt == "volume":
            nst.update(status="skipped", reason="merged_into_outline")
        else:
            nst.update(status="pending", digest="", edited_at=None, last_error={},
                       run_id="", task_id="")


def set_mode(store, user: dict, mode: str) -> dict:
    """POST /flow/mode：写项目级 generation_mode；stepwise→auto 时自动放行当前 done 阶段。"""
    if mode not in ("auto", "stepwise"):
        raise HTTPException(status_code=400, detail={"code": "BAD_MODE", "message": "模式只能是 auto/stepwise"})
    cfg = store.load_config()
    _assert_supported(store, cfg)
    _write_mode(store, mode)
    with _lock(store):
        flow, corrupt = read_flow(store)
        if not flow or corrupt:
            return get_flow(store, with_card=True)
        flow["mode"] = mode
        flow["mode_source"] = "project"
        _reconcile(store, flow)
        cur = _current(flow)
        if mode == "auto" and cur and cur["key"] in _MIDDLE_STAGES and cur.get("status") == "done":
            try:
                _continue_locked(store, flow, cur["key"], user)
            except HTTPException as exc:
                logger.warning("切换 auto 自动放行失败: project=%s stage=%s detail=%s",
                               store.dir.name, cur["key"], exc.detail)
        _save(store, flow)
    return get_flow(store, with_card=True)


def abort(store, user: dict) -> dict:
    """POST /flow/abort：停止当前 run 并把阶段显式置 stopped（run 侧写随后覆盖）。"""
    from . import core

    with _lock(store):
        flow, corrupt = read_flow(store)
        if not flow or corrupt:
            raise HTTPException(status_code=409, detail={
                "code": "FLOW_NOT_FOUND", "message": "阶段流程不存在"})
        cur = _current(flow)
        handle = core._RUNS.get(store.dir.name)
        if not cur or cur.get("status") != "running" or handle is None:
            raise HTTPException(status_code=409, detail={
                "code": "STAGE_NOT_READY", "message": "当前没有运行中的阶段"})
        handle.stop_event.set()
        cur.update(status="stopped", finished_at=_now(),
                   last_error={"code": "user_stopped", "message": "已手动停止，可重试（再次计费）"})
        _save(store, flow)
    return get_flow(store, with_card=True)


# ---------- run 结束钩子（唯一主链路接线） ----------

def on_run_finished(store, agent: str, run_id: str, status: str) -> None:
    """run 收尾同步 flow：置 done/failed/stopped；auto 模式下推进下一阶段。

    无 flow 文件立即返回（老项目零副作用）；任何异常只留日志，不影响 run 已完成的收尾。
    """
    key = AGENT_STAGE.get(agent)
    if not key:
        return
    with _lock(store):
        flow, corrupt = read_flow(store)
        if corrupt or not flow:
            return
        st = _stage(flow, key)
        if st is None:
            return
        if not st.get("run_id") and st.get("status") in ("confirmed", "skipped"):
            return  # 已确认阶段的产品被手动重生成：只由 GET digest 标记 edited，不回退状态
        if st.get("run_id") and run_id and st["run_id"] != run_id:
            # 手动触发的其他 run：只记录，不接管状态（GET 对账会按最新侧写收敛）
            logger.info("flow 忽略非编排 run: project=%s agent=%s run=%s",
                        store.dir.name, agent, run_id)
            return
        run = read_run_file(store.dir, run_id) if run_id else None
        if run:
            _apply_run_status(st, run)
        elif status == "ok":
            st.update(status="confirmed" if key == "chapters" else "done", finished_at=_now())
        elif status == "stopped":
            st.update(status="stopped", finished_at=_now())
        else:
            st.update(status="failed", finished_at=_now(),
                      last_error={"code": "run_failed", "message": "生成失败，可重试（再次计费）"})
        advance = (flow.get("mode") == "auto" and st.get("status") == "done"
                   and key in _MIDDLE_STAGES)
        _save(store, flow)
    if advance:
        threading.Thread(target=_auto_advance, args=(store, key), daemon=True).start()


def _auto_advance(store, key: str) -> None:
    """等待当前 run 线程退出（释放占槽）后，自动确认并起下一阶段。"""
    from . import core

    pid = store.dir.name
    for _ in range(200):  # 最多等 10s；超时则放弃自动推进，用户可手动确认
        handle = core._RUNS.get(pid)
        if handle is None or handle.thread is None or not handle.thread.is_alive():
            break
        time.sleep(0.05)
    try:
        with _lock(store):
            flow, corrupt = read_flow(store)
            if corrupt or not flow or flow.get("mode") != "auto":
                return
            st = _stage(flow, key)
            if st is None or st.get("status") != "done":
                return
            _continue_locked(store, flow, key, {})
            _save(store, flow)
    except Exception:
        logger.exception("auto 模式自动推进失败（不阻断已完成的 run）: project=%s stage=%s",
                         pid, key)
