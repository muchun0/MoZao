# -*- coding: utf-8 -*-
"""
小说生成 Web 后端（FastAPI）：多用户 SaaS 版。

相对单用户版的改动：
1. 用户系统：注册/登录/登出，token 鉴权（SQLite 持久化）
2. 项目隔离：每个项目归属一个用户，只能看/操作自己的项目（管理员可见全部）
3. Key 集中管理：LLM API Key 只放服务端环境变量，前端不再需要填 Key
4. 按单本收费：创建项目时按字数档位从余额扣费，余额不足拒绝
5. 人工收款：充值由管理员命令行手动加余额（见 webapp/admin_cli.py）
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
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
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.pipeline.stages import unique_chapter_titles
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline, Volume
from novel_gen.utils.chapter_splitter import split_chapters, summarize_preview
from novel_gen.store.project import ProjectStore, _safe_name

from . import db
from . import mailer
from . import verify_code
from .logger import get_logger
from .outline_parser import extract_text, parse_outline_text
from .pricing import FREE_REGISTER_BONUS, PRICE_PER_10K, PRICE_PER_10K_HQ, price_for, tier_name_for, tiers
from .referral import (AGENT_DISCOUNT, COMMISSION_BASE, COMMISSION_RATE,
                       REFERRAL_BONUS, agent_price, round_money)

logger = get_logger("server")

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = BASE_DIR / "projects"
OUTPUT_DIR = BASE_DIR / "output"
WEB_DIR = Path(__file__).resolve().parent / "frontend" / "dist"

app = FastAPI(title="AI 小说生成器 API", version="2.0.0")

# 允许前端跨域（前端独立部署时用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    pacing_density: str = "medium"  # 爽点密度
    plot_speed: str = "medium"      # 剧情速度
    custom_prompt: str = ""         # 用户自定义提示词
    high_quality_mode: bool = False  # 高质量模式：场景级生成+动态模块+参数分层


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


def _open_pipeline(store: ProjectStore) -> NovelPipeline:
    cfg = store.load_config()
    # 若 config.json 未落盘 key（设计如此），从服务端环境变量补齐
    if cfg.llm.provider != "mock" and not cfg.llm.api_key:
        cfg.llm.api_key = _server_api_key(cfg.llm.provider)
    if cfg.search.provider and not cfg.search.api_key:
        from novel_gen.config import SEARCH_PROVIDER_DEFAULTS
        cfg.search.api_key = os.environ.get(
            SEARCH_PROVIDER_DEFAULTS.get(cfg.search.provider, {}).get("env_key", ""), "")
    llm = create_llm(cfg.llm)
    return NovelPipeline(cfg, store, llm)


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
            outline = store.load_outline()
            chapters = store.list_chapters()
            total = len(outline.chapters) if outline else cfg.num_chapters
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
                "chapters_done": len(chapters),
                "chapters_total": total,
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


def _settle_price(store: ProjectStore) -> None:
    """生成完成后按 AI 实际生成字数结算扣费（创建时不扣费，真正生成文字才扣）。

    ai_words = 全书当前总字数 - 用户手写原字数（创建时记录；user_words=0 时即全书总字数）。
    所有项目统一：未产生任何 AI 文字（ai_words<=0）不收费；已结算项目幂等跳过。
    余额不足时扣到 0 并记录欠费（debt.json），不阻塞交付。
    消费返佣在结算足额扣费时发放（按下级实际消费金额）。

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
        user_words = store.user_words()
        chapters = store.list_chapters()
        total_words = sum(len(c.content) for c in chapters)
        ai_words = max(0, total_words - user_words)
        if ai_words <= 0:
            # 未真正生成任何 AI 文字：不收费
            db.update_project_price(project_id, 0.0)
            sp.write_text(json.dumps({"ai_words": 0, "final_price": 0.0}), encoding="utf-8")
            return
        discount = db.project_discount(project_id)  # 创建时折扣快照
        # 计费封顶：按用户创建时填的目标 AI 字数收，AI 多写的部分平台承担
        target_total = store.load_config().total_words or 0
        target_ai_words = max(0, target_total - user_words)
        billable_words = min(ai_words, target_ai_words) if target_ai_words > 0 else ai_words
        hq = bool(store.load_config().__dict__.get('high_quality_mode', False))
        final_price = round_money(agent_price(price_for(billable_words, high_quality=hq), discount))
        bal = db.get_user(uid)["balance"]
        if bal >= final_price:
            db.change_balance(uid, -final_price,
                              f"《{title}》生成结算：AI 生成 {ai_words} 字（计费 {billable_words} 字），扣费 {final_price:.2f} 元")
            _grant_consume_commission(uid, project_id, final_price, title)
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
                                  "target_ai_words": target_ai_words, "final_price": final_price}),
                      encoding="utf-8")
        logger.info("结算完成: project=%s ai_words=%d billable=%d target=%d final=%.2f balance=%.2f",
                    project_id, ai_words, billable_words, target_ai_words, final_price,
                    db.get_user(uid)["balance"])
    except Exception:
        logger.exception("结算异常: project=%s", store.dir.name)


def _run_outline_only(store: ProjectStore, handle: _RunHandle) -> None:
    """只跑 research → outline → editor_review → world，不写正文。
    完成后 phase 标记为 outline_done，等用户编辑大纲后再 /start。"""
    cfg = store.load_config()
    logger.info("大纲生成线程启动: project=%s title=%s", store.dir.name, cfg.effective_title())
    try:
        pipeline = _open_pipeline(store)
        pipeline.run_research()
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
    except Exception as exc:
        handle.error = f"{type(exc).__name__}: {exc}"
        logger.exception("大纲生成失败: project=%s error=%s", store.dir.name, exc)
    finally:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(store.dir.name, None)


def _run_generation(store: ProjectStore, handle: _RunHandle, start_from: int = 0) -> None:
    """后台线程：从断点续跑整条流水线，结束后自动导出。

    手动编排阶段并在每章之间检查停止标记，使「停止」真实生效。
    start_from > 0 时：跳过 research/outline/world，直接从 start_from 章开始生成
    （用于「从某章重新生成」，此时这些前置产物已存在）。
    """
    cfg = store.load_config()
    logger.info("生成线程启动: project=%s title=%s start_from=%d",
                store.dir.name, cfg.effective_title(), start_from)
    try:
        pipeline = _open_pipeline(store)

        if start_from > 0:
            done = start_from - 1
        else:
            # 已有大纲则复用，不重新生成（用户上传大纲/之前已生成过的情况）
            existing_outline = store.load_outline()
            has_outline = bool(existing_outline and existing_outline.chapters)
            if not has_outline:
                pipeline.run_research()
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=research", store.dir.name)
                    return
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
        for no in range(done + 1, total + 1):
            if handle.stop_event.is_set():
                print(f"[web] 收到停止请求，第 {no} 章暂停")
                logger.info("生成停止: project=%s 阶段=writing 第 %d 章暂停", store.dir.name, no)
                break
            pipeline.write_chapter(no, silent=False)

        # 未停止则导出成品
        if not handle.stop_event.is_set():
            pipeline.export()
        logger.info("生成完成: project=%s title=%s", store.dir.name, cfg.effective_title())
    except Exception as exc:  # noqa: BLE001
        handle.error = f"{type(exc).__name__}: {exc}"
        logger.exception("生成失败: project=%s title=%s error=%s",
                         store.dir.name, cfg.effective_title(), exc)
    finally:
        _GEN_SEMAPHORE.release()
        _settle_price(store)
        with _LOCK:
            _RUNS.pop(store.dir.name, None)
        logger.info("生成线程结束: project=%s", store.dir.name)


# ---------- 基础 ----------
@app.get("/api/health")
def health():
    return {"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/genres")
def genres():
    return {"genres": GENRES}


@app.get("/api/pricing")
def pricing():
    return {
        "tiers": tiers(),
        "register_bonus": FREE_REGISTER_BONUS,
        "billing_mode": "by_words",   # 按字数计费
        "price_per_10k": PRICE_PER_10K,
        "price_per_10k_hq": PRICE_PER_10K_HQ,
    }


