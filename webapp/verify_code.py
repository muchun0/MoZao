# -*- coding: utf-8 -*-
"""
邮箱验证码（注册验证 / 忘记密码重置）。

机制：
- 6 位数字，10 分钟有效（CODE_TTL）
- 同一邮箱同用途 60 秒冷却（COOLDOWN），防刷
- 连续错 5 次自动作废（MAX_ATTEMPTS），防爆破
- 校验成功即消费（一次性），单进程内存存储，内测规模足够

发邮件失败时验证码不会入库，用户可重试。
"""
from __future__ import annotations

import random
import threading
import time

from .mailer import send_mail

_codes: dict[str, dict] = {}  # key: "purpose:email" -> {code, expires_at, attempts, last_sent}
_lock = threading.Lock()

CODE_TTL = 600      # 10 分钟有效
COOLDOWN = 60       # 同邮箱同用途冷却（秒）
MAX_ATTEMPTS = 5    # 最大尝试次数

_TITLES = {
    "reg": "【墨造】注册验证码",
    "reset": "【墨造】重置密码验证码",
}
_TEXTS = {
    "reg": "你正在注册墨造 · AI 小说创作平台账号，验证码如下（10 分钟内有效，请勿泄露）：",
    "reset": "你正在重置墨造 · AI 小说创作平台账号密码，验证码如下（10 分钟内有效，请勿泄露）：",
}


def _gen_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def send_code(purpose: str, email: str) -> tuple[bool, str]:
    """生成验证码并发邮件。返回 (ok, 用户可见消息)。"""
    email = (email or "").strip().lower()
    if not email:
        return False, "邮箱不能为空"
    key = f"{purpose}:{email}"
    with _lock:
        now = time.time()
        rec = _codes.get(key)
        if rec and now - rec.get("last_sent", 0) < COOLDOWN:
            wait = int(COOLDOWN - (now - rec["last_sent"]))
            return False, f"发送过于频繁，请 {wait} 秒后再试"
        code = _gen_code()
        _codes[key] = {"code": code, "expires_at": now + CODE_TTL,
                       "attempts": 0, "last_sent": now}
    html = (
        "<p>" + _TEXTS.get(purpose, "验证码如下") + "</p>"
        f"<p style='font-size:28px;letter-spacing:6px;color:#232f55'><b>{code}</b></p>"
        "<p style='color:#888'>如果这不是你的操作，请忽略本邮件。</p>"
    )
    ok = send_mail(_TITLES.get(purpose, "【墨造】验证码"), html, to_addr=email)
    if not ok:
        return False, "验证码邮件发送失败，请稍后重试或联系站长"
    return True, "验证码已发送，请查收邮箱"


def verify_code(purpose: str, email: str, code: str) -> tuple[bool, str]:
    """校验验证码，成功即消费。返回 (ok, 用户可见消息)。"""
    email = (email or "").strip().lower()
    key = f"{purpose}:{email}"
    code = (code or "").strip()
    with _lock:
        rec = _codes.get(key)
        if not rec:
            return False, "验证码不存在或已过期，请重新获取"
        if rec["attempts"] >= MAX_ATTEMPTS:
            _codes.pop(key, None)
            return False, "错误次数过多，验证码已失效，请重新获取"
        if time.time() > rec["expires_at"]:
            _codes.pop(key, None)
            return False, "验证码已过期，请重新获取"
        if rec["code"] != code:
            rec["attempts"] += 1
            return False, "验证码错误"
        _codes.pop(key, None)
        return True, "ok"
