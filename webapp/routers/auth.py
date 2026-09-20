# -*- coding: utf-8 -*-
"""auth路由（第三批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层

@app.post("/api/send-register-code", status_code=201)
def send_register_code(req: SendCodeReq):
    """注册验证码：邮箱格式 + 未被注册校验，通过则发邮件。"""
    email = (req.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if not db._valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if db.get_user_by_email(email):
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    ok, msg = verify_code.send_code("reg", email)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "msg": msg}


@app.post("/api/register", status_code=201)
def register(req: RegisterReq):
    email = (req.email or "").strip().lower()
    # 邮箱占用提前校验（避免消耗验证码）
    if db.get_user_by_email(email):
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    # 验证码校验
    ok, why = verify_code.verify_code("reg", email, req.code)
    if not ok:
        logger.warning("注册失败: username=%s reason=%s", req.username, why)
        raise HTTPException(status_code=400, detail=why)
    # 第一个注册的用户自动成为管理员（站长）
    is_first = db.get_user(1) is None
    ok, msg = db.create_user(req.username, req.password,
                             is_admin=is_first, invite_code=req.invite_code,
                             email=email)
    if not ok:
        logger.warning("注册失败: username=%s reason=%s", req.username, msg)
        raise HTTPException(status_code=400, detail=msg)
    user = db.get_user_by_name(req.username)
    # 注册赠送余额
    if FREE_REGISTER_BONUS > 0:
        db.change_balance(user["id"], FREE_REGISTER_BONUS, "注册赠送")
        user = db.get_user(user["id"])  # 重新取余额快照
    # 第一个管理员：把历史项目分配给他
    if is_first:
        _assign_legacy_projects()
    # 拉新奖励：注册填了上级邀请码且上级是代理 → 给上级发奖励（默认关闭）
    if REFERRAL_BONUS > 0 and user.get("referrer_id"):
        ref = db.get_user(user["referrer_id"])
        if ref and ref.get("is_agent"):
            db.pay_commission(ref["id"], user["id"], REFERRAL_BONUS, "register",
                              note=f"拉新奖励（{user['username']} 注册）")
    token = db.create_session(user["id"])
    logger.info("注册成功: uid=%d username=%s is_admin=%d referrer=%s",
                user["id"], user["username"], int(is_first), user.get("referrer_id"))
    return {"token": token, "user": _public_user(user), "note": msg if msg != "ok" else ""}


@app.post("/api/send-reset-code", status_code=201)
def send_reset_code(req: SendCodeReq):
    """忘记密码验证码：邮箱已注册才发；未注册也返回同样提示（不暴露注册状态）。"""
    email = (req.email or "").strip().lower()
    if not email or not db._valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if db.get_user_by_email(email):
        ok, msg = verify_code.send_code("reset", email)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    logger.info("发送重置验证码请求: email=%s", email)
    return {"ok": True, "msg": "若该邮箱已注册，验证码邮件已发送，请查收"}


@app.post("/api/reset-password")
def reset_password(req: ResetPasswordReq):
    """忘记密码重置：验证码 + 新密码。成功则删除该账号全部会话，强制重新登录。"""
    email = (req.email or "").strip().lower()
    if not email or not db._valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    ok, why = verify_code.verify_code("reset", email, req.code)
    if not ok:
        logger.warning("重置密码失败: email=%s reason=%s", email, why)
        raise HTTPException(status_code=400, detail=why)
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=400, detail="该邮箱未注册")
    db.update_password(user["id"], req.new_password)
    db.delete_user_sessions(user["id"])
    logger.info("密码重置成功: uid=%d email=%s", user["id"], email)
    return {"ok": True, "msg": "密码已重置，请使用新密码登录"}


@app.post("/api/login")
def login(req: LoginReq, request: Request):
    ip = _client_ip(request)
    if not _login_ratelimit(ip):
        logger.warning("登录限流拦截: ip=%s identifier=%s", ip, req.username)
        raise HTTPException(status_code=429, detail="尝试次数过多，请 5 分钟后再试")
    # req.username 支持「用户名 或 邮箱」两种登录方式
    uid = db.authenticate(req.username, req.password)
    if uid is None:
        _record_login_fail(ip)
        logger.warning("登录失败: identifier=%s", req.username)
        raise HTTPException(status_code=401, detail="用户名/邮箱或密码错误")
    user = db.get_user(uid)
    token = db.create_session(uid)
    logger.info("登录成功: uid=%d username=%s", uid, user["username"])
    return {"token": token, "user": _public_user(user)}


@app.post("/api/logout")
def logout(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        db.delete_session(authorization[7:].strip())
        logger.info("退出登录: token 已注销")
    return {"ok": True}
