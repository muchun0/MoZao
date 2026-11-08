# -*- coding: utf-8 -*-
"""小墨AI 会话编排（service 层）：校验 → 模型来源决策 → 余额闸门 → 组装 → LLM → 校验
→ 卡片 → 落库 → 按字数结算。

分层（AGENTS.md §11.2）：
  - 本层不写 SQL（会话/消息/计费全部走 webapp/db/）；
  - 不写 HTTP 细节（鉴权与参数模型在 routers/assistant.py）；
  - 只读项目产物（projects/<id>/），绝不写回。

计费口径（TECH-小墨AI计费 v0.2，已 G4 通过）：
  - 扣费对象 = 会话发起用户（D1=1A）；管理员跨项目豁免；
  - 3 元/万字、四舍五入到分，复用 webapp/pricing.py 与 webapp/referral.py；
  - BYOK / 管理员豁免 due=0，但写审计行；
  - 失败 / 空回答 / 降级 / 安全拦截不扣费；
  - 结算失败按 D3=3B：回答照常返回 + 待补扣记录。
"""
from __future__ import annotations

import re
import uuid

from fastapi import HTTPException

from novel_gen.config import _apply_provider_defaults, validate_llm_config
from novel_gen.llm.factory import create_llm
from novel_gen.pipeline.guard import AgentGuard
from novel_gen.pipeline.trace import TraceRecorder
from novel_gen.safety import (basic_content_check, detect_prompt_injection,
                              sanitize_system_text)
from novel_gen.store.wordcount import count_words

from .. import db
from ..config_overrides import apply_global_overrides
from ..core import _server_api_key, _user_llm_cfg
from ..core_settle import _grant_consume_commission
from ..logger import get_logger
from ..pricing import price_for
from ..referral import agent_price, round_money
from . import cards, context
from .prompt import load_persona

logger = get_logger("assistant")

__all__ = ["MODES", "send_message", "retry_pending_billing"]

#: 子功能白名单（UI 显式选择，后端按 mode 走不同输出要求）
MODES = ("chapter_eval", "revision_advice")
MAX_CONTENT = 4000
DEGRADED_TEXT = "小墨暂时开小差了（模型调用失败），请稍后重试；你的问题已保存。"
SAFE_TEXT = "这次回答触发了内容安全策略，已拦截。请换个问法或聚焦具体章节问题。"

# 模型来源（计费口径）
MODE_PLATFORM = "platform"      # 平台 Key，按字数扣费
MODE_USER_LLM = "user_llm"      # 用户自备 Key，不扣费但留审计
MODE_ADMIN_FREE = "admin_free"  # 管理员跨项目，不扣费但留审计

# 请求级幂等键（D1=1A：前端必传 Idempotency-Key）
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


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


def _resolve_source(cfg, user: dict, owner_id: int | None, byok: dict | None,
                    billing_on: bool) -> str:
    """模型来源决策（TECH §2.1）：返回 platform / user_llm / admin_free。

    优先级：强制 BYOK（require_user_key）→ 管理员跨项目豁免 → 用户 BYOK → 平台 Key。
    """
    uid = int(user["id"])
    native = cfg.llm.provider != "mock"
    if bool(getattr(cfg, "assistant_require_user_key", False)) and byok is None and native:
        # 平台 Key 兜底已关闭：明确提示用户去配置自己的模型（不静默失败）
        raise HTTPException(
            status_code=400,
            detail="小墨AI 仅支持使用你自己配置的模型：请先在「模型设置」填写 API Key 后再对话",
        )
    if not billing_on:
        return MODE_USER_LLM if byok is not None else MODE_PLATFORM
    is_admin = bool(user.get("is_admin"))
    cross_project = is_admin and (owner_id is None or int(owner_id) != uid)
    if cross_project:
        return MODE_ADMIN_FREE
    if byok is not None:
        return MODE_USER_LLM
    return MODE_PLATFORM


