# -*- coding: utf-8 -*-
"""R25 内联写作服务：生成 → 人工采纳两段式（基座 §4.1）。

职责：
- 入参校验与错误码（`InlineError`，路由层映射 HTTP）；
- 复用 `Memory.build_context` 组装上下文，不另写一套设定注入；
- LLM 出多候选 + 确定性校验（空/解释性/高相似度）；
- 生成阶段按候选合计 AI 字数写 `settle.json`（`kind=inline`），采纳阶段不再计费；
- 采纳统一走 `novel_gen.store.inline_edit.apply_inline_text`（版本化 + 乐观锁）。

边界：本模块不写 SQL、不直接拼正文落盘、不做 HTTP 异常映射。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from novel_gen.safety import basic_content_check
from novel_gen.store.inline_drafts import (
    RETENTION_SECONDS, find_by_gen_id, find_by_token, invalidate_chapter,
    list_for_chapter, mark_adopted, new_gen_id, upsert_generation)
from novel_gen.store.inline_edit import (
    StaleBaseError, apply_inline_text, content_hash, current_version, normalize_text)
from novel_gen.store.wordcount import count_words

from . import db
from .config_overrides import apply_global_overrides
from .core_runtime import PROJECTS_DIR, _LOCK, _RUNS
from .db import config as config_db
from .inline_prompt import build_prompt, max_tokens, parse_candidates
from .logger import get_logger
from .pricing import price_for
from .referral import agent_price, round_money
from .settle_ledger import content_digest, settle_product

logger = get_logger("inline")

__all__ = ["InlineError", "generate", "adopt", "estimate", "list_drafts", "parse_time"]

MODES = ("continue", "rewrite", "expand")
MIN_SELECTION = 5
TARGET_MIN, TARGET_MAX, TARGET_DEFAULT = 100, 800, 400
MAX_INSTRUCTION = 500

# 单进程部署假设（与 `_RUNS` 一致）：同 token 并发请求等待首个请求完成，
# 避免跨标签页重复点击触发两次 LLM 与两次扣费。
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}
_SLOT_LOCK = threading.Lock()
_ACTIVE: dict[str, int] = {}
_INLINE_SLOT_LIMIT = 2


class InlineError(Exception):
    """内联写作业务异常（路由层转换为 HTTP 状态码 + code）。"""

    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def parse_time(text: str) -> float:
    """ISO 时间 → epoch 秒（供路由/测试复用）。"""
    from novel_gen.store.inline_drafts import _parse_iso
    return _parse_iso(text)


# ---------- 开关 / 环境 ----------

def _flag(key: str, default: Any) -> Any:
    """读 sys_config 开关（未配行时用默认值；解析失败降级默认，不阻塞）。"""
    try:
        row = config_db.get_config(key)
    except Exception:
        logger.warning("读取内联开关失败，使用默认值: %s", key)
        return default
    if not row:
        return default
    try:
        return config_db.coerce_config_type(row["config_type"], row["config_value"])
    except Exception:
        logger.warning("内联开关值非法，使用默认值: %s=%r", key, row.get("config_value"))
        return default


def _inline_enabled() -> bool:
    return bool(_flag("editor_inline_enabled", True))


def _project_busy(project_id: str) -> bool:
    with _LOCK:
        handle = _RUNS.get(project_id)
        return bool(handle and handle.thread and handle.thread.is_alive())


def _balance(uid: int) -> float:
    try:
        return float((db.get_user(uid) or {}).get("balance") or 0.0)
    except Exception:
        logger.exception("读取余额失败: uid=%s", uid)
        return 0.0


def _daily_used(uid: int) -> int:
    """用户当日 inline 调用次数：汇总其各项目 settle.json 当日 `inline:*` 条目。

    不新增状态文件；账本淘汰可能少计，作为频控的近似值足够（不用于计费）。
    """
    try:
        pids = db.list_user_projects(uid) or []
    except Exception:
        logger.warning("读取用户项目列表失败，跳过日频控: uid=%s", uid)
        return 0
    today = time.localtime()[:3] + (0, 0, 0, 0, 0, -1)
    midnight = time.mktime(today)
    total = 0
    for pid in list(pids)[:200]:
        path = PROJECTS_DIR / str(pid) / "settle.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        for key, entry in (data.get("entries") or {}).items():
            if str(key).startswith("inline:") and int((entry or {}).get("ts") or 0) >= midnight:
                total += 1
    return total


class _token_guard:
    """同 token 单飞：首个请求执行，其余等待并回放首个请求的结果。"""

    def __init__(self, token: str):
        self.token = token
        self.owner = False
        self.event: Optional[threading.Event] = None

    def __enter__(self):
        with _INFLIGHT_LOCK:
            event = _INFLIGHT.get(self.token)
            if event is None:
                event = threading.Event()
                _INFLIGHT[self.token] = event
                self.owner = True
            self.event = event
        return self

    def wait(self, timeout: float = 90.0) -> None:
        if self.event is not None and not self.owner:
            self.event.wait(timeout)

    def __exit__(self, *exc):
        if self.owner and self.event is not None:
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(self.token, None)
            self.event.set()
        return False


def _acquire_slot(project_id: str) -> bool:
    with _SLOT_LOCK:
        if _ACTIVE.get(project_id, 0) >= _INLINE_SLOT_LIMIT:
            return False
        _ACTIVE[project_id] = _ACTIVE.get(project_id, 0) + 1
        return True


def _release_slot(project_id: str) -> None:
    with _SLOT_LOCK:
        left = _ACTIVE.get(project_id, 0) - 1
        if left > 0:
            _ACTIVE[project_id] = left
        else:
            _ACTIVE.pop(project_id, None)


# ---------- 入参 ----------

def _parse_generate(payload: dict, chapter_content: str) -> dict:
    if not isinstance(payload, dict):
        raise InlineError(400, "BAD_ANCHOR", "请求体格式不正确")
    mode = str(payload.get("mode") or "continue").strip().lower()
    if mode not in MODES:
        raise InlineError(400, "BAD_ANCHOR", f"不支持的模式：{mode}")
    anchor = payload.get("anchor") or {}
    try:
        start, end = int(anchor.get("start")), int(anchor.get("end"))
    except (TypeError, ValueError):
        raise InlineError(400, "BAD_ANCHOR", "anchor.start / anchor.end 必须是整数")
    text = normalize_text(chapter_content)
    if not (0 <= start <= end <= len(text)):
        raise InlineError(400, "BAD_ANCHOR", f"锚点越界：0 ≤ start ≤ end ≤ {len(text)}")
    if mode == "continue":
        end = start  # 续写是零宽插入：以光标点为插入位置
    elif end - start < MIN_SELECTION:
        raise InlineError(400, "BAD_ANCHOR", f"建议选中整句（≥{MIN_SELECTION} 字）后再{ '改写' if mode == 'rewrite' else '扩写'}")
    instruction = str(payload.get("instruction") or "").strip()
    if len(instruction) > MAX_INSTRUCTION:
        raise InlineError(400, "BAD_INSTRUCTION", f"指令最多 {MAX_INSTRUCTION} 字")
    try:
        candidates = int(payload.get("candidates") or _flag("inline_candidates_default", 2))
    except (TypeError, ValueError):
        candidates = 2
    candidates = max(2, min(3, candidates))
    try:
        target_words = int(payload.get("target_words") or TARGET_DEFAULT)
    except (TypeError, ValueError):
        target_words = TARGET_DEFAULT
    target_words = max(TARGET_MIN, min(TARGET_MAX, target_words))
    try:
        base_version = int(payload.get("base_version"))
    except (TypeError, ValueError):
        raise InlineError(409, "STALE_BASE", "缺少合法的 base_version，请刷新正文后重试")
    base_hash = str(payload.get("base_hash") or "")
    if not base_hash:
        raise InlineError(409, "STALE_BASE", "缺少 base_hash，请刷新正文后重试")
    token = str(payload.get("client_token") or "").strip()
    if not token or len(token) > 100:
        raise InlineError(400, "BAD_INSTRUCTION", "缺少合法的 client_token")
    return {"mode": mode, "anchor": {"start": start, "end": end},
            "base_version": base_version, "base_hash": base_hash,
            "instruction": instruction, "candidates": candidates,
            "target_words": target_words, "client_token": token}


def _parse_adopt(payload: dict, ch_no: int) -> dict:
    if not isinstance(payload, dict):
        raise InlineError(400, "BAD_ANCHOR", "请求体格式不正确")
    gen_id = str(payload.get("gen_id") or "").strip()
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not gen_id or not candidate_id:
        raise InlineError(400, "BAD_ANCHOR", "缺少 gen_id / candidate_id")
    try:
        base_version = int(payload.get("base_version"))
    except (TypeError, ValueError):
        raise InlineError(409, "STALE_BASE_KEEP_CANDIDATES", "缺少合法的 base_version")
    base_hash = str(payload.get("base_hash") or "")
    token = str(payload.get("client_token") or "").strip()
    return {"gen_id": gen_id, "candidate_id": candidate_id, "ch": int(ch_no),
            "base_version": base_version, "base_hash": base_hash, "client_token": token}


# ---------- 生成 ----------

def _response(item: dict, *, replayed: bool, balance: float = 0.0) -> dict:
    return {
        "gen_id": item.get("gen_id"),
        "candidates": [{"id": c.get("id"), "text": c.get("text")}
                       for c in (item.get("candidates") or [])],
        "words": int(item.get("words") or 0),
        "charged": float(item.get("charged") or 0.0),
        "balance": balance,
        "expires_at": item.get("expires_at"),
        "anchor": item.get("anchor"),
        "mode": item.get("mode"),
        "base_version": item.get("base_version"),
        "base_hash": item.get("base_hash"),
        "replayed": replayed,
    }


def _replay_or_expired(store, token: str, uid: int) -> dict:
    prior = find_by_token(store, token)
    if not prior:
        raise InlineError(409, "INLINE_BUSY", "该次生成正在进行，请稍后重试")
    item = find_by_gen_id(store, prior.get("gen_id"))
    if item is None:
        raise InlineError(410, "GEN_EXPIRED", "该次生成记录已过期（超过 48 小时），请重新生成")
    if str(item.get("kind") or "inline") != "inline":
        raise InlineError(409, "TOKEN_CONFLICT", "该 client_token 已用于剧情走向生成，请换一个")
    return _response(item, replayed=True, balance=_balance(uid))


def generate(store, llm, *, project_id: str, uid: int, payload: dict,
             request_id: str = "") -> dict:
    """生成内联候选（生成即计费，未采纳也计费）。"""
    if not _inline_enabled():
        raise InlineError(403, "INLINE_DISABLED", "内联写作未开启")
    cfg = store.load_config()
    apply_global_overrides(cfg)
    ch_no = int(payload.get("ch") or 0)
    chapter = store.load_chapter(ch_no) if ch_no else None
    if chapter is None:
        raise InlineError(404, "CHAPTER_NOT_FOUND", f"章节 {ch_no} 未生成")
    req = _parse_generate(payload, chapter.content)
    req["ch"] = ch_no

    # 幂等：同 token 先查记录（跨请求/重放不调 LLM、不扣费）
    if find_by_token(store, req["client_token"]):
        return _replay_or_expired(store, req["client_token"], uid)

    if _project_busy(project_id):
        raise InlineError(409, "PROJECT_BUSY", "整章生成中，稍后再试")
    cur_v = current_version(store, ch_no)
    if cur_v != req["base_version"] or content_hash(chapter.content) != req["base_hash"]:
        raise InlineError(409, "STALE_BASE", "正文已变化，请刷新后重新定位")
    limit = int(_flag("inline_daily_limit", 200) or 200)
    if limit > 0 and _daily_used(uid) >= limit:
        raise InlineError(429, "RATE_LIMITED", f"今日内联写作次数已达上限（{limit} 次）")

    from .core_settle import _settle_billing_mode
    mode = _settle_billing_mode(store)
    if mode == "platform" and _balance(uid) <= 0:
        raise InlineError(402, "INSUFFICIENT_BALANCE", "余额不足：内联写作按生成字数计费，请充值后再用")

    if not _acquire_slot(project_id):
        raise InlineError(429, "INLINE_BUSY", "该项目内联生成过多，请稍后重试")
    guard = _token_guard(req["client_token"])
    try:
        with guard:
            if not guard.owner:
                guard.wait()
                return _replay_or_expired(store, req["client_token"], uid)
            return _generate_worker(store, llm, project_id=project_id, uid=uid,
                                    cfg=cfg, ch_no=ch_no, req=req, mode=mode,
                                    request_id=request_id)
    finally:
        _release_slot(project_id)


def _generate_worker(store, llm, *, project_id, uid, cfg, ch_no, req, mode,
                     request_id) -> dict:
    """真正的 LLM 调用 + 计费 + 落记录（调用方已持 token 锁与并发槽）。"""
    system, user = build_prompt(store, cfg, store.load_chapter(ch_no), req)
    candidates: list[dict] = []
    for attempt in range(2):
        try:
            raw = llm.chat(system, user, temperature=0.85, max_tokens=max_tokens(req))
        except Exception as exc:
            logger.exception("内联生成 LLM 调用失败: project=%s ch=%s req=%s",
                             project_id, ch_no, request_id)
            raise InlineError(500, "LLM_FAILED", f"模型调用失败：{type(exc).__name__}")
        candidates = parse_candidates(raw, req["candidates"])
        if candidates:
            break
        logger.warning("内联候选为空/无效，重试一次: project=%s ch=%s attempt=%d",
                       project_id, ch_no, attempt + 1)
    if not candidates:
        raise InlineError(422, "LLM_EMPTY", "模型未产出可用候选，本次不计费，请重试")
    joined = "\n\n".join(c["text"] for c in candidates)
    hits = basic_content_check(joined)
    if hits:
        logger.warning("内联候选命中合规拦截（不计费）: project=%s ch=%s hits=%s",
                       project_id, ch_no, hits[:3])
        raise InlineError(422, "COMPLIANCE_BLOCKED", "生成内容未通过合规校验，本次不计费")

    words = sum(count_words("chapter", {"content": c["text"]}) for c in candidates)
    gen_id = new_gen_id()
    try:
        settled = settle_product(store, "inline", gen_id, words, mode=mode,
                                 project_id=project_id, content_sha=content_digest(joined))
    except Exception:
        logger.exception("内联结算失败: project=%s gen=%s", project_id, gen_id)
        raise InlineError(500, "SETTLE_FAILED", "计费失败，未返回候选，请重试")
    if not settled:
        raise InlineError(500, "SETTLE_FAILED", "计费失败，未返回候选，请重试")

    now = time.time()
    item = {
        "gen_id": gen_id,
        "client_token": req["client_token"],
        "kind": "inline",
        "ch": ch_no,
        "mode": req["mode"],
        "anchor": req["anchor"],
        "base_version": req["base_version"],
        "base_hash": req["base_hash"],
        "instruction": req["instruction"],
        "candidates": candidates,
        "words": words,
        "charged": float(settled.get("charged") or 0.0),
        "charge_entry": settled.get("entry") or "",
        "adopted": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + RETENTION_SECONDS)),
    }
    try:
        # 同章单飞：新批次成功后才作废旧批次（失败不牵连上一批已付费候选）
        invalidate_chapter(store, ch_no)
        item, _ = upsert_generation(store, item)
    except Exception:
        logger.exception("内联生成记录落盘失败: project=%s gen=%s", project_id, gen_id)
        raise InlineError(500, "WRITE_FAILED", "生成记录写入失败，请重试")
    logger.info("内联生成: project=%s ch=%d gen=%s mode=%s words=%d charged=%.2f req=%s",
                project_id, ch_no, gen_id, req["mode"], words,
                float(settled.get("charged") or 0.0), request_id)
    return _response(item, replayed=False, balance=_balance(uid))


# ---------- 采纳 ----------

def adopt(store, *, project_id: str, uid: int, ch_no: int, payload: dict) -> dict:
    """人工采纳候选：一次写入版本，不二次计费（生成阶段已计费）。"""
    req = _parse_adopt(payload, ch_no)
    item = find_by_gen_id(store, req["gen_id"])
    if item is None:
        raise InlineError(410, "GEN_EXPIRED", "候选不存在或已过期，请重新生成")
    if int(item.get("ch") or 0) != int(ch_no):
        raise InlineError(400, "BAD_ANCHOR", "候选与章节不匹配")
    if item.get("invalidated_at"):
        raise InlineError(410, "GEN_EXPIRED", "该批候选已被同章新生成作废，请使用最新一批")
    if parse_time(item.get("expires_at")) <= time.time():
        raise InlineError(410, "GEN_EXPIRED", "候选已过期（超过 48 小时），请重新生成")

    adopted = item.get("adopted")
    if isinstance(adopted, dict):
        if req["client_token"] and adopted.get("client_token") == req["client_token"]:
            return _adopt_response(store, ch_no, adopted.get("version"), uid, replayed=True)
        raise InlineError(409, "ALREADY_ADOPTED", "该批候选已被采纳，请勿重复提交")

    if _project_busy(project_id):
        raise InlineError(409, "PROJECT_BUSY", "整章生成中，稍后再试")
    from .core import _is_locked
    if _is_locked(store, "chapter", ch_no):
        raise InlineError(409, "CHAPTER_LOCKED", "本章已被锁定，请先解锁再采纳")
    candidate = next((c for c in (item.get("candidates") or [])
                      if c.get("id") == req["candidate_id"]), None)
    if candidate is None:
        raise InlineError(400, "BAD_ANCHOR", "候选不存在，请重新生成")

    anchor = item.get("anchor") or {}
    try:
        result = apply_inline_text(
            store, ch_no, int(anchor.get("start")), int(anchor.get("end")),
            str(candidate.get("text") or ""),
            base_version=req["base_version"], base_hash=req["base_hash"],
            label=f"inline:adopt:{req['gen_id']}")
    except StaleBaseError as exc:
        raise InlineError(409, "STALE_BASE_KEEP_CANDIDATES", exc.detail)
    except Exception as exc:
        from novel_gen.store.inline_edit import InlineEditError
        if isinstance(exc, InlineEditError):
            raise InlineError(exc.status_code, exc.code, exc.detail)
        logger.exception("内联采纳落盘失败: project=%s ch=%s gen=%s",
                         project_id, ch_no, req["gen_id"])
        raise InlineError(500, "WRITE_FAILED", "正文写入失败，请重试")

    mark_adopted(store, req["gen_id"], candidate_id=req["candidate_id"],
                 version=result["version"], client_token=req["client_token"])
    logger.info("内联采纳: project=%s ch=%d gen=%s cand=%s v=%d words=%d",
                project_id, ch_no, req["gen_id"], req["candidate_id"],
                result["version"], result["words"])
    return _adopt_response(store, ch_no, result["version"], uid, replayed=False)


def _adopt_response(store, ch_no: int, version, uid: int, *, replayed: bool) -> dict:
    chapter = store.load_chapter(ch_no)
    return {
        "version": int(version or 0),
        "selected": True,
        "replayed": replayed,
        "balance": _balance(uid),
        "chapter": {
            "no": ch_no,
            "title": chapter.title if chapter else "",
            "content": chapter.content if chapter else "",
            "source": chapter.source if chapter else "",
            "summary_stale": bool(chapter.summary_stale) if chapter else False,
        },
    }


# ---------- 只读 ----------

def estimate(store, *, project_id: str, chars: int, candidates: int) -> dict:
    """触发前预估（只读）：按「单候选字数 × 候选数」估字数与金额。"""
    chars = max(TARGET_MIN, min(TARGET_MAX, int(chars or TARGET_DEFAULT)))
    candidates = max(2, min(3, int(candidates or 2)))
    words = chars * candidates
    discount = db.project_discount(project_id)
    price = round_money(agent_price(price_for(words), discount))
    return {"words": words, "price": price, "discount": discount,
            "chars_per_candidate": chars, "candidates": candidates}


def list_drafts(store, ch_no: int) -> list[dict]:
    """该章 48h 内未采纳、未作废的候选记录（重开面板不重复计费）。"""
    out = []
    for item in list_for_chapter(store, ch_no, kind="inline"):
        out.append({
            "gen_id": item.get("gen_id"),
            "mode": item.get("mode"),
            "anchor": item.get("anchor"),
            "base_version": item.get("base_version"),
            "base_hash": item.get("base_hash"),
            "candidates": [{"id": c.get("id"), "text": c.get("text")}
                           for c in (item.get("candidates") or [])],
            "words": int(item.get("words") or 0),
            "charged": float(item.get("charged") or 0.0),
            "created_at": item.get("created_at"),
            "expires_at": item.get("expires_at"),
        })
    return out
