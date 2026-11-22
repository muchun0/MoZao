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
4. **prompt 注入只进内存态**：人设（agent_prompts 当前版本，默认=角色底色 md 全文）
   注入 `cfg.persona_override`（role_prompt 位置，system 开头），user_prompt 清洗后追加
   其后；Skill 拼入 `cfg.skill_prompt_append`，均不落 config.json。建书时填写的
   custom_prompt 仍由 config.json 持久化、经 `_skill_append` 注入（不再被人设覆盖）。
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.reader_ledger import commit_chapter_account
from novel_gen.pipeline import stale_guard
from novel_gen.pipeline import handover
from novel_gen.pipeline import ledger_proposal
from novel_gen.pipeline import task_contract
from novel_gen.pipeline import task_snapshot
from novel_gen.pipeline import task_state
from novel_gen.store.run_context import RunContext
from novel_gen.store import run_context as run_files
from novel_gen.store.schemas import Chapter
from novel_gen.store.wordcount import count_words

from . import agent_prompts
from .error_text import sanitize_error
from . import db
from . import gates
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
    # REQ-R32：外部派发必须显式声明角色（off 模式不强制，保持旧客户端兼容）
    role: str = ""
    params: dict = Field(default_factory=dict)
    user_prompt: str = ""
    skill_ids: list[int] = Field(default_factory=list)
    # 档3：已知前文不一致仍要继续（门禁 409 后的「仍然继续」出口）
    allow_inconsistent: bool = False


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
    """手写/内联采纳章守卫：单章拒重写；范围起点 ≤ 最大受保护章号拒。

    判定口径：`chapter_versions.json` 索引中**当前 selected 版本**的
    `source in {user, inline}`（`save_chapter` 用户编辑保存时记 source=user，
    R25 内联采纳记 source=inline），叠加存量 HEAD 同口径兜底（无版本索引的 legacy 项目）。
    不用 `user_managed` 标记：`select()`（用户选择/回滚版本）也置位它但只改 selected，
    选中 AI 版本后 source 仍为 ai，不算手写——旧实现误用 user_managed 会把
    「用户只是选中 AI 重生成版本」判成手写（存量缺陷，见 docs/decisions.md）。
    """
    if agent != "writer.chapter":
        return
    if params.get("scope") == "single":
        if _is_user_chapter(store, params["ch_no"]):
            raise HTTPException(
                status_code=400,
                detail="该章为人工修订或内联采纳内容，AI 不自动重写；如需修改请使用「编辑本章」手动调整")
        return
    user_max = max((no for no in _user_chapter_numbers(store)), default=0)
    if not user_max:
        return
    scope = params.get("scope")
    start = 1 if scope == "all" else int(params.get("from_ch") or 1)
    if start <= user_max:
        raise HTTPException(
            status_code=400,
            detail=f"第 {start} 章起范围内含人工修订/内联采纳内容（到第 {user_max} 章），"
                   f"重新生成会覆盖，已禁止；请从第 {user_max + 1} 章开始")


# R25 基座：内联采纳（inline）与用户手写（user）同属「人已拍板、AI 不得自动覆盖」的受保护来源，
# 统一在此声明，避免三处判定各写一份字面量导致漏改。
_PROTECTED_SOURCES = {"user", "inline"}


def _selected_version_is_user(rec: dict) -> bool:
    """版本索引单条记录中，当前 selected 版本是否受保护来源（user/inline）。

    `history` 的 source 在存档时可靠记录（versioned 门面与 legacy 路径一致）；
    `select()` 只改 selected + user_managed，不改 history——选中 AI 版本后
    selected 指向的 history 条目 source 仍为 ai。selected 缺失/条目不在
    history（索引损坏）→ False，由调用方兜底查 HEAD。
    """
    if not isinstance(rec, dict):
        return False
    selected = rec.get("selected")
    for h in rec.get("history") or []:
        if isinstance(h, dict) and h.get("v") == selected:
            return h.get("source") in _PROTECTED_SOURCES
    return False


def _is_user_chapter(store: ProjectStore, no: int) -> bool:
    """单章是否受保护（selected 版本 source in {user,inline} 优先，存量 HEAD 兜底）。"""
    rec = (store.load_chapter_versions() or {}).get(str(no)) or {}
    if _selected_version_is_user(rec):
        return True
    ch = store.load_chapter(no)
    return ch is not None and ch.source in _PROTECTED_SOURCES


def _user_chapter_numbers(store: ProjectStore) -> list[int]:
    """全部受保护章号（selected 版本 ∪ 存量 HEAD，source in {user,inline}）。"""
    numbers: set[int] = set()
    for no, rec in (store.load_chapter_versions() or {}).items():
        if _selected_version_is_user(rec) and str(no).isdigit():
            numbers.add(int(no))
    for c in store.list_chapters():
        if c.source in _PROTECTED_SOURCES:
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
    return round_money(agent_price(price_for(words), discount))  # 统一高质量单价


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


