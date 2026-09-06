# -*- coding: utf-8 -*-
"""
代理/分销业务参数（站长可按需修改，重启服务后生效）。

现实中代理制的三个关键参数：
1. AGENT_DISCOUNT     代理本人消费折扣（拿货价）。现实常见 7-9 折，默认 8 折。
2. COMMISSION_RATE    返佣比例：下级真实消费金额的百分之几给上级。现实常见 5%-20%，默认 10%。
3. COMMISSION_BASE    返佣基数：
       "consume"  按下级消费返佣（推荐）：下级真正花钱建书时上级才得佣，
                   与"按单本收费"商业模式对齐，且防"充值不消费白拿"的刷量。
       "recharge" 按下级充值返佣：下级充值即返（含站长人工充值），
                   对代理更友好但容易被刷，站长要评估。
       "both"     不提供：充值和消费都返会双重返佣，站长亏本。
4. REFERRAL_BONUS    注册拉新奖励（元/人，默认 0 = 关闭）。
       现实中纯拉新返利是刷量重灾区，且国内《禁止传销条例》对"以拉人头本身计酬"是红线；
       若要开，建议小额（如 1-2 元）并仅限"上级是代理"时发放。
"""
from __future__ import annotations

# 代理本人创建项目时的折扣价比例（0.8 = 8 折）
AGENT_DISCOUNT = 0.8

# 返佣比例（下级消费/充值金额的 10%）
COMMISSION_RATE = 0.10

# 返佣基数：consume（推荐）| recharge
COMMISSION_BASE = "consume"

# 注册拉新奖励（元），默认 0 关闭
REFERRAL_BONUS = 0.0


def round_money(v: float) -> float:
    """金额四舍五入到分。"""
    return round(v + 1e-9, 2)


def agent_price(price: float, discount: float = AGENT_DISCOUNT) -> float:
    """代理价 = 原价 × 折扣，四舍五入到分。"""
    return round_money(price * discount)
