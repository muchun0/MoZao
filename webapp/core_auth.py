# -*- coding: utf-8 -*-
"""
墨造核心层·鉴权：用户/管理员鉴权、项目访问控制、登录失败限流与用户序列化。

从 core.py 拆出（业务域拆分，行为零变化）。本模块只承载「鉴权与账号安全」一个内聚概念，
不包含任何生成、结算、项目业务逻辑。
"""
from __future__ import annotations

import threading
import time as _time

from fastapi import Depends, Header, HTTPException

from . import db
from .logger import get_logger
from .core_runtime import PROJECTS_DIR

logger = get_logger("server")


# ---------- 鉴权 ----------
def _require_user(authorization: str = Header(default="")) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization[7:].strip()
    uid = db.get_session_user(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    user = db.get_user(uid)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def _require_admin(user: dict = Depends(_require_user)) -> dict:
    """管理后台专用：仅站长（is_admin）可访问。"""
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="仅站长可访问管理后台")
    return user


def _check_project_access(project_id: str, user: dict) -> None:
    """校验当前用户是否有权访问该项目（管理员可访问全部）。"""
    if user["is_admin"]:
        return
    owner = db.project_owner(project_id)
    if owner is None:
        # 未登记归属的历史项目：普通用户无权访问
        raise HTTPException(status_code=403, detail="无权访问该项目")
    if owner != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该项目")


def _assign_legacy_projects() -> None:
    """把磁盘上未登记归属的历史项目全部分配给管理员。"""
    admin = db.get_user_by_name("admin")
    if not admin:
        return
    if not PROJECTS_DIR.exists():
        return
    owners = db.all_projects()
    for d in PROJECTS_DIR.iterdir():
        if d.is_dir() and d.name not in owners:
            db.claim_project(d.name, admin["id"])


# ---------- 基础 ----------
def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email") or "",
        "balance": user["balance"],
        "is_admin": bool(user["is_admin"]),
        "is_agent": bool(user.get("is_agent", 0)),
        "agent_discount": float(user.get("agent_discount", 1.0)),
        "invite_code": user.get("invite_code") or db.get_invite_code(user["id"]),
        "referrer_id": user.get("referrer_id"),
    }


# P2 安全：登录失败限流（按 IP，防爆破）
_login_fails: dict[str, list] = {}
_LOGIN_LOCK = threading.Lock()
_LOGIN_MAX = 8        # 最多失败次数
_LOGIN_WINDOW = 300  # 5 分钟窗口


def _client_ip(request) -> str:
    return request.client.host if request and request.client else "unknown"


def _login_ratelimit(ip: str):
    now = _time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        if len(fails) >= _LOGIN_MAX:
            return False
        _login_fails[ip] = fails
    return True


def _record_login_fail(ip: str):
    now = _time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        fails.append(now)
        _login_fails[ip] = fails


__all__ = [
    "logger",
    "_require_user", "_require_admin", "_check_project_access", "_assign_legacy_projects",
    "_public_user",
    "_login_fails", "_LOGIN_LOCK", "_LOGIN_MAX", "_LOGIN_WINDOW",
    "_client_ip", "_login_ratelimit", "_record_login_fail",
]
