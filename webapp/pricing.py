# -*- coding: utf-8 -*-
"""
定价策略（按单本卖）：按目标字数分档定价，创建项目时从余额扣费。

档位顺序：按 max_words 升序；total_words <= max_words 命中该档。
最后一个档位 max_words 用 None 表示"不设上限"。
每档可带 original_price（原价），用于前端展示"原价 X 现价 Y"优惠标识。
价格可随时改，重启服务后生效。
"""
from __future__ import annotations

# (档位名, 字数上限, 现价元, 原价元或None)。None 表示不设上限。
PRICE_TIERS: list[tuple[str, int | None, float, float | None]] = [
    ("极短篇", 5_000, 0.99, 2.99),
    ("短篇", 25_000, 9.9, None),
    ("中篇", 100_000, 19.9, None),
    ("长篇", 300_000, 39.9, None),
    ("超长篇", None, 69.9, None),
]

# 免费体验：每用户注册赠金（元）。内测引流用，可设 0 关闭。
FREE_REGISTER_BONUS = 10.0


def price_for(total_words: int) -> float:
    """按字数返回现价。"""
    for name, cap, price, _orig in PRICE_TIERS:
        if cap is None or total_words <= cap:
            return price
    return PRICE_TIERS[-1][2]


def original_price_for(total_words: int) -> float | None:
    """按字数返回原价（无优惠时返回 None）。"""
    for name, cap, _price, orig in PRICE_TIERS:
        if cap is None or total_words <= cap:
            return orig
    return PRICE_TIERS[-1][3]


def tier_name_for(total_words: int) -> str:
    for name, cap, _price, _orig in PRICE_TIERS:
        if cap is None or total_words <= cap:
            return name
    return PRICE_TIERS[-1][0]


def tiers() -> list[dict]:
    return [{"name": n, "max_words": c, "price": p, "original_price": o}
            for n, c, p, o in PRICE_TIERS]