def _operator_name(user: dict | None) -> str:
    """操作者显示名（只用于 state/审计留痕，不落 open_id 等技术标识）。"""
    if not isinstance(user, dict):
        return ""
    for key in ("username", "nickname", "name", "email"):
        val = str(user.get(key) or "").strip()
        if val:
            return val
    return ""


def _update_task_progress(store: ProjectStore, **kw) -> None:
    """运行中更新进度（U1）：失败只留痕，不阻断生成线程。

    R30 M0：task.json 是 `runs/<task_id>/state.json` 的投影。state 存在时先写 state
    再投影（单一权威源），不存在时走原逻辑（开关关闭行为逐字不变）。
    原子写（tmp + os.replace）避免 Windows 并发读读到半截 JSON → 轮询误停（R1）。
    """
    task_id = task_state.task_id_from_task_json(store)
    if task_id:
        try:
            updated = task_state.update_progress(store, task_id, **kw)
            if updated is not None:
                task_state.project_now(store, task_id)
                return
        except Exception:
            logger.exception("任务状态进度更新失败（降级回写 task.json）: project=%s",
                             store.dir.name)
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


def _baseline_refs(manifest: dict) -> list:
    """基线引用（快照 in_refs）：world/outline 选中版本 + 已写最大章。"""
    base = manifest.get("baseline") or {}
    refs = []
    if base.get("world_v") is not None:
        refs.append(f"world/v{base['world_v']}.json")
    if base.get("outline_v") is not None:
        refs.append(f"outline/v{base['outline_v']}.json")
    return refs


