# -*- coding: utf-8 -*-
"""
墨造核心层门面（组装壳）：统一再导出 + FastAPI 实例创建。

从原 server.py 拆分而来（第三批优化）。路由按域见 webapp/routers/。
本模块不再承载业务实现，仅做三件事：
1. 保留全部原导入，作为 `from .core import *`（server.py 与各 routers）的再导出面，
   保证拆分子模块后导出符号集合零变化（HTTPException/Depends/模型/工具函数等）；
2. 星号导入 6 个业务域子模块，把其公开 API 统一汇入本门面（鉴权/项目/结算/生成/模型/运行时）；
3. 创建 FastAPI 实例 `app` 与 lifespan/CORS（server.py 与各 routers 均 `@app.xxx`）。

业务域子模块（每个 <500 行、单一内聚概念，按 AGENTS.md §11.1 拆分，行为零变化）：
- core_runtime.py  运行时：路径配置/任务注册表/并发槽位/任务持久化
- core_models.py   请求/响应 Pydantic 模型
- core_auth.py     鉴权/项目访问/登录限流/用户序列化
- core_project.py  项目工具/管线打开/锁定/章节辅助
- core_settle.py   结算链
- core_generate.py 预生成注入/生成线程
"""
from __future__ import annotations


import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel_gen.config import (
    GENRES,
    PROVIDER_DEFAULTS,
    NovelConfig,
    LLMConfig,
    SearchConfig,
    validate_config,
)
from novel_gen.export.cover import generate_best_cover
from novel_gen.export.book_formats import export_epub, export_docx
from novel_gen.llm.factory import create_llm
from novel_gen.pipeline.guard import AgentGuardError
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.pipeline.stages import unique_chapter_titles
from novel_gen.pipeline.style_anchor import build_teardown_anchor
from novel_gen.store.schemas import (
    Chapter, ChapterPlan, Outline, World, Character,
    EnemyCard, LocationCard, MechanismBaseline, ClockCard, FactionCard,
)
from novel_gen.utils.chapter_splitter import split_chapters, summarize_preview
from novel_gen.utils.paths import get_output_dir, get_projects_dir
from novel_gen.store.project import ProjectStore, _safe_name
from novel_gen.store.wordcount import count_words

from . import db
from . import mailer
from . import settle_ledger
from . import verify_code
from . import teardown_lib
from .config_overrides import apply_global_overrides
from .user_memory import UserMemoryService
from .logger import get_logger
from .outline_parser import extract_text, parse_outline_text
from .pricing import FREE_REGISTER_BONUS, price_for, tiers
from .referral import (AGENT_DISCOUNT, COMMISSION_BASE, COMMISSION_RATE,
                       REFERRAL_BONUS, agent_price, round_money)

# ---------- 业务域子模块：统一汇入门面（导出面不变） ----------
from .core_runtime import *  # noqa: F401,F403  logger/路径常量/任务注册表/槽位/任务持久化
from .core_models import *  # noqa: F401,F403  请求/响应模型
from .core_auth import *  # noqa: F401,F403  鉴权/登录限流/用户序列化
from .core_project import *  # noqa: F401,F403  项目工具/管线打开/锁定/章节辅助
from .core_settle import *  # noqa: F401,F403  结算链
from .core_generate import *  # noqa: F401,F403  预生成注入/生成线程


@asynccontextmanager
async def _lifespan(_app):
    """服务启动：重拾崩溃残留的生成任务 + 导入拆书库种子。"""
    try:
        _resume_interrupted_tasks()
    except Exception:
        logger.exception("启动重拾异常")
    try:
        teardown_lib.seed_from_knowledge_base()
    except Exception:
        logger.exception("拆书库种子导入异常")
    yield


app = FastAPI(title="AI 小说生成器 API", version="2.0.0", lifespan=_lifespan)

# CORS：P2 安全修复，不再通配 "*" + credentials（高危）。
# 生产前后端同域部署时无需跨域；如需独立域名，用环境变量 ALLOWED_ORIGINS 逗号分隔白名单。
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


__all__ = [n for n in globals() if not n.startswith("__")]
