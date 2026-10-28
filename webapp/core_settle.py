# -*- coding: utf-8 -*-
"""
墨造核心层·结算：统一结算计算、版本提交结算、存量兜底与消费返佣。

从 core.py 拆出（业务域拆分，行为零变化）。本模块只承载「计费/结算」一个内聚概念，
是资金链路的权威入口（P0），扣费一律经 settle_ledger（先落账本再扣费，幂等不重扣）。
"""
from __future__ import annotations

import json
from typing import Optional

from novel_gen.store.project import ProjectStore
from novel_gen.store.wordcount import count_words

from . import db
from . import settle_ledger
from .logger import get_logger
from .pricing import price_for
from .referral import COMMISSION_BASE, COMMISSION_RATE, agent_price, round_money

logger = get_logger("server")

# monkeypatch 接缝说明：测试通过 `monkeypatch.setattr(webapp.core, "_project_use_user_llm",
# ...)` 补丁门面属性；因此 _compute_settle/_settle_billing_mode/_settle_incremental 内对
# `_project_use_user_llm` 的引用一律经延迟 `from . import core` + `core._project_use_user_llm()`
# 解析（与 generate_service 既有模式一致），保证补丁在调用时命中门面。


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
    计费已统一高质量单价（普通模式下线），price 不再区分模式；`hq` 固定 True 仅供兼容返回。
    """
    from . import core  # 延迟门面引用：保留 _project_use_user_llm 的 monkeypatch 接缝
    user_words = store.user_words()
    chapters = store.list_chapters()
    total_words = sum(len(c.content or "") for c in chapters)
    ai_words = max(0, total_words - user_words)
    target_total = store.load_config().total_words or 0
    target_ai_words = max(0, target_total - user_words)
    billable_words = min(ai_words, target_ai_words) if target_ai_words > 0 else ai_words
    discount = db.project_discount(store.dir.name)
    price = round_money(agent_price(price_for(billable_words), discount))
    return {
        "ai_words": ai_words,
        "billable_words": billable_words,
        "target_ai_words": target_ai_words,
        "price": price,
        "hq": True,
        "discount": discount,
        "use_user_llm": core._project_use_user_llm(store),
    }


def _settle_billing_mode(store: ProjectStore) -> str:
    """计费模式：自备 Key → `user_llm`（平台不扣费）；否则 `platform`（按生成字数扣费）。"""
    from . import core  # 延迟门面引用：保留 _project_use_user_llm 的 monkeypatch 接缝
    return settle_ledger.billing_mode_of(core._project_use_user_llm(store))


def _settle_incremental(store: ProjectStore) -> tuple[bool, str]:
    """生成中断判定（v2 口径下本函数不再扣费，仅保留停止信号契约）。

    历史背景：旧实现在此按「全书累计 AI 字数差」增量扣费，重写同章不增加累计字数
    ⇒ 天然免费，与「每个产物版本都计费」冲突。TECH-Agent用户化与版本化 §4.4 起改为
    「版本提交点结算」：扣费由 `settle_ledger.settle_version` 完成，本函数只回答
    「是否该停止」——平台付费项目余额 ≤ 0 停止；自备 Key（BYOK）项目永不因平台余额停止。

    Returns:
        (should_stop, 原因)；不停止时原因为空串。
    """
    from . import core  # 延迟门面引用：保留 _project_use_user_llm 的 monkeypatch 接缝
    try:
        project_id = store.dir.name
        uid = db.project_owner(project_id)
        if uid is None:
            return False, ""
        if core._project_use_user_llm(store):
            return False, ""
        new_bal = float(db.get_user(uid)["balance"])
        if new_bal <= 0:
            return True, f"余额已耗尽（当前余额 {new_bal:.2f} 元），已停止生成。充值后可从断点继续。"
        return False, ""
    except Exception:
        logger.exception("生成中断判定异常: project=%s", store.dir.name)
        return False, ""


def _settle_product(store: ProjectStore, kind: str, key: str, payload, *,
                    mode: str, project_id: str) -> Optional[dict]:
    """按产物结算一次（同内容不重复收费）。失败只留痕，不阻断生成主链路。"""
    try:
        data = payload if isinstance(payload, dict) else payload.to_dict()
        words = count_words(kind, data)
        digest = settle_ledger.content_digest(json.dumps(data, ensure_ascii=False, sort_keys=True))
        return settle_ledger.settle_product(store, kind, key, words, mode=mode,
                                            project_id=project_id, content_sha=digest)
    except Exception:
        logger.exception("产物结算失败（不阻断生成）: project=%s kind=%s key=%s",
                         project_id, kind, key)
        return None


def _commit_and_settle(store: ProjectStore, kind: str, payload, *,
                       mode: str, project_id: str,
                       source: str = "ai") -> Optional[dict]:
    """run 收尾统一提交产物版本并按真实版本号结算（TECH-Agent用户化与版本化 §3.10）。

    - `versioned_product=True`：`commit_head` 把当前 HEAD 存档为一个版本（与最新存档同内容
      幂等返回 None），仅当产生新版本才 `settle_version`——一次 run 一次提交一次结算；
    - `False`：回退 `_settle_product`（内容 sha 幂等口径，行为与阶段 0.5 一致）。
    失败只留痕，不阻断生成主链路。
    """
    try:
        data = payload if isinstance(payload, dict) else payload.to_dict()
        if not store.versioned_enabled():
            return _settle_product(store, kind, "all", data, mode=mode,
                                   project_id=project_id)
        from novel_gen.store.versioned import VersionedProduct
        v = VersionedProduct(store, kind).commit_head("all", source=source)
        if v is None:
            return None  # 与最新存档内容一致（幂等），不重复结算
        words = count_words(kind, data)
        digest = settle_ledger.content_digest(json.dumps(data, ensure_ascii=False, sort_keys=True))
        return settle_ledger.settle_version(store, kind, "all", v, words,
                                            mode=mode, project_id=project_id,
                                            content_sha=digest)
    except Exception:
        logger.exception("产物提交结算失败（不阻断生成）: project=%s kind=%s",
                         project_id, kind)
        return None


def _settle_price(store: ProjectStore) -> None:
    """存量项目兜底结算（v2 账本上线后只对历史项目执行一次）。

    1. 已有 v2 账本 → 直接返回（结算由版本提交点负责，绝不重扣）；
    2. 有 v1 `settle.json` → 迁移为 v2（`settled_ai_words/settled_price` 转 legacy 基线，
       迁移过程不扣费）；
    3. 两者均无 → 按旧口径算一次（沿用 `target_ai_words` 封顶仅此一处生效），
       走 `settle_legacy` 扣费并落 v2 账本，此后该项目的结算全部走版本账本。
    """
    try:
        project_id = store.dir.name
        raw = settle_ledger.read_raw(store)
        if raw is not None:
            if raw.get("mode") != settle_ledger.LEDGER_MODE:
                settle_ledger.migrate_v1(store)
            return
        if db.project_owner(project_id) is None:
            return
        calc = _compute_settle(store)
        mode = _settle_billing_mode(store)
        # 无 AI 文字或自备 Key：不扣费（仍落账本，避免后续重复走旧口径）
        price = 0.0 if (calc["ai_words"] <= 0 or calc["use_user_llm"]) else calc["price"]
        result = settle_ledger.settle_legacy(
            store, ai_words=calc["ai_words"], due_words=calc["billable_words"],
            price=price, mode=mode, project_id=project_id)
        logger.info("存量结算完成: project=%s ai_words=%d billable=%d price=%.2f charged=%.2f mode=%s",
                    project_id, calc["ai_words"], calc["billable_words"], price,
                    result.get("charged", 0.0), mode)
    except Exception:
        logger.exception("存量结算异常: project=%s", store.dir.name)


__all__ = [
    "logger",
    "_grant_consume_commission", "_compute_settle", "_settle_billing_mode",
    "_settle_incremental", "_settle_product", "_commit_and_settle", "_settle_price",
]
