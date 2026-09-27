# -*- coding: utf-8 -*-
"""
墨造核心层：共享状态、鉴权、项目工具、结算、生成线程与任务持久化。

从原 server.py 拆分而来（第三批优化）。路由按域见 webapp/routers/。
本模块不包含任何 @app 路由；server.py 仅负责组装。
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
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline, World, Character
from novel_gen.utils.chapter_splitter import split_chapters, summarize_preview
from novel_gen.utils.paths import get_output_dir, get_projects_dir
from novel_gen.store.project import ProjectStore, _safe_name

from . import db
from . import mailer
from . import verify_code
from . import teardown_lib
from .logger import get_logger
from .outline_parser import extract_text, parse_outline_text
from .pricing import FREE_REGISTER_BONUS, PRICE_PER_10K, PRICE_PER_10K_HQ, price_for, tiers
from .referral import (AGENT_DISCOUNT, COMMISSION_BASE, COMMISSION_RATE,
                       REFERRAL_BONUS, agent_price, round_money)

logger = get_logger("server")

BASE_DIR = Path(__file__).resolve().parent.parent
# 数据目录可配置：.env 的 DATA_DIR；未配置时回退代码根，行为与历史版本一致
PROJECTS_DIR = get_projects_dir()
_MAX_UPLOAD = 10 * 1024 * 1024  # P2 上传大小上限 10MB
OUTPUT_DIR = get_output_dir()
WEB_DIR = Path(__file__).resolve().parent / "frontend" / "dist"

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


# ---------- 运行中的任务注册表：project_id -> 控制句柄 ----------
class _RunHandle:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None


_RUNS: dict[str, _RunHandle] = {}
_LOCK = threading.Lock()
# 全局并发生成数限制：防止单用户开太多生成任务导致 LLM API 限流/数据库连接耗尽。
# 每个生成任务开始时 acquire，结束时 release。3 路并发对当前额度和连接池是安全水位。
_GEN_SEMAPHORE = threading.Semaphore(3)


def _acquire_slot(project_id: str, busy_msg: str = "系统繁忙，已有多个任务在生成，请稍后再试") -> _RunHandle:
    """占用一个生成槽位：检查是否已在运行 + 建句柄 + 申请并发信号量。失败抛 409/429。"""
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail=busy_msg)
    return handle


def _release_slot(project_id: str) -> None:
    """释放生成槽位：归还信号量并移除句柄。"""
    _GEN_SEMAPHORE.release()
    with _LOCK:
        _RUNS.pop(project_id, None)


# ---------- 请求/响应模型 ----------
class LLMReq(BaseModel):
    provider: str = "deepseek"
    model: str = ""
    base_url: str = ""
    temperature: float = 0.8
    top_p: float = 0.8
    max_tokens: int = 8000


class SearchReq(BaseModel):
    provider: str = ""      # "" / bocha / serper
    api_key: str = ""


class ProjectCreateReq(BaseModel):
    title: str = Field(..., min_length=1)
    genre: str = "玄幻"
    total_words: int = 100_000
    chapter_words: int = 2500
    style: str = ""
    premise: str = ""
    background: str = ""
    hero: str = ""
    characters: list[str] = []
    plot_direction: str = ""
    forbidden: list[str] = []
    editor_review: bool = True
    de_ai: bool = True
    anti_similarity: bool = True
    character_depth: bool = True    # 人物立体化：角色卡一致性/选择代价/生活体感/出场标签与对话辨识度
    confirm: bool = False
    memory_window: int = 5
    mode: str = "auto"              # auto=现有工作流；expand=AI扩写（提供大纲/简要内容）
    expand_input: str = ""          # 扩写模式必填：用户提供的大纲或简要内容
    user_content: str = ""          # 续写/扩写已有章节：用户手写正文（粘贴）；上传走 /api/parse-content 预览后由前端带回
    content_type: str = ""          # expand 模式输入类型：""=大纲/梗概；"chapters"=已有分章正文（续写）；"chapters_expand"=扩写已有章节
    expand_targets: list[int] = []  # 扩写范围：章节号列表，空=全部
    llm: LLMReq = Field(default_factory=LLMReq)
    search: SearchReq = Field(default_factory=SearchReq)
    skill_id: int = 0               # 写作风格 skill id，0=不选
    style_role_id: int = 0          # 写作风格角色 id（style_roles），0=不选
    pacing_density: str = "medium"  # 爽点密度
    plot_speed: str = "medium"      # 剧情速度
    custom_prompt: str = ""         # 用户自定义提示词
    high_quality_mode: bool = False  # 高质量模式：场景级生成+动态模块+参数分层
    with_research: bool = False     # expand 模式可选：生成前先做一次题材调研（产出 research.json + 沉淀拆书库）；auto 模式始终调研，此字段忽略
class OutlineImportReq(BaseModel):
    """项目详情页「重新导入大纲」：以用户传入为准，覆盖前自动备份。"""
    text: str = Field(..., min_length=1)


# 用户内容粘贴上限（需求确认：单次粘贴不超过 10 万字）
MAX_PASTE_CHARS = 100_000


def _backup_outline(store, prefix: str) -> None:
    """自动备份当前大纲为快照（outline_snapshots/），供覆盖/重生成后一键还原。

    与项目页手动保存快照一致，名称自动加时间戳，避免覆盖同名快照。
    """
    try:
        outline = store.load_outline()
        if not outline:
            return
        import json
        import datetime
        snap_dir = store.dir / "outline_snapshots"
        snap_dir.mkdir(exist_ok=True)
        base = f"{prefix}_{datetime.datetime.now().strftime('%m%d_%H%M%S')}"
        name = base
        i = 1
        while (snap_dir / f"{name}.json").exists():
            name = f"{base}_{i}"
            i += 1
        (snap_dir / f"{name}.json").write_text(
            json.dumps(outline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("自动备份大纲失败: project=%s", store.dir.name)


class RegisterReq(BaseModel):
    username: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=4)
    email: str = ""          # 邮箱（必填，全站唯一，格式校验）
    code: str = ""           # 邮箱验证码（必填，先"获取验证码"）
    invite_code: str = ""   # 上级邀请码（选填）


class SendCodeReq(BaseModel):
    email: str = ""


class ResetPasswordReq(BaseModel):
    email: str = ""
    code: str = ""
    new_password: str = Field(..., min_length=4)


class BindReferrerReq(BaseModel):
    invite_code: str = Field(..., min_length=1)


class RechargeAdminReq(BaseModel):
    amount: float = Field(..., gt=0)


class RefundApplyReq(BaseModel):
    amount: float = Field(..., gt=0)
    reason: str = ""


class RefundHandleReq(BaseModel):
    action: str  # approve 确认已退 / reject 拒绝
    admin_note: str = ""


class LoginReq(BaseModel):
    username: str
    password: str


# ---------- 辅助 ----------
def _cfg_to_dict(cfg: NovelConfig) -> dict:
    return {
        "title": cfg.title,
        "genre": cfg.genre,
        "total_words": cfg.total_words,
        "chapter_words": cfg.chapter_words,
        "num_chapters": cfg.num_chapters,
        "style": cfg.style,
        "style_role_id": getattr(cfg, "style_role_id", 0),
        "premise": cfg.premise,
        "background": cfg.background,
        "hero": cfg.hero,
        "characters": list(cfg.characters),
        "plot_direction": cfg.plot_direction,
        "forbidden": list(cfg.forbidden),
        "editor_review": cfg.editor_review,
        "de_ai": cfg.de_ai,
        "anti_similarity": cfg.anti_similarity,
        "character_depth": cfg.character_depth,
        "confirm": cfg.confirm,
        "memory_window": cfg.memory_window,
        "mode": cfg.mode,
        "expand_input": cfg.expand_input,
        "with_research": bool(getattr(cfg, "with_research", False)),
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "temperature": cfg.llm.temperature,
            "top_p": cfg.llm.top_p,
            "max_tokens": cfg.llm.max_tokens,
            "has_api_key": bool(cfg.llm.api_key),
        },
        "search": {"provider": cfg.search.provider, "has_api_key": bool(cfg.search.api_key)},
    }


def _server_api_key(provider: str) -> str:
    """从服务端环境变量取 API Key（Key 集中管理，前端不可见）。"""
    env_key = PROVIDER_DEFAULTS.get(provider, {}).get("env_key", "")
    return os.environ.get(env_key, "") if env_key else ""


def _resolve_llm(provider: str, model: str, base_url: str,
                 temperature: float, top_p: float, max_tokens: int) -> LLMConfig:
    """组装 LLMConfig：api_key 一律用服务端环境变量，忽略前端传入。"""
    return LLMConfig(
        provider=provider,
        model=model,
        api_key=_server_api_key(provider),
        base_url=base_url,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )


def _find_project(project_id: str) -> ProjectStore:
    p = PROJECTS_DIR / project_id
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return ProjectStore.open(p)


def _cover_files(store: ProjectStore) -> tuple:
    """返回 (output 封面路径, 项目内封面路径)。书名固定，路径可预判。"""
    cfg = store.load_config()
    filename = f"{_safe_name(cfg.effective_title())}.png"
    return OUTPUT_DIR / filename, store.dir / filename


def _cover_used_count(store: ProjectStore) -> int:
    """读取该项目已生成封面次数（_cover_count.txt）。"""
    p = store.dir / "_cover_count.txt"
    try:
        return int(p.read_text(encoding="utf-8-sig").strip()) if p.exists() else 0
    except Exception:
        return 0


def _user_llm_cfg(uid: int | None) -> dict | None:
    """读用户自配 LLM；未配 key 返回 None。"""
    if uid is None:
        return None
    u = db.get_user_llm(uid)
    if u.get("api_key") and u.get("provider"):
        return u
    return None


def _project_use_user_llm(store: ProjectStore) -> bool:
    """该项目是否在用用户自己的 key（是则不扣费）。"""
    uid = db.project_owner(store.dir.name)
    return _user_llm_cfg(uid) is not None


def _open_pipeline(store: ProjectStore, trace_id: str = "") -> NovelPipeline:
    cfg = store.load_config()
    uid = db.project_owner(store.dir.name)
    user_llm = _user_llm_cfg(uid)
    if user_llm is not None:
        # 用户自配：用用户的 provider/key/base_url/model
        cfg.llm.provider = user_llm["provider"]
        cfg.llm.api_key = user_llm["api_key"]
        if user_llm.get("base_url"):
            cfg.llm.base_url = user_llm["base_url"]
        if user_llm.get("model"):
            cfg.llm.model = user_llm["model"]
    elif cfg.llm.provider != "mock" and not cfg.llm.api_key:
        # 平台 key：从服务端环境变量补齐
        cfg.llm.api_key = _server_api_key(cfg.llm.provider)
    if cfg.search.provider and not cfg.search.api_key:
        from novel_gen.config import SEARCH_PROVIDER_DEFAULTS
        cfg.search.api_key = os.environ.get(
            SEARCH_PROVIDER_DEFAULTS.get(cfg.search.provider, {}).get("env_key", ""), "")
    # 模块0：Agent 加固配置支持 .env 覆盖（生产动态调阈值，不改代码）
    try:
        from novel_gen.config import apply_guard_env_overrides
        apply_guard_env_overrides(cfg.agent_guard)
    except Exception:
        pass
    llm = create_llm(cfg.llm)
    # 模块1：注入全链路 trace（MySQL sink 自身吞异常；任何失败不影响生成）
    from novel_gen.pipeline.trace import TraceRecorder
    from webapp.db.traces import MysqlTraceSink
    try:
        recorder = TraceRecorder(
            sink=MysqlTraceSink(),
            trace_id=trace_id,
            project_id=store.dir.name,
            user_id=int(uid or 0),
        )
    except Exception:
        recorder = TraceRecorder(project_id=store.dir.name, user_id=int(uid or 0))
    return NovelPipeline(cfg, store, llm, trace=recorder)


def _guard_user_input(text, field: str) -> None:
    """API 输入注入检测（模块3）：命中典型攻击话术抛 400，拒绝写入。

    覆盖创建项目/导入大纲/编辑章节/技能与风格角色等所有用户可写输入点。
    """
    if not text or not str(text).strip():
        return
    try:
        from novel_gen.safety import detect_prompt_injection
        hits = detect_prompt_injection(str(text))
        if hits:
            logger.warning("输入注入拦截: field=%s hits=%s", field, hits)
            raise HTTPException(
                status_code=400,
                detail=f"输入内容包含异常指令（{field}），已被安全策略拦截，请修改后重试",
            )
    except HTTPException:
        raise
    except Exception:
        # 检测器自身异常不阻断业务（降级放行）
        pass


# ---------- 鉴权 ----------
def _require_user(authorization: str = Header(default="")) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization[7:].strip()
    uid = db.get_session_user(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    user = db.get_user(uid)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def _require_admin(user: dict = Depends(_require_user)) -> dict:
    """管理后台专用：仅站长（is_admin）可访问。"""
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="仅站长可访问管理后台")
    return user


def _check_project_access(project_id: str, user: dict) -> None:
    """校验当前用户是否有权访问该项目（管理员可访问全部）。"""
    if user["is_admin"]:
        return
    owner = db.project_owner(project_id)
    if owner is None:
        # 未登记归属的历史项目：普通用户无权访问
        raise HTTPException(status_code=403, detail="无权访问该项目")
    if owner != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问该项目")


def _assign_legacy_projects() -> None:
    """把磁盘上未登记归属的历史项目全部分配给管理员。"""
    admin = db.get_user_by_name("admin")
    if not admin:
        return
    if not PROJECTS_DIR.exists():
        return
    owners = db.all_projects()
    for d in PROJECTS_DIR.iterdir():
        if d.is_dir() and d.name not in owners:
            db.claim_project(d.name, admin["id"])


# ---------- 任务持久化（崩溃/重启后自动重拾） ----------
_TASK_FILE = "task.json"


def _mark_task(store: ProjectStore, kind: str, **kw) -> None:
    """落盘任务标记（服务重启后据此重拾）。线程启动时调用。"""
    try:
        store.dir.joinpath(_TASK_FILE).write_text(
            json.dumps({"kind": kind, "started_at": time.time(), **kw}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def _clear_task(store: ProjectStore) -> None:
    """清除任务标记。线程正常结束/用户停止/异常退出时调用。"""
    try:
        p = store.dir / _TASK_FILE
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _resume_interrupted_tasks() -> None:
    """扫描 PROJECTS_DIR 下残留的 task.json，逐个重拾生成任务。

    重拾后从断点继续（章节产物文件是唯一权威来源，不重复生成）；
    并发槽不足时跳过并告警（用户可手动重新触发）。
    """
    if not PROJECTS_DIR.exists():
        return
    resumed = skipped = 0
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        tf = d / _TASK_FILE
        if not tf.exists():
            continue
        project_id = d.name
        try:
            meta = json.loads(tf.read_text(encoding="utf-8"))
            kind = meta.get("kind", "")
            store = ProjectStore.open(d)
            handle = _RunHandle()
            with _LOCK:
                _RUNS[project_id] = handle
            if not _GEN_SEMAPHORE.acquire(blocking=False):
                with _LOCK:
                    _RUNS.pop(project_id, None)
                skipped += 1
                logger.warning("启动重拾跳过（并发槽满）: project=%s kind=%s", project_id, kind)
                continue
            if kind == "generation":
                target = _run_generation
                args = (store, handle, int(meta.get("start_from", 0) or 0))
            elif kind == "outline":
                target = _run_outline_only
                args = (store, handle)
            elif kind == "batch":
                target = _run_batch
                args = (store, handle, int(meta.get("start", 1) or 1), int(meta.get("end", 1) or 1))
            else:
                _clear_task(store)
                _GEN_SEMAPHORE.release()
                with _LOCK:
                    _RUNS.pop(project_id, None)
                logger.warning("未知任务标记，已清理: project=%s kind=%s", project_id, kind)
                continue
            t = threading.Thread(target=target, args=args, daemon=True)
            handle.thread = t
            t.start()
            resumed += 1
            logger.info("启动重拾任务: project=%s kind=%s", project_id, kind)
        except Exception:
            logger.exception("启动重拾失败: project=%s", project_id)
    logger.info("启动重拾完成: resumed=%d skipped=%d", resumed, skipped)


# ---------- 项目列表（按用户过滤） ----------
def _list_projects(user: dict, sort: str = "created", show_archived: bool = False) -> list[dict]:
    if not PROJECTS_DIR.exists():
        return []
    allowed = None if user["is_admin"] else db.list_user_projects(user["id"])
    items = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        if allowed is not None and d.name not in allowed:
            continue
        try:
            store = ProjectStore.open(d)
            cfg = store.load_config()
            state = store.load_state()
            # 轻量统计走 meta 缓存（章节未变化时零章节读取），首次/变更后自动重算
            meta = store.load_or_refresh_meta()
            archived = getattr(state, "archived", False)
            if archived and not show_archived:
                continue
            items.append({
                "id": d.name,
                "title": cfg.effective_title(),
                "genre": cfg.genre,
                "total_words": cfg.total_words,
                "created": d.stat().st_mtime,
                "updated": d.stat().st_mtime,
                "phase": state.phase,
                "archived": archived,
                "chapters_done": meta["chapters_done"],
                "chapters_total": meta["chapters_total"],
                "generated_words": meta["generated_words"],
                "running": d.name in _RUNS,
                "has_error": bool(_RUNS.get(d.name) and _RUNS[d.name].error),
                "price": db.project_price(d.name),
            })
        except Exception:
            continue
    # 排序
    if sort == "name":
        items.sort(key=lambda x: x["title"])
    elif sort == "updated":
        items.sort(key=lambda x: x["updated"], reverse=True)
    else:  # created
        items.sort(key=lambda x: x["created"], reverse=True)
    return items


def _grant_consume_commission(buyer_uid: int, project_id: str, amount: float, title: str) -> None:
    """消费返佣：下级实际消费（生成结算全额扣费）时，按实付金额给上级代理返佣。

    创建时不扣费，因此返佣随结算发放；余额不足未足额扣费的项目不返佣（v1 简化，日志可查）。
    """
    if COMMISSION_BASE != "consume":
        return
    buyer = db.get_user(buyer_uid)
    if not buyer or not buyer.get("referrer_id"):
        return
    ref = db.get_user(buyer["referrer_id"])
    if ref and ref.get("is_agent"):
        commission = round_money(amount * COMMISSION_RATE)
        db.pay_commission(
            ref["id"], buyer_uid, commission, "consume",
            project_id=project_id,
            note=f"下级 {buyer['username']} 消费《{title}》",
        )
        logger.info("消费返佣(结算): agent_uid=%d buyer_uid=%d amount=%.2f project=%s",
                    ref["id"], buyer_uid, commission, project_id)


def _compute_settle(store: ProjectStore) -> dict:
    """统一结算计算（单一事实来源）。

    返回 ai_words / billable_words / target_ai_words / price（按 billable 封顶后整本价）
    / hq / discount / use_user_llm。增量结算与整批结算共用，避免两套口径漂移。
    """
    user_words = store.user_words()
    chapters = store.list_chapters()
    total_words = sum(len(c.content or "") for c in chapters)
    ai_words = max(0, total_words - user_words)
    target_total = store.load_config().total_words or 0
    target_ai_words = max(0, target_total - user_words)
    billable_words = min(ai_words, target_ai_words) if target_ai_words > 0 else ai_words
    hq = bool(store.load_config().__dict__.get("high_quality_mode", False))
    discount = db.project_discount(store.dir.name)
    price = round_money(agent_price(price_for(billable_words, high_quality=hq), discount))
    return {
        "ai_words": ai_words,
        "billable_words": billable_words,
        "target_ai_words": target_ai_words,
        "price": price,
        "hq": hq,
        "discount": discount,
        "use_user_llm": _project_use_user_llm(store),
    }


def _settle_incremental(store: ProjectStore) -> tuple[bool, str]:
    """每生成完一章后增量结算：按该章新增 AI 字数实时扣费。

    与整批结束的 _settle_price 幂等互补（settle.json 已存在时整批跳过，不会双扣）。
    资金安全（P0）：**先落盘结算基线再扣费**——宁可漏扣（站长后台补偿），
    也不允许"扣费成功但 settle.json 未写"的崩溃窗口导致重启后重复扣费。
    返回 (should_stop, 原因)；余额耗尽时停止生成。
    """
    try:
        project_id = store.dir.name
        uid = db.project_owner(project_id)
        if uid is None:
            return False, ""
        title = store.load_config().effective_title()
        calc = _compute_settle(store)
        ai_words, billable_words = calc["ai_words"], calc["billable_words"]
        target_ai_words = calc["target_ai_words"]

        sp = store.dir / "settle.json"
        settled_ai, settled_price = 0, 0.0
        if sp.exists():
            try:
                old = json.loads(sp.read_text(encoding="utf-8"))
                settled_ai = int(old.get("settled_ai_words", 0) or 0)
                settled_price = float(old.get("settled_price", 0.0) or 0.0)
            except Exception:
                settled_ai, settled_price = 0, 0.0

        # 已结算基线按封顶口径换算，避免 target 变更导致多扣
        prev_b = min(settled_ai, target_ai_words) if target_ai_words > 0 else settled_ai
        billable_new = max(0, billable_words - prev_b)

        if billable_new > 0 and not calc["use_user_llm"]:
            price = round_money(agent_price(price_for(billable_new, high_quality=calc["hq"]),
                                            calc["discount"]))
            bal = float(db.get_user(uid)["balance"])
            actual = min(bal, price)
            new_settled_price = round_money(settled_price + actual)
            # P0：先落盘结算基线（含本次实扣金额），再扣费；崩溃后重跑按基线跳过，不重扣
            sp.write_text(json.dumps({
                "settled_ai_words": ai_words,
                "settled_price": new_settled_price,
                "target_ai_words": target_ai_words,
                "status": "incremental",
            }, ensure_ascii=False), encoding="utf-8")
            if actual > 0:
                db.change_balance(uid, -actual,
                                  f"《{title}》章节增量结算：{billable_new} 字，扣 {actual:.2f} 元")
                try:
                    _grant_consume_commission(uid, project_id, actual, title)
                except Exception:
                    pass
            settled_price = new_settled_price
        else:
            # 无新增或 BYOK：仅推进基线（幂等），不扣费
            sp.write_text(json.dumps({
                "settled_ai_words": ai_words,
                "settled_price": settled_price,
                "target_ai_words": target_ai_words,
                "status": "incremental",
            }, ensure_ascii=False), encoding="utf-8")
        try:
            db.update_project_price(project_id, settled_price)
        except Exception:
            pass
        new_bal = float(db.get_user(uid)["balance"])
        if new_bal <= 0:
            return True, f"余额已耗尽（扣费后余额 {new_bal:.2f} 元），已停止生成。充值后可从断点继续。"
        return False, ""
    except Exception:
        logger.exception("增量结算异常: project=%s", store.dir.name)
        return False, ""
def _settle_price(store: ProjectStore) -> None:
    """生成完成后整批结算兜底（仅当从未走增量结算时生效，幂等）。

    ai_words = 全书当前总字数 - 用户手写原字数；未产生 AI 文字不收费；
    settle.json 已存在（增量结算已落盘）则跳过，不会双扣；
    余额不足扣到 0 并记录 debt.json。消费返佣在结算足额扣费时发放。
    """
    try:
        project_id = store.dir.name
        sp = store.dir / "settle.json"
        if sp.exists():
            return
        uid = db.project_owner(project_id)
        if uid is None:
            sp.write_text(json.dumps({"mode": "skip"}), encoding="utf-8")
            return
        title = store.load_config().effective_title()
        calc = _compute_settle(store)
        ai_words, billable_words = calc["ai_words"], calc["billable_words"]
        final_price = calc["price"]
        if ai_words <= 0:
            # 未真正生成任何 AI 文字：不收费
            db.update_project_price(project_id, 0.0)
            sp.write_text(json.dumps({"ai_words": 0, "final_price": 0.0}), encoding="utf-8")
            return
        # 用户自配 key：平台未花 LLM 钱，不扣费
        if calc["use_user_llm"]:
            db.update_project_price(project_id, 0.0)
            sp.write_text(json.dumps({"ai_words": ai_words, "billable_words": 0,
                                      "final_price": 0.0, "mode": "user_llm_free"}),
                          encoding="utf-8")
            return
        # P0 修复：先落盘幂等标记再扣费。崩溃/异常时 settle.json 已存在，
        # 重启后不再重复扣费；宁可漏扣（站长后台补偿）也不重复扣。
        sp.write_text(json.dumps({"ai_words": ai_words, "billable_words": billable_words,
                                  "target_ai_words": calc["target_ai_words"],
                                  "final_price": final_price,
                                  "hq": calc["hq"], "status": "charging"}), encoding="utf-8")
        bal = db.get_user(uid)["balance"]
        if bal >= final_price:
            db.change_balance(uid, -final_price,
                              f"《{title}》生成结算：AI 生成 {ai_words} 字（计费 {billable_words} 字），扣费 {final_price:.2f} 元")
            try:
                _grant_consume_commission(uid, project_id, final_price, title)
            except Exception:
                pass
        else:
            short = round_money(final_price - bal)
            if bal > 0:
                db.change_balance(uid, -bal,
                                  f"《{title}》生成结算：AI 生成 {ai_words} 字，余额不足仅扣 {bal:.2f} 元")
            store.dir.joinpath("debt.json").write_text(
                json.dumps({"owe": short, "ai_words": ai_words, "final_price": final_price}),
                encoding="utf-8")
            logger.warning("结算扣费余额不足: uid=%d project=%s final=%.2f bal=%.2f 欠费=%.2f",
                           uid, project_id, final_price, bal, short)
        db.update_project_price(project_id, final_price)
        sp.write_text(json.dumps({"ai_words": ai_words, "billable_words": billable_words,
                                  "target_ai_words": calc["target_ai_words"],
                                  "final_price": final_price,
                                  "hq": calc["hq"], "status": "done"}),
                      encoding="utf-8")
        logger.info("结算完成: project=%s ai_words=%d billable=%d target=%d final=%.2f balance=%.2f",
                    project_id, ai_words, billable_words, calc["target_ai_words"], final_price,
                    db.get_user(uid)["balance"])
    except Exception:
        logger.exception("结算异常: project=%s", store.dir.name)
def _run_outline_only(store: ProjectStore, handle: _RunHandle) -> None:
    """只跑 research → outline → editor_review → world，不写正文。
    完成后 phase 标记为 outline_done，等用户编辑大纲后再 /start。"""
    import uuid
    cfg = store.load_config()
    trace_id = uuid.uuid4().hex
    _mark_task(store, "outline")
    logger.info("大纲生成线程启动: project=%s title=%s trace=%s",
                store.dir.name, cfg.effective_title(), trace_id)
    try:
        pipeline = _open_pipeline(store, trace_id=trace_id)
        pipeline.run_research()
        # 爆款拆书库：调研成果自动沉淀（失败不影响生成，内部已捕获异常）
        teardown_lib.sink_research(pipeline.store.load_research(), store.dir.name)
        if handle.stop_event.is_set():
            return
        pipeline.run_outline()
        if handle.stop_event.is_set():
            return
        pipeline.run_editor_review()
        if handle.stop_event.is_set():
            return
        pipeline.run_world()
        if handle.stop_event.is_set():
            return
        # 标记阶段完成
        pipeline._mark_state("outline_done")
        logger.info("大纲生成完成: project=%s chapters=%d", store.dir.name, len(pipeline.store.load_outline().chapters))
    except AgentGuardError as exc:
        # 模块2：成本防护超限——返回已生成的有效结果 + 超限提示，不抛 500
        handle.error = f"{exc}（已保留当前已生成内容，可从断点继续）"
        logger.warning("大纲生成被成本防护终止: project=%s %s", store.dir.name, exc)
        try:
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    except Exception as exc:
        handle.error = f"{type(exc).__name__}: {exc}"
        logger.exception("大纲生成失败: project=%s error=%s", store.dir.name, exc)
        try:
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    finally:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(store.dir.name, None)
        _clear_task(store)


def _research_needed(cfg, has_outline: bool) -> bool:
    """是否需要执行题材调研：无大纲必跑（auto 全自动）；expand 模式勾选「附带调研」也跑。

    expand 勾选调研时只补调研（research.json + 拆书库沉淀），用户已导入的大纲不被重写——
    是否生成大纲仍由 has_outline 单独决定。
    """
    if not has_outline:
        return True
    return cfg.mode == "expand" and bool(getattr(cfg, "with_research", False))


def _run_generation(store: ProjectStore, handle: _RunHandle, start_from: int = 0) -> None:
    """后台线程：从断点续跑整条流水线，结束后自动导出。

    手动编排阶段并在每章之间检查停止标记，使「停止」真实生效。
    start_from > 0 时：跳过 research/outline/world，直接从 start_from 章开始生成
    （用于「从某章重新生成」，此时这些前置产物已存在）。
    """
    import uuid
    cfg = store.load_config()
    trace_id = uuid.uuid4().hex
    _mark_task(store, "generation", start_from=start_from)
    logger.info("生成线程启动: project=%s title=%s start_from=%d trace=%s",
                store.dir.name, cfg.effective_title(), start_from, trace_id)
    try:
        pipeline = _open_pipeline(store, trace_id=trace_id)

        if start_from > 0:
            done = start_from - 1
        else:
            # 已有大纲则复用，不重新生成（用户上传大纲/之前已生成过的情况）
            existing_outline = store.load_outline()
            has_outline = bool(existing_outline and existing_outline.chapters)
            if _research_needed(cfg, has_outline):
                pipeline.run_research()
                # 爆款拆书库：调研成果自动沉淀（失败不影响生成，内部已捕获异常）
                teardown_lib.sink_research(pipeline.store.load_research(), store.dir.name)
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=research", store.dir.name)
                    return
            if not has_outline:
                pipeline.run_outline()
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=outline", store.dir.name)
                    return
                pipeline.run_editor_review()
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=editor_review", store.dir.name)
                    return
            # 世界观：有则复用，没有则生成
            existing_world = store.load_world()
            if not (existing_world and (existing_world.world_view or existing_world.characters)):
                pipeline.run_world()
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=world", store.dir.name)
                    return
            done, _total = pipeline.progress()

        done, total = pipeline.progress()
        try:
            st = store.load_state()
            st.phase = "writing"
            st.last_chapter = done
            st.total_chapters = total
            st.error = ""
            store.save_state(st)
        except Exception:
            pass
        for no in range(done + 1, total + 1):
            if handle.stop_event.is_set():
                print(f"[web] 收到停止请求，第 {no} 章暂停")
                logger.info("生成停止: project=%s 阶段=writing 第 %d 章暂停", store.dir.name, no)
                break
            stop, why = _settle_incremental(store)
            if stop:
                handle.error = why
                logger.warning("余额耗尽停止: project=%s 第 %d 章后 %s", store.dir.name, no, why)
                break
            pipeline.write_chapter(no, silent=False)
            stop, why = _settle_incremental(store)
            if stop:
                handle.error = why
                logger.warning("余额耗尽停止: project=%s 第 %d 章后 %s", store.dir.name, no, why)
                break

        # 未停止则导出成品，并把状态统一回写为 done，避免 phase 滞后
        if not handle.stop_event.is_set():
            pipeline.export()
            try:
                st = store.load_state()
                st.phase = "done"
                st.last_chapter = len(store.list_chapters())
                outline = store.load_outline()
                st.total_chapters = len(outline.chapters) if outline else st.total_chapters
                store.save_state(st)
            except Exception:
                pass
        logger.info("生成完成: project=%s title=%s", store.dir.name, cfg.effective_title())
    except AgentGuardError as exc:
        # 模块2：成本防护超限——不抛 500，保留已生成产物 + 超限提示（可从断点继续）
        handle.error = f"{exc}（已保留当前已生成内容，可从断点继续）"
        logger.warning("生成被成本防护终止: project=%s %s", store.dir.name, exc)
        try:
            store.dir.joinpath("failed.json").write_text(
                json.dumps({"error": handle.error, "title": cfg.effective_title(),
                            "guard": True}, ensure_ascii=False),
                encoding="utf-8")
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        handle.error = f"{type(exc).__name__}: {exc}"
        # P1 修复：失败落盘标记，前端可展示 failed 状态与原因
        try:
            store.dir.joinpath("failed.json").write_text(
                json.dumps({"error": handle.error, "title": cfg.effective_title()}, ensure_ascii=False),
                encoding="utf-8")
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
        logger.exception("生成失败: project=%s title=%s error=%s",
                         store.dir.name, cfg.effective_title(), exc)
    finally:
        _GEN_SEMAPHORE.release()
        _settle_price(store)
        with _LOCK:
            _RUNS.pop(store.dir.name, None)
        _clear_task(store)
        logger.info("生成线程结束: project=%s", store.dir.name)


def _run_batch(store: ProjectStore, handle: _RunHandle, start: int, end: int) -> None:
    """批量生成章节线程：指定起始/结束章逐章生成，结束后整批结算。

    独立成模块级函数，便于启动重拾复用（task.json 记录 start/end）。
    """
    import uuid
    project_id = store.dir.name
    trace_id = uuid.uuid4().hex
    _mark_task(store, "batch", start=start, end=end)
    try:
        pipeline = _open_pipeline(store, trace_id=trace_id)
        outline = store.load_outline()
        research = store.load_research()
        for no in range(start, end + 1):
            if handle.stop_event.is_set() or no > len(outline.chapters):
                break
            if _is_locked(store, "chapter", no):
                logger.info("批量生成跳过锁定章节: project=%s no=%d", project_id, no)
                continue
            plan = outline.chapters[no - 1]
            pipeline.chapter_writer.write(no, plan.title, plan.goal, research,
                                          is_final=(no == len(outline.chapters)))
            stop, why = _settle_incremental(store)
            if stop:
                handle.error = why
                logger.warning("批量生成余额耗尽: project=%s 第 %d 章后 %s", project_id, no, why)
                break
        _settle_price(store)
    except AgentGuardError as exc:
        # 模块2：成本防护超限——保留已生成产物 + 超限提示
        handle.error = f"{exc}（已保留当前已生成内容，可从断点继续）"
        logger.warning("批量生成被成本防护终止: project=%s %s", project_id, exc)
    except Exception as e:
        handle.error = f"{type(e).__name__}: {e}"
        logger.exception("批量生成失败: %s", project_id)
    finally:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        _clear_task(store)


# ---------- 基础 ----------
def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email") or "",
        "balance": user["balance"],
        "is_admin": bool(user["is_admin"]),
        "is_agent": bool(user.get("is_agent", 0)),
        "agent_discount": float(user.get("agent_discount", 1.0)),
        "invite_code": user.get("invite_code") or db.get_invite_code(user["id"]),
        "referrer_id": user.get("referrer_id"),
    }


# P2 安全：登录失败限流（按 IP，防爆破）
import time as _time
_login_fails: dict[str, list] = {}
_LOGIN_LOCK = threading.Lock()
_LOGIN_MAX = 8        # 最多失败次数
_LOGIN_WINDOW = 300  # 5 分钟窗口


def _client_ip(request) -> str:
    return request.client.host if request and request.client else "unknown"


def _login_ratelimit(ip: str):
    now = _time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        if len(fails) >= _LOGIN_MAX:
            return False
        _login_fails[ip] = fails
    return True


def _record_login_fail(ip: str):
    now = _time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        fails.append(now)
        _login_fails[ip] = fails


class SkillReq(BaseModel):
    name: str
    description: str = ""
    prompt_append: str = ""
    is_active: int = 1
    sort_order: int = 0
    role: str = ""  # 空=全局；researcher/architect/writer/reviewer=专属角色


class StyleRoleReq(BaseModel):
    name: str = ""
    description: str = ""
    role_prompt: str = ""
    is_active: int = 1
    sort_order: int = 0


class StyleRoleSkillReq(BaseModel):
    skill_type: str = "admin"   # admin=平台写作技能 / user=我的技能
    skill_id: int = 0


class AgentSetReq(BaseModel):
    discount: float = 0.8


class UserLlmReq(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class OutlineChapterReq(BaseModel):
    title: str = ""
    goal: str = ""
    core_event: str = ""
    conflict: str = ""
    character_moves: str = ""
    foreshadowing: str = ""
    emotion: str = ""
    transition: str = ""
    tag: str = ""


class OutlineSaveReq(BaseModel):
    title: str = ""
    intro: str = ""
    chapters: list[OutlineChapterReq] = []


def _regen_limit_path(store: ProjectStore) -> Path:
    return store.dir / "regen_count.json"


def _check_regen_limit(store: ProjectStore, project_id: str) -> None:
    """重生成免费，但每项目每日限 10 次（防滥用）。超限返回 429。"""
    p = _regen_limit_path(store)
    today = datetime.now().strftime("%Y-%m-%d")
    data = {}
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    cnt = int(data.get(today, 0))
    if cnt >= REGEN_DAILY_LIMIT:
        raise HTTPException(status_code=429,
                            detail=f"今日该项目重生成次数已达上限（{REGEN_DAILY_LIMIT} 次/日），请明天再试")
    data[today] = cnt + 1
    try:
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.warning("重生成计数写入失败: project=%s", project_id)


def _outline_diff(outline, store: ProjectStore) -> tuple[list[int], list[int]]:
    """返回 (changed, removed)：
    changed = 已生成章节中，标题/目标与当前大纲不一致的章节号（大纲编辑后需要重生成的候选）
    removed = 已生成正文存在但已不在大纲中的章节号（大纲删章，文件保留标"已废弃"）
    """
    changed: list[int] = []
    removed: list[int] = []
    for ch in store.list_chapters():
        if ch.no > len(outline.chapters):
            removed.append(ch.no)
            continue
        plan = outline.chapters[ch.no - 1]
        if ch.title != plan.title or (ch.goal and ch.goal != plan.goal):
            changed.append(ch.no)
    return changed, removed


class ChapterEditReq(BaseModel):
    title: str = ""
    content: str = ""
    summary: str = ""   # 可留空：留空时自动用 LLM 重新生成摘要
    regen_summary: bool = True   # 是否在正文变更后重新生成摘要


def _load_prev_content(store: ProjectStore, no: int) -> str:
    old = store.load_chapter(no)
    return old.content if old else ""


def _regenerate_summary(store: ProjectStore, ch) -> str:
    """用 LLM 重新生成章节摘要（正文被人工修改后，记忆库要跟着更新）。失败返回空串。"""
    try:
        pipeline = _open_pipeline(store)
        return pipeline.chapter_writer._gen_summary(ch.no, ch.title, ch.content)
    except Exception:
        return ""


def _load_lock(store: ProjectStore) -> dict:
    """读取项目锁定状态（lock.json）。无文件视为全未锁定。"""
    f = store.dir / "lock.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_lock(store: ProjectStore, lock: dict) -> None:
    f = store.dir / "lock.json"
    store._write_json(f, lock)


def _is_locked(store: ProjectStore, kind: str, key="") -> bool:
    lock = _load_lock(store)
    if kind == "outline":
        return bool(lock.get("outline", False))
    if kind == "world_view":
        return bool(lock.get("world_view", False))
    sec = lock.get(kind, {}) or {}
    return bool(sec.get(str(key), False))


def _assert_not_locked(store: ProjectStore, kind: str, key="", msg="") -> None:
    if _is_locked(store, kind, key):
        raise HTTPException(status_code=409, detail=msg or "内容已被用户锁定，如需重生成请先解锁")



__all__ = [n for n in globals() if not n.startswith("__")]