@app.post("/api/parse-content")
async def parse_content(file: UploadFile | None = File(None), text: str = Form(""),
                        user: dict = Depends(_require_user)):
    """解析用户手写内容（上传文件或粘贴文本）：提取文本 + 章节切分预览。"""
    try:
        if file is not None:
            data = await file.read()
            text = extract_text(file.filename or "", data)
        raw = text or ""
    except ValueError as e:
        logger.info("解析内容失败（文件读取/格式）：%s，user=%s", e, user["username"])
        raise HTTPException(status_code=400, detail=str(e))
    if not raw.strip():
        raise HTTPException(status_code=400, detail="内容为空，请粘贴文本或上传文件")
    chs = split_chapters(raw)
    if not chs:
        raise HTTPException(status_code=400, detail="无法识别内容，请检查文本格式")
    many = len(chs) > 1 or bool(chs[0]["title"])
    return {
        "ok": True,
        "content_type": "chapters" if many else "outline",
        "total_chars": len(raw),
        "full_text": raw,          # 服务端统一提取的完整文本（docx/txt/md 均已转码），前端填回"已有内容"
        "chapters": summarize_preview(chs),
    }


@app.post("/api/parse-outline")
async def parse_outline(file: UploadFile = File(...), user: dict = Depends(_require_user)):
    """上传大纲文档（txt/md/docx），智能解析成新建表单字段。"""
    try:
        data = await file.read()
        text = extract_text(file.filename or "", data)
    except ValueError as e:
        logger.info(f"解析大纲失败（文件读取/格式）：{e}，user={user['username']}")
        raise HTTPException(status_code=400, detail=str(e))
    api_key = _server_api_key("deepseek")
    if not api_key:
        raise HTTPException(status_code=503, detail="服务端未配置解析模型（DEEPSEEK_API_KEY），请联系站长")
    llm = create_llm(LLMConfig(provider="deepseek", model="", base_url="", api_key=api_key,
                               temperature=0.2, top_p=0.7, max_tokens=2000))
    try:
        result = parse_outline_text(text, llm)
    except Exception as e:
        logger.exception(f"解析大纲异常：{e}，user={user['username']}")
        raise HTTPException(status_code=500, detail="大纲解析失败，请稍后重试")
    logger.info(f"解析大纲成功：{file.filename or '?'}，title={result.get('title')}，user={user['username']}")
    return {"ok": True, "data": result}


# ---------- 用户 ----------
@app.post("/api/send-register-code", status_code=201)
def send_register_code(req: SendCodeReq):
    """注册验证码：邮箱格式 + 未被注册校验，通过则发邮件。"""
    email = (req.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if not db._valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if db.get_user_by_email(email):
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    ok, msg = verify_code.send_code("reg", email)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "msg": msg}


@app.post("/api/register", status_code=201)
def register(req: RegisterReq):
    email = (req.email or "").strip().lower()
    # 邮箱占用提前校验（避免消耗验证码）
    if db.get_user_by_email(email):
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    # 验证码校验
    ok, why = verify_code.verify_code("reg", email, req.code)
    if not ok:
        logger.warning("注册失败: username=%s reason=%s", req.username, why)
        raise HTTPException(status_code=400, detail=why)
    # 第一个注册的用户自动成为管理员（站长）
    is_first = db.get_user(1) is None
    ok, msg = db.create_user(req.username, req.password,
                             is_admin=is_first, invite_code=req.invite_code,
                             email=email)
    if not ok:
        logger.warning("注册失败: username=%s reason=%s", req.username, msg)
        raise HTTPException(status_code=400, detail=msg)
    user = db.get_user_by_name(req.username)
    # 注册赠送余额
    if FREE_REGISTER_BONUS > 0:
        db.change_balance(user["id"], FREE_REGISTER_BONUS, "注册赠送")
        user = db.get_user(user["id"])  # 重新取余额快照
    # 第一个管理员：把历史项目分配给他
    if is_first:
        _assign_legacy_projects()
    # 拉新奖励：注册填了上级邀请码且上级是代理 → 给上级发奖励（默认关闭）
    if REFERRAL_BONUS > 0 and user.get("referrer_id"):
        ref = db.get_user(user["referrer_id"])
        if ref and ref.get("is_agent"):
            db.pay_commission(ref["id"], user["id"], REFERRAL_BONUS, "register",
                              note=f"拉新奖励（{user['username']} 注册）")
    token = db.create_session(user["id"])
    logger.info("注册成功: uid=%d username=%s is_admin=%d referrer=%s",
                user["id"], user["username"], int(is_first), user.get("referrer_id"))
    return {"token": token, "user": _public_user(user), "note": msg if msg != "ok" else ""}


@app.post("/api/send-reset-code", status_code=201)
def send_reset_code(req: SendCodeReq):
    """忘记密码验证码：邮箱已注册才发；未注册也返回同样提示（不暴露注册状态）。"""
    email = (req.email or "").strip().lower()
    if not email or not db._valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    if db.get_user_by_email(email):
        ok, msg = verify_code.send_code("reset", email)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    logger.info("发送重置验证码请求: email=%s", email)
    return {"ok": True, "msg": "若该邮箱已注册，验证码邮件已发送，请查收"}


@app.post("/api/reset-password")
def reset_password(req: ResetPasswordReq):
    """忘记密码重置：验证码 + 新密码。成功则删除该账号全部会话，强制重新登录。"""
    email = (req.email or "").strip().lower()
    if not email or not db._valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    ok, why = verify_code.verify_code("reset", email, req.code)
    if not ok:
        logger.warning("重置密码失败: email=%s reason=%s", email, why)
        raise HTTPException(status_code=400, detail=why)
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=400, detail="该邮箱未注册")
    db.update_password(user["id"], req.new_password)
    db.delete_user_sessions(user["id"])
    logger.info("密码重置成功: uid=%d email=%s", user["id"], email)
    return {"ok": True, "msg": "密码已重置，请使用新密码登录"}


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


@app.post("/api/login")
def login(req: LoginReq):
    # req.username 支持「用户名 或 邮箱」两种登录方式
    uid = db.authenticate(req.username, req.password)
    if uid is None:
        logger.warning("登录失败: identifier=%s", req.username)
        raise HTTPException(status_code=401, detail="用户名/邮箱或密码错误")
    user = db.get_user(uid)
    token = db.create_session(uid)
    logger.info("登录成功: uid=%d username=%s", uid, user["username"])
    return {"token": token, "user": _public_user(user)}


@app.post("/api/logout")
def logout(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        db.delete_session(authorization[7:].strip())
        logger.info("退出登录: token 已注销")
    return {"ok": True}


@app.post("/api/admin/login")
def admin_login(req: LoginReq):
    """管理后台登录：仅站长（is_admin=1）可登录，普通用户返回 403。支持用户名或邮箱。"""
    uid = db.authenticate(req.username, req.password)
    if uid is None:
        logger.warning("管理登录失败: identifier=%s", req.username)
        raise HTTPException(status_code=401, detail="用户名/邮箱或密码错误")
    user = db.get_user(uid)
    if not user["is_admin"]:
        logger.warning("非管理员尝试登录管理后台: uid=%d username=%s", uid, user["username"])
        raise HTTPException(status_code=403, detail="非管理员账号无权登录管理后台")
    token = db.create_session(uid)
    logger.info("管理后台登录成功: uid=%d username=%s", uid, user["username"])
    return {"token": token, "user": _public_user(user)}


# ---------- 管理后台 ----------
@app.get("/api/admin/stats")
def admin_stats(user: dict = Depends(_require_admin)):
    return {"stats": db.admin_stats()}


# ---------- 写作风格 Skill（仅站长管理；前台只读启用列表） ----------

class SkillReq(BaseModel):
    name: str
    description: str = ""
    prompt_append: str = ""
    is_active: int = 1
    sort_order: int = 0
    role: str = ""  # 空=全局；researcher/architect/writer/reviewer=专属角色


@app.get("/api/skills")
def public_skills():
    """前台创建小说时用：只返回启用中的 skill。"""
    return {"skills": db.list_skills(only_active=True)}


@app.get("/api/admin/skills")
def admin_list_skills(user: dict = Depends(_require_admin)):
    return {"skills": db.list_skills()}


@app.post("/api/admin/skills")
def admin_create_skill(req: SkillReq, user: dict = Depends(_require_admin)):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="名称不能为空")
    sid = db.create_skill(req.name.strip(), req.description, req.prompt_append,
                          req.is_active, req.sort_order, req.role)
    logger.info("新建写作风格: id=%d name=%s by=%s", sid, req.name, user["username"])
    return {"ok": True, "id": sid}


