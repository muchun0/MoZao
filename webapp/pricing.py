# -*- coding: utf-8 -*-
"""
定价策略（按字数计费）：统一单价每万字 3 元，线性计算

- 现价 = total_words / 10000 × PRICE_PER_10K（精确到分）
- 自 2026-10-27 起统一高质量单价：普通模式（2 元/万字）已下线，新建书固定高质量模式。
- 价格可随时改，重启服务后生效。
"""
from __future__ import annotations

# 每万字单价（元），线性计费（统一高质量单价；普通模式已下线）
PRICE_PER_10K = 3.0


def price_for(total_words: int) -> float:
    """按字数线性计费：统一单价 PRICE_PER_10K 元/万字（不再区分普通/高质量模式）。"""
    return round(total_words / 10_000 * PRICE_PER_10K, 2)

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
