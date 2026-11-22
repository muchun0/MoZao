# -*- coding: utf-8 -*-
"""R30 交接 / 提案路由：人工处理入口（只读列表 + 放行/驳回）。

安全约束：
- 全部接口先 `_check_project_access`（越权 403 且不写盘）；
- 读取接口零 LLM、零计费、零写盘（index 缺失时按目录重建属幂等修复）；
- 动作接口只处理 open 工单/提案，重复处理 400，不存在 404；
- 能力开关关闭时统一 404（与 R22 只读接口口径一致）。
"""
from ..core import *  # noqa: F401,F403  共享 app / 鉴权 / 项目定位
from ..core_runtime import _clear_task
from .. import generate_service
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from novel_gen.pipeline import handover, ledger_proposal, task_state


def _open_store(project_id: str, user: dict):
    _check_project_access(project_id, user)
    return _find_project(project_id)


def _operator(user: dict) -> str:
    for key in ("username", "nickname", "name", "email"):
        val = str((user or {}).get(key) or "").strip()
        if val:
            return val
    return ""


class ResolveReq(BaseModel):
    action: str
    reason: str = ""


class ReasonReq(BaseModel):
    reason: str = ""


# ---------- 交接工单 ----------

@app.get("/api/projects/{project_id}/handovers")
def list_handovers(project_id: str,
                   status: str = Query("open"),
                   severity: str = Query(""),
                   limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0),
                   user: dict = Depends(_require_user)):
    """待我处理列表（只读）：按状态/严重度过滤，返回阻塞计数供顶部结论使用。"""
    store = _open_store(project_id, user)
    cfg = store.load_config()
    if not bool(getattr(cfg, "handover_v1", False)):
        raise HTTPException(status_code=404, detail="交接工单未启用")
    if status not in ("", handover.OPEN, handover.AUTO_PASSED,
                      handover.RESOLVED, handover.CLOSED):
        raise HTTPException(status_code=400, detail=f"非法状态过滤：{status}")
    if severity not in ("", "high", "mid", "low"):
        raise HTTPException(status_code=400, detail=f"非法严重度过滤：{severity}")
    items = handover.list_handovers(store, status=status, severity=severity,
                                    limit=limit, offset=offset)
    return {"items": items,
            "pending_blocking": handover.pending_count(store, blocking_only=True)}


@app.post("/api/projects/{project_id}/handovers/{handover_id}/resolve")
def resolve_handover(project_id: str, handover_id: str, req: ResolveReq,
                     user: dict = Depends(_require_user)):
    """处理工单：approve 放行（阻塞任务自动续跑）/ reject 驳回 / revise 记修正 / abort 终止。"""
    store = _open_store(project_id, user)
    cfg = store.load_config()
    if not bool(getattr(cfg, "handover_v1", False)):
        raise HTTPException(status_code=404, detail="交接工单未启用")
    operator = _operator(user)
    try:
        data = handover.resolve(store, handover_id, req.action,
                                by=operator, reason=req.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resumed = False
    task_id = str(data.get("task_id") or "")
    from .. import supervisor_service
    if req.action == "repair" and supervisor_service.mode_of(cfg) == "on":
        out = supervisor_service.repair_after_handover(store, user, handover_id)
        return {"handover": data, "resumed": True, "repair": out}
    if task_id:
        state = task_state.load_state(store, task_id)
        if state is not None and state.get("state") == task_state.PENDING_REVIEW:
            if req.action == "approve" and supervisor_service.mode_of(cfg) == "on":
                out = supervisor_service.resume(store, user, task_id)
                resumed = bool(out.get("started"))
            elif req.action == "approve":
                out = generate_service.resume_pending_run(store, task_id, user)
                resumed = bool(out.get("resumed"))
            elif req.action == "abort":
                task_state.transition(store, task_id, task_state.ABORTED,
                                      by=operator, reason="handover abort")
                _clear_task(store)
            # reject：保持 pending_review，等待作者重写/改方案后再次放行
    return {"handover": data, "resumed": resumed}


# ---------- 暂停 / 放行（任务级入口） ----------

@app.post("/api/projects/{project_id}/agent-runs/{task_id}/pause")
def pause_agent_run(project_id: str, task_id: str, user: dict = Depends(_require_user)):
    """人工暂停（human_stop）：当前章完整落盘后转 pending_review。"""
    store = _open_store(project_id, user)
    return generate_service.pause_agent_run(store, task_id, user)


@app.post("/api/projects/{project_id}/agent-runs/{task_id}/resume")
def resume_agent_run(project_id: str, task_id: str, user: dict = Depends(_require_user)):
    """放行续跑：只跑剩余章，不重写已完成章；余额不足 402。"""
    store = _open_store(project_id, user)
    return generate_service.resume_pending_run(store, task_id, user)


# ---------- 账本提案 ----------

@app.get("/api/projects/{project_id}/proposals")
def list_proposals(project_id: str,
                   status: str = Query("open"),
                   limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0),
                   user: dict = Depends(_require_user)):
    """待合并提案（只读）；未合并绝不进入 world/注入上下文。"""
    store = _open_store(project_id, user)
    cfg = store.load_config()
    if not bool(getattr(cfg, "ledger_proposal", False)):
        raise HTTPException(status_code=404, detail="账本提案未启用")
    if status not in ("", ledger_proposal.OPEN, ledger_proposal.MERGED,
                      ledger_proposal.REJECTED):
        raise HTTPException(status_code=400, detail=f"非法状态过滤：{status}")
    return {"items": ledger_proposal.list_proposals(store, status=status,
                                                    limit=limit, offset=offset)}


@app.post("/api/projects/{project_id}/proposals/{proposal_id}/merge")
def merge_proposal(project_id: str, proposal_id: str, req: ReasonReq,
                   user: dict = Depends(_require_user)):
    """合并提案：应用路径变更 + source=user 版本提交（相关章节按版本上游自动 stale）。"""
    store = _open_store(project_id, user)
    cfg = store.load_config()
    if not bool(getattr(cfg, "ledger_proposal", False)):
        raise HTTPException(status_code=404, detail="账本提案未启用")
    try:
        data = ledger_proposal.merge_proposal(store, proposal_id, by=_operator(user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"proposal": data}


@app.post("/api/projects/{project_id}/proposals/{proposal_id}/reject")
def reject_proposal(project_id: str, proposal_id: str, req: ReasonReq,
                    user: dict = Depends(_require_user)):
    """驳回提案：不改任何产物，只标 rejected + 审计。"""
    store = _open_store(project_id, user)
    cfg = store.load_config()
    if not bool(getattr(cfg, "ledger_proposal", False)):
        raise HTTPException(status_code=404, detail="账本提案未启用")
    try:
        data = ledger_proposal.reject_proposal(store, proposal_id,
                                               by=_operator(user), reason=req.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"proposal": data}
