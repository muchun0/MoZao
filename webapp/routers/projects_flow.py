# -*- coding: utf-8 -*-
"""R27 分步确认式生成流路由（编排层只读状态 + 确认放行 + 模式切换 + 停止）。

分层：本文件只做 HTTP 输入输出与鉴权；状态机、幂等、起 run 全在
`webapp/generation_flow.py`，生成仍走 `generate_service.start_agent_run` 单一内核。
"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import generation_flow  # noqa: F401  分步编排服务
from pydantic import BaseModel, Field


class FlowStartReq(BaseModel):
    mode: str | None = None


class FlowConfirmReq(BaseModel):
    stage: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    digest: str = ""
    client_token: str = ""
    force: bool = False


class FlowModeReq(BaseModel):
    mode: str = Field(..., min_length=1)


@app.get("/api/projects/{project_id}/supervisor")
def get_project_supervisor(project_id: str, with_card: int = 0,
                           user: dict = Depends(_require_user)):
    """REQ-R32：固化编排只读观测（计划/游标/预算/决策日志/角色台账/下一步）。

    off 模式返回 supported=false（前端隐藏面板）；不写盘、不扣费。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    from .. import supervisor_service
    return supervisor_service.get_supervisor(store, with_card=bool(with_card))


@app.get("/api/projects/{project_id}/generation/flow")
def get_generation_flow(project_id: str, with_card: int = 1,
                        user: dict = Depends(_require_user)):
    """流程状态 + 当前确认卡；expand 项目返回 supported=false；无 flow 返回 flow=null。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return generation_flow.get_flow(store, with_card=bool(with_card))


@app.post("/api/projects/{project_id}/generation/flow/start")
def start_generation_flow(project_id: str, req: FlowStartReq,
                          user: dict = Depends(_require_user)):
    """创建分步流程并起首阶段（双击幂等：已有流程直接返回现状）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return generation_flow.start_flow(store, user, req.mode)


@app.post("/api/projects/{project_id}/generation/flow/confirm")
def confirm_generation_stage(project_id: str, req: FlowConfirmReq,
                             user: dict = Depends(_require_user)):
    """阶段确认：continue / regenerate / skip / back_outline（带 digest 与幂等键）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return generation_flow.confirm(store, user, req.stage, req.action, req.digest,
                                   req.client_token, req.force)


@app.post("/api/projects/{project_id}/generation/flow/mode")
def set_generation_flow_mode(project_id: str, req: FlowModeReq,
                             user: dict = Depends(_require_user)):
    """切换项目级生成模式（auto / stepwise），写 config.json 的 generation_mode。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return generation_flow.set_mode(store, user, req.mode)


@app.post("/api/projects/{project_id}/generation/flow/abort")
def abort_generation_flow(project_id: str, user: dict = Depends(_require_user)):
    """停止当前阶段 run 并置 stopped（不会自动重发，重试需再次计费）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return generation_flow.abort(store, user)
