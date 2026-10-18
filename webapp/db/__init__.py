# -*- coding: utf-8 -*-
"""
多用户数据层（MySQL）：连接池 + 按业务域拆分的数据访问函数。

拆分说明：原 webapp/db.py（1409 行单文件）按域拆分为本包：
    base.py        连接池/事务包装/表结构 DDL/建表初始化（init_db）
    users.py       用户/密码/代理邀请/返佣/认证/会话/余额/管理统计
    projects.py    项目归属/退款
    skills.py      平台技能/用户技能/技能市场
    style_roles.py 风格角色与技能挂载/组装/种子化
    teardowns.py   爆款拆书库

所有公开函数在此统一 re-export，调用方保持
`from webapp import db` / `db.xxx()`（含 db._connect、db._valid_email 等内部符号）不变。
"""
from .base import (  # noqa: F401
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME,
    _TxConn, _connect, _column_names, _get_pool, _valid_email,
    init_db,
)
from .users import *  # noqa: F401,F403
from .projects import *  # noqa: F401,F403
from .skills import *  # noqa: F401,F403
from .style_roles import *  # noqa: F401,F403
from .teardowns import *  # noqa: F401,F403
from .traces import *  # noqa: F401,F403
from .config import *  # noqa: F401,F403

# 模块加载时初始化（建表 + 老数据补全 + 风格角色种子化）。
# 必须在全部子模块导入完成之后调用：init_db 内部延迟导入的
# _seed_style_roles（style_roles.py）等此时已就绪。
init_db()
