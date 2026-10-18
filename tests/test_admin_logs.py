# -*- coding: utf-8 -*-
"""管理后台「系统日志」接口回归测试。

回归背景：`GET /api/admin/logs`（webapp/routers/admin.py admin_logs）函数体内
`from .logger import LOG_FILE, read_logs` 在 routers 包内被解析为
`webapp.routers.logger`（模块不存在），调用时抛
`ModuleNotFoundError: No module named 'webapp.routers.logger'` 导致 500，
管理端系统日志页空白。修复为 `from ..logger import ...`（logger 实际位于
webapp/logger.py）。本文件防止该导入错误回归。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp import db  # noqa: E402


def _db_ok() -> bool:
    try:
        with db._connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


_NEED_DB = pytest.mark.skipif(not _db_ok(), reason="本机 MySQL 不可用")


@pytest.mark.p2
def test_admin_logs_endpoint_callable():
    """回归：admin_logs 可调用并返回 logs/log_file，不再因 logger 导入缺失崩溃。

    直接调用路由函数（绕过 Depends，手工注入 user），不依赖 HTTP/MySQL。
    """
    from webapp.routers import admin as admin_router

    result = admin_router.admin_logs(lines=50, user={"username": "tester", "is_admin": True})
    assert "logs" in result
    assert "log_file" in result
    assert isinstance(result["logs"], str)


@pytest.mark.p2
def test_read_logs_never_raises():
    """read_logs 尾部读取：日志文件缺失返回占位串，不抛异常。"""
    from webapp.logger import read_logs

    data = read_logs(200)
    assert isinstance(data, str)
    assert data != ""


@pytest.mark.p2
@_NEED_DB
def test_admin_logs_http_200():
    """回归（HTTP 层）：管理员 GET /api/admin/logs 返回 200 与 logs 文本。

    原 bug 复现路径：请求到达 admin_logs 函数体时 `from .logger import ...`
    被解析为 webapp.routers.logger（不存在）→ ModuleNotFoundError → 500，
    管理端「系统日志」页空白。修复后应 200。
    """
    from fastapi.testclient import TestClient
    from webapp.server import app

    c = TestClient(app)
    # 未登录 → 401
    assert c.get("/api/admin/logs").status_code == 401

    admin = db.get_user_by_name("admin")
    atoken = db.create_session(admin["id"])
    r = c.get("/api/admin/logs", headers={"Authorization": f"Bearer {atoken}"})
    assert r.status_code == 200
    body = r.json()
    assert "logs" in body
    assert "log_file" in body
    assert isinstance(body["logs"], str)
