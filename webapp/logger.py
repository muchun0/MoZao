# -*- coding: utf-8 -*-
"""
统一日志：文件（滚动）+ 控制台。

日志文件：项目根 logs/app.log，单个最大 5MB，保留 3 个备份（app.log.1/2/3）。
用法：
    from webapp.logger import get_logger
    logger = get_logger("server")
    logger.info(...) / logger.warning(...) / logger.error(...)

敏感约定：禁止在日志中记录密码、token、API Key、邀请码等凭据。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOGS_DIR / "app.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """配置根 logger（幂等，重复调用无副作用）。"""
    global _configured
    if _configured:
        return
    _configured = True
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)

    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES,
                                 backupCount=BACKUP_COUNT, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass  # 日志目录不可写时退化为仅控制台

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get_logger(name: str = "app") -> logging.Logger:
    """获取命名 logger；模块首次导入时自动完成日志配置。"""
    setup_logging()
    return logging.getLogger(name)


def read_logs(lines: int = 200) -> str:
    """读取日志文件尾部（供管理后台查看）。"""
    setup_logging()
    if not LOG_FILE.exists():
        return "(暂无日志)"
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            data = f.readlines()
        return "".join(data[-max(1, min(lines, 2000)):])
    except OSError as e:
        return f"(读取日志失败: {e})"
