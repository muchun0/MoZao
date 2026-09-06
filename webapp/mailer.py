# -*- coding: utf-8 -*-
"""
邮件通知：退款申请等事件发邮件给站长。

配置（根目录 .env）：
    ADMIN_EMAIL=站长收件邮箱
    SMTP_HOST=smtp.qq.com        # 发件 SMTP 服务器
    SMTP_PORT=465                # SSL 端口（QQ/163 用 465）
    SMTP_USER=发件邮箱
    SMTP_PASS=发件邮箱授权码      # 不是登录密码，是邮箱设置里的 SMTP 授权码

未配置时 send_mail 返回 False 且不抛异常（退款申请仍入库，站长可在后台查看）。
"""
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from .logger import get_logger

logger = get_logger("mailer")


def _load_env() -> None:
    """从项目根 .env 读取环境变量（与 run_web.py 同款手写解析，无第三方依赖）。

    每次调用都覆盖 os.environ 中的值，因此修改 .env 后无需重启服务即可生效。
    """
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ[key] = value
    except Exception:
        pass


def is_configured() -> bool:
    _load_env()
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER")
                and os.getenv("SMTP_PASS") and os.getenv("ADMIN_EMAIL"))


def send_mail(subject: str, html: str) -> bool:
    """给站长发邮件。成功返回 True；未配置或失败返回 False（不抛异常）。"""
    _load_env()
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "465") or 465)
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    to_addr = os.getenv("ADMIN_EMAIL", "")
    if not (host and user and password and to_addr):
        logger.warning("邮件未发送（SMTP 未完整配置）: subject=%s", subject)
        return False
    try:
        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("AI小说生成器", "utf-8")), user))
        msg["To"] = to_addr
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                s.login(user, password)
                s.sendmail(user, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                s.login(user, password)
                s.sendmail(user, [to_addr], msg.as_string())
        logger.info("邮件发送成功: to=%s subject=%s", to_addr, subject)
        return True
    except Exception as e:
        logger.error("邮件发送失败: to=%s subject=%s error=%s: %s",
                     to_addr, subject, type(e).__name__, e)
        return False


def notify_refund_request(username: str, user_id: int, amount: float,
                          reason: str, admin_url: str) -> bool:
    """退款申请通知。"""
    subject = f"【退款申请】{username} 申请退款 {amount:.2f} 元"
    html = (
        "<h3>新的退款申请</h3>"
        f"<p>用户：<b>{username}</b>（ID {user_id}）</p>"
        f"<p>金额：<b>{amount:.2f}</b> 元</p>"
        f"<p>原因：{reason or '（未填写）'}</p>"
        "<p>请登录管理后台处理：<a href='" + admin_url + "'>管理后台 - 退款管理</a></p>"
    )
    return send_mail(subject, html)
