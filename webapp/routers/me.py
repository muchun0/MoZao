# -*- coding: utf-8 -*-
"""me路由（第三批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层
from ..user_memory import UserMemoryService  # 用户级记忆库（跨书创作偏好继承）

@app.get("/api/me")
def me(user: dict = Depends(_require_user)):
    return {"user": _public_user(user)}


@app.get("/api/me/recharges")
def my_recharges(user: dict = Depends(_require_user)):
    return {"recharges": db.list_recharges(user["id"])}


@app.get("/api/me/referral")
def my_referral(user: dict = Depends(_require_user)):
    """我的推广信息：邀请码、推广链接、下级数、累计返佣、返佣明细、上级。"""
    invite_code = db.get_invite_code(user["id"])
    downline = db.list_downline(user["id"])
    commissions = db.list_commissions(user["id"])
    ref = db.get_referrer(user["id"])
    return {
        "invite_code": invite_code,
        "invite_link": f"/?invite={invite_code}",
        "is_agent": bool(user.get("is_agent")),
        "agent_discount": float(user.get("agent_discount", 1.0)),
        "commission_rate": COMMISSION_RATE,
        "referral_bonus": REFERRAL_BONUS,
        "referrer": ref,
        "downline_count": len(downline),
        "downline": [{"id": r["id"], "username": r["username"], "created_at": r["created_at"]}
                     for r in downline[:50]],
        "total_commission": db.sum_commission(user["id"]),
        "commissions": [
            {"id": c["id"], "amount": c["amount"], "kind": c["kind"],
             "project_id": c["project_id"], "note": c["note"], "created_at": c["created_at"]}
            for c in commissions[:50]
        ],
    }


@app.post("/api/me/bind-referrer")
def bind_referrer(req: BindReferrerReq, user: dict = Depends(_require_user)):
    """注册时没填邀请码的用户，可在首次使用前补绑上级。"""
    if user.get("referrer_id"):
        raise HTTPException(status_code=400, detail="你已经绑定了上级，无法更换")
    ok, why = db.bind_referrer(user["id"], req.invite_code)
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    return {"ok": True, "message": "绑定成功"}


@app.get("/api/me/llm")
def me_llm(user: dict = Depends(_require_user)):
    """返回用户自配模型配置（api_key 脱敏：只留前4后4）。"""
    u = db.get_user_llm(user["id"])
    key = u.get("api_key", "") or ""
    masked = (key[:4] + "..." + key[-4:]) if len(key) >= 10 else ("****" if key else "")
    return {
        "provider": u.get("provider", ""),
        "api_key": key,        # 完整 key 也返回，方便前端回填编辑（仅本人可见）
        "api_key_masked": masked,
        "base_url": u.get("base_url", ""),
        "model": u.get("model", ""),
        "enabled": bool(key and u.get("provider")),
    }


@app.put("/api/me/llm")
def save_me_llm(req: UserLlmReq, user: dict = Depends(_require_user)):
    provider = (req.provider or "").strip().lower()
    api_key = (req.api_key or "").strip()
    base_url = (req.base_url or "").strip()
    model = (req.model or "").strip()
    if api_key:
        if provider not in ("deepseek", "openai", "zhipu", "qwen", "moonshot", "custom"):
            raise HTTPException(status_code=400, detail="暂不支持该 provider")
    else:
        # 清空 = 回到平台 key
        provider = ""
    db.set_user_llm(user["id"], provider, api_key, base_url, model)
    logger.info("用户更新自配模型: uid=%d provider=%s has_key=%s",
                user["id"], provider, bool(api_key))
    return {"ok": True, "enabled": bool(api_key and provider)}


@app.get("/api/me/refundable")
def me_refundable(user: dict = Depends(_require_user)):
    """可退金额（仅含充值部分，赠金/返佣不可退）。"""
    return {"refundable": db.refundable_amount(user["id"])}


@app.post("/api/me/refund-request")
def me_refund_request(req: RefundApplyReq, user: dict = Depends(_require_user)):
    """提交退款申请 → 入库 + 邮件通知站长。"""
    ok, why = db.create_refund_request(user["id"], req.amount, req.reason)
    if not ok:
        logger.warning("退款申请被拒: uid=%d amount=%.2f reason=%s", user["id"], req.amount, why)
        raise HTTPException(status_code=400, detail=why)
    # 邮件通知站长（失败不影响申请入库）
    mail_ok = mailer.notify_refund_request(
        user["username"], user["id"], req.amount, req.reason,
        admin_url="http://127.0.0.1:8000/admin.html",
    )
    logger.info("退款申请提交: uid=%d username=%s amount=%.2f email_sent=%s",
                user["id"], user["username"], req.amount, mail_ok)
    return {"ok": True, "message": "退款申请已提交，站长处理后到账",
            "email_sent": mail_ok}


@app.get("/api/me/refund-requests")
def me_refund_requests(user: dict = Depends(_require_user)):
    return {"requests": db.list_refund_requests(user["id"])}


@app.get("/api/me/user-memory")
def my_user_memory(user: dict = Depends(_require_user)):
    """我的用户级记忆库摘要（只读）。

    用户级记忆 = 跨书创作偏好继承的沉淀结果（题材/节奏/避雷偏好、跨书纪律、
    个人文风锚条数），由生成过程自动写入；失败返回 None（降级安全）。
    """
    try:
        return {"memory": UserMemoryService(user["id"]).summary()}
    except Exception:
        logger.exception("用户级记忆摘要失败: uid=%d", user["id"])
        return {"memory": None}


@app.post("/api/me/user-memory/clear")
def clear_user_memory(user: dict = Depends(_require_user)):
    """清空我的用户级记忆库（隐私兜底）。

    删除该用户全部跨书沉淀（偏好/纪律/文风锚），不可恢复；之后新书不再继承。
    """
    try:
        UserMemoryService(user["id"]).clear()
    except Exception:
        logger.exception("用户级记忆清空失败: uid=%d", user["id"])
    return {"ok": True, "message": "用户级记忆已清空"}
