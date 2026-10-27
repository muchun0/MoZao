# -*- coding: utf-8 -*-
"""v2 版本级结算账本（P0 资金链路，TECH-Agent用户化与版本化 §4.4）。

设计要点（决策见 docs/decisions.md 2026-10-25 计费反转）：
1. **计费单元 = 单个产物版本**：一个新增版本结算一次，按该版本 AI 实际生成字数扣费；
   批量 N 章 = N 个版本 = N 次结算（替代旧「全书累计字数差 + 封顶」口径）。
2. **幂等键 `{kind}:{key}:v{v}`**：同一版本重复结算（重启/重入/并发重试）只记一次，绝不双扣。
3. **按模式分流**：`user_llm`（自备 Key）应付 0、`charged=0`，仍写 entries 供审计；
   `platform` 按 `price_for → agent_price → round_money` 计费，余额不足只扣到 0 并记 `debt.json`。
4. **先落账本再扣费**（AGENTS §11.7）：写入用临时文件 + `os.replace`（Windows 文件锁重试），
   崩溃窗口宁可漏扣也不重扣；差额记欠费不自动补扣。
5. **v1 基线只读迁移**：旧 `settle.json` 的 `settled_ai_words` / `settled_price` 转成
   `legacy_ai_words` / `legacy_billed_price`，迁移过程不扣费。
6. **outline / world / volume 暂无版本库**：0.5 阶段用 `settle_product`（内容摘要 + 序号自增）
   临时幂等分配版本号；1A 落地真实版本库后改传真实版本号。

账本文件：项目根 `settle.json`。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from novel_gen.store.wordcount import count_words

from . import db
from .logger import get_logger
from .pricing import price_for
from .referral import agent_price, round_money

__all__ = [
    "LEDGER_MODE",
    "billing_mode_of",
    "content_digest",
    "has_v2_ledger",
    "load_ledger",
    "migrate_v1",
    "read_raw",
    "settle_chapter_versions",
    "settle_legacy",
    "settle_product",
    "settle_version",
    "should_stop_now",
    "snapshot_chapter_versions",
    "version_charge_map",
]

logger = get_logger("settle_ledger")

LEDGER_MODE = "version_ledger"
_LEDGER_FILE = "settle.json"
_DEBT_FILE = "debt.json"
_MAX_ENTRIES = 500          # 账本条目上限（超出按插入顺序淘汰最旧，TECH §4.4.2）
_FREE_SOURCES = {"user", "legacy"}  # 人工编辑不计费；legacy=升级前已扣过费的存量版本，绝不补扣
_MODE_USER_LLM = "user_llm"
_MODE_PLATFORM = "platform"


# ---------- 账本读写 ----------

def _ledger_path(store) -> Path:
    return store.dir / _LEDGER_FILE


def read_raw(store) -> Optional[dict]:
    """读 `settle.json` 原始内容。

    Returns:
        None：文件不存在；dict：解析结果（损坏时返回 `{"mode": "corrupt"}` 并留痕）。
    """
    p = _ledger_path(store)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {"mode": "corrupt"}
    except Exception:
        logger.exception("结算账本解析失败（按已结算处理，不重扣）: project=%s", store.dir.name)
        return {"mode": "corrupt"}


def has_v2_ledger(store) -> bool:
    raw = read_raw(store)
    return bool(raw and raw.get("mode") == LEDGER_MODE)


def _fresh_ledger() -> dict:
    return {
        "mode": LEDGER_MODE,
        "entries": {},
        "ai_words_total": 0,
        "billed_price": 0.0,
        "legacy_ai_words": 0,
        "legacy_billed_price": 0.0,
    }


def _legacy_from_raw(raw: Optional[dict]) -> tuple[int, float]:
    """从 v1 账本提取已结算基线（v1 各状态字段名不同，统一兜底）。"""
    if not raw:
        return 0, 0.0
    ai = raw.get("settled_ai_words")
    if ai is None:
        ai = raw.get("ai_words")
    price = raw.get("settled_price")
    if price is None:
        price = raw.get("final_price")
    try:
        return max(0, int(ai or 0)), max(0.0, float(price or 0.0))
    except (TypeError, ValueError):
        return 0, 0.0


def load_ledger(store) -> dict:
    """读账本（统一返回 v2 结构）。

    v1 / 损坏 / 不存在 → 返回**内存态** v2（保留 legacy 基线），不写盘、不扣费；
    真正落盘发生在下一次结算（先落账本再扣费）。读盘失败不能阻断结算链路。
    """
    raw = read_raw(store)
    if raw and raw.get("mode") == LEDGER_MODE:
        data = _fresh_ledger()
        data["entries"] = dict(raw.get("entries") or {})
        data["ai_words_total"] = int(raw.get("ai_words_total") or 0)
        data["billed_price"] = float(raw.get("billed_price") or 0.0)
        data["legacy_ai_words"] = int(raw.get("legacy_ai_words") or 0)
        data["legacy_billed_price"] = float(raw.get("legacy_billed_price") or 0.0)
        return data
    legacy_ai, legacy_price = _legacy_from_raw(raw)
    data = _fresh_ledger()
    data["legacy_ai_words"] = legacy_ai
    data["legacy_billed_price"] = legacy_price
    return data


def migrate_v1(store) -> dict:
    """把 v1 账本迁移为 v2 并落盘（只迁移基线，不扣费）。已是 v2 则直接返回。"""
    data = load_ledger(store)
    if not has_v2_ledger(store) and read_raw(store) is not None:
        _write_ledger(store, data)
    return data


def _atomic_write_json(path: Path, data: Any) -> None:
    """原子写 JSON：临时文件 + `os.replace`；Windows 文件锁失败重试后直写回退。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    for _ in range(3):
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    path.write_text(text, encoding="utf-8")


