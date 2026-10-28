# -*- coding: utf-8 -*-
"""
墨造核心层·项目工具：项目查找、管线创建、LLM 解析、输入防护、大纲/锁定/章节辅助。

从 core.py 拆出（业务域拆分，行为零变化）。本模块只承载「项目级公共工具」一个内聚概念，
不包含鉴权、结算、生成线程主体逻辑。
"""
from __future__ import annotations

import datetime
import json
import os

from fastapi import HTTPException

from novel_gen.config import LLMConfig, PROVIDER_DEFAULTS, NovelConfig
from novel_gen.llm.factory import create_llm
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.store.project import ProjectStore, _safe_name

from . import db
from .config_overrides import apply_global_overrides
from .logger import get_logger
from .core_runtime import OUTPUT_DIR, PROJECTS_DIR, _RUNS

logger = get_logger("server")


# 用户内容粘贴上限（需求确认：单次粘贴不超过 10 万字）
MAX_PASTE_CHARS = 100_000


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


def _apply_global_overrides(cfg: NovelConfig) -> None:
    """把 sys_config 后台全局覆盖合并进 cfg（内存态，不落盘，REQ-后台开关管理）。

    实现已抽到 webapp/config_overrides.py，供生成链路与小墨AI 对话共用；
    此处保留同名入口（既有调用与测试不变）。
    """
    apply_global_overrides(cfg)


def _open_pipeline(store: ProjectStore, trace_id: str = "") -> NovelPipeline:
    cfg = store.load_config()
    # REQ-后台开关管理：后台全局覆盖（以 sys_config 表为准，内存态；先于环境变量兜底）
    _apply_global_overrides(cfg)
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


def _backup_outline(store, prefix: str) -> None:
    """自动备份当前大纲为快照（outline_snapshots/），供覆盖/重生成后一键还原。

    与项目页手动保存快照一致，名称自动加时间戳，避免覆盖同名快照。
    """
    try:
        outline = store.load_outline()
        if not outline:
            return
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


__all__ = [
    "logger", "MAX_PASTE_CHARS",
    "_cfg_to_dict", "_server_api_key", "_resolve_llm", "_find_project", "_list_projects",
    "_cover_files", "_cover_used_count", "_user_llm_cfg", "_project_use_user_llm",
    "_apply_global_overrides", "_open_pipeline", "_guard_user_input", "_backup_outline",
    "_outline_diff", "_load_prev_content", "_regenerate_summary",
    "_load_lock", "_save_lock", "_is_locked", "_assert_not_locked",
]
