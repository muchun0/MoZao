# -*- coding: utf-8 -*-
"""R28 小墨建议「应用到正文」：来源校验 → 单段校验 → 结算 → 基座写通道。

口径（TECH-R28 §4）：
- 不新增写通路：落盘统一走 `novel_gen.store.inline_edit.apply_inline_text`（`source=inline`）；
- 不依赖 `review.json`：小墨自由建议按文本改写应用，不做 R1 issue 解析；
- 计费只走 `settle.json` v2（`kind=inline`，幂等键 `apply:<client_token>`），不写 `assistant_billing`；
- 先结算后写盘；写盘失败同步回滚账本与扣费（宁可漏扣不重扣）。

本模块不写 SQL（会话/消息经 `webapp/db`），不写 HTTP（路由层做异常映射）。
"""
from __future__ import annotations

from novel_gen.store.inline_edit import (
    InlineEditError, StaleBaseError, apply_inline_text, content_hash,
    current_version, normalize_text)
from novel_gen.store.wordcount import count_words

from .. import db
from ..logger import get_logger
from ..settle_ledger import content_digest, load_ledger, rollback_entry, settle_product

logger = get_logger("assistant_inline")

__all__ = ["ApplyError", "apply_suggestion", "MAX_APPLY_TEXT"]

#: 单次可写入的正文字数上限（跨段/超长一律拒绝，避免借应用之名改整章）
MAX_APPLY_TEXT = 2000


class ApplyError(Exception):
    """应用建议的业务异常（路由层转换为 HTTP 状态码 + code）。"""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


# ---------- 入参 ----------

def _parse(payload: dict, ch_no: int) -> dict:
    if not isinstance(payload, dict):
        raise ApplyError(400, "BAD_ANCHOR", "请求体格式不正确")
    try:
        cid = int(payload.get("conversation_id"))
        mid = int(payload.get("message_id"))
    except (TypeError, ValueError):
        raise ApplyError(400, "MESSAGE_MISMATCH", "缺少合法的 conversation_id / message_id")
    anchor = payload.get("anchor") or {}
    if not isinstance(anchor, dict):
        raise ApplyError(400, "BAD_ANCHOR", "锚点格式不正确")
    try:
        start, end = int(anchor.get("start")), int(anchor.get("end"))
    except (TypeError, ValueError):
        raise ApplyError(400, "BAD_ANCHOR", "anchor.start / anchor.end 必须是整数")
    body_ch = payload.get("ch")
    if body_ch not in (None, "", 0) and int(body_ch) != int(ch_no):
        raise ApplyError(400, "BAD_ANCHOR", "锚点章节与路径章节不一致")
    text = normalize_text(payload.get("text") or "").strip()
    if not text:
        raise ApplyError(400, "BAD_ANCHOR", "缺少建议文本")
    token = str(payload.get("client_token") or "").strip()
    if not token or len(token) > 100:
        raise ApplyError(400, "BAD_ANCHOR", "缺少合法的 client_token")
    try:
        base_version = int(payload.get("base_version"))
    except (TypeError, ValueError):
        raise ApplyError(409, "STALE_BASE", "缺少合法的 base_version，请刷新正文后重试")
    base_hash = str(payload.get("base_hash") or "")
    if not base_hash:
        raise ApplyError(409, "STALE_BASE", "缺少 base_hash，请刷新正文后重试")
    return {"conversation_id": cid, "message_id": mid, "ch": int(ch_no),
            "start": start, "end": end, "text": text, "token": token,
            "base_version": base_version, "base_hash": base_hash,
            "edited": bool(payload.get("edited"))}


# ---------- 来源校验 ----------