def _build_llm(store, cfg, user_id: int, byok: dict | None, mode_src: str):
    """LLM 客户端：用户 BYOK 优先；平台/管理员模式回落到服务端 Key。"""
    if byok is not None:
        cfg.llm.provider = byok["provider"]
        cfg.llm.api_key = byok["api_key"]
        if byok.get("base_url"):
            cfg.llm.base_url = byok["base_url"]
        if byok.get("model"):
            cfg.llm.model = byok["model"]
        cfg.llm.thinking = byok.get("thinking") or "auto"
    elif mode_src in (MODE_PLATFORM, MODE_ADMIN_FREE):
        if cfg.llm.provider != "mock" and not cfg.llm.api_key:
            cfg.llm.api_key = _server_api_key(cfg.llm.provider)
    # 对话前校验：与生成链路同一判据，避免「配了不生效」到调用时才暴露
    _apply_provider_defaults(cfg.llm)
    issues = validate_llm_config(cfg.llm)
    if issues:
        raise HTTPException(status_code=400, detail="模型配置不可用：" + "；".join(issues))
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


# ---------------- 计费辅助 ----------------

def _normalize_request_id(raw: str | None) -> str:
    """严格校验前端传入的幂等键（D1=1A），缺失/非法直接 400。"""
    rid = (raw or "").strip()
    if not _REQUEST_ID_RE.match(rid):
        raise HTTPException(
            status_code=400,
            detail="缺少或非法的 Idempotency-Key（需 8-64 位字母/数字/下划线/连字符）",
        )
    return rid


