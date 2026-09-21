# -*- coding: utf-8 -*-
"""统一路径解析：数据目录与代码目录隔离。

配置项：环境变量 ``DATA_DIR``（数据根目录，可在项目根 ``.env`` 中配置）。
未设置/空白时回退代码根目录，行为与历史版本完全一致（向后兼容）。

派生目录：
    PROJECTS_DIR = DATA_DIR / "projects"
    OUTPUT_DIR   = DATA_DIR / "output"
    LOGS_DIR     = DATA_DIR / "logs"

解析规则：
    - 绝对路径原样使用（推荐，如 D:\\NovelData）；
    - 相对路径基于代码根解析（不依赖当前工作目录）；
    - 支持 ``~`` 展开用户主目录。

约束：本模块保持零依赖（仅 stdlib + python-dotenv），可被 webapp 与 novel_gen
双方安全引用，避免循环导入。
"""
from __future__ import annotations

import os
from pathlib import Path

_env_loaded = False


def load_env_once() -> None:
    """幂等加载 <代码根>/.env（不覆盖已存在的系统环境变量）。"""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
        load_dotenv(get_base_dir() / ".env", override=False)
    except Exception:
        # .env 缺失/不可读时静默降级（与历史行为一致）
        pass


def get_base_dir() -> Path:
    """代码根目录：novel_gen/utils/paths.py 向上三级。"""
    return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """数据根目录：优先 DATA_DIR，未设置/空白时回退代码根。"""
    load_env_once()
    raw = os.environ.get("DATA_DIR", "").strip()
    if not raw:
        return get_base_dir()
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        p = get_base_dir() / p
    return p


def get_projects_dir() -> Path:
    """用户小说项目根目录。"""
    return get_data_dir() / "projects"


def get_output_dir() -> Path:
    """导出文件目录。"""
    return get_data_dir() / "output"


def get_logs_dir() -> Path:
    """运行日志目录。"""
    return get_data_dir() / "logs"
