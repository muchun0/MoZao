# -*- coding: utf-8 -*-
"""R26 剧情走向候选服务：生成（计费）→ 采纳为便签（不计费）。

与 R25 `inline_service` 的分工：
- 走向候选只出「方向」，不写正文、不落版本；采纳只写 `continue_drafts.json` v2；
- 计费走 `settle_product(kind="branch", key=gen_id)`，与 `kind=inline` 分开记账；
- 幂等复用 `inline_drafts.tokens`（同 `client_token` 重放返回同一批，不重复扣费）；
- 生成记录以 `kind=branch` 存 `inline_drafts.json`，允许多批共存（不被内联作废）。

边界：本模块不写 SQL、不拼正文、不做 HTTP 异常映射。
"""
from __future__ import annotations

import time

from novel_gen.safety import basic_content_check
from novel_gen.store import continue_drafts
from novel_gen.store.inline_drafts import (
    RETENTION_SECONDS, find_by_gen_id, find_by_token, new_gen_id as _new_inline_id,
    upsert_generation)
from novel_gen.store.wordcount import count_words

from .branch_prompt import STUCK_CUSTOM, STUCK_TYPES, build_prompt, joined_text, max_tokens, parse_directions
from .config_overrides import apply_global_overrides
from .logger import get_logger

logger = get_logger("branch")

__all__ = ["BranchError", "generate", "adopt", "list_notes", "remove_note",
           "COUNT_MIN", "COUNT_MAX", "COUNT_DEFAULT"]

COUNT_MIN, COUNT_MAX, COUNT_DEFAULT = 3, 5, 4
MAX_STUCK_TEXT = 200
KIND = "branch"


class BranchError(Exception):
    """剧情走向业务异常（路由层转换为 HTTP 状态码 + code）。"""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


# ---------- 入参 ----------

def _count_of(payload: dict) -> int:
    try:
        count = int(payload.get("count") or COUNT_DEFAULT)
    except (TypeError, ValueError):
        count = COUNT_DEFAULT
    return max(COUNT_MIN, min(COUNT_MAX, count))