def _check_source(store, req: dict, uid: int, is_admin: bool) -> dict:
    """按 message_id 回读建议：归属 / 角色 / 文本一致 / 锚点一致（防盲写）。"""
    conv = db.get_conversation(req["conversation_id"], store.dir.name)
    if not conv:
        raise ApplyError(404, "CONVERSATION_NOT_FOUND", "会话不存在")
    if int(conv["user_id"]) != int(uid) and not is_admin:
        raise ApplyError(403, "FORBIDDEN", "无权使用该会话中的建议")
    msg = db.get_message(req["message_id"])
    if not msg or int(msg.get("conversation_id") or 0) != req["conversation_id"]:
        raise ApplyError(400, "MESSAGE_MISMATCH", "找不到对应的建议消息，请重新提问")
    if msg.get("role") != "assistant":
        raise ApplyError(400, "MESSAGE_MISMATCH", "只能应用小墨的回答")
    if req["edited"]:
        raise ApplyError(400, "MESSAGE_MISMATCH", "建议已被修改，请重新选择原始建议后再应用")
    content = normalize_text(msg.get("content") or "")
    if req["text"] not in content:
        raise ApplyError(400, "MESSAGE_MISMATCH", "建议文本与消息不一致，请重新选择后再应用")
    anchor = ((msg.get("extra") or {}).get("anchor")) or {}
    if anchor.get("ch") and int(anchor["ch"]) != req["ch"]:
        raise ApplyError(400, "BAD_ANCHOR", "该建议来自其他章节，请回到对应章节再应用")
    if anchor.get("start") is not None and int(anchor["start"]) != req["start"]:
        raise ApplyError(409, "STALE_BASE", "建议锚点与当前选段不一致，请重新选段")
    if anchor.get("end") is not None and int(anchor["end"]) != req["end"]:
        raise ApplyError(409, "STALE_BASE", "建议锚点与当前选段不一致，请重新选段")
    return msg


# ---------- 锚点 / 文本校验 ----------

def _check_anchor(store, req: dict) -> str:
    """返回规范化后的正文；跨段 / 越界 / 超长一律 400（不写盘、不扣费）。"""
    chapter = store.load_chapter(req["ch"])
    if chapter is None:
        raise ApplyError(404, "CHAPTER_NOT_FOUND", f"章节 {req['ch']} 未生成")
    cur = normalize_text(chapter.content)
    if not (0 <= req["start"] < req["end"] <= len(cur)):
        raise ApplyError(400, "BAD_ANCHOR", f"锚点越界：0 ≤ start < end ≤ {len(cur)}")
    if "\n\n" in cur[req["start"]:req["end"]]:
        raise ApplyError(400, "CROSS_PARAGRAPH", "一次只能应用到一个自然段，请分开选段")
    if "\n\n" in req["text"]:
        raise ApplyError(400, "CROSS_PARAGRAPH", "建议文本跨了两个自然段，不能一次写入")
    if len(req["text"]) > MAX_APPLY_TEXT:
        raise ApplyError(400, "TEXT_TOO_LONG", f"建议文本最多 {MAX_APPLY_TEXT} 字")
    return cur


# ---------- 幂等 ----------

def _prior_state(store, key: str, sha: str) -> str:
    """同一 client_token 的历史结算：same=重放，diff=复用令牌但文本不同。"""
    ledger = load_ledger(store)
    prefix = f"inline:{key}:v"
    hits = {k: v for k, v in (ledger.get("entries") or {}).items() if str(k).startswith(prefix)}
    if not hits:
        return ""
    last_key = max(hits, key=lambda k: int(str(k).rsplit(":v", 1)[-1] or 0)
                   if str(k).rsplit(":v", 1)[-1].isdigit() else 0)
    last_sha = str((hits.get(last_key) or {}).get("content_sha") or "")
    return "same" if last_sha and last_sha == sha else "diff"


def _balance(uid: int) -> float:
    try:
        return float((db.get_user(uid) or {}).get("balance") or 0.0)
    except Exception:
        logger.warning("读取余额失败（按 0 处理）: uid=%s", uid)
        return 0.0


def _chapter_view(store, ch_no: int) -> dict:
    chapter = store.load_chapter(ch_no)
    return {
        "no": ch_no,
        "title": chapter.title if chapter else "",
        "content": chapter.content if chapter else "",
        "source": chapter.source if chapter else "",
        "summary_stale": bool(chapter.summary_stale) if chapter else False,
    }


