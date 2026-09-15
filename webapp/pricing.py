# -*- coding: utf-8 -*-
"""
定价策略（按字数计费）：每万字 2 元，线性计算，创建项目时从余额扣费。

- 现价 = total_words / 10000 × PRICE_PER_10K（精确到分）
- 档位（PRICE_TIERS）仅用于：档位名称展示、原价（优惠标识）参考、字数范围提示；
  档位不再决定价格，价格完全由字数决定。
- 原价只在 PRICE_TIERS 中配置了 original 的档位展示（如极短篇 2.99）；
  未配置原价的档位不显示"原价/现价"优惠标识。
- 价格可随时改，重启服务后生效。
"""
from __future__ import annotations

# 每万字单价（元），线性计费
PRICE_PER_10K = 2.0
# 高质量模式：场景级生成 + 动态模块 + 参数分层，单价更高
PRICE_PER_10K_HQ = 3.0


def price_for(total_words: int, high_quality: bool = False) -> float:
    """按字数线性计费：普通 PRICE_PER_10K 元/万字，高质量模式 PRICE_PER_10K_HQ 元/万字。"""
    rate = PRICE_PER_10K_HQ if high_quality else PRICE_PER_10K
    return round(total_words / 10_000 * rate, 2)

# 展示档位：(档位名, 字数上限或None(不限), 原价元或None)。仅用于名称/原价/范围展示。
PRICE_TIERS: list[tuple[str, int | None, float | None]] = [
    ("极短篇", 5_000, 2.99),
    ("短篇", 25_000, None),
    ("中篇", 100_000, None),
    ("长篇", 300_000, None),
    ("超长篇", None, None),
]

# 免费体验：每用户注册赠金（元）。内测引流用，可设 0 关闭。
FREE_REGISTER_BONUS = 10.0



def original_price_for(total_words: int) -> float | None:
    """按字数返回该档位的原价（无优惠配置时返回 None）。"""
    for name, cap, orig in PRICE_TIERS:
        if cap is None or total_words <= cap:
            return orig
    return PRICE_TIERS[-1][2]


def tier_name_for(total_words: int) -> str:
    for name, cap, _orig in PRICE_TIERS:
        if cap is None or total_words <= cap:
            return name
    return PRICE_TIERS[-1][0]


def tiers() -> list[dict]:
    """返回展示用档位；price 为按该档字数上限计算的参考现价（不限档为 None）。"""
    return [
        {
            "name": n,
            "max_words": c,
            "price": price_for(c) if c is not None else None,
            "original_price": o,
        }
        for n, c, o in PRICE_TIERS
    ]