def _balance_of(user: dict) -> float:
    try:
        return float(user.get("balance") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _discount_of(user: dict) -> float:
    """代理本人折扣（D3=A：代理 8 折），非代理恒 1.0。"""
    if not user.get("is_agent"):
        return 1.0
    try:
        return float(user.get("agent_discount") or 1.0)
    except (TypeError, ValueError):
        return 1.0


def _money_eq(left: float, right: float) -> bool:
    """金额相等判断（已到分，仍用容差避免浮点误差）。"""
    return abs(float(left or 0) - float(right or 0)) < 1e-9


def _fee_note(mode_src: str, billing: dict) -> str:
    """回答末尾的计费说明（D4=4A：展示本次字数与实扣金额）。"""
    if mode_src == MODE_USER_LLM:
        return "本次使用你的自备 Key，不计费"
    if mode_src == MODE_ADMIN_FREE:
        return "管理员跨项目对话，不计费"
    status = billing.get("billing_status") or "skipped"
    if status == "skipped":
        return "本条未计费"
    if status == "failed":
        return "本条计费未完成，已记录待补扣（不影响本次回答）"
    chars = int(billing.get("chars") or 0)
    due = float(billing.get("due") or 0)
    charged = float(billing.get("charged") or 0)
    owe = float(billing.get("owe") or 0)
    text = f"本次回答 {chars} 字，应付 {due:.2f} 元，实扣 {charged:.2f} 元"
    if owe > 0:
        text += f"，欠费 {owe:.2f} 元（已扣至 0）"
    return text


def _with_fee_notice(base: str, mode_src: str, billing: dict) -> str:
    fee = _fee_note(mode_src, billing)
    return f"{base}；{fee}" if base else fee


def _safe_update_extra(message_id: int, patch: dict) -> None:
    """消息 extra 更新失败只留痕，不阻断主链路（回放仍可从账本恢复）。"""
    if not message_id:
        return
    try:
        db.update_message_extra(int(message_id), patch)
    except Exception:
        logger.exception("消息计费状态更新失败: message=%s patch=%s", message_id, patch)


def _payload(msg_id: int, cid: int, content: str, mode: str, chapter_no: int,
             card_list: list, tokens_in: int, tokens_out: int, notice: str,
             *, mode_src: str, degraded: bool = False,
             user_message_id: int = 0, billing: dict | None = None) -> dict:
    """统一响应结构：在既有字段基础上追加计费字段（前端按需展示）。"""
    b = billing or {"billing_status": "skipped", "billing_id": 0, "chars": 0,
                    "due": 0.0, "charged": 0.0, "owe": 0.0}
    return {
        "message_id": int(msg_id), "conversation_id": int(cid), "content": content,
        "mode": mode, "chapter_no": int(chapter_no), "cards": card_list or [],
        "citations": [],
        "usage": {"token_in": int(tokens_in or 0), "token_out": int(tokens_out or 0)},
        "degraded": bool(degraded), "notice": notice or "",
        "user_message_id": int(user_message_id or 0),
        "billing_mode": mode_src,
        "billing_status": b.get("billing_status", "skipped"),
        "billing_id": int(b.get("billing_id") or 0),
        "chars": int(b.get("chars") or 0),
        "due": float(b.get("due") or 0),
        "charged": float(b.get("charged") or 0),
        "owe": float(b.get("owe") or 0),
    }


def _settle_answer(*, mode_src: str, user: dict, project_id: str, project_title: str,
                   cid: int, request_id: str, user_message_id: int,
                   assistant_message_id: int, answer: str) -> dict:
    """成功回答的计费：算字数 → 算应付 → 原子扣费 → 回写消息状态 → 足额返佣。

    D3=3B：结算异常时返回 failed 并落待补扣行，回答照常返回。
    """
    chars = count_words("assistant", {"content": answer})
    due = 0.0
    if mode_src == MODE_PLATFORM:
        due = round_money(agent_price(price_for(chars), _discount_of(user)))
    try:
        row = db.charge_assistant_billing(
            user_id=int(user["id"]), project_id=project_id, conversation_id=int(cid),
            request_id=request_id, user_message_id=int(user_message_id),
            assistant_message_id=int(assistant_message_id), mode=mode_src,
            chars=chars, due=due,
        )
    except Exception:
        logger.exception("小墨计费结算失败（返回回答并记待补扣）: project=%s request=%s",
                         project_id, request_id)
        return _record_failed(mode_src=mode_src, user=user, project_id=project_id,
                              cid=cid, request_id=request_id,
                              user_message_id=user_message_id,
                              assistant_message_id=assistant_message_id,
                              chars=chars, due=due)

    billing = {
        "billing_status": "billed" if mode_src == MODE_PLATFORM else "skipped",
        "billing_id": int(row["id"]),
        "chars": int(row.get("chars") or chars),
        "due": float(row.get("due") or due),
        "charged": float(row.get("charged") or 0.0),
        "owe": float(row.get("owe") or 0.0),
    }
    patch = {"billing_status": billing["billing_status"],
             "billing_id": billing["billing_id"],
             "chars": billing["chars"], "due": billing["due"],
             "charged": billing["charged"], "owe": billing["owe"]}
    _safe_update_extra(user_message_id, patch)
    _safe_update_extra(assistant_message_id, patch)

    # 返佣：仅平台 Key 且足额扣费（沿用既有「未足额不返佣」规则）
    if (mode_src == MODE_PLATFORM and billing["charged"] > 0
            and _money_eq(billing["charged"], billing["due"])
            and not int(row.get("commission_granted") or 0)):
        try:
            _grant_consume_commission(int(user["id"]), project_id,
                                      billing["charged"], project_title)
            db.mark_assistant_commission_granted(billing["billing_id"])
        except Exception:
            logger.critical("小墨消费返佣失败（不重复扣用户，待补偿）: project=%s billing=%s",
                            project_id, billing["billing_id"])
    logger.info("小墨计费完成: project=%s request=%s mode=%s chars=%d due=%.2f charged=%.2f owe=%.2f",
                project_id, request_id, mode_src, billing["chars"], billing["due"],
                billing["charged"], billing["owe"])
    return billing


def _record_failed(*, mode_src: str, user: dict, project_id: str, cid: int,
                   request_id: str, user_message_id: int, assistant_message_id: int,
                   chars: int, due: float) -> dict:
    """D3=3B：结算失败落待补扣行；若落库也失败，CRITICAL 留痕供人工补扣。"""
    billing_id = 0
    try:
        row = db.record_pending_assistant_billing(
            user_id=int(user["id"]), project_id=project_id, conversation_id=int(cid),
            request_id=request_id, user_message_id=int(user_message_id),
            assistant_message_id=int(assistant_message_id), mode=mode_src,
            chars=chars, due=due,
        )
        billing_id = int(row["id"])
    except Exception:
        logger.critical("小墨待补扣记录写入失败（需人工补扣）: project=%s request=%s "
                        "user=%s mode=%s chars=%d due=%.2f",
                        project_id, request_id, user.get("id"), mode_src, chars, due)
    billing = {"billing_status": "failed", "billing_id": billing_id,
               "chars": int(chars), "due": float(due), "charged": 0.0,
               "owe": float(due)}
    patch = {"billing_status": "failed", "billing_id": billing_id, "chars": int(chars),
             "due": float(due), "charged": 0.0, "owe": float(due)}
    _safe_update_extra(user_message_id, patch)
    _safe_update_extra(assistant_message_id, patch)
    return billing


def _billing_from_extra(extra: dict) -> dict:
    return {
        "billing_status": extra.get("billing_status") or "pending",
        "billing_id": int(extra.get("billing_id") or 0),
        "chars": int(extra.get("chars") or 0),
        "due": float(extra.get("due") or 0),
        "charged": float(extra.get("charged") or 0),
        "owe": float(extra.get("owe") or 0),
    }


def _respond_existing(row: dict) -> dict:
    """幂等命中：直接回放已存回答，不调用模型、不重复扣费。"""
    mid = int(row.get("assistant_message_id") or 0)
    msg = db.get_message(mid) if mid else None
    if not msg:
        # 账本已存在但回答缺失：拒绝重新生成，避免"同键生成两次再扣两次"
        raise HTTPException(status_code=409,
                            detail="该请求已受理但回答缺失，请刷新会话查看最新消息")
    extra = msg.get("extra") or {}
    status = row.get("status") or "settled"
    mode_src = row.get("mode") or "platform"
    if status != "settled":
        billing_status = "failed"
    elif mode_src == MODE_PLATFORM:
        billing_status = "billed"
    else:
        billing_status = "skipped"
    billing = {"billing_status": billing_status,
               "billing_id": int(row["id"]),
               "chars": int(row.get("chars") or 0),
               "due": float(row.get("due") or 0),
               "charged": float(row.get("charged") or 0),
               "owe": float(row.get("owe") or 0)}
    notice = "重复请求已命中幂等键，返回已存回答（不重复调用模型、不重复扣费）"
    return _payload(
        int(msg["id"]), int(msg["conversation_id"]), msg.get("content") or "",
        extra.get("mode") or "chapter_eval", int(extra.get("chapter_no") or 0),
        extra.get("cards") or [], int(extra.get("token_in") or 0),
        int(extra.get("token_out") or 0),
        _with_fee_notice(notice, mode_src, billing),
        mode_src=mode_src, user_message_id=int(row.get("user_message_id") or 0),
        billing=billing,
    )


def _recover_prior_answer(*, prior: dict, mode_src: str, user: dict, project_id: str,
                          project_title: str, cid: int, request_id: str) -> dict:
    """上次在"落助手消息后、结算前"中断：只补结算，不重复调用模型。"""
    extra = prior.get("extra") or {}
    status = extra.get("billing_status")
    billing = _billing_from_extra(extra)
    notice = "该请求上次已回答但未完成结算，本次仅补结算，不重复调用模型"
    if status in ("billed", "failed", "skipped"):
        if status == "billed":
            notice = "重复请求已命中已存回答（不重复调用模型、不重复扣费）"
        return _payload(
            int(prior["id"]), int(prior["conversation_id"]), prior.get("content") or "",
            extra.get("mode") or "chapter_eval", int(extra.get("chapter_no") or 0),
            extra.get("cards") or [], int(extra.get("token_in") or 0),
            int(extra.get("token_out") or 0),
            _with_fee_notice(notice, mode_src, billing),
            mode_src=mode_src, user_message_id=int(extra.get("user_message_id") or 0),
            billing=billing,
        )
    billing = _settle_answer(
        mode_src=mode_src, user=user, project_id=project_id, project_title=project_title,
        cid=cid, request_id=request_id,
        user_message_id=int(extra.get("user_message_id") or 0),
        assistant_message_id=int(prior["id"]), answer=prior.get("content") or "",
    )
    return _payload(
        int(prior["id"]), int(prior["conversation_id"]), prior.get("content") or "",
        extra.get("mode") or "chapter_eval", int(extra.get("chapter_no") or 0),
        extra.get("cards") or [], int(extra.get("token_in") or 0),
        int(extra.get("token_out") or 0),
        _with_fee_notice(notice, mode_src, billing),
        mode_src=mode_src, user_message_id=int(extra.get("user_message_id") or 0),
        billing=billing,
    )


def retry_pending_billing(billing_id: int) -> dict:
    """后台「待补扣重试」：重新执行扣费，不重复调用模型；足额时补发返佣。"""
    row = db.retry_pending_assistant_billing(int(billing_id))
    if (row.get("mode") == MODE_PLATFORM and float(row.get("charged") or 0) > 0
            and _money_eq(row.get("charged"), row.get("due"))
            and not int(row.get("commission_granted") or 0)):
        title = row.get("project_id") or ""
        try:
            from ..core import _find_project
            title = _find_project(row["project_id"]).load_config().title or title
        except Exception:
            logger.warning("补扣返佣取项目标题失败，使用 project_id 代替: %s", row.get("project_id"))
        try:
            _grant_consume_commission(int(row["user_id"]), row["project_id"],
                                      float(row["charged"]), title)
            db.mark_assistant_commission_granted(int(row["id"]))
        except Exception:
            logger.critical("小墨待补扣返佣失败（待补偿）: billing=%s", billing_id)
    return row


def send_message(store, user: dict, conversation: dict, content: str, mode: str,
                 chapter_no: int | None = None, request_id: str | None = None) -> dict:
    """发一条消息：返回 {message_id, conversation_id, content, mode, chapter_no, 计费字段...}。"""
    text = _validate(content, mode)
    chapter_no = _resolve_chapter_no(store, chapter_no)
    cfg = store.load_config()
    # 后台开关以 sys_config 为准（与生成链路一致：老项目快照没有新字段也能生效）
    apply_global_overrides(cfg)
    if not bool(getattr(cfg, "assistant_enabled", True)):
        raise HTTPException(status_code=403, detail="小墨AI 当前未开启")
    cid = int(conversation["id"])
    project_id = store.dir.name
    uid = int(user["id"])
    turns, token_limit, max_tokens = _assistant_limits(cfg)

    billing_on = bool(getattr(cfg, "assistant_billing_enabled", True))
    byok = _user_llm_cfg(uid)
    mode_src = _resolve_source(cfg, user, db.project_owner(project_id), byok, billing_on)

    rid = ""
    if billing_on:
        rid = _normalize_request_id(request_id)
        # 幂等预检：先查账本，再查"已落回答未结算"的中断场景
        existing = db.get_assistant_billing_by_request(project_id, rid)
        if existing:
            return _respond_existing(existing)
        prior = db.find_assistant_message_by_request(cid, rid)
        if prior:
            return _recover_prior_answer(
                prior=prior, mode_src=mode_src, user=user, project_id=project_id,
                project_title=cfg.title, cid=cid, request_id=rid,
            )
        # 平台 Key + 余额 ≤ 0：前置拦截，不调模型、不落消息
        if mode_src == MODE_PLATFORM and _balance_of(user) <= 0:
            raise HTTPException(
                status_code=402,
                detail="余额不足：小墨AI 按回答字数计费（3 元/万字），请充值后再对话",
            )

    snap, positive, built, history = _build_assistant_context(
        store, cfg, chapter_no, mode, text, cid, turns, token_limit)

    user_extra = {"mode": mode, "chapter_no": chapter_no, "token_in": built["tokens"]}
    if billing_on:
        user_extra.update({"request_id": rid, "billing_status": "pending"})
    # 先落用户消息（携带 request_id），再调模型：失败重试有稳定锚点
    user_msg_id = db.add_message(cid, "user", text, user_extra)

    llm = _build_llm(store, cfg, uid, byok, mode_src)
    try:
        answer = llm.chat(built["system"], built["user"], max_tokens=max_tokens) or ""
    except Exception:
        logger.exception("小墨对话 LLM 调用失败（降级，不计费）: project=%s chapter=%s",
                         project_id, chapter_no)
        _safe_update_extra(user_msg_id, {"billing_status": "skipped", "degraded": True})
        return _payload(
            0, cid, DEGRADED_TEXT, mode, chapter_no, [], built["tokens"], 0,
            "模型调用失败，本条回答为降级提示；本条未计费",
            mode_src=mode_src, degraded=True, user_message_id=user_msg_id,
        )

    if not answer.strip():
        logger.warning("小墨对话返回空回答（不计费）: project=%s chapter=%s",
                       project_id, chapter_no)
        _safe_update_extra(user_msg_id, {"billing_status": "skipped"})
        return _payload(
            0, cid, DEGRADED_TEXT, mode, chapter_no, [], built["tokens"], 0,
            "模型返回空回答，本条回答为降级提示；本条未计费",
            mode_src=mode_src, degraded=True, user_message_id=user_msg_id,
        )

    blocked = bool(basic_content_check(answer))
    if blocked:
        logger.warning("小墨对话输出命中合规拦截（不计费）: project=%s chapter=%s",
                       project_id, chapter_no)
        answer = SAFE_TEXT
    card_list = cards.build_cards(store, snap, positive)
    extra = {
        "mode": mode, "chapter_no": chapter_no,
        "token_in": built["tokens"],
        "token_out": context.estimate_tokens(answer),
        "cards": card_list, "citations": [],
        "level": built.get("level", 0), "history": len(history),
    }
    if billing_on:
        extra.update({"request_id": rid, "user_message_id": user_msg_id,
                      "billing_status": "skipped" if blocked else "pending"})
    # 落助手消息（先 pending），随后结算并回写 billed/failed
    msg_id = db.add_message(cid, "assistant", answer, extra)

    billing: dict | None = None
    if billing_on and not blocked:
        billing = _settle_answer(
            mode_src=mode_src, user=user, project_id=project_id,
            project_title=cfg.title, cid=cid, request_id=rid,
            user_message_id=user_msg_id, assistant_message_id=msg_id, answer=answer,
        )
    elif billing_on and blocked:
        _safe_update_extra(user_msg_id, {"billing_status": "skipped"})
        billing = {"billing_status": "skipped", "billing_id": 0, "chars": 0,
                   "due": 0.0, "charged": 0.0, "owe": 0.0}

    notice = _with_fee_notice(_notice(built, snap, positive), mode_src,
                             billing or {"billing_status": "skipped"})
    return _payload(
        msg_id, cid, answer, mode, chapter_no, card_list, built["tokens"],
        extra["token_out"], notice, mode_src=mode_src,
        user_message_id=user_msg_id, billing=billing,
    )
