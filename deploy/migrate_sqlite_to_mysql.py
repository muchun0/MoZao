# -*- coding: utf-8 -*-
"""一次性迁移工具：SQLite(novel_web.db) -> MySQL(mozao)

适用场景：服务器从旧 SQLite 版升级到 MySQL 版后，把存量数据搬入 MySQL。

用法（在 novel-gen 容器内执行）：
    python deploy/migrate_sqlite_to_mysql.py [sqlite_path]

参数：
    sqlite_path   SQLite 库路径，默认 /data/novel_web.db（服务器 data 卷挂载点）

MySQL 连接配置来自环境变量（.env 注入）：DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME。

行为：
    - 先备份 SQLite 到 <path>.bak（已存在则跳过）
    - 建表（webapp.db.init_db）
    - 逐表清空目标表后重插（幂等，可重复运行）
表：users / sessions / project_owner / recharges / commission_log / refund_requests
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TABLES = ["users", "sessions", "project_owner", "recharges", "commission_log", "refund_requests"]

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data/novel_web.db")
BAK = SRC.with_suffix(".db.bak")


def main() -> None:
    if not SRC.exists():
        print(f"[失败] SQLite 库不存在: {SRC}")
        sys.exit(1)

    # 1) 备份
    if not BAK.exists():
        shutil.copy2(SRC, BAK)
        print("已备份 SQLite ->", BAK)
    else:
        print("备份已存在，跳过备份:", BAK)

    # 2) 建表（webapp.db 模块加载时自动 init_db）
    import webapp.db as wdb  # noqa: E402
    wdb.init_db()
    print(f"已建表/校验表结构（{wdb.DB_NAME} @ {wdb.DB_HOST}）")

    # 3) 读 SQLite
    src = sqlite3.connect(str(SRC))
    src.row_factory = sqlite3.Row

    # 4) 写 MySQL
    dst = pymysql.connect(
        host=wdb.DB_HOST, port=wdb.DB_PORT, user=wdb.DB_USER,
        password=wdb.DB_PASSWORD, database=wdb.DB_NAME,
        charset="utf8mb4", autocommit=False)

    total = 0
    with dst.cursor() as cur:
        for table in TABLES:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"[{table}] 0 条，跳过")
                continue
            cols = list(rows[0].keys())
            col_sql = ", ".join(f"`{c}`" for c in cols)
            ph = ", ".join(["%s"] * len(cols))
            cur.execute(f"DELETE FROM `{table}`")
            sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({ph})"
            for r in rows:
                cur.execute(sql, [r[c] for c in cols])
            dst.commit()
            total += len(rows)
            print(f"[{table}] 迁移 {len(rows)} 条")

    dst.close()
    src.close()
    print(f"迁移完成，共 {total} 条记录。")


if __name__ == "__main__":
    main()