def _write_ledger(store, data: dict) -> None:
    data["mode"] = LEDGER_MODE
    data["updated_at"] = int(time.time())
    entries = data.get("entries") or {}
    # 超上限淘汰最旧（dict 保序，最旧即第一个键）
    while len(entries) > _MAX_ENTRIES:
        entries.pop(next(iter(entries)))
    _atomic_write_json(_ledger_path(store), data)


# ---------- 通用工具 ----------

def content_digest(text: str) -> str:
    """内容摘要（sha256），用于无版本库产物的「同内容不重复收费」判定。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def billing_mode_of(use_user_llm: bool) -> str:
    """把「是否用用户自备 Key」映射为计费模式。"""
    return _MODE_USER_LLM if use_user_llm else _MODE_PLATFORM


def _entry_key(kind: str, key: str, v: int) -> str:
    return f"{kind}:{key}:v{int(v)}"


def _entry_version(entry_key: str) -> int:
    try:
        return int(entry_key.rsplit(":v", 1)[1])
    except (IndexError, ValueError):
        return 0


def _balance(uid: int) -> float:
    return float((db.get_user(uid) or {}).get("balance") or 0.0)


def should_stop_now(store, mode: str) -> bool:
    """平台付费项目余额 ≤ 0 时应停止后续生成；BYOK 项目永不因平台余额停止。"""
    if mode != _MODE_PLATFORM:
        return False
    uid = db.project_owner(store.dir.name)
    if uid is None:
        return False
    try:
        return _balance(uid) <= 0
    except Exception:
        logger.exception("读取余额失败，按不停止处理: project=%s", store.dir.name)
        return False


# ---------- 结算核心 ----------

def settle_version(store, kind: str, key: str, v: int, words: int, *,
                   mode: str, project_id: str,
                   content_sha: str = "") -> dict:
    """按「产物版本」结算一次（幂等）。

    Args:
        store: 项目存储。
        kind: 产物类型（chapter / outline / world / volume / legacy）。
        key: 产物键（章节号 / all / 卷号）。
        v: 版本号（幂等键组成部分）。
        words: 该版本 AI 实际生成字数（`<=0` 直接跳过，不写账本）。
        mode: `platform` 或 `user_llm`。
        project_id: 项目 ID（用于取折扣与落项目累计金额）。
        content_sha: 内容摘要（审计用，可选）。

    Returns:
        dict：`charged / due / balance / billed_price / duplicate / should_stop`。
    """
    if mode not in (_MODE_PLATFORM, _MODE_USER_LLM):
        raise ValueError(f"未知计费模式: {mode}")
    ek = _entry_key(kind, key, v)
    if words <= 0:
        # 无 AI 文本（如用户手写）不计费，也不写账本
        return {"charged": 0.0, "due": 0.0, "balance": 0.0, "billed_price": 0.0,
                "duplicate": False, "should_stop": False, "entry": ek}

    ledger = load_ledger(store)
    if ek in (ledger.get("entries") or {}):
        bal = _safe_balance(store)
        return {"charged": 0.0, "due": 0.0, "balance": bal,
                "billed_price": float(ledger.get("billed_price") or 0.0),
                "duplicate": True, "should_stop": should_stop_now(store, mode), "entry": ek}

    due = 0.0
    if mode == _MODE_PLATFORM:
        discount = db.project_discount(project_id)
        due = round_money(agent_price(price_for(words), discount))  # 统一高质量单价
    return _book_and_charge(store, entry_key=ek, words=words, due=due, mode=mode,
                            project_id=project_id, content_sha=content_sha)


def settle_product(store, kind: str, key: str, words: int, *,
                   mode: str, project_id: str,
                   content_sha: str = "") -> dict:
    """无版本库产物的结算通道（0.5 临时方案）。

    幂等策略：同 `(kind, key)` 最近一次结算的 `content_sha` 与本次相同 → 视为同一产物，
    不重复收费；否则版本号自增（`max(v)+1`）。1A 落地真实版本库后改传真实版本号。
    """
    ledger = load_ledger(store)
    prefix = f"{kind}:{key}:v"
    existing = {ek: e for ek, e in (ledger.get("entries") or {}).items()
                if ek.startswith(prefix)}
    if existing:
        last_ek = max(existing, key=_entry_version)
        last_sha = (existing[last_ek] or {}).get("content_sha") or ""
        if content_sha and last_sha and content_sha == last_sha:
            return {"charged": 0.0, "due": 0.0, "balance": _safe_balance(store),
                    "billed_price": float(ledger.get("billed_price") or 0.0),
                    "duplicate": True, "should_stop": should_stop_now(store, mode),
                    "entry": last_ek}
        v = _entry_version(last_ek) + 1
    else:
        v = 1
    return settle_version(store, kind, key, v, words, mode=mode,
                          project_id=project_id, content_sha=content_sha)


def settle_legacy(store, *, ai_words: int, due_words: int, price: float,
                  mode: str, project_id: str) -> dict:
    """存量项目一次性兜底结算（仅 `_settle_price` 在「无任何账本」时调用）。"""
    del due_words  # 旧口径的封顶字数只用于算价，账本只记实际 AI 字数
    return _book_and_charge(store, entry_key="legacy:all:v1", words=int(ai_words),
                            due=float(price), mode=mode, project_id=project_id,
                            content_sha="legacy")


# ---------- 章节版本结算 ----------

def snapshot_chapter_versions(store, no: int) -> set[int]:
    """写章前快照：记录该章当前已有版本号集合。"""
    rec = (store.load_chapter_versions() or {}).get(str(no)) or {}
    return {v for v in rec.get("versions") or [] if isinstance(v, int)}


def settle_chapter_versions(store, no: int, before: set[int], *,
                            mode: str, project_id: str) -> list[dict]:
    """结算「写章前快照之后新增的版本」。

    只结算新增版本，因此：`save_chapter` 幂等返回 None（内容未变）不扣费；
    存量 legacy 版本（升级前已扣费）不会被补扣；候选版本（`promote=False`）照样计费。
    """
    rec = (store.load_chapter_versions() or {}).get(str(no)) or {}
    history = {h.get("v"): h for h in (rec.get("history") or []) if isinstance(h, dict)}
    new_versions = sorted(set(rec.get("versions") or []) - set(before))
    results: list[dict] = []
    for v in new_versions:
        source = (history.get(v) or {}).get("source") or "ai"
        if source in _FREE_SOURCES:
            continue
        content = _read_version_content(store, no, v)
        words = count_words("chapter", {"content": content})
        if words <= 0:
            logger.warning("章节版本无正文，跳过结算: project=%s no=%d v=%d", project_id, no, v)
            continue
        results.append({**settle_version(store, "chapter", str(no), v, words, mode=mode,
                                         project_id=project_id,
                                         content_sha=content_digest(content)),
                        "words": words})
    return results


def _read_version_content(store, no: int, v: int) -> str:
    """读版本存档正文；文件缺失（极端淘汰竞态）返回空串并留痕，不阻断结算链路。"""
    p = store.chapter_version_path(no, v)
    if not p.exists():
        logger.warning("版本存档缺失，按 0 字处理: project=%s no=%d v=%d", store.dir.name, no, v)
        return ""
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        logger.exception("版本存档解析失败: project=%s no=%d v=%d", store.dir.name, no, v)
        return ""
    return (d.get("content") or "") if isinstance(d, dict) else ""


# ---------- 落账 + 扣费 ----------

def _safe_balance(store) -> float:
    uid = db.project_owner(store.dir.name)
    if uid is None:
        return 0.0
    try:
        return _balance(uid)
    except Exception:
        logger.exception("读取余额失败: project=%s", store.dir.name)
        return 0.0


def _book_and_charge(store, *, entry_key: str, words: int, due: float, mode: str,
                     project_id: str, content_sha: str) -> dict:
    """写账本条目 → 扣费 → 返佣 → 欠费记录 → 回写项目累计金额。"""
    uid = db.project_owner(project_id)
    if uid is None:
        # 无归属用户（后台/异常数据）：只记账不扣费，避免资金流向不明
        logger.warning("项目无归属用户，仅记账不扣费: project=%s entry=%s", project_id, entry_key)
    title = _project_title(store)
    ledger = load_ledger(store)
    entries = ledger.setdefault("entries", {})
    balance = _balance(uid) if uid is not None else 0.0
    actual = min(balance, due) if due > 0 else 0.0
    actual = round_money(max(0.0, actual))

    entries[entry_key] = {
        "words": int(words),
        "due": round_money(due),
        "charged": actual,
        "mode": mode,
        "content_sha": content_sha,
        "ts": int(time.time()),
        "uid": uid,
    }
    ledger["ai_words_total"] = int(ledger.get("ai_words_total") or 0) + int(words)
    ledger["billed_price"] = round_money(float(ledger.get("billed_price") or 0.0) + actual)

    # 1) 先落账本（崩溃后重跑命中幂等键，不重扣）
    _write_ledger(store, ledger)

    # 2) 再扣费
    if uid is not None and actual > 0:
        _charge(uid, actual, title, words, entry_key, project_id)
    # 3) 返佣（消费返佣，失败只留痕不阻断）
    if uid is not None and actual > 0:
        _grant_commission(uid, project_id, actual, title)
    # 4) 欠费（余额不足的差额，不自动补扣）
    if due > actual:
        _record_debt(store, entry_key, words, due, actual)

    # 5) 回写项目累计金额（前端/后台展示）
    try:
        db.update_project_price(project_id, ledger["billed_price"])
    except Exception:
        logger.exception("回写项目累计金额失败: project=%s", project_id)

    new_balance = _balance(uid) if uid is not None else balance
    logger.info("版本结算: project=%s entry=%s words=%d due=%.2f charged=%.2f mode=%s uid=%s",
                project_id, entry_key, words, due, actual, mode, uid)
    return {
        "charged": actual,
        "due": round_money(due),
        "balance": new_balance,
        "billed_price": ledger["billed_price"],
        "duplicate": False,
        "should_stop": mode == _MODE_PLATFORM and new_balance <= 0 and due > 0,
        "entry": entry_key,
    }


def _project_title(store) -> str:
    try:
        return store.load_config().effective_title()
    except Exception:
        return store.dir.name


def _charge(uid: int, actual: float, title: str, words: int, entry_key: str,
            project_id: str) -> None:
    try:
        db.change_balance(
            uid, -actual,
            f"《{title}》版本结算 {entry_key}：AI 生成 {words} 字，扣 {actual:.2f} 元",
        )
    except Exception:
        # 核心路径异常必须留痕；账本已落盘，重启不会重扣（宁可漏扣不重扣）
        logger.exception("扣费失败（账本已落盘，不重扣）: project=%s uid=%d entry=%s",
                         project_id, uid, entry_key)


def _grant_commission(uid: int, project_id: str, actual: float, title: str) -> None:
    """消费返佣。延迟导入避免 core ↔ settle_ledger 模块级循环依赖。"""
    try:
        from .core import _grant_consume_commission
        _grant_consume_commission(uid, project_id, actual, title)
    except Exception:
        logger.exception("消费返佣失败（不影响结算）: project=%s uid=%d", project_id, uid)


def _record_debt(store, entry_key: str, words: int, due: float, charged: float) -> None:
    """记录欠费差额（不自动补扣，站长后台处理）。"""
    p = store.dir / _DEBT_FILE
    data: dict = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8-sig"))
            data = raw if isinstance(raw, dict) else {}
        except Exception:
            data = {}
    owe = round_money(float(data.get("owe") or 0.0) + (due - charged))
    items = list(data.get("entries") or [])
    items.append({"entry": entry_key, "words": int(words),
                  "due": round_money(due), "charged": round_money(charged),
                  "ts": int(time.time())})
    data.update({"owe": owe, "entries": items})
    try:
        _atomic_write_json(p, data)
        logger.warning("余额不足记欠费: project=%s entry=%s due=%.2f charged=%.2f owe=%.2f",
                       store.dir.name, entry_key, due, charged, owe)
    except Exception:
        logger.exception("欠费记录写入失败: project=%s entry=%s", store.dir.name, entry_key)


# ---------- 版本金额反查（阶段 1C：版本弹窗展示每版计费字数与金额，TECH §10.2） ----------

def version_charge_map(store, kind: str, key: str) -> dict[int, dict]:
    """按版本号反查结算金额：{v: {charged, due, mode}}。

    幂等键格式 `{kind}:{key}:v{v}`（§4.4.2），章节/大纲/世界观产物通用；
    无账本或无对应条目返回空表——只读查询，任何异常都不抛给上层（展示降级为不显示金额）。
    """
    prefix = f"{kind}:{key}:v"
    out: dict[int, dict] = {}
    try:
        ledger = load_ledger(store)
        for entry_key, entry in (ledger.get("entries") or {}).items():
            if not isinstance(entry, dict) or not str(entry_key).startswith(prefix):
                continue
            v = _entry_version(str(entry_key))
            if v < 1:  # 版本号从 1 起；解析失败（0）跳过
                continue
            out[v] = {"charged": float(entry.get("charged") or 0.0),
                      "due": float(entry.get("due") or 0.0),
                      "mode": str(entry.get("mode") or "")}
    except Exception:
        logger.exception("版本金额反查失败（降级为空表）: project=%s kind=%s key=%s",
                         store.dir.name, kind, key)
    return out