def _parse_generate(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise BranchError(400, "BAD_STUCK", "请求体格式不正确")
    stuck = str(payload.get("stuck_type") or "").strip().lower()
    if stuck and stuck not in STUCK_TYPES and stuck != STUCK_CUSTOM:
        raise BranchError(
            400, "BAD_STUCK",
            f"不支持的卡点类型：{stuck}（可选 {'/'.join(STUCK_TYPES)}/{STUCK_CUSTOM}）")
    stuck_text = ""
    if stuck == STUCK_CUSTOM:
        stuck_text = str(payload.get("stuck_text") or "").strip()
        if len(stuck_text) > MAX_STUCK_TEXT:
            logger.info("走向候选：stuck_text 超长截断 %d → %d", len(stuck_text), MAX_STUCK_TEXT)
            stuck_text = stuck_text[:MAX_STUCK_TEXT]
    elif stuck:
        # 非 custom 时忽略自由文本（不报错，只留痕）
        if str(payload.get("stuck_text") or "").strip():
            logger.info("走向候选：stuck_type=%s 时忽略 stuck_text", stuck)
    token = str(payload.get("client_token") or "").strip()
    if not token or len(token) > 100:
        raise BranchError(400, "BAD_STUCK", "缺少合法的 client_token")
    return {"count": _count_of(payload), "stuck_type": stuck,
            "stuck_text": stuck_text, "client_token": token}


def _parse_adopt(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise BranchError(400, "BAD_ITEM", "请求体格式不正确")
    gen_id = str(payload.get("gen_id") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()
    if not gen_id or not item_id:
        raise BranchError(400, "BAD_ITEM", "缺少 gen_id / item_id")
    return {"gen_id": gen_id, "item_id": item_id,
            "client_token": str(payload.get("client_token") or "").strip()}


# ---------- 开关 / 计费 ----------

def _flag(key: str, default):
    """复用 R25 同一份 sys_config 读取实现（未配行 / 解析失败均回默认）。"""
    from .inline_service import _flag as inline_flag
    return inline_flag(key, default)


def _billing_enabled() -> bool:
    """走向候选计费开关：默认 False（首周灰度，旧免费体验不被突然打断）。"""
    return bool(_flag("branches_billing_enabled", False))


def _new_branch_gen_id() -> str:
    return "bg_" + _new_inline_id().split("_", 1)[-1]


def _response(item: dict, *, replayed: bool, balance: float = 0.0) -> dict:
    return {
        "gen_id": item.get("gen_id"),
        "items": [{"id": i.get("id"), "direction": i.get("direction"),
                   "summary": i.get("summary"), "source_tag": i.get("source_tag"),
                   "tags": list(i.get("tags") or [])} for i in (item.get("items") or [])],
        "words": int(item.get("words") or 0),
        "charged": float(item.get("charged") or 0.0),
        "balance": balance,
        "billing": bool(item.get("billing")),
        "replayed": replayed,
    }


def _replay(store, token: str, uid: int) -> dict:
    from .inline_service import _balance
    prior = find_by_token(store, token)
    if not prior:
        raise BranchError(409, "BRANCH_BUSY", "该次生成正在进行，请稍后重试")
    item = find_by_gen_id(store, prior.get("gen_id"))
    if item is None:
        raise BranchError(410, "GEN_EXPIRED", "该次生成记录已过期（超过 48 小时），请重新生成")
    if str(item.get("kind") or "") != KIND:
        raise BranchError(409, "TOKEN_CONFLICT", "该 client_token 已用于内联写作，请换一个")
    return _response(item, replayed=True, balance=_balance(uid))


# ---------- 生成 ----------

def generate(store, llm, *, project_id: str, uid: int, payload: dict,
             request_id: str = "") -> dict:
    """生成 3~5 个剧情走向；计费开关开启时按候选合计字数结算一次。"""
    from .core_settle import _settle_billing_mode
    from .inline_service import _balance, _project_busy, _token_guard

    ch_no = int(payload.get("ch") or 0)
    chapter = store.load_chapter(ch_no) if ch_no else None
    if chapter is None:
        raise BranchError(404, "CHAPTER_NOT_FOUND", f"章节 {ch_no} 未生成")
    req = _parse_generate(payload)
    req["ch"] = ch_no

    if find_by_token(store, req["client_token"]):
        return _replay(store, req["client_token"], uid)
    if _project_busy(project_id):
        raise BranchError(409, "PROJECT_BUSY", "整章生成中，稍后再试")

    cfg = store.load_config()
    apply_global_overrides(cfg)
    billing = _billing_enabled()
    mode = _settle_billing_mode(store)
    if billing and mode == "platform" and _balance(uid) <= 0:
        raise BranchError(402, "INSUFFICIENT_BALANCE", "余额不足：走向候选按生成字数计费，请先充值")

    guard = _token_guard(req["client_token"])
    with guard:
        if not guard.owner:
            guard.wait()
            return _replay(store, req["client_token"], uid)
        return _worker(store, llm, project_id=project_id, uid=uid, cfg=cfg,
                       chapter=chapter, req=req, billing=billing, mode=mode,
                       request_id=request_id)


def _worker(store, llm, *, project_id, uid, cfg, chapter, req, billing, mode,
            request_id) -> dict:
    system, user = build_prompt(store, cfg, chapter, req)
    items: list[dict] = []
    for attempt in range(2):
        try:
            raw = llm.chat(system, user, temperature=0.9, max_tokens=max_tokens())
        except Exception as exc:
            logger.exception("走向候选 LLM 调用失败: project=%s ch=%s req=%s",
                             project_id, chapter.no, request_id)
            raise BranchError(500, "LLM_FAILED", f"模型调用失败：{type(exc).__name__}")
        items = parse_directions(raw, req["count"])
        if items:
            break
        logger.warning("走向候选为空/解析失败，重试一次: project=%s ch=%s attempt=%d",
                       project_id, chapter.no, attempt + 1)
    if not items:
        raise BranchError(422, "LLM_EMPTY", "模型未产出可用走向，本次不计费，请重试")

    joined = joined_text(items)
    hits = basic_content_check(joined)
    if hits:
        logger.warning("走向候选命中合规拦截（不计费）: project=%s ch=%s hits=%s",
                       project_id, chapter.no, hits[:3])
        raise BranchError(422, "COMPLIANCE_BLOCKED", "生成内容未通过合规校验，本次不计费")

    words = sum(count_words("chapter", {"content": joined_text([i])}) for i in items)
    gen_id = _new_branch_gen_id()
    charged = 0.0
    entry = ""
    if billing:
        from .settle_ledger import content_digest, settle_product
        try:
            settled = settle_product(store, KIND, gen_id, words, mode=mode,
                                     project_id=project_id, content_sha=content_digest(joined))
        except Exception:
            logger.exception("走向候选结算失败: project=%s gen=%s", project_id, gen_id)
            raise BranchError(500, "SETTLE_FAILED", "计费失败，未返回候选，请重试")
        if not settled:
            raise BranchError(500, "SETTLE_FAILED", "计费失败，未返回候选，请重试")
        charged = float(settled.get("charged") or 0.0)
        entry = str(settled.get("entry") or "")

    now = time.time()
    item = {
        "gen_id": gen_id,
        "client_token": req["client_token"],
        "kind": KIND,
        "ch": chapter.no,
        "stuck_type": req["stuck_type"],
        "stuck_text": req["stuck_text"],
        "items": items,
        "words": words,
        "charged": charged,
        "charge_entry": entry,
        "billing": bool(billing),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(now + RETENTION_SECONDS)),
    }
    try:
        # 走向候选允许多批共存，不作废旧批次（与内联的「同章单飞」不同）
        item, _ = upsert_generation(store, item)
    except Exception:
        logger.exception("走向候选生成记录落盘失败: project=%s gen=%s", project_id, gen_id)
        raise BranchError(500, "WRITE_FAILED", "生成记录写入失败，请重试")
    from .inline_service import _balance
    logger.info("走向候选生成: project=%s ch=%d gen=%s count=%d words=%d charged=%.2f billing=%s req=%s",
                project_id, chapter.no, gen_id, len(items), words, charged, billing, request_id)
    return _response(item, replayed=False, balance=_balance(uid))


# ---------- 采纳为便签 ----------

def _time_of(text: str) -> float:
    from .inline_service import parse_time
    return parse_time(text)


def adopt(store, *, project_id: str, ch_no: int, payload: dict) -> dict:
    """把某个走向存为写作便签：不改正文、不落版本、不再计费。"""
    req = _parse_adopt(payload)
    item = find_by_gen_id(store, req["gen_id"])
    if item is None:
        raise BranchError(410, "GEN_EXPIRED", "该批走向不存在或已过期，请重新生成")
    if str(item.get("kind") or "") != KIND:
        raise BranchError(400, "BAD_ITEM", "该记录不是剧情走向候选")
    if int(item.get("ch") or 0) != int(ch_no):
        raise BranchError(400, "BAD_ITEM", "候选与章节不匹配")
    if _time_of(item.get("expires_at")) <= time.time():
        raise BranchError(410, "GEN_EXPIRED", "该批走向已过期（超过 48 小时），请重新生成")
    direction = next((i for i in (item.get("items") or [])
                      if i.get("id") == req["item_id"]), None)
    if direction is None:
        raise BranchError(400, "BAD_ITEM", "走向不存在，请刷新后重试")

    note = {
        "direction": direction.get("direction", ""),
        "summary": direction.get("summary", ""),
        "source_tag": direction.get("source_tag", ""),
        "tags": direction.get("tags") or [],
        "gen_id": req["gen_id"],
        "item_id": req["item_id"],
        "origin": "branch",
    }
    try:
        saved, created = continue_drafts.add_note(
            store, ch_no, note, dedup_key=(req["gen_id"], req["item_id"]))
    except Exception:
        logger.exception("走向便签落盘失败: project=%s ch=%s gen=%s",
                         project_id, ch_no, req["gen_id"])
        raise BranchError(500, "WRITE_FAILED", "便签写入失败，请重试")
    logger.info("走向便签: project=%s ch=%d gen=%s item=%s draft=%s created=%s",
                project_id, ch_no, req["gen_id"], req["item_id"],
                saved.get("draft_id"), created)
    return {"draft_id": saved.get("draft_id"), "duplicated": not created,
            "item": saved}


# ---------- 便签只读 / 删除 ----------

def list_notes(store, ch_no: int) -> list[dict]:
    try:
        return continue_drafts.list_notes(store, ch_no)
    except Exception:
        logger.exception("读取走向便签失败（降级为空）: ch=%s", ch_no)
        return []


def remove_note(store, ch_no: int, draft_id: str) -> dict:
    try:
        deleted = continue_drafts.remove_note(store, ch_no, draft_id)
    except Exception:
        logger.exception("删除走向便签失败: ch=%s draft=%s", ch_no, draft_id)
        raise BranchError(500, "WRITE_FAILED", "便签删除失败，请重试")
    if not deleted:
        raise BranchError(404, "NOTE_NOT_FOUND", "便签不存在或已删除")
    return {"deleted": True, "draft_id": draft_id}
