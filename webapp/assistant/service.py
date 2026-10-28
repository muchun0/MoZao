# -*- coding: utf-8 -*-
"""小墨AI 会话编排（service 层）：校验 → 注入防护 → 组装 → LLM → 校验 → 卡片 → 落库。

分层（AGENTS.md §11.2）：
  - 本层不写 SQL（会话/消息落库全部走 webapp/db/assistant.py）；
  - 不写 HTTP 细节（鉴权与参数模型在 routers/assistant.py）；
  - 只读项目产物（projects/<id>/），绝不写回。
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException

from novel_gen.llm.factory import create_llm
from novel_gen.pipeline.guard import AgentGuard
from novel_gen.pipeline.trace import TraceRecorder
from novel_gen.safety import (basic_content_check, detect_prompt_injection,
                              sanitize_system_text)

from .. import db
from ..config_overrides import apply_global_overrides
from ..core import _server_api_key, _user_llm_cfg
from ..logger import get_logger
from . import cards, context
from .prompt import load_persona

logger = get_logger("assistant")

__all__ = ["MODES", "send_message"]

#: 子功能白名单（UI 显式选择，后端按 mode 走不同输出要求）
MODES = ("chapter_eval", "revision_advice")
MAX_CONTENT = 4000
DEGRADED_TEXT = "小墨暂时开小差了（模型调用失败），请稍后重试；你的问题已保存。"
SAFE_TEXT = "这次回答触发了内容安全策略，已拦截。请换个问法或聚焦具体章节问题。"


def _resolve_chapter_no(store, chapter_no: int | None) -> int:
    """章节号解析：缺省取项目最后一章；显式传入必须已生成。"""
    chapters = store.list_chapters()
    if not chapters:
        raise HTTPException(status_code=400, detail="项目还没有已生成章节，先完成章节生成再来找小墨")
    nos = {int(c.no) for c in chapters}
    if chapter_no in (None, 0):
        return max(nos)
    if int(chapter_no) not in nos:
        raise HTTPException(status_code=400, detail=f"第 {chapter_no} 章尚未生成")
    return int(chapter_no)


def _build_llm(store, cfg, user_id: int):
    """LLM 客户端：用户 BYOK 优先；require_user_key 开启时不做平台 Key 兜底。"""
    owner_id = db.project_owner(store.dir.name)
    user_llm = _user_llm_cfg(owner_id or user_id)
    if user_llm is not None:
        cfg.llm.provider = user_llm["provider"]
        cfg.llm.api_key = user_llm["api_key"]
        if user_llm.get("base_url"):
            cfg.llm.base_url = user_llm["base_url"]
        if user_llm.get("model"):
            cfg.llm.model = user_llm["model"]
    elif bool(getattr(cfg, "assistant_require_user_key", True)) and cfg.llm.provider != "mock":
        # 平台 Key 兜底已关闭：明确提示用户去配置自己的模型（不静默失败）
        raise HTTPException(
            status_code=400,
            detail="小墨AI 仅支持使用你自己配置的模型：请先在「模型设置」填写 API Key 后再对话",
        )
    elif cfg.llm.provider != "mock" and not cfg.llm.api_key:
        cfg.llm.api_key = _server_api_key(cfg.llm.provider)
    llm = create_llm(cfg.llm)
    try:
        from ..db.traces import MysqlTraceSink
        recorder = TraceRecorder(sink=MysqlTraceSink(), trace_id=uuid.uuid4().hex,
                                 project_id=store.dir.name, user_id=int(user_id),
                                 scene="assistant")
    except Exception:
        logger.warning("trace sink 初始化失败，本次对话不落 agent_trace（降级）")
        recorder = TraceRecorder(trace_id=uuid.uuid4().hex,
                                 project_id=store.dir.name, user_id=int(user_id),
                                 scene="assistant")
    llm.recorder = recorder
    # 成本防护：单次调用场景，scene_limits 不含 assistant（不限次），仅保留超限留痕
    guard = AgentGuard(cfg.agent_guard, recorder=recorder)
    guard.enter_scene("assistant")
    llm.guard = guard
    return llm


def _history(conversation_id: int, turns: int) -> list[dict]:
    try:
        return db.recent_messages(conversation_id, limit=max(2, turns * 2))
    except Exception:
        logger.exception("对话历史读取失败（降级无历史）: conversation=%s", conversation_id)
        return []


def _validate(content: str, mode: str) -> str:
    text = (content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="请先输入内容")
    if len(text) > MAX_CONTENT:
        raise HTTPException(status_code=400, detail=f"单条消息不超过 {MAX_CONTENT} 字")
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"不支持的对话模式：{mode}")
    hits = detect_prompt_injection(text)
    if hits:
        logger.warning("小墨对话输入注入拦截: hits=%s", hits)
        raise HTTPException(status_code=400, detail="检测到疑似指令注入，已被安全策略拦截，请修改后重试")
    return text


def _notice(meta: dict, snap: dict, positive: dict) -> str:
    notes = []
    if meta.get("level", 0) >= 2:
        notes.append("本章较长，已按裁剪后的上下文评估")
    if not snap.get("content"):
        notes.append("本章正文缺失，仅按账本事实评估")
    for err in (positive.get("errors") or []):
        notes.append(err)
    return "；".join(notes)


def _assistant_limits(cfg) -> tuple[int, int, int]:
    """从 cfg 解析小墨AI对话参数（turns/token_limit/max_tokens，带下限保护）。"""
    turns = max(1, int(getattr(cfg, "assistant_history_turns", 6) or 6))
    token_limit = max(2000, int(getattr(cfg, "assistant_context_token_limit", 20000) or 20000))
    max_tokens = max(200, int(getattr(cfg, "assistant_max_tokens", 1500) or 1500))
    return turns, token_limit, max_tokens


def _build_assistant_context(store, cfg, chapter_no: int | None, mode: str,
                            text: str, cid: int, turns: int, token_limit: int):
    """构建小墨AI对话上下文：快照+正向快照+历史+组装 prompt。"""
    snap = context.snapshot(store, chapter_no, target_words=cfg.chapter_words)
    snap["bible"] = sanitize_system_text(snap.get("bible") or "")
    positive = context.positive_snapshot(store, chapter_no, cfg, snap=snap)
    history = _history(cid, turns)  # 落库前取历史，避免把本次提问算进上下文
    built = context.build_prompt(load_persona(), snap, positive, history,
                                text, mode, token_limit=token_limit)
    return snap, positive, built, history


def send_message(store, user: dict, conversation: dict, content: str, mode: str,
                 chapter_no: int | None = None) -> dict:
    """发一条消息：返回 {message_id, conversation_id, content, mode, chapter_no, ...}。"""
    text = _validate(content, mode)
    chapter_no = _resolve_chapter_no(store, chapter_no)
    cfg = store.load_config()
    # 后台开关以 sys_config 为准（与生成链路一致：老项目快照没有新字段也能生效）
    apply_global_overrides(cfg)
    if not bool(getattr(cfg, "assistant_enabled", True)):
        raise HTTPException(status_code=403, detail="小墨AI 当前未开启")
    cid = int(conversation["id"])
    turns, token_limit, max_tokens = _assistant_limits(cfg)

    snap, positive, built, history = _build_assistant_context(
        store, cfg, chapter_no, mode, text, cid, turns, token_limit)

    llm = _build_llm(store, cfg, int(user["id"]))
    try:
        answer = llm.chat(built["system"], built["user"], max_tokens=max_tokens) or ""
    except Exception:
        logger.exception("小墨对话 LLM 调用失败（降级）: project=%s chapter=%s",
                         store.dir.name, chapter_no)
        try:
            db.add_message(cid, "user", text,
                           {"mode": mode, "chapter_no": chapter_no, "degraded": True})
        except Exception:
            logger.exception("降级落库失败: conversation=%s", cid)
        return {"message_id": 0, "conversation_id": cid, "content": DEGRADED_TEXT,
                "mode": mode, "chapter_no": chapter_no, "cards": [], "citations": [],
                "usage": {"token_in": built["tokens"], "token_out": 0},
                "degraded": True, "notice": "模型调用失败，本条回答为降级提示"}

    if basic_content_check(answer):
        logger.warning("小墨对话输出命中合规拦截: project=%s chapter=%s",
                       store.dir.name, chapter_no)
        answer = SAFE_TEXT
    card_list = cards.build_cards(store, snap, positive)
    extra = {
        "mode": mode, "chapter_no": chapter_no,
        "token_in": built["tokens"],
        "token_out": context.estimate_tokens(answer),
        "cards": card_list, "citations": [],
        "level": built.get("level", 0), "history": len(history),
    }
    user_msg_id = db.add_message(cid, "user", text,
                                 {"mode": mode, "chapter_no": chapter_no,
                                  "token_in": built["tokens"]})
    msg_id = db.add_message(cid, "assistant", answer, extra)
    return {"message_id": msg_id, "conversation_id": cid, "content": answer,
            "mode": mode, "chapter_no": chapter_no, "cards": card_list, "citations": [],
            "usage": {"token_in": built["tokens"], "token_out": extra["token_out"]},
            "degraded": False, "notice": _notice(built, snap, positive),
            "user_message_id": user_msg_id}
