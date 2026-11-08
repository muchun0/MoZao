# -*- coding: utf-8 -*-
"""回归：导入 webapp.db 不得建表 / 连库 / 灌默认配置。

历史问题：``webapp/db/__init__.py`` 在 import 时调用 init_db()，实验 worktree
仅 import 就把 120 项开关 seed 进共享库，污染 devtest 测试数据。
这里用子进程隔离验证两点：导入期零连接、零 sys_config 行数变化。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp import db  # noqa: E402

_PROBE = """
import sys
sys.path.insert(0, r"{root}")
import webapp
import webapp.db
from webapp.db import base
assert base._pool is None, "import webapp.db 触发了连接池创建"
import webapp.core
assert base._pool is None, "import webapp.core 触发了连接池创建"
import webapp.server
assert base._pool is None, "import webapp.server 触发了连接池创建"
print("OK")
"""


def _run_probe(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", _PROBE.format(root=str(ROOT))],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )


@pytest.mark.p1
def test_import_does_not_connect_db():
    """把 DB 指到不可达地址：导入仍须成功，证明导入期没有任何连接尝试。"""
    proc = _run_probe({
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "1",
        "MOZAO_AUTO_INIT": "0",
    })
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def _count_sys_config() -> int | None:
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM sys_config").fetchone()
        return int(row["n"])
    except Exception:
        return None


@pytest.mark.p1
def test_import_does_not_write_sys_config():
    """导入后 sys_config 行数不变；MySQL 不可用时跳过。"""
    before = _count_sys_config()
    if before is None:
        pytest.skip("本机 MySQL 不可用")
    proc = _run_probe({"MOZAO_AUTO_INIT": "1"})
    assert proc.returncode == 0, proc.stderr
    assert _count_sys_config() == before


@pytest.mark.p1
@pytest.mark.parametrize("env, expected", [
    ({}, True),                                    # 开发/测试默认开
    ({"MOZAO_ENV": "development"}, True),
    ({"MOZAO_ENV": "production"}, False),          # 生产默认关
    ({"MOZAO_ENV": "prod"}, False),
    ({"MOZAO_ENV": "production", "MOZAO_AUTO_INIT": "1"}, True),   # 显式开启优先
    ({"MOZAO_AUTO_INIT": "0"}, False),             # 显式关闭优先
])
def test_auto_init_switch(monkeypatch, env, expected):
    """MOZAO_AUTO_INIT 显式值优先，未设置时按 MOZAO_ENV 推断。"""
    monkeypatch.delenv("MOZAO_AUTO_INIT", raising=False)
    monkeypatch.delenv("MOZAO_ENV", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert db.auto_init_enabled() is expected