def start_agent_run(store: ProjectStore, req: AgentRunReq, user: dict,
                    internal: dict | None = None) -> dict:
    """统一入口：守卫 → 占槽 → task.json → 起线程 → 202 返回体。

    `internal` 非空 = Supervisor 内部派发（REQ-R32）：
    {"node": "chapter:3", "role": "writer", "attempt": 1, "action": "write|review|repair",
     "task_id": "..."}。内部派发跳过每日上限与外部一致性门禁，params 由编排层规整，
    但仍过白名单/余额/锁定与技能限额。
    """
    from . import core  # 延迟 import：避免循环引用

    uid = db.project_owner(store.dir.name)
    mode = resolve_billing_mode(uid)
    guard_balance(mode, uid)
    guard_agent_product(req.agent)
    if internal:
        params = dict(req.params or {})
        params.update({"_internal": True, "_node": str(internal.get("node") or ""),
                       "_role": str(internal.get("role") or ""),
                       "_attempt": int(internal.get("attempt") or 0),
                       "_action": str(internal.get("action") or "")})
        if internal.get("task_id"):
            params["_task_id"] = str(internal["task_id"])
        from novel_gen.pipeline import roles as role_contract
        try:
            role_contract.validate_dispatch(
                str(internal.get("role") or ""), req.agent,
                str(internal.get("node") or ""), str(internal.get("action") or ""),
                internal=True)
        except role_contract.RoleContractError as exc:
            raise HTTPException(status_code=400, detail=f"内部派发契约不满足：{exc}") from exc
    else:
        params = guard_params(req.agent, req.params)
        cfg0 = store.load_config()
        if str(getattr(cfg0, "supervisor_mode", "off")) in ("on", "shadow"):
            # R32 F2：非法 role↔agent↔node 入口 400，不静默改派；
            # 老客户端没带 role 时按 agent 推断（仍走同一套角色契约校验）。
            from novel_gen.pipeline import roles as role_contract
            node = {"researcher": "stage:research", "architect.outline": "stage:outline",
                    "architect.world": "stage:world", "writer.chapter": "stage:chapters",
                    "architect.volume": "stage:outline"}.get(req.agent, "")
            role = str(req.role or "") or role_contract.infer_role(req.agent)
            try:
                role_contract.validate_dispatch(role, req.agent, node)
            except role_contract.RoleContractError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            req.role = role
    # REQ-创作阶段审阅门 M0：世界门前置（门 1 必须已通过）——在占槽/计费之前拒绝
    gates.guard_agent(store, req.agent)
    guard_locked(store, req.agent, params)
    guard_user_chapter(store, req.agent, params)
    # 档3：章节一致性门禁——必须在计费/占槽之前（被拦截的请求不占槽、不产生任何扣费）
    if not internal:
        verdict = stale_guard.guard_staleness(
            store, params, cfg=store.load_config(), agent=req.agent,
            allow_inconsistent=bool(req.allow_inconsistent))
        if verdict.get("blocked"):
            raise HTTPException(status_code=409, detail=verdict.get("detail") or {})
        if verdict.get("unstable_chapters"):
            # 已知不一致继续：标记本次 run，供记忆标注与 upstream.unstable 落痕
            params["_allow_inconsistent"] = True
            params["_stale_chapters"] = list(verdict["unstable_chapters"])
    guard_skill_limits(req.skill_ids)
    if not internal:
        _check_daily_run_limit(store)  # P2-F2：单项目每日上限（0=不限）

    task_id = str((internal or {}).get("task_id") or uuid.uuid4().hex[:12])
    run_id = uuid.uuid4().hex
    handle = core._acquire_slot(store.dir.name)
    # P0-1：新一轮开始前清掉上一次失败残留，否则前端会一直显示旧错误、并把新运行当失败
    try:
        st = store.load_state()
        if st.error:
            st.error = ""
            store.save_state(st)
    except Exception:
        logger.exception("清理上次失败状态失败（降级）: project=%s", store.dir.name)
    cfg = store.load_config()
    state_on = bool(getattr(cfg, "task_state_machine", False))
    try:
        core._mark_task(store, "agent_run", task_id=task_id, agent=req.agent,
                        params=params, run_id=run_id, user_prompt=req.user_prompt,
                        skill_ids=list(req.skill_ids),
                        node=str((internal or {}).get("node") or ""),
                        role=str((internal or {}).get("role") or req.role or ""))
        if state_on:
            manifest = task_contract.build_manifest(
                store, task_id, req.agent, params,
                operator=_operator_name(user), user_prompt=req.user_prompt,
                skill_ids=tuple(req.skill_ids))
            task_state.create_state(store, manifest, state=task_state.QUEUED)
            task_state.transition(store, task_id, task_state.RUNNING, by="start",
                                  reason=f"run started agent={req.agent}")
            task_state.project_now(store, task_id)
            task_snapshot.record_step(
                store, task_id, "run_start",
                in_refs=_baseline_refs(manifest),
                model=manifest.get("model", ""),
                switches=task_snapshot.switches_of(cfg),
                extra={"agent": req.agent, "scope": manifest.get("scope"),
                       "node": params.get("_node", ""), "role": params.get("_role", "")})
        # 进度阶段按 agent 先落一次，避免首屏把章节 run 显示成「正在生成大纲」
        try:
            _update_task_progress(store, phase={
                "writer.chapter": "chapter",
                "architect.outline": "outline",
                "architect.world": "world",
                "architect.volume": "volume",
            }.get(req.agent, "outline"))
        except Exception:
            logger.exception("初始进度阶段写入失败（降级）: project=%s agent=%s",
                             store.dir.name, req.agent)
        t = threading.Thread(
            target=_run_agent_task,
            args=(store, handle, req.agent, params, req.user_prompt,
                  tuple(req.skill_ids), task_id, run_id, internal),
            daemon=True)
        handle.thread = t
        handle.task_id = task_id
        t.start()
    except Exception:
        core._release_slot(store.dir.name)
        core._clear_task(store)
        raise
    logger.info("agent run 启动: project=%s agent=%s task=%s run=%s node=%s role=%s",
                store.dir.name, req.agent, task_id, run_id,
                params.get("_node", ""), params.get("_role", ""))
    return {
        "started": True,
        "task_id": task_id,
        "run_id": run_id,  # R27 分步编排据此把 run 与阶段绑定（纯新增字段）
        "agent": req.agent,
        "billing_mode": mode,
        "estimated_price": _estimated_price(req.agent, params, store, mode),
        "node_id": params.get("_node", ""),
        "role": params.get("_role", ""),
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


def current_task_progress(store: ProjectStore) -> dict:
    """读取当前运行中任务的进度快照（供 GET /projects/{id} 暴露真实进度）。

    前端原先只能拿 chapters_done/chapters_total：大纲/世界观阶段 chapters_done 恒为 0，
    进度条长期停在 0% 后突跳 100%。真实阶段与 task_done/task_total 只存在 task.json 里，
    这里把它一并带回，让项目详情接口成为单一进度来源（轮询不依赖 task_id）。

    只读 + 全程兜底：任何异常都降级为空字典，绝不影响项目详情本身的返回。
    """
    try:
        task_file = store.dir / "task.json"
        if not task_file.exists():
            return {}
        meta = json.loads(task_file.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or meta.get("kind") != "agent_run":
            return {}
        progress = dict(meta.get("progress") or {})
        started_at = meta.get("started_at")
        if started_at:
            try:
                progress["elapsed"] = round(max(0.0, time.time() - float(started_at)), 1)
            except (TypeError, ValueError):
                pass
        return {
            "task_id": meta.get("task_id", ""),
            "agent": meta.get("agent", ""),
            "started_at": started_at,
            "progress": progress,
        }
    except Exception:
        # 进度是增强信息：读不到只降级，不能让项目详情 500
        logger.exception("task.json 进度读取失败（降级）: project=%s", store.dir.name)
        return {}


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
                      "node_id": meta.get("node", ""), "role": meta.get("role", ""),
                      "started_at": meta.get("started_at"),
                      "progress": {**progress, "elapsed": elapsed}}
            state = task_state.load_state(store, task_id)
            if state is not None and state.get("state") == task_state.PENDING_REVIEW:
                # R30：待人工任务不按 running 轮询（AgentPanel 停止轮询并指向体检页）
                return {**common, "status": "pending_review",
                        "handover_ids": list(state.get("pending_handover_ids") or []),
                        "resume_chapters": list(
                            (state.get("resume") or {}).get("remaining_chapters") or [])}
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
        "node_id": run.get("node", ""), "role": run.get("role", ""),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "outputs": run.get("outputs") or [],
        "billing": run.get("billing") or {},
        "error": sanitize_error(run.get("error", ""), ""),
    }


