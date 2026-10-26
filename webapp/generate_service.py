# -*- coding: utf-8 -*-
"""Agent 统一触发入口（TECH-Agent用户化与版本化 §4，阶段 1B）。

职责：把「单 agent 触发」收敛为一条全异步链路——前置守卫（纯函数群）→
`_acquire_slot` 占槽 → `task.json(kind=agent_run)` 落盘 → 后台线程执行 →
run 侧写留痕。返回 `task_id` 供轮询四态状态机（running/resumed/done|stopped|failed）。

设计要点：
1. **守卫判定顺序固定**：访问(router 层)→计费/余额→agent/产物→params→锁定→手写章→Skill，
   前序不过后续不判（§4.1；拒绝码语义稳定供前端分支渲染）。
2. **一次 run 一次 commit**：产物在 run 收尾统一 `commit_head` + `settle_version`（§3.10），
   中间落盘只写 HEAD；写章按「新增版本」逐章结算（复用 settle_ledger 幂等键）。
3. **停止语义**：stop_event 置位时当前未完成章不提交、已完成章保留，run 记 stopped（I-5）。
4. **prompt 注入只进内存态**：人设（agent_prompts 当前版本）+ user_prompt 拼入
   `cfg.custom_prompt`、Skill 拼入 `cfg.skill_prompt_append`，不落 config.json；
   user_prompt 走 sanitize 属阶段 4，本期仅原样留痕于 run 侧写（≤4KB）。
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from novel_gen.store.project import ProjectStore
from novel_gen.store.run_context import RunContext
from novel_gen.store import run_context as run_files
from novel_gen.store.wordcount import count_words

from . import agent_prompts
from . import db
from . import settle_ledger
from . import teardown_lib
from .logger import get_logger
from .pricing import price_for
from .referral import agent_price, round_money

logger = get_logger("generate_service")

__all__ = [
    "ALLOWED_AGENTS",
    "AgentRunReq",
    "assert_unified_generate_enabled",
    "estimate_agent_price",
    "get_agent_run",
    "guard_agent_product",
    "guard_balance",
    "guard_locked",
    "guard_params",
    "guard_skill_limits",
    "guard_user_chapter",
    "resolve_billing_mode",
    "resume_agent_run",
    "start_agent_run",
    "unified_generate_enabled",
]

# 可独立触发的 agent 白名单（§4.1；与 agent_prompts.EDITABLE_AGENTS 同源）
ALLOWED_AGENTS = agent_prompts.EDITABLE_AGENTS
_SKILL_LIMIT = 5  # 单 run 挂载 Skill 上限（TECH §6.7）

# 预估字数（estimated_price 用；真实结算按产物实际字数）
_EST_WORDS = {"researcher": 2000, "architect.outline": 4000,
              "architect.volume": 1500, "architect.world": 3000}


class AgentRunReq(BaseModel):
    """POST /agent-runs 请求体（§4.1）。"""

    agent: str = Field(..., min_length=1)
    params: dict = Field(default_factory=dict)
    user_prompt: str = ""
    skill_ids: list[int] = Field(default_factory=list)


# ---------- 全局紧急回退开关（评审 I-12：sys_config 全站生效，不做单项目灰度） ----------

def unified_generate_enabled() -> bool:
    """unified_generate 开关判定：sys_config 表优先，无记录用注册表默认值。"""
    try:
        row = db.get_config("unified_generate")
        if row is not None:
            return str(row["config_value"]).strip().lower() in ("true", "1", "yes")
    except Exception:
        logger.exception("unified_generate 开关读取失败，按注册表默认值处理")
    from novel_gen.config import feature_default
    return bool(feature_default("unified_generate"))


def assert_unified_generate_enabled() -> None:
    """开关关闭 → 新入口整体 503（全局紧急回退）。"""
    if not unified_generate_enabled():
        raise HTTPException(status_code=503, detail="Agent 统一触发入口已临时关闭，请使用原有生成入口")


# ---------- 守卫群（纯函数，不碰线程/槽位） ----------

def resolve_billing_mode(uid: int | None) -> str:
    """计费模式：用户已配自备 Key → user_llm；否则 platform。"""
    return settle_ledger.billing_mode_of(uid is not None and _has_user_llm(uid))


def _has_user_llm(uid: int | None) -> bool:
    if uid is None:
        return False
    u = db.get_user_llm(uid)
    return bool(u.get("api_key") and u.get("provider"))


def guard_balance(mode: str, uid: int | None) -> None:
    """平台付费且余额 ≤ 0 → 402（自备 Key 不校验平台余额）。"""
    if mode != "platform" or uid is None:
        return
    balance = float(db.get_user(uid)["balance"])
    if balance <= 0:
        raise HTTPException(status_code=402, detail="余额不足，请先充值后再生成")


def guard_agent_product(agent: str) -> None:
    """agent 必须可独立触发（白名单外 → 400）。"""
    if agent not in ALLOWED_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"agent 不支持独立触发：{agent}（可选：{', '.join(ALLOWED_AGENTS)}）")


def guard_params(agent: str, params: dict) -> dict:
    """params schema 校验（§4.1）：未知键 / 缺必填 / 互斥冲突 → 400；返回规整副本。

    - writer.chapter：scope=single→ch_no；range→from_ch（可带 to_ch）；all→无其它键；
      ch_no 与 from_ch 互斥。
    - architect.volume：vol_no 必填。
    - researcher / architect.outline / architect.world：只允许空或 scope="product"。
    """
    params = dict(params or {})
    if agent == "writer.chapter":
        allowed = {"scope", "ch_no", "from_ch", "to_ch"}
        unknown = set(params) - allowed
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知参数：{sorted(unknown)}")
        ch_no, from_ch = params.get("ch_no"), params.get("from_ch")
        if ch_no is not None and from_ch is not None:
            raise HTTPException(status_code=400, detail="ch_no 与 from_ch 互斥，只能传一个")
        scope = params.get("scope")
        if scope is None:
            scope = "single" if ch_no is not None else ("range" if from_ch is not None else "all")
        if scope not in ("single", "range", "all"):
            raise HTTPException(status_code=400, detail=f"非法 scope：{scope}")
        if scope == "single" and ch_no is None:
            raise HTTPException(status_code=400, detail="scope=single 缺少必填参数 ch_no")
        if scope == "range" and from_ch is None:
            raise HTTPException(status_code=400, detail="scope=range 缺少必填参数 from_ch")
        for k in ("ch_no", "from_ch", "to_ch"):
            if params.get(k) is not None and (not isinstance(params[k], int) or params[k] < 1):
                raise HTTPException(status_code=400, detail=f"参数 {k} 必须为正整数")
        params["scope"] = scope
        return params
    if agent == "architect.volume":
        unknown = set(params) - {"vol_no"}
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知参数：{sorted(unknown)}")
        vol_no = params.get("vol_no")
        if not isinstance(vol_no, int) or vol_no < 1:
            raise HTTPException(status_code=400, detail="architect.volume 缺少必填参数 vol_no（正整数）")
        return params
    # researcher / architect.outline / architect.world：产物级，无业务参数
    unknown = set(params) - {"scope"}
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知参数：{sorted(unknown)}")
    if params.get("scope") not in (None, "product"):
        raise HTTPException(status_code=400, detail="产物级 agent 仅支持 scope=product")
    return {}


def guard_locked(store: ProjectStore, agent: str, params: dict) -> None:
    """锁定守卫：agent → lock kind 映射（outline/world_view/volume/chapter）。

    writer.chapter 的 range/all 不在此拒绝——逐章执行时跳过锁定章（与批量语义一致）。
    """
    from . import core  # 延迟 import：避免 core ↔ generate_service 循环引用

    if agent == "architect.outline":
        core._assert_not_locked(store, "outline")
    elif agent == "architect.world":
        core._assert_not_locked(store, "world_view")
    elif agent == "architect.volume":
        core._assert_not_locked(store, "volume", params["vol_no"],
                                  "该分卷已被锁定，如需重生成请先解锁")
    elif agent == "writer.chapter" and params.get("scope") == "single":
        core._assert_not_locked(store, "chapter", params["ch_no"],
                                "本章已被用户锁定，如需重生成请先解锁")


def guard_user_chapter(store: ProjectStore, agent: str, params: dict) -> None:
    """手写章守卫：单章手写拒重写；范围起点 ≤ 最大手写章号拒。

    判定口径：`chapter_versions.json` 索引的 `user_managed` 标记（用户编辑保存即置位，
    versioned 门面与 legacy 路径一致），叠加存量 HEAD `source=="user"` 双保险——
    现网 `load_chapter().source` 只反映 chapter 对象自身字段，`save_chapter(source="user")`
    不改写 HEAD 的 source 值，单看 HEAD 会漏判（存量缺陷，见 docs/decisions.md）。
    """
    if agent != "writer.chapter":
        return
    if params.get("scope") == "single":
        if _is_user_chapter(store, params["ch_no"]):
            raise HTTPException(
                status_code=400,
                detail="该章为用户手写内容，AI 不自动重写；如需修改请使用「编辑本章」手动调整")
        return
    user_max = max((no for no in _user_chapter_numbers(store)), default=0)
    if not user_max:
        return
    scope = params.get("scope")
    start = 1 if scope == "all" else int(params.get("from_ch") or 1)
    if start <= user_max:
        raise HTTPException(
            status_code=400,
            detail=f"第 {start} 章起范围内含用户手写内容（到第 {user_max} 章），"
                   f"重新生成会覆盖手写内容，已禁止；请从第 {user_max + 1} 章开始")


def _is_user_chapter(store: ProjectStore, no: int) -> bool:
    """单章是否用户手写/已人工管理（索引 user_managed 优先，存量 HEAD source 兜底）。"""
    rec = (store.load_chapter_versions() or {}).get(str(no)) or {}
    if isinstance(rec, dict) and rec.get("user_managed"):
        return True
    ch = store.load_chapter(no)
    return ch is not None and ch.source == "user"


def _user_chapter_numbers(store: ProjectStore) -> list[int]:
    """全部手写/人工管理章号（索引 user_managed ∪ HEAD source==user）。"""
    numbers: set[int] = set()
    for no, rec in (store.load_chapter_versions() or {}).items():
        if isinstance(rec, dict) and rec.get("user_managed") and str(no).isdigit():
            numbers.add(int(no))
    for c in store.list_chapters():
        if c.source == "user":
            numbers.add(c.no)
    return sorted(numbers)


def guard_skill_limits(skill_ids: list[int]) -> None:
    """Skill 挂载上限 ≤5（TECH §6.7 全局 token 预算配套）。"""
    if len(skill_ids or []) > _SKILL_LIMIT:
        raise HTTPException(status_code=400, detail=f"挂载 Skill 最多 {_SKILL_LIMIT} 个")


# ---------- 启动 / 重拾 / 查询 ----------

def _estimated_price(agent: str, params: dict, store: ProjectStore, mode: str) -> float:
    """预估价（仅供前端提示；真实结算按产物实际字数）。BYOK 恒 0。"""
    if mode != "platform":
        return 0.0
    cfg = store.load_config()
    hq = bool(getattr(cfg, "high_quality_mode", False))
    if agent == "writer.chapter":
        outline = store.load_outline()
        total = len(outline.chapters) if outline else 0
        scope = params.get("scope")
        if scope == "single":
            n = 1
        elif scope == "range":
            n = max(0, int(params.get("to_ch") or total) - int(params["from_ch"]) + 1)
        else:
            done = {c.no for c in store.list_chapters()}
            n = max(0, total - len(done))
        words = n * int(getattr(cfg, "chapter_words", 2500) or 2500)
    else:
        words = _EST_WORDS.get(agent, 2000)
    discount = db.project_discount(store.dir.name)
    return round_money(agent_price(price_for(words, high_quality=hq), discount))


def estimate_agent_price(store: ProjectStore, req: AgentRunReq, user: dict) -> dict:
    """只读预估价（UX 评审 U2）：复用 agent/params 守卫校验，不占槽、不扣费、不校验余额/锁。

    供前端在参数变化时实时刷新预估金额（真实结算仍按产物实际字数）。
    """
    uid = db.project_owner(store.dir.name)
    mode = resolve_billing_mode(uid)
    guard_agent_product(req.agent)
    params = guard_params(req.agent, req.params)
    return {"agent": req.agent, "billing_mode": mode,
            "estimated_price": _estimated_price(req.agent, params, store, mode)}


def _today_start_ts() -> float:
    """Asia/Shanghai（UTC+8）当日 0 点时间戳，用于每日防滥用计数。"""
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _runs_today_count(store: ProjectStore) -> int:
    """统计本日已启动的 run 侧写数（P2-F2 每日上限；侧写超量由 prune 兜底）。"""
    runs_dir = store.dir / "agent_runs"
    if not runs_dir.is_dir():
        return 0
    today = _today_start_ts()
    n = 0
    for p in runs_dir.glob("*.json"):
        if p.name.endswith(".tmp"):
            continue
        try:
            data = run_files.read_run_file(store.dir, p.stem)
        except Exception:
            continue
        try:
            if float((data or {}).get("started_at") or 0) >= today:
                n += 1
        except (TypeError, ValueError):
            continue
    return n


def _agent_run_daily_limit() -> int:
    """单项目每日 Agent run 启动上限（sys_config 优先，注册表默认兜底；0=不限）。"""
    try:
        row = db.get_config("agent_run_daily_limit")
        if row is not None:
            try:
                return int(str(row["config_value"]).strip())
            except (TypeError, ValueError):
                logger.warning("agent_run_daily_limit 配置非法，回退默认: %r",
                               row.get("config_value"))
    except Exception:
        logger.exception("agent_run_daily_limit 读取失败（回退默认）")
    from novel_gen.config import feature_default

    try:
        return int(feature_default("agent_run_daily_limit"))
    except (KeyError, TypeError, ValueError):
        return 0


def _check_daily_run_limit(store: ProjectStore) -> None:
    """每日上限检查（P2-F2）：防误触/脚本刷量导致意外扣费；0=不限。"""
    limit = _agent_run_daily_limit()
    if limit <= 0:
        return
    count = _runs_today_count(store)
    if count >= limit:
        logger.info("agent run 每日上限拦截: project=%s count=%d limit=%d",
                    store.dir.name, count, limit)
        raise HTTPException(status_code=429,
                            detail=f"今日已启动 {count} 次生成，已达每日上限（{limit} 次），请明日再试")


def _update_task_progress(store: ProjectStore, **kw) -> None:
    """运行中更新 task.json 的 progress（U1）：失败只留痕，不阻断生成线程。

    原子写（tmp + os.replace）避免 Windows 并发读读到半截 JSON → 轮询误停（R1）。
    """
    p = store.dir / "task.json"
    try:
        meta = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if not isinstance(meta, dict):
            meta = {}
        prog = dict(meta.get("progress") or {})
        prog.update(kw)
        meta["progress"] = prog
        store._write_json(p, meta)
    except Exception:
        logger.exception("task.json progress 更新失败（降级）: project=%s", store.dir.name)


def start_agent_run(store: ProjectStore, req: AgentRunReq, user: dict) -> dict:
    """统一入口：守卫 → 占槽 → task.json → 起线程 → 202 返回体。"""
    from . import core  # 延迟 import：避免循环引用

    uid = db.project_owner(store.dir.name)
    mode = resolve_billing_mode(uid)
    guard_balance(mode, uid)
    guard_agent_product(req.agent)
    params = guard_params(req.agent, req.params)
    guard_locked(store, req.agent, params)
    guard_user_chapter(store, req.agent, params)
    guard_skill_limits(req.skill_ids)
    _check_daily_run_limit(store)  # P2-F2：单项目每日上限（0=不限）

    task_id = uuid.uuid4().hex[:12]
    run_id = uuid.uuid4().hex
    handle = core._acquire_slot(store.dir.name)
    try:
        core._mark_task(store, "agent_run", task_id=task_id, agent=req.agent,
                        params=params, run_id=run_id, user_prompt=req.user_prompt,
                        skill_ids=list(req.skill_ids))
        t = threading.Thread(
            target=_run_agent_task,
            args=(store, handle, req.agent, params, req.user_prompt,
                  tuple(req.skill_ids), task_id, run_id),
            daemon=True)
        handle.thread = t
        handle.task_id = task_id
        t.start()
    except Exception:
        core._release_slot(store.dir.name)
        core._clear_task(store)
        raise
    logger.info("agent run 启动: project=%s agent=%s task=%s run=%s",
                store.dir.name, req.agent, task_id, run_id)
    return {
        "started": True,
        "task_id": task_id,
        "agent": req.agent,
        "billing_mode": mode,
        "estimated_price": _estimated_price(req.agent, params, store, mode),
    }


def resume_agent_run(store: ProjectStore, handle, meta: dict) -> None:
    """启动重拾（task.json 残留）：沿用原 task_id / run_id 起线程（I-6）。

    槽位已由 `_resume_interrupted_tasks` 申请，本函数只负责起线程。
    """
    task_id = str(meta.get("task_id") or "")
    agent = str(meta.get("agent") or "")
    if not task_id or agent not in ALLOWED_AGENTS:
        raise ValueError(f"agent_run 任务标记非法: task_id={task_id} agent={agent}")
    t = threading.Thread(
        target=_run_agent_task,
        args=(store, handle, agent, dict(meta.get("params") or {}),
              str(meta.get("user_prompt") or ""),
              tuple(meta.get("skill_ids") or ()),
              task_id, str(meta.get("run_id") or uuid.uuid4().hex)),
        daemon=True)
    handle.thread = t
    handle.task_id = task_id
    t.start()
    logger.info("agent run 重拾: project=%s agent=%s task=%s", store.dir.name, agent, task_id)


def get_agent_run(store: ProjectStore, task_id: str) -> dict:
    """四态状态机：running / resumed / done|stopped|failed / not_found。"""
    from . import core  # 延迟 import：避免循环引用

    task_file = store.dir / "task.json"
    if task_file.exists():
        try:
            meta = json.loads(task_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        if meta.get("kind") == "agent_run" and meta.get("task_id") == task_id:
            progress = meta.get("progress") or {}
            elapsed = round(max(0.0, time.time() - float(meta.get("started_at") or time.time())),
                            1) if meta.get("started_at") else 0.0
            common = {"task_id": task_id, "agent": meta.get("agent", ""),
                      "run_id": meta.get("run_id", ""),
                      "started_at": meta.get("started_at"),
                      "progress": {**progress, "elapsed": elapsed}}
            handle = core._RUNS.get(store.dir.name)
            if handle is not None and handle.thread is not None and handle.thread.is_alive():
                return {**common, "status": "running"}
            # task.json 在但线程不在：重启竞态兜底（lifespan 重拾前的瞬时态）
            return {**common, "status": "resumed"}
    run = _find_run_by_task(store, task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    status = {"ok": "done"}.get(run.get("status"), run.get("status") or "failed")
    return {
        "task_id": task_id,
        "status": status,
        "agent": run.get("agent", ""),
        "run_id": run.get("run_id", ""),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "outputs": run.get("outputs") or [],
        "billing": run.get("billing") or {},
        "error": run.get("error", ""),
    }


def _find_run_by_task(store: ProjectStore, task_id: str) -> dict | None:
    """按 task_id 反查 run 侧写（新的在前）。"""
    runs_dir = store.dir / "agent_runs"
    if not runs_dir.is_dir():
        return None
    files = sorted((p for p in runs_dir.glob("*.json") if not p.name.endswith(".tmp")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        data = run_files.read_run_file(store.dir, p.stem)
        if data and data.get("task_id") == task_id:
            return data
    return None


# ---------- 后台线程主函数 ----------

def _apply_pre_run_injections(store: ProjectStore, cfg, uid: int | None) -> None:
    """所有 Agent run 统一执行的预生成注入（旧路径 core._run_outline_only/_run_generation 原有逻辑）。

    - 双锚风格锚定（题材文风锚）；
    - 用户级记忆库（跨书偏好/纪律/文风锚）。
    失败一律降级（只记日志），不阻断生成。
    """
    from . import core

    try:
        core._inject_teardown_style_anchor(store, cfg)
    except Exception:
        logger.exception("风格锚注入失败（降级）: project=%s", store.dir.name)
    if uid is not None:
        try:
            core._inject_user_memory(store, cfg, uid)
        except Exception:
            logger.exception("用户记忆注入失败（降级）: project=%s", store.dir.name)


def _sink_research_safe(store: ProjectStore) -> None:
    """调研成果沉淀到拆书库（失败静默，绝不阻塞生成）。"""
    try:
        report = store.load_research()
        teardown_lib.sink_research(report, store.dir.name)
    except Exception:
        logger.exception("拆书库沉淀失败（降级）: project=%s", store.dir.name)


def _run_agent_task(store: ProjectStore, handle, agent: str, params: dict,
                    user_prompt: str, skill_ids: tuple, task_id: str, run_id: str) -> None:
    """run 生命周期：begin_run → 分发执行 → 收尾结算 → 侧写留痕 → 清任务释放槽。"""
    from . import core  # 延迟 import：避免循环引用

    project_id = store.dir.name
    trace_id = uuid.uuid4().hex
    status = "ok"
    outputs: list[dict] = []
    entries: list[dict] = []
    prompt_version, prompt_sha256 = "", ""
    ctx = RunContext(run_id=run_id, agent=agent, skill_ids=tuple(skill_ids),
                     user_prompt=user_prompt, task_id=task_id)
    store.begin_run(ctx)
    try:
        cfg = store.load_config()
        uid = db.project_owner(project_id)
        mode = settle_ledger.billing_mode_of(_has_user_llm(uid))
        hq = bool(getattr(cfg, "high_quality_mode", False))
        # 统一预运行注入：风格锚 + 用户记忆（旧路径 core._run_outline_only/_run_generation 原有逻辑）
        _apply_pre_run_injections(store, cfg, uid)
        # 人设 + user_prompt 注入（内存态，不落盘；sanitize 属阶段 4）
        prompt_text, prompt_version, prompt_sha256 = agent_prompts.get_prompt_meta(store, agent)
        pipeline = core._open_pipeline(store, trace_id=trace_id)
        custom = prompt_text if not user_prompt else f"{prompt_text}\n\n{user_prompt}"
        pipeline.cfg.custom_prompt = custom
        pipeline.cfg.skill_prompt_append = _build_skill_append(skill_ids)
        billing = {"mode": mode, "hq": hq, "project_id": project_id}

        if agent == "researcher":
            _exec_researcher(store, pipeline, handle)
        elif agent == "architect.outline":
            _exec_outline(store, pipeline, handle, billing, outputs, entries)
        elif agent == "architect.world":
            _exec_world(store, pipeline, handle, billing, outputs, entries)
        elif agent == "architect.volume":
            _exec_volume(store, pipeline, params, billing, outputs, entries)
        elif agent == "writer.chapter":
            _exec_chapters(store, pipeline, handle, params, billing, outputs, entries)
            if handle.stop_event.is_set():
                status = "stopped"
        if handle.stop_event.is_set() and status == "ok":
            status = "stopped"
    except Exception as exc:  # noqa: BLE001 — 线程内必须兜底，状态落 failed
        status = "failed"
        handle.error = f"{type(exc).__name__}: {exc}"
        logger.exception("agent run 失败: project=%s agent=%s task=%s",
                         project_id, agent, task_id)
    finally:
        store.end_run()
        _write_run_sidecar(store, ctx, status, params, prompt_version, prompt_sha256,
                           trace_id, outputs, entries, handle)
        core._clear_task(store)
        core._release_slot(project_id)
        logger.info("agent run 结束: project=%s agent=%s task=%s status=%s",
                    project_id, agent, task_id, status)


def _write_run_sidecar(store: ProjectStore, ctx: RunContext, status: str, params: dict,
                       prompt_version: str, prompt_sha256: str, trace_id: str,
                       outputs: list[dict], entries: list[dict], handle) -> None:
    """组装并落 run 侧写（§3.3 字段）；失败只留痕不影响线程收尾。"""
    try:
        cfg = store.load_config()
        billed_words = sum(int(e.get("words") or 0) for e in entries)
        billed_price = round_money(sum(float(e.get("charged") or 0.0) for e in entries))
        run_files.write_run_file(store.dir, {
            "run_id": ctx.run_id,
            "task_id": ctx.task_id,
            "project_id": store.dir.name,
            "agent": ctx.agent,
            "started_at": ctx.started_at,
            "finished_at": time.time(),
            "status": status,
            "params": params,
            "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256,
            "user_prompt": ctx.user_prompt,
            "skill_ids": list(ctx.skill_ids),
            "model": cfg.llm.model or "",
            "temperature": cfg.llm.temperature,
            "seed": None,  # best-effort 字段：provider 不暴露 seed，恒 null（S-4）
            "trace_id": trace_id,
            "billing_mode": settle_ledger.billing_mode_of(
                _has_user_llm(db.project_owner(store.dir.name))),
            "billing": {"billed_words": billed_words, "billed_price": billed_price,
                        "entries": entries, "wasted_words": 0},
            "outputs": outputs,
            "compliance_rejected": False,
            "error": getattr(handle, "error", None) or "",
        })
    except Exception:
        logger.exception("run 侧写落盘失败: project=%s run=%s", store.dir.name, ctx.run_id)


def _build_skill_append(skill_ids: tuple) -> str:
    """按 skill_ids 查库拼接 prompt_append（仅激活技能）；失败降级为空串。"""
    parts: list[str] = []
    for sid in skill_ids:
        try:
            sk = db.get_skill(int(sid))
            if sk and sk.get("is_active") and (sk.get("prompt_append") or "").strip():
                parts.append(sk["prompt_append"].strip())
        except Exception:
            logger.warning("Skill 读取失败（跳过）: skill_id=%s", sid)
    return "\n\n".join(parts)


def _record_settle(result: dict | None, words: int, outputs: list[dict],
                   entries: list[dict], kind: str, key: str, v: int | None) -> None:
    """聚合一次结算到 run 侧写账目（charged/words/outputs）。"""
    if v is not None:
        outputs.append({"kind": kind, "key": str(key), "v": int(v)})
    if result:
        entries.append({"entry": result.get("entry", ""), "charged": result.get("charged", 0.0),
                        "words": int(words)})


# ---------- 各 agent 执行分发 ----------

def _exec_researcher(store: ProjectStore, pipeline, handle) -> None:
    """题材调研：删旧 research.json 重跑（researcher.run 有复用短路）。"""
    from . import core

    _update_task_progress(store, phase="research")
    research_path = store.dir / "research.json"
    old = research_path.read_text(encoding="utf-8") if research_path.exists() else None
    try:
        research_path.unlink(missing_ok=True)
        try:
            core._inject_teardown_material(store, store.load_config())
        except Exception:
            logger.exception("拆书库素材注入失败（降级）: project=%s", store.dir.name)
        result = pipeline.run_research(silent=True)
        _sink_research_safe(store)
        if result is None:
            raise RuntimeError("调研未产出（扩写模式下调研不适用）")
    except Exception:
        if old is not None:
            research_path.write_text(old, encoding="utf-8")  # 失败恢复旧产物
        raise


def _exec_outline(store: ProjectStore, pipeline, handle, billing: dict,
                  outputs: list[dict], entries: list[dict]) -> None:
    """大纲：删旧 outline/editor_review → 调研素材注入 → run_outline(含 research) + editor_review → 沉淀调研 → 收尾提交结算。"""
    from . import core

    _update_task_progress(store, phase="outline")
    outline_path = store.dir / "outline.json"
    review_path = store.dir / "editor_review.json"
    old_outline = outline_path.read_text(encoding="utf-8") if outline_path.exists() else None
    old_review = review_path.read_text(encoding="utf-8") if review_path.exists() else None
    try:
        outline_path.unlink(missing_ok=True)
        review_path.unlink(missing_ok=True)
        try:
            core._inject_teardown_material(store, store.load_config())
        except Exception:
            logger.exception("拆书库素材注入失败（降级）: project=%s", store.dir.name)
        pipeline.run_outline(silent=True)
        _sink_research_safe(store)
        if handle.stop_event.is_set():
            return
        pipeline.run_editor_review(silent=True)
        # 一次 run 一次 commit（§3.10）；versioned 关闭时回退内容幂等结算
        r = core._commit_and_settle(store, "outline", store.load_outline(),
                                    mode=billing["mode"], hq=billing["hq"],
                                    project_id=billing["project_id"])
        _record_product_settle(store, r, "outline", billing, outputs, entries)
    except Exception:
        if old_outline is not None:
            outline_path.write_text(old_outline, encoding="utf-8")
        if old_review is not None:
            review_path.write_text(old_review, encoding="utf-8")
        raise


def _exec_world(store: ProjectStore, pipeline, handle, billing: dict,
                outputs: list[dict], entries: list[dict]) -> None:
    """世界观：删 world.json → 调研素材注入 → run_world（内部按需跑 research）→ 沉淀调研 → 收尾提交结算，失败恢复旧 world。"""
    from . import core

    _update_task_progress(store, phase="world")
    old_world = None
    try:
        old_world = store.load_world()
    except Exception:
        old_world = None
    try:
        store.world_path().unlink(missing_ok=True)
        try:
            core._inject_teardown_material(store, store.load_config())
        except Exception:
            logger.exception("拆书库素材注入失败（降级）: project=%s", store.dir.name)
        pipeline.run_world(silent=True)
        _sink_research_safe(store)
        r = core._commit_and_settle(store, "world", store.load_world(),
                                    mode=billing["mode"], hq=billing["hq"],
                                    project_id=billing["project_id"])
        _record_product_settle(store, r, "world", billing, outputs, entries)
    except Exception:
        if old_world is not None:
            store.save_world(old_world)
        raise


def _record_product_settle(store: ProjectStore, result: dict | None, kind: str,
                           billing: dict, outputs: list[dict], entries: list[dict]) -> None:
    """产物级结算聚合：从 entry 键（kind:key:vN）解析版本号填 outputs。"""
    if not result:
        return
    v = None
    entry = str(result.get("entry") or "")
    try:
        v = int(entry.rsplit(":v", 1)[1])
    except (IndexError, ValueError):
        pass
    key = "all"
    if ":v" in entry:
        key = entry.split(":")[1] if entry.count(":") >= 2 else "all"
    data = store.load_outline() if kind == "outline" else store.load_world()
    words = count_words(kind, data.to_dict() if hasattr(data, "to_dict") else (data or {}))
    _record_settle(result, words, outputs, entries, kind, key, v)


def _exec_volume(store: ProjectStore, pipeline, params: dict, billing: dict,
                 outputs: list[dict], entries: list[dict]) -> None:
    """卷纲：LLM 重写指定卷 intro + 章规划（复用 regenerate_volume 口径），整纲提交一次。"""
    _update_task_progress(store, phase="volume")
    project_id = billing["project_id"]
    vol_no = params["vol_no"]
    outline = store.load_outline()
    if not outline or not outline.volumes:
        raise HTTPException(status_code=400, detail="尚无大纲")
    vol = next((v for v in outline.volumes if v.no == vol_no), None)
    if vol is None:
        raise HTTPException(status_code=404, detail="分卷不存在")
    targets = [c for c in outline.chapters if c.volume_no == vol_no]
    if not targets:
        raise HTTPException(status_code=400, detail="该分卷下暂无章节")
    cfg = store.load_config()
    sys = ("你是资深网文大纲策划。根据总纲重写指定分卷的简介和该卷章节规划，"
           "保持与其他分卷的衔接，不要改动其他分卷。只输出JSON。")
    user_msg = (
        f"书名：{outline.title}\n题材：{cfg.genre}\n总纲：{outline.intro}\n"
        f"要重写的分卷：{vol.name}（当前简介：{vol.intro}）\n"
        f"该卷共 {len(targets)} 章。请输出JSON："
        '{"intro":"分卷简介","chapters":[{"title":"章名","goal":"本章目标"}...]}，'
        f"chapters 数量必须为 {len(targets)}，顺序对应。")
    if pipeline.cfg.custom_prompt:
        sys = f"{sys}\n\n{pipeline.cfg.custom_prompt}"
    raw = pipeline.llm.chat(sys, user_msg, temperature=0.7, max_tokens=2000)
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    vol.intro = str(data.get("intro", vol.intro))
    new_chs = data.get("chapters", [])
    for i, ch in enumerate(targets):
        if i < len(new_chs):
            ch.title = str(new_chs[i].get("title", ch.title))
            ch.goal = str(new_chs[i].get("goal", ch.goal))
    vol_words = count_words("volume", {"name": vol.name, "intro": vol.intro,
                                       "chapters": [{"title": c.title, "goal": c.goal}
                                                    for c in targets]})
    if store.versioned_enabled():
        v = store.save_outline(outline, commit=True, source="ai")
        if v is not None:
            r = settle_ledger.settle_version(store, "outline", "all", v, vol_words,
                                             mode=billing["mode"], hq=billing["hq"],
                                             project_id=project_id)
            _record_settle(r, vol_words, outputs, entries, "outline", "all", v)
    else:
        store.save_outline(outline)
        r = settle_ledger.settle_product(store, "volume", str(vol_no), vol_words,
                                         mode=billing["mode"], hq=billing["hq"],
                                         project_id=project_id)
        entries.append({"entry": r.get("entry", ""), "charged": r.get("charged", 0.0),
                        "words": vol_words})


def _record_chapter_feedback(store: ProjectStore, no: int, results: list[dict],
                             outputs: list[dict], entries: list[dict]) -> None:
    """聚合一章的结算到账目 + 写一条章节反馈到 run 侧写 outputs（F1）。

    反馈语义：
    - v = 本次快照后新增版本中的最新号（多版本场景取最大 = 最终稿）；
    - kept_previous = selected 不是该版本 → 质检未过等场景下保留了旧稿，新稿为候选；
    - quality = 该版本的质检记录（score/passed/issue_count/...），无质检记录为 None。
    """
    new_versions: list[int] = []
    words = 0
    for r in results:
        v = None
        try:
            v = int(str(r.get("entry", "")).rsplit(":v", 1)[1])
        except (IndexError, ValueError):
            pass
        if v is not None:
            new_versions.append(v)
        if r:
            entries.append({"entry": r.get("entry", ""), "charged": r.get("charged", 0.0),
                            "words": int(r.get("words") or 0)})
            words = int(r.get("words") or 0)
    if not new_versions:
        return  # 幂等无新增版本：账目已入，无反馈条目
    latest = max(new_versions)
    rec = (store.load_chapter_versions() or {}).get(str(no)) or {}
    selected = rec.get("selected")
    history = {h.get("v"): h for h in (rec.get("history") or [])
               if isinstance(h, dict) and h.get("v") is not None}
    quality = (history.get(latest) or {}).get("quality")
    kept = selected is not None and selected != latest
    outputs.append({"kind": "chapter", "key": str(no), "v": latest, "no": no,
                    "words": words, "quality": quality, "selected": selected,
                    "kept_previous": bool(kept),
                    "candidate_version": latest if kept else None})


def _exec_chapters(store: ProjectStore, pipeline, handle, params: dict, billing: dict,
                   outputs: list[dict], entries: list[dict]) -> None:
    """章节：按 scope 循环（快照版本 → 写章 → 结算新增版本），章末判停止/余额。
    U1：每章写前/写后更新 task.json progress——task_done/task_total 为本次任务视角
    （任意 scope 0→100%），chapters_done/chapters_total 为书级完成数（N2：重生成
    已完成章时取 len(done | written) 并集，不重复计数）；F1：结算后回读版本索引，
    反馈 quality/kept_previous/candidate_version 进 run 侧写。
    """
    from . import core

    project_id = billing["project_id"]
    outline = store.load_outline()
    if not outline or not outline.chapters:
        raise HTTPException(status_code=400, detail="尚无大纲章节，无法生成")
    total = len(outline.chapters)
    existing = store.list_chapters()
    done = {c.no for c in existing}
    baseline_done = len(done)  # 运行前已完成章数（书级）
    scope = params.get("scope")
    if scope == "single":
        numbers = [params["ch_no"]]
    elif scope == "range":
        end = min(int(params.get("to_ch") or total), total)
        numbers = list(range(params["from_ch"], end + 1))
    else:  # all：全部未完成章
        numbers = [n for n in range(1, total + 1) if n not in done]
    research = store.load_research()
    _update_task_progress(store, phase="chapter", chapters_total=total,
                          chapters_done=baseline_done, current_chapter=0,
                          task_total=len(numbers), task_done=0)
    written: set[int] = set()
    for no in numbers:
        if handle.stop_event.is_set():
            break
        if no < 1 or no > total:
            raise HTTPException(status_code=400,
                                detail=f"章节号非法（大纲共 {total} 章）：{no}")
        if core._is_locked(store, "chapter", no):
            logger.info("agent run 跳过锁定章节: project=%s no=%d", project_id, no)
            continue
        if settle_ledger.should_stop_now(store, billing["mode"]):
            handle.error = "余额已耗尽，已停止生成。充值后可重新触发。"
            break
        plan = outline.chapters[no - 1]
        prev = settle_ledger.snapshot_chapter_versions(store, no)
        _update_task_progress(store, chapters_total=total,
                              chapters_done=len(done | written), current_chapter=no,
                              task_total=len(numbers), task_done=len(written))
        pipeline.chapter_writer.write(no, plan.title, plan.goal, research,
                                      is_final=(no == total))
        results = settle_ledger.settle_chapter_versions(store, no, prev,
                                                        mode=billing["mode"],
                                                        hq=billing["hq"],
                                                        project_id=project_id)
        _record_chapter_feedback(store, no, results, outputs, entries)
        written.add(no)
        _update_task_progress(store, chapters_total=total,
                              chapters_done=len(done | written), current_chapter=no,
                              task_total=len(numbers), task_done=len(written))
        if settle_ledger.should_stop_now(store, billing["mode"]):
            handle.error = "余额已耗尽，已停止生成。充值后可重新触发。"
            break
