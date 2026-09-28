# -*- coding: utf-8 -*-
"""admin路由（第三批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层

@app.post("/api/admin/login")
def admin_login(req: LoginReq, request: Request):
    """管理后台登录：仅站长（is_admin=1）可登录，普通用户返回 403。支持用户名或邮箱。"""
    ip = _client_ip(request)
    if not _login_ratelimit(ip):
        raise HTTPException(status_code=429, detail="尝试次数过多，请 5 分钟后再试")
    uid = db.authenticate(req.username, req.password)
    if uid is None:
        _record_login_fail(ip)
        logger.warning("管理登录失败: identifier=%s", req.username)
        raise HTTPException(status_code=401, detail="用户名/邮箱或密码错误")
    user = db.get_user(uid)
    if not user["is_admin"]:
        logger.warning("非管理员尝试登录管理后台: uid=%d username=%s", uid, user["username"])
        raise HTTPException(status_code=403, detail="非管理员账号无权登录管理后台")
    token = db.create_session(uid)
    logger.info("管理后台登录成功: uid=%d username=%s", uid, user["username"])
    return {"token": token, "user": _public_user(user)}


@app.get("/api/admin/stats")
def admin_stats(user: dict = Depends(_require_admin)):
    return {"stats": db.admin_stats()}


@app.get("/api/admin/quality-report")
def admin_quality_report(project_id: str = "", days: int = 0,
                         user: dict = Depends(_require_admin)):
    """P2-4 质量指标报表（只读）：按项目/时间维度聚合 agent_trace（质检得分/重写/问题/弃书/成本）。"""
    from ..db.traces import quality_report as _quality_report
    return _quality_report(project_id=project_id, days=int(days or 0))


@app.get("/api/admin/skills")
def admin_list_skills(user: dict = Depends(_require_admin)):
    return {"skills": db.list_skills()}


@app.post("/api/admin/skills")
def admin_create_skill(req: SkillReq, user: dict = Depends(_require_admin)):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="名称不能为空")
    sid = db.create_skill(req.name.strip(), req.description, req.prompt_append,
                          req.is_active, req.sort_order, req.role)
    logger.info("新建写作风格: id=%d name=%s by=%s", sid, req.name, user["username"])
    return {"ok": True, "id": sid}


@app.put("/api/admin/skills/{skill_id}")
def admin_update_skill(skill_id: int, req: SkillReq, user: dict = Depends(_require_admin)):
    if not db.get_skill(skill_id):
        raise HTTPException(status_code=404, detail="Skill 不存在")
    db.update_skill(skill_id, name=req.name.strip(), description=req.description,
                     prompt_append=req.prompt_append, is_active=req.is_active,
                     sort_order=req.sort_order, role=req.role)
    logger.info("更新写作风格: id=%d by=%s", skill_id, user["username"])
    return {"ok": True}


@app.delete("/api/admin/skills/{skill_id}")
def admin_delete_skill(skill_id: int, user: dict = Depends(_require_admin)):
    if not db.get_skill(skill_id):
        raise HTTPException(status_code=404, detail="Skill 不存在")
    db.delete_skill(skill_id)
    logger.info("删除写作风格: id=%d by=%s", skill_id, user["username"])
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(user: dict = Depends(_require_admin)):
    return {"users": db.list_users_admin()}


@app.post("/api/admin/users/{user_id}/recharge")
def admin_recharge(user_id: int, req: RechargeAdminReq, user: dict = Depends(_require_admin)):
    """人工收款后给用户充值（amount > 0）。"""
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    new_balance = db.change_balance(user_id, req.amount, f"管理员手动充值 {req.amount:.1f}")
    logger.info("管理员充值: by=%s to_uid=%d to=%s amount=%.2f new_balance=%.2f",
                user["username"], user_id, target["username"], req.amount, new_balance)
    # 充值返佣（仅当配置为 recharge 基数时触发）
    if req.amount > 0 and target.get("referrer_id"):
        ref = db.get_user(target["referrer_id"])
        if ref and ref.get("is_agent") and COMMISSION_BASE == "recharge":
            commission = round_money(req.amount * COMMISSION_RATE)
            db.pay_commission(ref["id"], user_id, commission, "recharge",
                              note=f"下级 {target['username']} 充值返佣")
            logger.info("充值返佣: agent_uid=%d buyer_uid=%d amount=%.2f", ref["id"], user_id, commission)
    return {"ok": True, "balance": new_balance}


@app.post("/api/admin/users/{user_id}/agent")
def admin_set_agent(user_id: int, req: AgentSetReq, user: dict = Depends(_require_admin)):
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.set_agent(user_id, req.discount)
    logger.info("设为代理: by=%s target_uid=%d target=%s discount=%.2f",
                user["username"], user_id, target["username"], req.discount)
    return {"ok": True, "username": target["username"], "discount": req.discount}


@app.post("/api/admin/users/{user_id}/unagent")
def admin_unset_agent(user_id: int, user: dict = Depends(_require_admin)):
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.unset_agent(user_id)
    logger.info("取消代理: by=%s target_uid=%d target=%s", user["username"], user_id, target["username"])
    return {"ok": True, "username": target["username"]}


@app.get("/api/admin/users/{user_id}/downline")
def admin_downline(user_id: int, user: dict = Depends(_require_admin)):
    return {"downline": db.list_downline(user_id, limit=200)}


@app.get("/api/admin/users/{user_id}/commissions")
def admin_commissions(user_id: int, user: dict = Depends(_require_admin)):
    return {"commissions": db.list_commissions(user_id, limit=200),
            "total": db.sum_commission(user_id)}


@app.get("/api/admin/recharges")
def admin_recharges(user: dict = Depends(_require_admin)):
    rows = db.list_recharges(None, limit=200)
    names = {u["id"]: u["username"] for u in db.list_users_admin()}
    return {"recharges": [{**r, "username": names.get(r["user_id"], f"#{r['user_id']}")} for r in rows]}


@app.get("/api/admin/projects")
def admin_projects(user: dict = Depends(_require_admin)):
    """所有项目总览：标题 / 归属用户 / 题材 / 进度 / 状态。"""
    owners = {p["project_id"]: p["username"] for p in db.all_projects_with_owner()}
    items = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            try:
                store = ProjectStore.open(d)
                cfg = store.load_config()
                state = store.load_state()
                outline = store.load_outline()
                chapters = store.list_chapters()
                total = len(outline.chapters) if outline else cfg.num_chapters
                items.append({
                    "id": d.name,
                    "title": cfg.effective_title(),
                    "genre": cfg.genre,
                    "owner": owners.get(d.name, "（无归属）"),
                    "created": d.stat().st_mtime,
                    "phase": state.phase,
                    "chapters_done": len(chapters),
                    "chapters_total": total,
                    "running": d.name in _RUNS,
                })
            except Exception:
                continue
    return {"projects": items}


@app.get("/api/admin/settings")
def admin_settings(user: dict = Depends(_require_admin)):
    """系统参数（只读展示）：定价档位 + 返佣规则 + 健康状态。"""
    import time as _time
    return {
        "tiers": tiers(),
        "register_bonus": FREE_REGISTER_BONUS,
        "agent_discount": AGENT_DISCOUNT,
        "commission_rate": COMMISSION_RATE,
        "commission_base": COMMISSION_BASE,
        "referral_bonus": REFERRAL_BONUS,
        "server_time": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "2.1.0",
        "mail_configured": mailer.is_configured(),
    }


@app.get("/api/admin/logs")
def admin_logs(lines: int = 200, user: dict = Depends(_require_admin)):
    """读取系统日志尾部（供站长在后台排查问题）。"""
    from .logger import LOG_FILE, read_logs
    data = read_logs(lines)
    return {"logs": data, "log_file": str(LOG_FILE)}


@app.get("/api/admin/refunds")
def admin_refunds(status: str = "", user: dict = Depends(_require_admin)):
    """退款申请列表。status 可传 pending/approved/rejected，默认全部。"""
    rows = db.list_refund_requests(None, status or None)
    names = {u["id"]: u["username"] for u in db.list_users_admin()}
    return {"refunds": [{**r, "username": names.get(r["user_id"], f"#{r['user_id']}")} for r in rows],
            "mail_configured": mailer.is_configured()}


@app.post("/api/admin/refunds/{request_id}/handle")
def admin_handle_refund(request_id: int, req: RefundHandleReq,
                        user: dict = Depends(_require_admin)):
    """处理退款：approve=确认已线下转账并扣余额；reject=拒绝。"""
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 只能是 approve 或 reject")
    ok, why = db.handle_refund(request_id, req.action, req.admin_note)
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    logger.info("退款处理: request_id=%d action=%s by_admin=%s note=%s",
                request_id, req.action, user["username"], req.admin_note)
    return {"ok": True, "message": why}