def pause_agent_run(store: ProjectStore, task_id: str, user: dict | None = None) -> dict:
    """人工暂停（R30 human_stop）：当前章完成后转 pending_review，等待放行/终止。

    只允许停在 state 为 running 的任务；已待审/已结束返回 409，避免重复暂停。
    """
    from . import core

    cfg = store.load_config()
    if not bool(getattr(cfg, "task_state_machine", False)) or \
            not bool(getattr(cfg, "handover_v1", False)):
        raise HTTPException(status_code=404, detail="R30 编排能力未启用")
    state = task_state.load_state(store, task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if state.get("state") == task_state.PENDING_REVIEW:
        return {"paused": True, "task_id": task_id, "already_pending": True}
    if state.get("state") != task_state.RUNNING:
        raise HTTPException(status_code=409, detail=f"任务当前状态不可暂停：{state.get('state')}")
    handle = core._RUNS.get(store.dir.name)
    if handle is None or handle.task_id != task_id or handle.thread is None \
            or not handle.thread.is_alive():
        raise HTTPException(status_code=404, detail="任务不在运行中")
    handle.pause_requested = True
    handle.stop_event.set()
    logger.info("人工暂停请求: project=%s task=%s by=%s",
                store.dir.name, task_id, _operator_name(user))
    return {"paused": True, "task_id": task_id}


def resume_pending_run(store: ProjectStore, task_id: str, user: dict | None = None) -> dict:
    """放行续跑（R30 approve）：沿用 task_id，只跑剩余章，不重写已完成章、不重复结算。

    余额校验在占槽之前；任何异常都会把状态退回 pending_review 并释放槽位。
    """
    from . import core

    cfg = store.load_config()
    if not bool(getattr(cfg, "task_state_machine", False)) or \
            not bool(getattr(cfg, "handover_v1", False)):
        raise HTTPException(status_code=404, detail="R30 编排能力未启用")
    state = task_state.load_state(store, task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if state.get("state") != task_state.PENDING_REVIEW:
        raise HTTPException(status_code=409, detail=f"任务不在待审状态：{state.get('state')}")
    operator = _operator_name(user)
    remaining = [int(x) for x in ((state.get("resume") or {}).get("remaining_chapters") or [])]
    if not remaining:
        task_state.transition(store, task_id, task_state.DONE, by=operator,
                              reason="approve: no remaining chapters")
        core._clear_task(store)
        return {"resumed": False, "status": "done", "task_id": task_id}

    agent = str(state.get("agent") or "")
    if agent not in ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail=f"任务 agent 非法：{agent}")
    project_id = store.dir.name
    uid = db.project_owner(project_id)
    mode = resolve_billing_mode(uid)
    guard_balance(mode, uid)  # 余额不足时不允许放行续跑（避免跑到一半再停）

    params = dict(state.get("params") or {})
    params["_chapters"] = remaining
    prompt = str(state.get("user_prompt") or "")
    skills = tuple(int(x) for x in (state.get("skill_ids") or []))
    run_id = uuid.uuid4().hex
    handle = core._acquire_slot(project_id)
    try:
        core._mark_task(store, "agent_run", task_id=task_id, agent=agent,
                        params=params, run_id=run_id, user_prompt=prompt,
                        skill_ids=list(skills), wait_human=False)
        # 放行前关闭该任务的阻塞工单（动作留审计）
        for h in handover.pending_blocking_for_task(store, task_id):
            if h.get("status") == handover.OPEN:
                try:
                    handover.resolve(store, str(h["handover_id"]), "approve",
                                     by=operator, reason="放行续跑")
                except Exception:
                    logger.exception("工单放行标记失败（降级）: %s", h.get("handover_id"))
        task_state.clear_pending(store, task_id)
        task_state.transition(store, task_id, task_state.RUNNING, by=operator,
                              reason="approve resume")
        task_state.project_now(store, task_id)
        task_snapshot.record_step(
            store, task_id, "run_resume",
            model=str(getattr(cfg.llm, "model", "") or ""),
            switches=task_snapshot.switches_of(cfg),
            extra={"remaining": remaining, "operator": operator})
        t = threading.Thread(
            target=_run_agent_task,
            args=(store, handle, agent, params, prompt, skills, task_id, run_id),
            daemon=True)
        handle.thread = t
        handle.task_id = task_id
        t.start()
    except Exception:
        core._release_slot(project_id)
        try:
            task_state.transition(store, task_id, task_state.PENDING_REVIEW,
                                  by="system", reason="resume failed", force=True)
        except Exception:
            logger.exception("放行失败后状态回退失败: project=%s task=%s", project_id, task_id)
        raise
    logger.info("放行续跑: project=%s task=%s chapters=%s by=%s",
                project_id, task_id, remaining, operator)
    return {"resumed": True, "task_id": task_id, "run_id": run_id,
            "chapters": remaining, "billing_mode": mode}


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
                    user_prompt: str, skill_ids: tuple, task_id: str, run_id: str,
                    internal_meta: dict | None = None) -> None:
    """run 生命周期：begin_run → 分发执行 → 收尾结算 → 侧写留痕 → 清任务释放槽。"""
    from . import core  # 延迟 import：避免循环引用

    project_id = store.dir.name
    trace_id = uuid.uuid4().hex
    status = "ok"
    outputs: list[dict] = []
    entries: list[dict] = []
    prompt_version, prompt_sha256 = "", ""
    node_id = str((internal_meta or {}).get("node") or params.get("_node") or "")
    role = str((internal_meta or {}).get("role") or params.get("_role") or "")
    attempt = int((internal_meta or {}).get("attempt") or params.get("_attempt") or 0)
    ctx = RunContext(run_id=run_id, agent=agent, skill_ids=tuple(skill_ids),
                     user_prompt=user_prompt, task_id=task_id,
                     node_id=node_id, role=role, attempt=attempt)
    store.begin_run(ctx)
    cfg = None
    plan = None
    guard_obj = None
    try:
        cfg = store.load_config()
        # R32：内部派发沿用项目级预算（LLM 调用/Token 跨 run 累计），off 时保持原行为
        if (internal_meta or params.get("_internal")) and str(
                getattr(cfg, "supervisor_mode", "off")) in ("on", "shadow"):
            try:
                from novel_gen.pipeline import orchestration as orch
                plan = orch.load_plan(store)
            except Exception:
                plan = None
        uid = db.project_owner(project_id)
        mode = settle_ledger.billing_mode_of(_has_user_llm(uid))
        # 统一预运行注入：风格锚 + 用户记忆（旧路径 core._run_outline_only/_run_generation 原有逻辑）
        _apply_pre_run_injections(store, cfg, uid)
        # 人设 + user_prompt 注入（内存态，不落盘；sanitize 属阶段 4）
        prompt_text, prompt_version, prompt_sha256 = agent_prompts.get_prompt_meta(store, agent)
        pipeline = core._open_pipeline(store, trace_id=trace_id)
        guard_obj = getattr(pipeline, "guard", None)
        if plan and guard_obj is not None:
            b = plan.get("budget") or {}
            guard_obj.loop_count = int(b.get("llm_calls", 0) or 0)
            guard_obj.total_tokens = int(b.get("tokens", 0) or 0)
        # 完整人设注入到 role_prompt（persona_override，system 开头）；user_prompt 属用户可写
        # 内容，拼入前先 sanitize 剥离注入话术（人设保存时已清洗，此处清洗单次补充指令）。
        persona_parts: list[str] = [prompt_text]
        if user_prompt:
            from novel_gen.safety import sanitize_system_text
            extra_prompt = sanitize_system_text(user_prompt)
            if extra_prompt:
                persona_parts.append(extra_prompt)
        pipeline.cfg.persona_override = "\n\n".join(persona_parts)
        pipeline.cfg.skill_prompt_append = _build_skill_append(skill_ids)
        billing = {"mode": mode, "project_id": project_id}

        action = str((internal_meta or {}).get("action") or params.get("_action") or "")
        if action == "repair":
            _exec_repair(store, pipeline, params, billing, outputs, entries)
        elif action == "review":
            _exec_review(store, pipeline, params)
        elif action == "loremaster":
            _exec_loremaster(store, pipeline, params)
        elif agent == "researcher":
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
        # P0-1：错误必须同时落 handle（本次运行）与 state.json（运行结束后仍可见），
        # 且对外只给脱敏后的一句话，不把异常类名 / provider / api_key 暴露给作者。
        msg = sanitize_error(exc, "生成没有完成，请重试（已写好的章节不会丢失）")
        handle.error = msg
        try:
            st = store.load_state()
            st.error = msg
            store.save_state(st)
        except Exception:
            logger.exception("失败状态落盘失败（降级，仍保留 handle.error）: project=%s", project_id)
        logger.exception("agent run 失败: project=%s agent=%s task=%s",
                         project_id, agent, task_id)
    finally:
        store.end_run()
        if status in ("ok", "stopped", "pending_review"):
            # 成功/手动停止不算失败：清掉历史错误，避免下一批仍显示红字
            try:
                st = store.load_state()
                if st.error:
                    st.error = ""
                    store.save_state(st)
            except Exception:
                logger.exception("清理历史错误状态失败（降级）: project=%s", project_id)
        if getattr(handle, "pending_review", False):
            status = "pending_review"
        # R32：内部派发把项目级 LLM/Token 预算回写计划（绝对量，避免并发累加错账）
        if plan and guard_obj is not None:
            try:
                from novel_gen.pipeline import orchestration as orch
                with orch.lock_for(store):
                    plan = orch.load_plan(store) or plan
                    b = plan.setdefault("budget", {})
                    b["llm_calls"] = int(getattr(guard_obj, "loop_count", 0) or 0)
                    b["tokens"] = int(getattr(guard_obj, "total_tokens", 0) or 0)
                    orch.save_plan(store, plan)
            except Exception:
                logger.exception("项目级预算回写失败（降级）: project=%s", project_id)
        _write_run_sidecar(store, ctx, status, params, prompt_version, prompt_sha256,
                           trace_id, outputs, entries, handle)
        _finish_task_state(store, task_id, status, ctx, cfg, outputs)
        if status == "pending_review":
            # 待人工：保留 task.json 供进度展示（state 已标 wait_human，启动重拾会跳过）
            task_state.project_now(store, task_id)
        else:
            core._clear_task(store)
        core._release_slot(project_id)
        # R27：阶段编排同步（无 generation_stage.json 时立即返回；异常只留日志，
        # 不影响本次 run 已完成的结算与收尾）
        try:
            from . import generation_flow
            generation_flow.on_run_finished(store, agent, run_id, status,
                                            node_id=node_id, role=role)
        except Exception:
            logger.exception("分步流程状态同步失败（降级）: project=%s agent=%s",
                             project_id, agent)
        logger.info("agent run 结束: project=%s agent=%s task=%s status=%s",
                    project_id, agent, task_id, status)


