# -*- coding: utf-8 -*-
"""小墨AI 路由：只做 HTTP 输入输出与鉴权，业务在 webapp/assistant/service.py。"""
from pydantic import BaseModel, Field

from .. import db
from ..core import (Depends, HTTPException, _check_project_access, _find_project,
                    _require_user, app, logger)
from ..assistant import service

__all__ = []


class ConversationCreateReq(BaseModel):
    title: str = Field(default="", max_length=200)
    mode: str = "chapter_eval"


class MessageReq(BaseModel):
    content: str = Field(..., min_length=1)
    mode: str = "chapter_eval"
    chapter_no: int = 0
    refs: list[str] = []  # 一期预留：引用片段由前端拼进 content，不走结构化


def _owned_conversation(project_id: str, cid: int) -> dict:
    """会话归属校验：不存在/不属于本项目一律 404（不泄露其他项目会话存在性）。"""
    conv = db.get_conversation(cid, project_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@app.post("/api/projects/{project_id}/assistant/conversations")
def create_conversation(project_id: str, req: ConversationCreateReq,
                        user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    mode = req.mode if req.mode in service.MODES else "chapter_eval"
    cid = db.create_conversation(project_id, int(user["id"]), req.title, mode)
    logger.info("小墨会话创建: project=%s conv=%s user=%s", project_id, cid, user["id"])
    return {"id": cid, "title": req.title, "mode": mode}


@app.get("/api/projects/{project_id}/assistant/conversations")
def list_conversations(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    return {"items": db.list_conversations(project_id, int(user["id"]))}


@app.get("/api/projects/{project_id}/assistant/conversations/{cid}")
def get_conversation(project_id: str, cid: int, limit: int = 50, before_id: int = 0,
                     user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    conv = _owned_conversation(project_id, cid)
    if int(conv["user_id"]) != int(user["id"]) and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return {"conversation": conv,
            "messages": db.list_messages(cid, limit=limit, before_id=before_id)}


@app.post("/api/projects/{project_id}/assistant/conversations/{cid}/messages")
def send_message(project_id: str, cid: int, req: MessageReq,
                 user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    conv = _owned_conversation(project_id, cid)
    if int(conv["user_id"]) != int(user["id"]) and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    store = _find_project(project_id)
    return service.send_message(store, user, conv, req.content, req.mode,
                                chapter_no=req.chapter_no or None)


@app.delete("/api/projects/{project_id}/assistant/conversations/{cid}")
def delete_conversation(project_id: str, cid: int, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    conv = _owned_conversation(project_id, cid)
    if int(conv["user_id"]) != int(user["id"]) and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="无权删除该会话")
    ok = db.delete_conversation(cid, project_id)
    return {"deleted": bool(ok)}