@app.put("/api/admin/skills/{skill_id}")
def admin_update_skill(skill_id: int, req: SkillReq, user: dict = Depends(_require_admin)):
    if not db.get_skill(skill_id):
        raise HTTPException(status_code=404, detail="Skill 不存在")
    db.update_skill(skill_id, name=req.name.strip(), description=req.description,
                     prompt_append=req.prompt_append, is_active=req.is_active,
                     sort_order=req.sort_order, role=req.role)
    logger.info("更新写作风格: id=%d by=%s", skill_id, user["username"])
    return {"ok": True}


@app.delete("/api/admin/skills/{skill_id}")
def admin_delete_skill(skill_id: int, user: dict = Depends(_require_admin)):
    if not db.get_skill(skill_id):
        raise HTTPException(status_code=404, detail="Skill 不存在")
    db.delete_skill(skill_id)
    logger.info("删除写作风格: id=%d by=%s", skill_id, user["username"])
    return {"ok": True}


# ---------- 用户自定义 Skills ----------

@app.get("/api/my-skills")
def my_skills(user: dict = Depends(_require_user)):
    return {"skills": db.list_user_skills(user["id"])}


@app.post("/api/my-skills")
def create_my_skill(req: dict, user: dict = Depends(_require_user)):
    name = (req.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    sid = db.create_user_skill(
        user_id=user["id"],
        name=name,
        description=req.get("description", ""),
        prompt_append=req.get("prompt_append", ""),
        role=req.get("role", ""),
        is_active=1 if req.get("is_active", True) else 0,
    )
    logger.info("用户创建skill: uid=%d sid=%d name=%s", user["id"], sid, name)
    return {"ok": True, "id": sid}


@app.put("/api/my-skills/{skill_id}")
def update_my_skill(skill_id: int, req: dict, user: dict = Depends(_require_user)):
    if not db.get_user_skill(skill_id, user["id"]):
        raise HTTPException(status_code=404, detail="Skill不存在")
    fields = {}
    for k in ("name", "description", "prompt_append", "role", "is_active"):
        if k in req:
            v = req[k]
            if k == "is_active":
                v = 1 if v else 0
            fields[k] = v
    db.update_user_skill(skill_id, user["id"], **fields)
    return {"ok": True}


@app.delete("/api/my-skills/{skill_id}")
def delete_my_skill(skill_id: int, user: dict = Depends(_require_user)):
    db.delete_user_skill(skill_id, user["id"])
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(user: dict = Depends(_require_admin)):
    return {"users": db.list_users_admin()}


@app.post("/api/admin/users/{user_id}/recharge")
def admin_recharge(user_id: int, req: RechargeAdminReq, user: dict = Depends(_require_admin)):
    """人工收款后给用户充值（amount > 0）。"""
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    new_balance = db.change_balance(user_id, req.amount, f"管理员手动充值 {req.amount:.1f}")
    logger.info("管理员充值: by=%s to_uid=%d to=%s amount=%.2f new_balance=%.2f",
                user["username"], user_id, target["username"], req.amount, new_balance)
    # 充值返佣（仅当配置为 recharge 基数时触发）
    if req.amount > 0 and target.get("referrer_id"):
        ref = db.get_user(target["referrer_id"])
        if ref and ref.get("is_agent") and COMMISSION_BASE == "recharge":
            commission = round_money(req.amount * COMMISSION_RATE)
            db.pay_commission(ref["id"], user_id, commission, "recharge",
                              note=f"下级 {target['username']} 充值返佣")
            logger.info("充值返佣: agent_uid=%d buyer_uid=%d amount=%.2f", ref["id"], user_id, commission)
    return {"ok": True, "balance": new_balance}


class AgentSetReq(BaseModel):
    discount: float = 0.8


@app.post("/api/admin/users/{user_id}/agent")
def admin_set_agent(user_id: int, req: AgentSetReq, user: dict = Depends(_require_admin)):
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.set_agent(user_id, req.discount)
    logger.info("设为代理: by=%s target_uid=%d target=%s discount=%.2f",
                user["username"], user_id, target["username"], req.discount)
    return {"ok": True, "username": target["username"], "discount": req.discount}


@app.post("/api/admin/users/{user_id}/unagent")
def admin_unset_agent(user_id: int, user: dict = Depends(_require_admin)):
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.unset_agent(user_id)
    logger.info("取消代理: by=%s target_uid=%d target=%s", user["username"], user_id, target["username"])
    return {"ok": True, "username": target["username"]}


@app.get("/api/admin/users/{user_id}/downline")
def admin_downline(user_id: int, user: dict = Depends(_require_admin)):
    return {"downline": db.list_downline(user_id, limit=200)}


@app.get("/api/admin/users/{user_id}/commissions")
def admin_commissions(user_id: int, user: dict = Depends(_require_admin)):
    return {"commissions": db.list_commissions(user_id, limit=200),
            "total": db.sum_commission(user_id)}


@app.get("/api/admin/recharges")
def admin_recharges(user: dict = Depends(_require_admin)):
    rows = db.list_recharges(None, limit=200)
    names = {u["id"]: u["username"] for u in db.list_users_admin()}
    return {"recharges": [{**r, "username": names.get(r["user_id"], f"#{r['user_id']}")} for r in rows]}


@app.get("/api/admin/projects")
def admin_projects(user: dict = Depends(_require_admin)):
    """所有项目总览：标题 / 归属用户 / 题材 / 进度 / 状态。"""
    owners = {p["project_id"]: p["username"] for p in db.all_projects_with_owner()}
    items = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            try:
                store = ProjectStore.open(d)
                cfg = store.load_config()
                state = store.load_state()
                outline = store.load_outline()
                chapters = store.list_chapters()
                total = len(outline.chapters) if outline else cfg.num_chapters
                items.append({
                    "id": d.name,
                    "title": cfg.effective_title(),
                    "genre": cfg.genre,
                    "owner": owners.get(d.name, "（无归属）"),
                    "created": d.stat().st_mtime,
                    "phase": state.phase,
                    "chapters_done": len(chapters),
                    "chapters_total": total,
                    "running": d.name in _RUNS,
                })
            except Exception:
                continue
    return {"projects": items}


@app.get("/api/admin/settings")
def admin_settings(user: dict = Depends(_require_admin)):
    """系统参数（只读展示）：定价档位 + 返佣规则 + 健康状态。"""
    import time as _time
    return {
        "tiers": tiers(),
        "register_bonus": FREE_REGISTER_BONUS,
        "agent_discount": AGENT_DISCOUNT,
        "commission_rate": COMMISSION_RATE,
        "commission_base": COMMISSION_BASE,
        "referral_bonus": REFERRAL_BONUS,
        "server_time": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "2.1.0",
        "mail_configured": mailer.is_configured(),
    }


@app.get("/api/admin/logs")
def admin_logs(lines: int = 200, user: dict = Depends(_require_admin)):
    """读取系统日志尾部（供站长在后台排查问题）。"""
    from .logger import LOG_FILE, read_logs
    data = read_logs(lines)
    return {"logs": data, "log_file": str(LOG_FILE)}


# ---------- 退款管理（站长侧） ----------
@app.get("/api/admin/refunds")
def admin_refunds(status: str = "", user: dict = Depends(_require_admin)):
    """退款申请列表。status 可传 pending/approved/rejected，默认全部。"""
    rows = db.list_refund_requests(None, status or None)
    names = {u["id"]: u["username"] for u in db.list_users_admin()}
    return {"refunds": [{**r, "username": names.get(r["user_id"], f"#{r['user_id']}")} for r in rows],
            "mail_configured": mailer.is_configured()}


@app.post("/api/admin/refunds/{request_id}/handle")
def admin_handle_refund(request_id: int, req: RefundHandleReq,
                        user: dict = Depends(_require_admin)):
    """处理退款：approve=确认已线下转账并扣余额；reject=拒绝。"""
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 只能是 approve 或 reject")
    ok, why = db.handle_refund(request_id, req.action, req.admin_note)
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    logger.info("退款处理: request_id=%d action=%s by_admin=%s note=%s",
                request_id, req.action, user["username"], req.admin_note)
    return {"ok": True, "message": why}


@app.get("/api/me")
def me(user: dict = Depends(_require_user)):
    return {"user": _public_user(user)}


@app.get("/api/me/recharges")
def my_recharges(user: dict = Depends(_require_user)):
    return {"recharges": db.list_recharges(user["id"])}


# ---------- 代理中心 ----------
@app.get("/api/me/referral")
def my_referral(user: dict = Depends(_require_user)):
    """我的推广信息：邀请码、推广链接、下级数、累计返佣、返佣明细、上级。"""
    invite_code = db.get_invite_code(user["id"])
    downline = db.list_downline(user["id"])
    commissions = db.list_commissions(user["id"])
    ref = db.get_referrer(user["id"])
    return {
        "invite_code": invite_code,
        "invite_link": f"/?invite={invite_code}",
        "is_agent": bool(user.get("is_agent")),
        "agent_discount": float(user.get("agent_discount", 1.0)),
        "commission_rate": COMMISSION_RATE,
        "referral_bonus": REFERRAL_BONUS,
        "referrer": ref,
        "downline_count": len(downline),
        "downline": [{"id": r["id"], "username": r["username"], "created_at": r["created_at"]}
                     for r in downline[:50]],
        "total_commission": db.sum_commission(user["id"]),
        "commissions": [
            {"id": c["id"], "amount": c["amount"], "kind": c["kind"],
             "project_id": c["project_id"], "note": c["note"], "created_at": c["created_at"]}
            for c in commissions[:50]
        ],
    }


@app.post("/api/me/bind-referrer")
def bind_referrer(req: BindReferrerReq, user: dict = Depends(_require_user)):
    """注册时没填邀请码的用户，可在首次使用前补绑上级。"""
    if user.get("referrer_id"):
        raise HTTPException(status_code=400, detail="你已经绑定了上级，无法更换")
    ok, why = db.bind_referrer(user["id"], req.invite_code)
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    return {"ok": True, "message": "绑定成功"}


# ---------- 退款申请（用户侧） ----------
@app.get("/api/me/refundable")
def me_refundable(user: dict = Depends(_require_user)):
    """可退金额（仅含充值部分，赠金/返佣不可退）。"""
    return {"refundable": db.refundable_amount(user["id"])}


@app.post("/api/me/refund-request")
def me_refund_request(req: RefundApplyReq, user: dict = Depends(_require_user)):
    """提交退款申请 → 入库 + 邮件通知站长。"""
    ok, why = db.create_refund_request(user["id"], req.amount, req.reason)
    if not ok:
        logger.warning("退款申请被拒: uid=%d amount=%.2f reason=%s", user["id"], req.amount, why)
        raise HTTPException(status_code=400, detail=why)
    # 邮件通知站长（失败不影响申请入库）
    mail_ok = mailer.notify_refund_request(
        user["username"], user["id"], req.amount, req.reason,
        admin_url="http://127.0.0.1:8000/admin.html",
    )
    logger.info("退款申请提交: uid=%d username=%s amount=%.2f email_sent=%s",
                user["id"], user["username"], req.amount, mail_ok)
    return {"ok": True, "message": "退款申请已提交，站长处理后到账",
            "email_sent": mail_ok}


@app.get("/api/me/refund-requests")
def me_refund_requests(user: dict = Depends(_require_user)):
    return {"requests": db.list_refund_requests(user["id"])}


# ---------- 项目 ----------
@app.get("/api/projects")
def projects(user: dict = Depends(_require_user), sort: str = "created", show_archived: bool = False):
    return {"projects": _list_projects(user, sort=sort, show_archived=show_archived)}


@app.post("/api/projects", status_code=201)
def create_project(req: ProjectCreateReq, user: dict = Depends(_require_user)):
    cfg = NovelConfig(
        title=req.title,
        genre=req.genre,
        total_words=req.total_words,
        chapter_words=req.chapter_words,
        style=req.style,
        premise=req.premise,
        background=req.background,
        hero=req.hero,
        characters=list(req.characters),
        plot_direction=req.plot_direction,
        forbidden=list(req.forbidden),
        editor_review=req.editor_review,
        de_ai=req.de_ai,
        anti_similarity=req.anti_similarity,
        character_depth=req.character_depth,
        confirm=req.confirm,
        memory_window=req.memory_window,
        mode=req.mode,
        expand_input=req.expand_input,
        content_type=req.content_type,
        expand_targets=list(req.expand_targets),
        llm=_resolve_llm(req.llm.provider, req.llm.model, req.llm.base_url,
                         req.llm.temperature, req.llm.top_p, req.llm.max_tokens),
        search=SearchConfig(
            provider=req.search.provider,
            api_key=_server_api_key(req.search.provider)
            if req.search.provider else "",
            count=5,
        ),
        pacing_density=req.pacing_density,
        plot_speed=req.plot_speed,
        custom_prompt=req.custom_prompt,
        high_quality_mode=req.high_quality_mode,
    )

    # 注入写作风格 skill
    if req.skill_id:
        _sk = db.get_skill(req.skill_id)
        if _sk and _sk["is_active"]:
            cfg.skill_prompt_append = _sk["prompt_append"] or ""

    # 配置校验（非硬约束问题仅警告）
    issues = validate_config(cfg)
    real_issues = [i for i in issues if "硬约束" not in i]

    # 扩写模式校验：纯扩写需大纲/简要内容；续写/扩写已有章节需已有正文
    user_content = (req.user_content or "").strip()
    if req.mode == "expand":
        if req.content_type in ("chapters", "chapters_expand"):
            if not user_content:
                raise HTTPException(status_code=400, detail="续写/扩写模式必须提供已有内容（粘贴或上传）")
        elif not req.expand_input.strip():
            raise HTTPException(status_code=400, detail="AI 扩写模式必须填写『大纲或简要内容』")

    # 续写/扩写已有章节：先切分用户内容，确定 AI 需生成的字数（已有内容不收费）
    user_chapters = []
    user_words = 0
    if req.mode == "expand" and req.content_type in ("chapters", "chapters_expand"):
        user_chapters = split_chapters(user_content)
        if not user_chapters:
            raise HTTPException(status_code=400, detail="无法从提供的内容中识别出章节，请检查文本格式")
        user_words = sum(len(c["content"]) for c in user_chapters)
        if req.total_words <= user_words:
            raise HTTPException(
                status_code=400,
                detail=f"目标字数（{req.total_words}）需大于已有内容字数（{user_words}），"
                       f"请调高目标字数或减少已有内容",
            )

    # 按 AI 实际生成字数预估费用：创建时【不扣费】，仅做余额校验（防生成后付不起），
    # 生成完成后按实际 AI 字数结算扣费（见 _settle_price）
    ai_words = max(1, cfg.total_words - user_words)
    base_price = price_for(ai_words, high_quality=bool(cfg.__dict__.get('high_quality_mode', False)))
    discount = float(user.get("agent_discount", 1.0)) if user.get("is_agent") else 1.0
    price = agent_price(base_price, discount)  # 预估费用（展示用）
    if user["balance"] < price:
        logger.warning("创建项目失败(余额不足): uid=%d title=%s price=%.2f balance=%.2f",
                       user["id"], cfg.effective_title(), price, user["balance"])
        raise HTTPException(
            status_code=400,
            detail=f"余额不足：预估费用 {price:.1f} 元（按 AI 生成 {ai_words} 字"
                   f"{'，代理价' + str(int(discount * 100)) + ' 折' if discount < 1 else ''}，"
                   f"生成完成后扣费），当前余额 {user['balance']:.1f} 元。请先充值。",
        )

    store = ProjectStore.create(PROJECTS_DIR, cfg)
    # 写入用户手写章节（source=user，AI 流程不覆盖）
    if user_chapters:
        store.save_user_content(user_content)
        for i, c in enumerate(user_chapters, start=1):
            store.save_chapter(Chapter(no=i, title=c["title"] or f"第{i}章",
                                       content=c["content"], source="user"))
        store.set_user_words(user_words)
    # 创建仅登记归属：price=0（未扣费），折扣快照留待结算
    db.claim_project(store.dir.name, user["id"], 0.0, discount)
    logger.info("创建项目(未扣费): uid=%d project=%s title=%s mode=%s content_type=%s "
                "预估=%.2f(原价%.2f) total_words=%d user_words=%d ai_words=%d",
                user["id"], store.dir.name, cfg.effective_title(), cfg.mode, cfg.content_type,
                price, base_price, cfg.total_words, user_words, ai_words)
    return {
        "id": store.dir.name,
        "title": cfg.effective_title(),
        "price": price,          # 预估费用（生成完成后按实际结算）
        "base_price": base_price,
        "balance": db.get_user(user["id"])["balance"],
        "config": _cfg_to_dict(cfg),
        "warnings": real_issues,
    }


@app.get("/api/projects/{project_id}")
def project_detail(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    state = store.load_state()
    outline = store.load_outline()
    chapters = store.list_chapters()
    total = len(outline.chapters) if outline else cfg.num_chapters
    handle = _RUNS.get(project_id)
    return {
        "id": store.dir.name,
        "config": _cfg_to_dict(cfg),
        "state": state.to_dict(),
        "outline_title": outline.title if outline else "",
        "has_outline": bool(outline and outline.chapters),
        "outline_intro": outline.intro if outline else "",
        "chapters_done": len(chapters),
        "chapters_total": total,
        "running": bool(handle and handle.thread and handle.thread.is_alive()),
        "error": handle.error if handle else None,
        "generated_words": sum(len(c.content) for c in chapters),
        "has_txt": (OUTPUT_DIR / f"{_safe_name(cfg.effective_title())}.txt").exists(),
        "has_cover": _cover_files(store)[0].exists(),
        "cover_used": _cover_used_count(store),
        "cover_max": 5,
        "has_epub": (OUTPUT_DIR / f"{_safe_name(cfg.effective_title())}.epub").exists(),
        "has_docx": (OUTPUT_DIR / f"{_safe_name(cfg.effective_title())}.docx").exists(),
    }


@app.get("/api/projects/{project_id}/outline")
def project_outline(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        return {"chapters": []}
    return {
        "title": outline.title,
        "intro": outline.intro,
        "volumes": [v.to_dict() for v in outline.volumes],
        "chapters": [c.to_dict() for c in outline.chapters],
    }


# ---------- 大纲编辑 / 章节重写 / 差异（需求分析: 大纲编辑与章节重新生成 v1.0） ----------
# 决策：① 只做章节级大纲编辑 ② 双入口（仅本章重写 + 从本章重写后续） ③ 重生成免费+每项目每日 10 次
#       ④ 大纲删章后已生成正文保留（标"已废弃"） ⑤ 生成中禁止编辑（409）
REGEN_DAILY_LIMIT = 10


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
    volume_no: int = 0


class VolumeReq(BaseModel):
    no: int = 0
    name: str = ""
    intro: str = ""
    main_conflict: str = ""
    character_growth: str = ""
    climax: str = ""
    start_ch: int = 0
    end_ch: int = 0

class OutlineSaveReq(BaseModel):
    title: str = ""
    intro: str = ""
    volumes: list[VolumeReq] = []
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


@app.put("/api/projects/{project_id}/outline")
def save_outline(project_id: str, req: OutlineSaveReq, user: dict = Depends(_require_user)):
    """保存大纲（章节级编辑：改标题/目标、增删章）。
    不自动触发重生成；返回与已生成章节的差异，由用户显式选择重写动作。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中，请先停止再编辑大纲")
    if not req.chapters:
        raise HTTPException(status_code=400, detail="大纲至少需要 1 个章节")

    # 规范化：重排 no、空 title/goal 补默认（与生成端 OutlineGenerator._normalize 一致）
    chapters = []
    for i, c in enumerate(req.chapters, start=1):
        title = (c.title or "").strip() or f"第{i}章"
        goal = (c.goal or "").strip() or "推进主线剧情，制造冲突并留下悬念。"
        chapters.append(ChapterPlan(
            no=i, title=title, goal=goal,
            core_event=(c.core_event or "").strip(),
            conflict=(c.conflict or "").strip(),
            character_moves=(c.character_moves or "").strip(),
            foreshadowing=(c.foreshadowing or "").strip(),
            emotion=(c.emotion or "").strip(),
            transition=(c.transition or "").strip(),
            tag=(c.tag or "").strip(),
            volume_no=int(getattr(c, "volume_no", 0) or 0),
        ))
    unique_chapter_titles(chapters)  # 章节名全书唯一（重名自动加后缀）

    old = store.load_outline()
    volumes = []
    for i, v in enumerate(req.volumes, start=1):
        volumes.append(Volume(
            no=i, name=(v.name or "").strip(), intro=(v.intro or "").strip(),
            main_conflict=(v.main_conflict or "").strip(),
            character_growth=(v.character_growth or "").strip(),
            climax=(v.climax or "").strip(),
            start_ch=v.start_ch or 0, end_ch=v.end_ch or 0,
        ))
    new_outline = Outline(
        title=(req.title or "").strip() or (old.title if old else ""),
        intro=(req.intro or "").strip() or (old.intro if old else ""),
        volumes=volumes,
        chapters=chapters,
    )
    store.save_outline(new_outline)

    changed, removed = _outline_diff(new_outline, store)
    logger.info("大纲编辑: uid=%d project=%s chapters=%d→%d changed=%s removed=%s",
                user["id"], project_id, len(old.chapters) if old else 0, len(chapters), changed, removed)
    return {"saved": True, "total_chapters": len(chapters),
            "changed_chapters": changed, "removed_chapters": removed}


@app.get("/api/projects/{project_id}/outline/diff")
def outline_diff(project_id: str, user: dict = Depends(_require_user)):
    """大纲与已生成章节的差异（前端章节列表标记"与新大纲不一致"）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        return {"changed": [], "removed": []}
    changed, removed = _outline_diff(outline, store)
    return {"changed": changed, "removed": removed}


@app.post("/api/projects/{project_id}/chapters/{no}/rewrite")
def rewrite_chapter(project_id: str, no: int, user: dict = Depends(_require_user)):
    """单章重写：按当前大纲该章 goal/title 重新生成正文与摘要，后续章节不动。
    重写后该章摘要自动覆盖（append_summary 幂等）；前文记忆上下文不含本章旧摘要（memory < no）。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline or no < 1 or no > len(outline.chapters):
        raise HTTPException(status_code=400,
                            detail=f"章节号非法（大纲共 {len(outline.chapters) if outline else 0} 章）")
    if store.load_chapter(no) is None:
        raise HTTPException(status_code=400, detail=f"章节 {no} 尚未生成，无法重写")
    if store.load_chapter(no).source == "user":
        raise HTTPException(status_code=400, detail="该章为用户手写内容，AI 不自动重写；如需修改请使用「编辑本章」手动调整")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
    _check_regen_limit(store, project_id)

    plan = outline.chapters[no - 1]
    try:
        pipeline = _open_pipeline(store)
        research = store.load_research()
        # 大改风控：重写时自动加载 world + memory + verify_issues 作为硬约束
        chapter = pipeline.chapter_writer.write(
            no, plan.title, plan.goal, research, is_final=(no == len(outline.chapters)))
    except Exception as e:
        logger.exception("单章重写失败: project=%s no=%d", project_id, no)
        raise HTTPException(status_code=500, detail=f"重写失败：{e}")

    logger.info("章节重写: uid=%d project=%s no=%d words=%d",
                user["id"], project_id, no, len(chapter.content))
    return {"no": chapter.no, "title": chapter.title, "content": chapter.content,
            "summary": chapter.summary, "words": len(chapter.content)}


@app.post("/api/projects/{project_id}/expand-selection")
def expand_selection(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """选中内容扩写/续写：基于用户选中的文本，AI扩写或续写一段。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    selected = (req.get("text") or "").strip()
    mode = req.get("mode") or "expand"  # expand=扩写 / continue=续写
    if not selected:
        raise HTTPException(status_code=400, detail="未提供选中文本")
    cfg = store.load_config()
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        if mode == "continue":
            sys = "你是一位网络小说写手。基于以下文字，自然续写一段，保持文风和情节连贯，100-300字。不要重复原文。"
        else:
            sys = "你是一位网络小说写手。将以下文字扩写得更细腻生动，增加感官细节和情绪，保持原意和文风，扩写后200-500字。"
        user_msg = f"【原文】\n{selected}\n\n【请输出】"
        result = llm.chat(sys, user_msg, temperature=0.7, max_tokens=600)
    except Exception as e:
        logger.exception("选中扩写失败: project=%s", project_id)
        raise HTTPException(status_code=500, detail=f"扩写失败：{e}")
    return {"result": result, "mode": mode}


@app.get("/api/projects/{project_id}/stats/rhythm")
def rhythm_stats(project_id: str, user: dict = Depends(_require_user)):
    """节奏分析：每章字数分布+总进度。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapters = store.list_chapters()
    data = [{"no": c.no, "title": c.title, "words": len(c.content)} for c in chapters]
    total = sum(d["words"] for d in data)
    return {"chapters": data, "total_words": total, "chapter_count": len(data)}


@app.post("/api/projects/{project_id}/verify/consistency")
def verify_consistency(project_id: str, user: dict = Depends(_require_user)):
    """设定一致性校验：用LLM检查最近章节是否与世界设定矛盾。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    world = store.load_world()
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        # 读最近3章内容摘要
        summaries = store.load_summaries()[-3:]
        recent = "\n".join(f"第{s['no']}章: {s['summary']}" for s in summaries)
        world_txt = ""
        if world:
            if world.world_view:
                world_txt += f"世界观: {world.world_view}\n"
            if world.characters:
                world_txt += "人物: " + "; ".join(f"{c.name}({c.role})" for c in world.characters) + "\n"
        sys = "你是小说一致性审查员。检查以下最近章节摘要是否与世界设定矛盾，列出矛盾点（如有），JSON数组返回[{problem, suggestion}]，无矛盾返回空数组。"
        result = llm.chat(sys, f"【设定】\n{world_txt}\n【最近章节】\n{recent}", temperature=0.3, max_tokens=800)
        import json
        try:
            issues = json.loads(result)
        except Exception:
            issues = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"校验失败：{e}")
    return {"issues": issues}


@app.post("/api/projects/{project_id}/verify/logic")
def verify_logic(project_id: str, user: dict = Depends(_require_user)):
    """剧情逻辑审查：检查因果链是否断裂。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        summaries = store.load_summaries()[-5:]
        recent = "\n".join(f"第{s['no']}章: {s['summary']}" for s in summaries)
        sys = "你是小说逻辑审查员。检查以下章节摘要的剧情因果是否合理，列出逻辑漏洞（如有），JSON数组返回[{problem, suggestion}]，无问题返回空数组。"
        result = llm.chat(sys, f"【章节摘要】\n{recent}", temperature=0.3, max_tokens=800)
        import json
        try:
            issues = json.loads(result)
        except Exception:
            issues = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审查失败：{e}")
    return {"issues": issues}


@app.post("/api/projects/{project_id}/verify/compliance")
def verify_compliance(project_id: str, user: dict = Depends(_require_user)):
    """网文合规检测：检查敏感/违规内容。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        summaries = store.load_summaries()[-3:]
        recent = "\n".join(f"第{s['no']}章: {s['summary']}" for s in summaries)
        sys = "你是网文合规审核员。检查以下内容是否涉及政治敏感、色情低俗、暴力血腥、违法犯罪描写，列出风险点JSON数组[{issue, level}]，无风险返回空数组。"
        result = llm.chat(sys, f"【章节摘要】\n{recent}", temperature=0.2, max_tokens=600)
        import json
        try:
            issues = json.loads(result)
        except Exception:
            issues = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检测失败：{e}")
    return {"issues": issues}


@app.post("/api/projects/{project_id}/generate/alternatives")
def generate_alternatives(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """多方案生成：给定章节目标，生成2-3个不同剧情走向方案。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    goal = (req.get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="缺少章节目标")
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        sys = "你是小说剧情策划。针对给定章节目标，提供3个不同的剧情走向方案（不同选择/冲突/转折），每个方案2-3句话。JSON数组返回[{title, description}]。"
        result = llm.chat(sys, f"【章节目标】{goal}", temperature=0.8, max_tokens=800)
        import json
        try:
            alts = json.loads(result)
        except Exception:
            alts = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    return {"alternatives": alts}


@app.get("/api/projects/{project_id}/chapters")
def project_chapters(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapters = store.list_chapters()
    return {
        "chapters": [
            {"no": c.no, "title": c.title, "words": len(c.content), "summary": c.summary}
            for c in chapters
        ]
    }


@app.get("/api/projects/{project_id}/chapters/{no}")
def project_chapter(project_id: str, no: int, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    ch = store.load_chapter(no)
    if not ch:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")
    return {"no": ch.no, "title": ch.title, "content": ch.content, "summary": ch.summary}


class ChapterEditReq(BaseModel):
    title: str = ""
    content: str = ""
    summary: str = ""   # 可留空：留空时自动用 LLM 重新生成摘要
    regen_summary: bool = True   # 是否在正文变更后重新生成摘要


@app.put("/api/projects/{project_id}/chapters/{no}")
def edit_chapter(project_id: str, no: int, req: ChapterEditReq,
                 user: dict = Depends(_require_user)):
    """编辑单章：改标题/正文，可选重新生成摘要，供「人工修改后继续生成」使用。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    ch = store.load_chapter(no)
    if not ch:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")

    if req.title:
        ch.title = req.title
    if req.content:
        ch.content = req.content

    # 摘要处理：用户显式给 → 用之；否则若正文变了且允许 → 自动重新生成
    if req.summary:
        ch.summary = req.summary
    elif req.regen_summary and req.content and req.content != _load_prev_content(store, no):
        new_summary = _regenerate_summary(store, ch)
        if new_summary:
            ch.summary = new_summary

    store.save_chapter(ch)
    if ch.summary:
        store.append_summary(no, ch.summary)
    logger.info("章节编辑: uid=%d project=%s no=%d title=%s words=%d",
                user["id"], project_id, no, ch.title, len(ch.content))
    return {"no": ch.no, "title": ch.title, "summary": ch.summary, "words": len(ch.content)}


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


@app.post("/api/projects/{project_id}/regenerate-from/{no}")
def regenerate_from(project_id: str, no: int, user: dict = Depends(_require_user)):
    """从第 no 章重新生成：删除 no 及之后所有章节与记忆，然后从 no 重新开跑。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline or no < 1 or no > len(outline.chapters):
        raise HTTPException(status_code=400, detail=f"章节号非法（大纲共 {len(outline.chapters) if outline else 0} 章）")
    user_max = max((c.no for c in store.list_chapters() if c.source == "user"), default=0)
    if user_max and no <= user_max:
        raise HTTPException(status_code=400,
                            detail=f"第 {no} 章为用户手写内容（到第 {user_max} 章），重新生成会覆盖手写内容，已禁止；"
                                   f"请从第 {user_max + 1} 章开始")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    _check_regen_limit(store, project_id)

    # 删除 no 及之后章节 + 对应记忆
    for ch in store.list_chapters():
        if ch.no >= no:
            p = store.chapter_path(ch.no)
            if p.exists():
                p.unlink()
    summaries = [s for s in store.load_summaries() if int(s.get("no", 0)) < no]
    store._write_json(store.memory_path(), summaries)

    logger.info("章节重生成: uid=%d project=%s from_chapter=%d", user["id"], project_id, no)
    t = threading.Thread(target=_run_generation, args=(store, handle, no), daemon=True)
    handle.thread = t
    t.start()
    return {"started": True, "project_id": project_id, "from_chapter": no}


@app.post("/api/projects/{project_id}/cover")
def gen_cover(project_id: str, user: dict = Depends(_require_user)):
    """生成/重新生成程序化封面（PNG）。每本书最多生成 5 次。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    outline = store.load_outline()
    subtitle = (outline.intro[:18] + "…") if outline and len(outline.intro) > 18 else (outline.intro if outline else "")
    out_path, proj_path = _cover_files(store)
    # 封面次数限制：每本书最多 5 次
    COVER_MAX = 5
    count_file = proj_path.parent / "_cover_count.txt"
    used = int(count_file.read_text(encoding="utf-8").strip()) if count_file.exists() else 0
    if used >= COVER_MAX:
        raise HTTPException(400, detail=f"封面最多生成 {COVER_MAX} 次，已用完。当前封面已保留，如需调整请联系站长。")
    # 拼封面用的"书籍内容摘要"：文风 + 简介 + 世界观，传给 AI 让画面贴合小说
    cover_parts = []
    if getattr(cfg, "style", "").strip():
        cover_parts.append("文风：" + cfg.style.strip()[:80])
    if getattr(cfg, "premise", "").strip():
        cover_parts.append("故事核心：" + cfg.premise.strip()[:120])
    if getattr(cfg, "background", "").strip():
        cover_parts.append("世界观：" + cfg.background.strip()[:120])
    cover_extra = "；".join(cover_parts)
    # AI 封面优先（硅基流动 ERNIE-Image-Turbo），失败自动回退程序化封面
    generate_best_cover(
        title=cfg.effective_title(),
        genre=cfg.genre,
        subtitle=subtitle,
        out_path=proj_path,
        extra=cover_extra,
    )
    # 复制到 output/ 主输出
    proj_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(proj_path.read_bytes())
    # 计数 +1
    count_file.write_text(str(used + 1), encoding="utf-8")
    return {
        "ok": True,
        "cover_url": f"/api/projects/{project_id}/cover",
        "cover_used": used + 1,
        "cover_max": COVER_MAX,
        "cover_remaining": COVER_MAX - used - 1,
    }


@app.get("/api/projects/{project_id}/cover")
def get_cover(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    out_path, proj_path = _cover_files(store)
    p = out_path if out_path.exists() else proj_path
    if not p.exists():
        raise HTTPException(status_code=404, detail="封面尚未生成")
    return FileResponse(str(p), media_type="image/png")


@app.get("/api/projects/{project_id}/world")
def project_world(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    if not world:
        return {"world_view": "", "characters": [], "foreshadowing": []}
    return {
        "world_view": world.world_view,
        "characters": [c.to_dict() for c in world.characters],
        "foreshadowing": list(world.foreshadowing),
        "locked_settings": list(world.locked_settings),
    }


@app.get("/api/projects/{project_id}/research")
def project_research(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    r = store.load_research()
    if not r:
        return {}
    return r.to_dict()


# ---------- 生成控制 ----------
@app.post("/api/projects/{project_id}/generate-outline")
def generate_outline_only(project_id: str, user: dict = Depends(_require_user)):
    """只跑 research → outline → editor_review → world，不写正文。
    生成完后用户可以编辑大纲，再调 /start 开始写正文。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙，已有多个任务在生成，请稍后再试")
    try:
        t = threading.Thread(target=_run_outline_only, args=(store, handle), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True, "project_id": project_id, "stage": "outline"}


@app.post("/api/projects/{project_id}/outline/regenerate")
def regen_outline(project_id: str, user: dict = Depends(_require_user)):
    """重新生成大纲：删除旧大纲，重新跑 outline 阶段。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙")
    try:
        from novel_gen.store.schemas import Outline
        store.save_outline(Outline(title="", intro="", chapters=[]))
        t = threading.Thread(target=_run_outline_only, args=(store, handle), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True}


@app.post("/api/projects/{project_id}/world/regenerate")
def regen_world(project_id: str, user: dict = Depends(_require_user)):
    """重新生成世界观。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙")
    try:
        def _do():
            try:
                pipeline = _open_pipeline(store)
                from novel_gen.store.schemas import World
                store.save_world(World(world_view="", characters=[], foreshadowing=[]))
                pipeline.run_world()
            except Exception as e:
                handle.error = str(e)
            finally:
                _GEN_SEMAPHORE.release()
                with _LOCK:
                    _RUNS.pop(project_id, None)
        t = threading.Thread(target=_do, daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True}


@app.post("/api/projects/{project_id}/start")
def start_generation(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙，已有多个任务在生成，请稍后再试")
    try:
        t = threading.Thread(target=_run_generation, args=(store, handle), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True, "project_id": project_id}


@app.post("/api/projects/{project_id}/stop")
def stop_generation(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    handle = _RUNS.get(project_id)
    if not handle:
        return {"stopped": False, "message": "项目未在运行"}
    handle.stop_event.set()
    return {"stopped": True, "project_id": project_id, "message": "已请求停止，当前章节完成后生效"}


@app.post("/api/projects/{project_id}/refund")
def refund_project(project_id: str, user: dict = Depends(_require_user)):
    """未开始生成（零章节产出）的项目：全额退费 + 删除作品。

    退费条件：项目归属当前用户、尚未生成任何章节正文、创建时确有扣费。
    退费时同步冲正上级消费返佣（防刷量），然后删除项目目录与归属记录。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    title = store.load_config().effective_title()

    if store.list_chapters():
        raise HTTPException(status_code=400, detail="该项目已开始生成正文，无法退费删除")

    price = db.project_price(project_id)  # 新项目创建不扣费 price=0：未生成直接删除，无需退费
    # 若正在生成（调研/大纲/设定阶段，尚未产出章节）：先请求停止并等待线程结束
    handle = _RUNS.get(project_id)
    if handle:
        handle.stop_event.set()
        if handle.thread and handle.thread.is_alive():
            handle.thread.join(timeout=30)

    # 退费（历史项目创建时已扣费才退；新项目 price=0 直接删除）
    if price > 0:
        db.change_balance(user["id"], price, f"项目退费：《{title}》（未开始生成）")
        # 返佣冲正：该项目触发的消费返佣扣回上级
        comm = db.find_commission_by_project(project_id)
        if comm:
            db.change_balance(comm["agent_id"], -comm["amount"],
                              f"返佣冲正：《{title}》项目退费")
            logger.info("返佣冲正: agent_uid=%d amount=%.2f project=%s",
                        comm["agent_id"], comm["amount"], project_id)

    # 删除作品：归属记录 + 项目目录 + 已导出的成品文件
    db.delete_project_owner(project_id)
    try:
        import shutil
        shutil.rmtree(store.dir, ignore_errors=True)
        from novel_gen.store.project import _safe_name
        for ext in ("txt", "epub", "docx", "png"):
            f = OUTPUT_DIR / f"{_safe_name(title)}.{ext}"
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception:
        logger.exception("删除项目文件失败: project=%s", project_id)

    balance = db.get_user(user["id"])["balance"]
    logger.info("项目退费删除: uid=%d project=%s title=%s price=%.2f new_balance=%.2f",
                user["id"], project_id, title, price, balance)
    return {"ok": True, "refunded": price, "balance": balance, "project_id": project_id}


# ========== P0: 项目管理增强 ==========

@app.post("/api/projects/{project_id}/archive")
def archive_project(project_id: str, user: dict = Depends(_require_user)):
    """归档/取消归档项目。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    state = store.load_state()
    state.archived = not getattr(state, "archived", False)
    store.save_state(state)
    return {"ok": True, "archived": state.archived}


@app.post("/api/projects/{project_id}/clone")
def clone_project(project_id: str, user: dict = Depends(_require_user)):
    """克隆项目（复制配置+大纲，不复制正文）。"""
    import datetime, json, re
    _check_project_access(project_id, user)
    src_store = _find_project(project_id)
    cfg = src_store.load_config()
    # 用时间戳+安全文件名，避免中文/特殊字符导致路径问题
    safe_title = re.sub(r'[^\w\u4e00-\u9fa5-]', '_', cfg.title)[:30]
    new_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + safe_title
    new_dir = PROJECTS_DIR / new_id
    new_dir.mkdir(parents=True, exist_ok=True)
    try:
        src_cfg_file = src_store.dir / "config.json"
        if src_cfg_file.exists():
            cfg_data = json.loads(src_cfg_file.read_text(encoding="utf-8"))
            cfg_data["title"] = cfg.title + "（副本）"
            (new_dir / "config.json").write_text(json.dumps(cfg_data, ensure_ascii=False, indent=2), encoding="utf-8")
        outline = src_store.load_outline()
        if outline:
            (new_dir / "outline.json").write_text(json.dumps(outline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        world = src_store.load_world()
        if world:
            (new_dir / "world.json").write_text(json.dumps(world.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        db.claim_project(new_id, user["id"], price=0.0)
        return {"ok": True, "project_id": new_id}
    except Exception as e:
        logger.exception("克隆项目失败: src=%s err=%s", project_id, e)
        import shutil
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"克隆失败: {str(e)}")


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user: dict = Depends(_require_user)):
    """删除项目（仅站长或项目所有者，且未开始生成时可删）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapters = store.list_chapters()
    if chapters and not user.get("is_admin"):
        raise HTTPException(status_code=400, detail="项目已开始生成，无法删除")
    # 停止正在运行的线程
    handle = _RUNS.get(project_id)
    if handle:
        handle.stop_event.set()
        if handle.thread and handle.thread.is_alive():
            handle.thread.join(timeout=30)
    # 删除项目文件
    db.delete_project_owner(project_id)
    import shutil
    shutil.rmtree(store.dir, ignore_errors=True)
    return {"ok": True}


@app.get("/api/projects/{project_id}/outline/snapshots")
def list_outline_snapshots(project_id: str, user: dict = Depends(_require_user)):
    """列出大纲快照。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    snap_dir = store.dir / "outline_snapshots"
    if not snap_dir.exists():
        return {"snapshots": []}
    snaps = []
    for f in sorted(snap_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        import json
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            snaps.append({
                "name": f.stem,
                "created": f.stat().st_mtime,
                "chapters": len(data.get("chapters", [])),
                "title": data.get("title", ""),
            })
        except Exception:
            continue
    return {"snapshots": snaps}


@app.post("/api/projects/{project_id}/outline/snapshots")
def save_outline_snapshot(project_id: str, name: str = "", user: dict = Depends(_require_user)):
    """保存当前大纲为快照。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        raise HTTPException(status_code=400, detail="还没有大纲可保存")
    import json, datetime
    snap_dir = store.dir / "outline_snapshots"
    snap_dir.mkdir(exist_ok=True)
    if not name:
        name = datetime.datetime.now().strftime("%m%d_%H%M")
    fname = f"{name}.json"
    (snap_dir / fname).write_text(json.dumps(outline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": name}


@app.post("/api/projects/{project_id}/outline/snapshots/{snap_name}/restore")
def restore_outline_snapshot(project_id: str, snap_name: str, user: dict = Depends(_require_user)):
    """从快照恢复大纲。"""
    import json
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    snap_file = store.dir / "outline_snapshots" / f"{snap_name}.json"
    if not snap_file.exists():
        raise HTTPException(status_code=404, detail="快照不存在")
    data = json.loads(snap_file.read_text(encoding="utf-8"))
    from novel_gen.store.schemas import Outline
    outline = Outline.from_dict(data)
    store.save_outline(outline)
    return {"ok": True, "chapters": len(outline.chapters)}




@app.get("/api/projects/{project_id}/foreshadowing")
def list_foreshadowing(project_id: str, user: dict = Depends(_require_user)):
    """伏笔管理：列出世界设定中的伏笔清单。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    return {"foreshadowing": [str(x) for x in (world.foreshadowing if world else [])]}


@app.post("/api/projects/{project_id}/foreshadowing")
def add_foreshadowing(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """添加一条伏笔。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    if world is None:
        raise HTTPException(status_code=400, detail="尚无世界观设定")
    item = (req.get("item") or "").strip()
    if not item:
        raise HTTPException(status_code=400, detail="伏笔内容为空")
    world.foreshadowing.append(item)
    store.save_world(world)
    return {"foreshadowing": [str(x) for x in world.foreshadowing]}


@app.delete("/api/projects/{project_id}/foreshadowing/{index}")
def delete_foreshadowing(project_id: str, index: int, user: dict = Depends(_require_user)):
    """删除一条伏笔。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    if world is None or index < 0 or index >= len(world.foreshadowing):
        raise HTTPException(status_code=404, detail="伏笔不存在")
    world.foreshadowing.pop(index)
    store.save_world(world)
    return {"foreshadowing": [str(x) for x in world.foreshadowing]}


@app.post("/api/projects/{project_id}/chapters/batch")
def batch_generate(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """批量生成章节：指定起始章和结束章，后台逐章生成。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    start = int(req.get("start", 1))
    end = int(req.get("end", start))
    if start < 1 or end < start:
        raise HTTPException(status_code=400, detail="章节范围非法")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="系统繁忙，已有多个任务在生成，请稍后再试")
    # 复用现有后台生成逻辑（从start到end逐章）
    def _batch():
        try:
            pipeline = _open_pipeline(store)
            outline = store.load_outline()
            research = store.load_research()
            for no in range(start, end + 1):
                if no > len(outline.chapters):
                    break
                plan = outline.chapters[no - 1]
                pipeline.chapter_writer.write(no, plan.title, plan.goal, research, is_final=(no == len(outline.chapters)))
        except Exception as e:
            logger.exception("批量生成失败: %s", project_id)
        finally:
            _GEN_SEMAPHORE.release()
    import threading
    t = threading.Thread(target=_batch, daemon=True)
    t.start()
    _RUNS[project_id] = type("R", (), {"thread": t, "stop_flag": False})()
    return {"msg": f"批量生成已启动：第{start}-{end}章"}


@app.get("/api/projects/{project_id}/download")
def download(project_id: str, format: str = "txt", user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    name = _safe_name(cfg.effective_title())
    fmt = (format or "txt").lower()

    if fmt == "txt":
        out = OUTPUT_DIR / f"{name}.txt"
        media = "text/plain; charset=utf-8"
        if not out.exists():
            try:
                pipeline = _open_pipeline(store)
                out = pipeline.export()
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"尚未生成可下载的 txt：{exc}")
    elif fmt == "epub":
        out = OUTPUT_DIR / f"{name}.epub"
        media = "application/epub+zip"
        if not out.exists():
            try:
                out = export_epub(store)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"导出 epub 失败：{exc}")
    elif fmt == "docx":
        out = OUTPUT_DIR / f"{name}.docx"
        media = ("application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document")
        if not out.exists():
            try:
                out = export_docx(store)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"导出 docx 失败：{exc}")
    elif fmt == "md":
        out = OUTPUT_DIR / f"{name}.md"
        media = "text/markdown; charset=utf-8"
        if not out.exists():
            try:
                pipeline = _open_pipeline(store)
                chapters_dir = pipeline.store.dir / "chapters"
                lines = [f"# {cfg.effective_title()}", ""]
                if cfg.premise:
                    lines += [f"> {cfg.premise}", ""]
                for ch_file in sorted(chapters_dir.glob("ch*.txt")):
                    try:
                        text = ch_file.read_text(encoding="utf-8")
                        first_nl = text.find("\n")
                        title = text[:first_nl].strip() if first_nl > 0 else ch_file.stem
                        body = text[first_nl+1:].strip() if first_nl > 0 else ""
                        lines += [f"## {title}", "", body, ""]
                    except Exception:
                        continue
                out.write_text("\n".join(lines), encoding="utf-8")
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"导出 Markdown 失败：{exc}")
    else:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式：{format}")

    if not out.exists():
        raise HTTPException(status_code=404, detail="尚未生成可下载的文件")
    logger.info("下载导出: uid=%d project=%s format=%s file=%s", user["id"], project_id, fmt, out.name)
    return FileResponse(str(out), filename=out.name, media_type=media)


# ---------- 静态前端（同源部署时） ----------
class _NoCacheStaticFiles(StaticFiles):
    """前端静态资源禁用浏览器缓存：html/js/css 返回 no-cache，避免旧 JS 长期缓存导致前端改动不生效。"""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # 304 NotModifiedResponse 无 path 属性（条件请求命中时返回），需跳过；
        # 只有普通文件响应才按扩展名加 no-cache，避免 AttributeError 导致前端资源 500
        path = getattr(resp, "path", None)
        if path and path.endswith((".html", ".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp


if WEB_DIR.exists():
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        file_path = WEB_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(WEB_DIR / "index.html"))

    app.mount("/", _NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="webui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

