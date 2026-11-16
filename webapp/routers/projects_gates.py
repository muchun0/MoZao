# -*- coding: utf-8 -*-
"""创作阶段审阅门路由（REQ-创作阶段审阅门 M0）。

只做 HTTP 输入输出与鉴权，状态机与体检全在 `webapp/gates.py`：
- GET   /gates                 读取有效门状态（含失效级联结果）
- POST  /gates/{gate}/approve  通过（或显式跳过世界门），绑定当前产物版本
- GET   /gates/{gate}/review   该门的判断材料（摘要 + 体检清单，零 LLM）
"""
from typing import Optional

from pydantic import BaseModel

from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import gates


class GateApproveReq(BaseModel):
    """通过门请求体：skip 仅对 world 门有效（§8#5 允许跳过世界观）。"""
    skip: bool = False
    note: str = ""


@app.get("/api/projects/{project_id}/gates")
def get_gates(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"gates": gates.compute(store)}


@app.post("/api/projects/{project_id}/gates/{gate}/approve")
def approve_gate(project_id: str, gate: str, req: GateApproveReq,
                 user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"gates": gates.approve(store, gate, user.get("id"), skip=req.skip,
                                  note=req.note)}


@app.get("/api/projects/{project_id}/gates/{gate}/review")
def review_gate(project_id: str, gate: str, from_ch: Optional[int] = None,
                to_ch: Optional[int] = None, user: dict = Depends(_require_user)):
    """门材料：outline / world / batch（batch 需 from_ch/to_ch）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return gates.review(store, gate, from_ch=from_ch or 0, to_ch=to_ch or 0)
