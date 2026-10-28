# -*- coding: utf-8 -*-
"""版本推荐 webapp 装配层（REQ-版本对比与推荐）。

novel_gen/pipeline/version_diff.py 是纯函数；本模块负责把 Web 侧数据
（项目 config、beat_gate、章级 user_managed）喂给它，并把 badge/reasons
附加到版本列表响应。novel_gen 不依赖 webapp，方向反过来依赖。
"""
from __future__ import annotations

from .logger import get_logger
from novel_gen.pipeline.version_diff import recommend_versions

logger = get_logger("version_recommend")

__all__ = ["attach_chapter_recommendations", "attach_product_recommendations"]


def _chapter_user_selected(store, no: int):
    """章级 user_managed 为 True 时，当前 selected 版是用户手选（应标 user_managed）。"""
    try:
        rec = (store.load_chapter_versions() or {}).get(str(no)) or {}
        if rec.get("user_managed"):
            sel = rec.get("selected")
            return sel if isinstance(sel, int) else None
    except Exception:
        logger.warning("读取章级 user_managed 失败（降级无 user_selected）: no=%s", no)
    return None


def attach_chapter_recommendations(store, no: int, versions: list[dict]) -> list[dict]:
    """章节版本列表附加推荐徽章。

    数据注入：
      - target_words：config.chapter_words；
      - user_selected_v：章级 user_managed 时的当前 selected（你的选择）；
      - beat_pass：beat_gate 按章对 HEAD 评估，只给当前 selected 版（历史版不伪造）。
    任何装配异常 → 原列表返回（badge 缺省由纯函数给 neutral），不拖垮接口。
    """
    try:
        cfg = store.load_config()
        target = getattr(cfg, "chapter_words", None)
    except Exception:
        target = None

    user_selected = _chapter_user_selected(store, no)

    beat_pass: dict[int, bool] = {}
    try:
        sel = next((v.get("v") for v in versions if v.get("selected")), None)
        beat = (store.load_beat_gate() or {}).get(str(no)) or {}
        if sel and beat.get("verdict") == "pass":
            beat_pass[sel] = True
    except Exception:
        logger.warning("读取 beat_gate 失败（降级不注入 beat）: no=%s", no)

    try:
        return recommend_versions(versions, target_words=target,
                                  user_selected_v=user_selected, beat_pass=beat_pass)
    except Exception:
        logger.exception("章节版本推荐装配失败（降级原列表）: no=%s", no)
        return versions


def attach_product_recommendations(store, versions: list[dict]) -> list[dict]:
    """outline/world 版本列表附加推荐徽章（无字数目标、无 beat）。"""
    try:
        return recommend_versions(versions)
    except Exception:
        logger.exception("产物版本推荐装配失败（降级原列表）")
        return versions