def _finish_task_state(store: ProjectStore, task_id: str, status: str, ctx: RunContext,
                       cfg, outputs: list[dict]) -> None:
    """收尾写快照 + 状态流转（M0）。任何异常只降级，不能影响已完成的结算/清任务。"""
    if not task_state.state_path(store, task_id).exists():
        return
    try:
        refs = [f"{o.get('kind')}:{o.get('key')}:v{o.get('v')}"
                for o in outputs if o.get("v") is not None]
        task_snapshot.record_step(
            store, task_id, "run_end", out_refs=refs,
            model=str(getattr(cfg.llm, "model", "") if cfg else ""),
            switches=task_snapshot.switches_of(cfg) if cfg else {},
            elapsed_ms=int(max(0.0, time.time() - ctx.started_at) * 1000),
            degraded=status in ("failed", "stopped"),
            extra={"status": status})
        target = {"ok": task_state.DONE, "stopped": task_state.ABORTED,
                  "pending_review": task_state.PENDING_REVIEW,
                  "failed": task_state.FAILED}.get(status, task_state.FAILED)
        task_state.transition(store, task_id, target, by="system", reason=f"run {status}")
    except Exception:
        logger.exception("任务状态收尾失败（降级）: project=%s task=%s status=%s",
                         store.dir.name, task_id, status)


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
            "node_id": ctx.node_id,
            "role": ctx.role,
            "attempt": ctx.attempt,
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
            "error": sanitize_error(getattr(handle, "error", None), ""),
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
                                    mode=billing["mode"],
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
                                    mode=billing["mode"],
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
    _persona = (getattr(pipeline.cfg, "persona_override", "") or "").strip()
    if _persona:
        sys = f"{sys}\n\n{_persona}"
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
                                             mode=billing["mode"],
                                             project_id=project_id)
            _record_settle(r, vol_words, outputs, entries, "outline", "all", v)
    else:
        store.save_outline(outline)
        r = settle_ledger.settle_product(store, "volume", str(vol_no), vol_words,
                                         mode=billing["mode"],
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


def _resolve_chapter_numbers(outline, params: dict, done: set) -> list[int]:
    """按 scope 解析本次要生成的章节号列表（single/range/all；_chapters=待审放行）。"""
    total = len(outline.chapters)
    explicit = params.get("_chapters")
    if isinstance(explicit, (list, tuple)) and explicit:
        nums: list[int] = []
        for x in explicit:
            try:
                nums.append(int(x))
            except (TypeError, ValueError):
                continue
        return nums
    scope = params.get("scope")
    if scope == "single":
        return [params["ch_no"]]
    if scope == "range":
        end = min(int(params.get("to_ch") or total), total)
        return list(range(params["from_ch"], end + 1))
    # all：全部未完成章
    return [n for n in range(1, total + 1) if n not in done]


def _generate_one_chapter(store, pipeline, outline, research, no: int, total: int,
                          billing: dict, project_id: str, outputs: list, entries: list,
                          unstable: list | None = None, task_id: str = "") -> list[dict]:
    """单章生成：快照版本 → 写章 → 结算新增版本 → 回读反馈 → R30 交接判定。

    R30：章后记录快照步（输入基线/产出版本/模型/开关），再判交接与账本提案；
    判定失败只降级，绝不影响已完成的写章与结算。返回本轮新建的交接工单。
    """
    cfg = store.load_config()
    t0 = time.time()
    plan = outline.chapters[no - 1]
    prev = settle_ledger.snapshot_chapter_versions(store, no)
    pipeline.chapter_writer.write(no, plan.title, plan.goal, research,
                                  is_final=(no == total),
                                  unstable_chapters=unstable or None)
    # REQ-R10 D1：章后确定性入账（伏笔状态推进），失败降级不阻断写章/结算
    commit_chapter_account(store, store.load_config(), no)
    results = settle_ledger.settle_chapter_versions(store, no, prev,
                                                    mode=billing["mode"],
                                                    project_id=project_id)
    # 章后账本统一提交（人物状态/章末状态/揭示跟踪/读者账本）：
    # 与 CLI resume 同源同口径；结算之后独立执行，失败降级不阻断写章与扣费
    pipeline.commit_chapter_ledgers(no, source="web_generation")
    _record_chapter_feedback(store, no, results, outputs, entries)

    snap_ref = ""
    if task_id:
        state = task_state.load_state(store, task_id) or {}
        in_refs = _baseline_refs(state)
        in_refs.append(f"chapters/{no}@{plan.title}")
        out_refs = [str(r.get("entry") or "") for r in results if r.get("entry")]
        snap_ref = task_snapshot.record_step(
            store, task_id, "chapter_write", in_refs=in_refs, out_refs=out_refs,
            model=str(getattr(cfg.llm, "model", "") or ""),
            switches=task_snapshot.switches_of(cfg),
            elapsed_ms=int(max(0.0, time.time() - t0) * 1000),
            extra={"chapter": no, "title": plan.title})
    created: list[dict] = []
    if task_id and bool(getattr(cfg, "handover_v1", False)):
        created = handover.evaluate_after_chapter(store, cfg, task_id, no, snap_ref)
    if task_id and bool(getattr(cfg, "ledger_proposal", False)):
        ledger_proposal.detect_new_entities(store, cfg, task_id, no)
    return created


def _record_role_invocations(store: ProjectStore, no: int, task_id: str = "") -> None:
    """章后确定性记录角色产出（REQ-R32 §8.3）：writer/reviewer/loremaster 各一条。"""
    from novel_gen.pipeline import orchestration as orch
    try:
        ch = store.load_chapter(no)
        if ch is None:
            return
        digest = orch.artifact_digest({"ch": no, "content": ch.content or ""})
        orch.record_role_invocation(store, node_id=f"chapter:{no}", role="writer",
                                    status="ok", digest=digest, task_id=task_id, words=len(ch.content or ""))
        review = next((r for r in store.load_review() if int(r.get("chapter", 0)) == int(no)), None)
        if review is not None:
            qc = review.get("quality_check") or {}
            orch.record_role_invocation(
                store, node_id=f"chapter:{no}", role="reviewer",
                status="ok" if qc.get("passed") else "failed",
                digest=orch.artifact_digest(qc), task_id=task_id)
        commit = store.load_chapter_commit() or {}
        if str(no) in (commit.get("chapters") or {}):
            orch.record_role_invocation(store, node_id=f"chapter:{no}", role="loremaster",
                                        status="ok", task_id=task_id)
    except Exception:
        logger.exception("角色产出记录失败（降级）: project=%s ch=%s", store.dir.name, no)


def _exec_review(store: ProjectStore, pipeline, params: dict) -> None:
    """审校编辑内部步（REQ-R32）：只跑质检并落 review.json，不改正文。"""
    no = int(params.get("ch_no") or params.get("_chapters", [0])[0] or 0)
    outline = store.load_outline()
    chapter = store.load_chapter(no) if no else None
    if outline is None or chapter is None or no < 1 or no > len(outline.chapters):
        raise HTTPException(status_code=400, detail=f"审校节点缺少章节：{no}")
    plan = outline.chapters[no - 1]
    cfg = store.load_config()
    from novel_gen.pipeline.quality import QualityChecker
    qc = QualityChecker(cfg, pipeline.llm, store, trace=pipeline.trace, guard=pipeline.guard,
                        memory=pipeline.memory)
    result = qc.check(no, plan.title, plan.goal, chapter.content, chapter.summary,
                      core_event=getattr(plan, "core_event", "") or "")
    qc.persist_review(no, result, strict=True)


def _exec_loremaster(store: ProjectStore, pipeline, params: dict) -> None:
    """设定管理员内部步（REQ-R32）：把章后账本收敛为一次统一提交。"""
    no = int(params.get("ch_no") or params.get("_chapters", [0])[0] or 0)
    if no < 1 or store.load_chapter(no) is None:
        raise HTTPException(status_code=400, detail=f"账本节点缺少章节：{no}")
    pipeline._commit_after_chapter(no, silent=True)


def _exec_repair(store: ProjectStore, pipeline, params: dict, billing: dict,
                 outputs: list[dict], entries: list[dict]) -> None:
    """有界修复内部步（REQ-R32 F5）：复用 ChapterReviser 定点改稿 + 复检 + 结算。

    修复范围由服务端确定性派生（review.json），不接受客户端传入的自由文本。
    """
    from novel_gen.pipeline.reviser import ChapterReviser
    from novel_gen.pipeline import supervisor as sup
    no = int(params.get("ch_no") or params.get("_chapters", [0])[0] or 0)
    outline = store.load_outline()
    chapter = store.load_chapter(no) if no else None
    if outline is None or chapter is None or no < 1 or no > len(outline.chapters):
        raise HTTPException(status_code=400, detail=f"修复节点缺少章节：{no}")
    plan = outline.chapters[no - 1]
    review = next((r for r in store.load_review() if int(r.get("chapter", 0)) == no), {})
    cfg = store.load_config()
    focus = params.get("_repair_focus") or sup.repair_scope_text(review)
    reviser = ChapterReviser(cfg, pipeline.llm, store, role_prompt=pipeline.cfg.persona_override or "")
    prev = settle_ledger.snapshot_chapter_versions(store, no)
    revised = reviser.fix(no, plan.title, plan.goal, chapter.content or "",
                          focus=focus, silent=False)
    if revised and revised != (chapter.content or ""):
        store.save_chapter(
            Chapter(no=no, title=plan.title, content=revised,
                    summary=chapter.summary, goal=plan.goal),
            source="rewrite", label="r32:repair")
        results = settle_ledger.settle_chapter_versions(
            store, no, prev, mode=billing["mode"], project_id=billing["project_id"])
        _record_chapter_feedback(store, no, results, outputs, entries)
        pipeline.commit_chapter_ledgers(no, source="r32_repair")
    # 复检并落 review.json（通过与否交给 Supervisor 决策）
    from novel_gen.pipeline.quality import QualityChecker
    qc = QualityChecker(cfg, pipeline.llm, store, trace=pipeline.trace, guard=pipeline.guard,
                        memory=pipeline.memory)
    result = qc.check(no, plan.title, plan.goal, revised or chapter.content or "",
                      chapter.summary, core_event=getattr(plan, "core_event", "") or "")
    qc.persist_review(no, result, strict=True)
    _record_role_invocations(store, no, str(params.get("_task_id") or ""))


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
    numbers = _resolve_chapter_numbers(outline, params, done)
    # 档3：已知不一致继续时，过期章号随 run 透传（记忆标注 + upstream.unstable）
    unstable = (list(params.get("_stale_chapters") or [])
                if params.get("_allow_inconsistent") else [])
    research = store.load_research()
    _update_task_progress(store, phase="chapter", chapters_total=total,
                          chapters_done=baseline_done, current_chapter=0,
                          task_total=len(numbers), task_done=0)
    written: set[int] = set()
    cfg = store.load_config()
    task_id = str(params.get("_task_id") or "") or task_state.task_id_from_task_json(store)
    state_on = (bool(getattr(cfg, "task_state_machine", False)) and bool(task_id)
                and task_state.state_path(store, task_id).exists())
    pending_on = bool(getattr(cfg, "handover_v1", False)) and state_on

    def _last_ref() -> str:
        if not task_id:
            return ""
        step = task_snapshot.latest_step(store, task_id)
        return f"runs/{task_id}/snapshot.json#{step['step_id']}" if step else ""

    def _enter_pending(handover_ids: list[str], reason: str) -> None:
        """转待审：剩余章号 = 本次范围里尚未完成的章（放行时不重写已完成章）。"""
        remaining = [n for n in numbers if n not in written]
        try:
            task_state.mark_pending(store, task_id, handover_ids=handover_ids,
                                    remaining_chapters=remaining,
                                    by="system", reason=reason)
            task_state.project_now(store, task_id)
            handle.pending_review = True
            logger.info("任务停在交接点: project=%s task=%s reason=%s remaining=%s",
                        project_id, task_id, reason, remaining)
        except Exception:
            logger.exception("转待审失败（降级继续）: project=%s task=%s",
                             project_id, task_id)

    def _stop_for_budget(no: int) -> None:
        if pending_on:
            h = handover.create_budget_handover(store, task_id, no, _last_ref())
            _enter_pending([str(h.get("handover_id") or "")], "budget_exhausted")
        else:
            handle.error = "余额已耗尽，已停止生成。充值后可重新触发。"

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
            _stop_for_budget(no)
            break
        _update_task_progress(store, chapters_total=total,
                              chapters_done=len(done | written), current_chapter=no,
                              task_total=len(numbers), task_done=len(written))
        created = _generate_one_chapter(store, pipeline, outline, research, no, total,
                                        billing, project_id, outputs, entries,
                                        unstable=unstable,
                                        task_id=task_id if state_on else "")
        written.add(no)
        _record_role_invocations(store, no, task_id)
        _update_task_progress(store, chapters_total=total,
                              chapters_done=len(done | written), current_chapter=no,
                              task_total=len(numbers), task_done=len(written))
        blocking = [h for h in (created or [])
                    if h.get("blocking") and h.get("status") == handover.OPEN]
        if blocking and pending_on:
            ids = [str(h.get("handover_id") or "") for h in blocking]
            _enter_pending(ids, f"handover:{blocking[0].get('trigger')}")
            break
        if settle_ledger.should_stop_now(store, billing["mode"]):
            _stop_for_budget(no)
            break

    # 人工暂停（human_stop）：当前章已完整落盘并结算，状态停在待审等放行
    if handle.stop_event.is_set() and bool(getattr(handle, "pause_requested", False)):
        if pending_on:
            last_ch = max(written) if written else 0
            h = handover.create_human_stop_handover(store, task_id, last_ch, _last_ref())
            _enter_pending([str(h.get("handover_id") or "")], "human_stop")
