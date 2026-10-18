# -*- coding: utf-8 -*-
"""章末承诺台账（REQ-超长篇生成攻坚 S3.1，2026-10-18）。

beat_gate 评估后，把"大纲承诺 vs 正文兑现"确定性落账，供下一章注入补偿块，
治"大纲承诺衰减"——NCP-Bench 实证强模型 20 轮后承诺存活率仅 42%，是全行业
本质弱点，必须账本化闭环（检测→记录→下章补偿）。

四类清单（纯确定性、零 LLM，不新增调用数）：
  - committed：已兑现节点（beat_gate 中 covered 拍）
  - missed：漏兑现节点（missing 拍）—— 下章必须补偿
  - planted：新埋节点（外部传入，如本章 reader_ledger 新 planted 伏笔）
  - must_carry_to_next：漏兑现节点清单（= missed，供下章补偿块消费）

补偿块注入（下一章）：仅当上一章存在漏兑现节点才生成补偿块文本，由
chapter_design / memory 在下一章 prompt 前段注入；无漏兑现则不注入（零 token）。

设计约束（与事件账本正交）：
  - 与 event_progress（场景内禁抢跑）不冲突：event_progress 防"提前消费后续事件"，
    本账本防"承诺未兑现"——前者是场景内约束，后者是章间闭环；
  - 仅在有漏兑现时注入，不注入则不增加任何 token 成本；
  - 异常一律降级空串不阻塞（§11.6：有日志留痕由调用方负责）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 补偿块单次最多列出的漏兑现节点数（避免注入块过长，其余后续处理）
_COMPENSATE_LIMIT = 3


def build_commitment_ledger(bg: dict, chapter_no: int,
                            new_planted: list[str] | None = None) -> dict:
    """从 beat_gate 评估结果构建章末承诺台账（纯函数、零 LLM）。

    bg：`compute_beat_coverage` / `assess_chapter` 的返回 dict。
    返回四类清单 + 元信息，供 store 落盘与下章补偿块消费。
    """
    planned = bg.get("planned") or []
    committed = [p["beat"] for p in planned if p.get("covered")]
    missed = [p["beat"] for p in planned if not p.get("covered")]
    # 兼容：bg 无 planned（skip / 旧结构）但有 missing → 直接取漏兑现拍
    if not planned:
        missed = list(bg.get("missing") or [])
    planted = list(new_planted or [])
    return {
        "chapter_no": int(chapter_no),
        "verdict": bg.get("verdict"),
        "coverage": bg.get("coverage"),
        "committed": committed,
        "missed": missed,
        "planted": planted,
        "must_carry_to_next": missed,
    }


def compensation_block(prev_ledger: dict | None, limit: int = _COMPENSATE_LIMIT) -> str:
    """上一章承诺台账 → 下章补偿块文本。

    仅当上一章存在漏兑现节点才返回非空补偿块；否则返回空串（零 token，不注入）。
    """
    if not prev_ledger:
        return ""
    missed = list(prev_ledger.get("missed") or prev_ledger.get("must_carry_to_next") or [])
    if not missed:
        return ""
    head = missed[:limit]
    items = "、".join(f"《{b}》" for b in head)
    extra = f"（其余 {len(missed) - limit} 个后续章节处理）" if len(missed) > limit else ""
    return (f"【上章未兑现节点补偿】上一章承诺但未兑现的剧情节点：{items}{extra}。"
            f"本章前半段必须优先兑现这些节点，不得遗漏或跳过。")
