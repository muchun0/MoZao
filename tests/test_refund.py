# -*- coding: utf-8 -*-
"""退款域 SQL 回归测试（P0 资金结算）。

背景 bug：SQL 中 LIKE 字面量 %（如 `note LIKE '管理员手动充值%'`）未转义为 %%，
pymysql 在参数化执行（mogrify = query % escaped_args）时把字面量 % 当作占位符，
抛 `TypeError: not enough arguments for format string`，导致
GET /api/me/refundable 500（webapp/db/projects.py refundable_amount /
create_refund_request 内部查询）。

本文件用 pymysql 同源 escape 逻辑（converters.escape_item）模拟 cursor.mogrify，
验证修复后的 SQL 可安全格式化且语义不变（仍匹配"管理员手动充值"前缀）。
不触碰真实 MySQL / 真实资金（monkeypatch 掉 _connect）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymysql
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.db import projects


def _mogrify(sql: str, params: tuple) -> str:
    """等价于 pymysql.cursors.Cursor.mogrify：query % tuple(conn.escape(p))。"""
    escaped = tuple(pymysql.converters.escape_item(p, "utf8mb4") for p in params)
    return sql % escaped


class _FakeCursor:
    def __init__(self, rows):
        self._rows = [dict(r) for r in (rows or [])]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """脚本化假连接：execute 按调用顺序弹出预设行，同时验证每条参数化 SQL 可安全 mogrify。"""

    def __init__(self, script):
        self._script = list(script)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if params is not None:
            _mogrify(sql, tuple(params))  # 修复前此处抛 TypeError
        self.executed.append((sql, tuple(params) if params is not None else None))
        return _FakeCursor([self._script.pop(0)] if self._script else [])


@pytest.mark.p0
def test_refundable_amount_sql_escaped(monkeypatch):
    """可退金额查询：LIKE 字面量 % 转义后 mogrify 不报错，语义不变。"""
    conn = _FakeConn([{"s": 500}, {"s": 50}])
    monkeypatch.setattr(projects, "_connect", lambda: conn)
    assert projects.refundable_amount(7) == 450.0
    # 仅 2 条查询，均带参数执行（修复前第二条 LIKE 查询即崩）
    assert len(conn.executed) == 2
    for sql, params in conn.executed:
        assert params == (7,)
        if "管理员手动充值" in sql:  # LIKE 查询：mogrify 后可安全格式化且语义不变
            assert "note LIKE '管理员手动充值%'" in _mogrify(sql, params), "转义后仍应匹配管理员手动充值前缀"


@pytest.mark.p0
def test_create_refund_request_sql_escaped(monkeypatch):
    """提交退款申请：内部可退金额查询同样转义，正常路径不报错。"""
    conn = _FakeConn([[], {"s": 100}, {"s": 0}, []])
    monkeypatch.setattr(projects, "_connect", lambda: conn)
    ok, msg = projects.create_refund_request(7, 50, "测试退款")
    assert ok is True and "已提交" in msg
    like_sql = [sql for sql, _ in conn.executed if "管理员手动充值" in sql]
    assert len(like_sql) == 1
    assert "管理员手动充值%%" in like_sql[0]  # SQL 源文本为转义写法
    assert "note LIKE '管理员手动充值%'" in _mogrify(like_sql[0], (7,))  # mogrify 后还原为单 %