def _replayed(store, ch_no: int, uid: int) -> dict:
    return {"version": current_version(store, ch_no), "words": 0, "charged": 0.0,
            "replayed": True, "balance": _balance(uid),
            "chapter": _chapter_view(store, ch_no)}


# ---------- 主流程 ----------

def apply_suggestion(store, *, project_id: str, uid: int, ch_no: int,
                     payload: dict, is_admin: bool = False) -> dict:
    """应用小墨建议到正文：一次写一个新版本，按写入字数结算一次。"""
    req = _parse(payload, ch_no)
    _check_source(store, req, uid, is_admin)
    cur = _check_anchor(store, req)

    # 并发/忙碌/锁定：与内联采纳同一口径（不进入写通道）
    from .. import inline_service
    from ..core import _is_locked
    if inline_service._project_busy(project_id):
        raise ApplyError(409, "PROJECT_BUSY", "整章生成中，稍后再试")
    if _is_locked(store, "chapter", ch_no):
        raise ApplyError(409, "CHAPTER_LOCKED", "本章已被锁定，请先解锁再应用")

    sha = content_digest(req["text"])
    key = f"apply:{req['token']}"
    prior = _prior_state(store, key, sha)
    if prior == "same":
        return _replayed(store, ch_no, uid)
    if prior == "diff":
        raise ApplyError(409, "TOKEN_REUSED", "同一幂等令牌不能用于不同文本，请重新发起")

    # 乐观锁预检（真正写入时 apply_inline_text 在项目锁内再校验一次）
    if int(req["base_version"]) != current_version(store, ch_no):
        raise ApplyError(409, "STALE_BASE", "正文版本已变化，请刷新后重新选段")
    if content_hash(cur) != req["base_hash"]:
        raise ApplyError(409, "STALE_BASE", "正文内容已变化，请刷新后重新选段")

    from ..core_settle import _settle_billing_mode
    mode = _settle_billing_mode(store)
    if mode == "platform" and _balance(uid) <= 0:
        raise ApplyError(402, "INSUFFICIENT_BALANCE", "余额不足：应用改写按写入字数计费，请充值后再用")

    words = count_words("chapter", {"content": req["text"]})
    try:
        settled = settle_product(store, "inline", key, words, mode=mode,
                                 project_id=project_id, content_sha=sha)
    except Exception:
        logger.exception("应用建议结算失败（正文未改动）: project=%s ch=%s",
                         project_id, ch_no)
        raise ApplyError(500, "SETTLE_FAILED", "计费失败，正文未改动，请重试")
    if settled.get("duplicate"):
        return _replayed(store, ch_no, uid)

    try:
        result = apply_inline_text(
            store, ch_no, req["start"], req["end"], req["text"],
            base_version=req["base_version"], base_hash=req["base_hash"],
            source="inline", label=f"inline:apply:{req['token']}")
    except StaleBaseError as exc:
        rollback_entry(store, settled["entry"])
        raise ApplyError(409, "STALE_BASE", exc.detail)
    except InlineEditError as exc:
        rollback_entry(store, settled["entry"])
        raise ApplyError(exc.status_code, exc.code, exc.detail)
    except Exception:
        rollback_entry(store, settled["entry"])
        logger.exception("应用建议写盘失败（已回滚计费）: project=%s ch=%s",
                         project_id, ch_no)
        raise ApplyError(500, "WRITE_FAILED", "正文写入失败，已回滚本次计费")

    logger.info("应用建议: project=%s ch=%s msg=%s v=%s words=%d charged=%.2f",
                project_id, ch_no, req["message_id"], result.get("version"),
                int(result.get("words") or 0), float(settled.get("charged") or 0.0))
    return {
        "version": int(result.get("version") or 0),
        "words": int(result.get("words") or words),
        "charged": float(settled.get("charged") or 0.0),
        "replayed": False,
        "balance": _balance(uid),
        "chapter": _chapter_view(store, ch_no),
    }
