# -*- coding: utf-8 -*-
"""小墨AI 人设提示词加载（AGENTS.md §11.3：提示词文本放 .md，不硬编码在 py）。"""
from __future__ import annotations

from pathlib import Path

from ..logger import get_logger

logger = get_logger("assistant")

__all__ = ["load_persona", "PERSONA_PATH"]

#: 提示词文件路径（novel_gen 侧统一存放，webapp 只读）
PERSONA_PATH = (Path(__file__).resolve().parents[2]
                / "novel_gen" / "pipeline" / "agents" / "prompts" / "assistant.md")

#: 读取失败时的最简人设（降级不阻塞，但仍留痕）
_FALLBACK = """你是本书的编辑顾问，只做诊断与建议，不写正文、不改稿。
只依据提示词中给出的数据作答，不自造分数、指标、伏笔状态；数据缺失就说没有这项记录。
输出 Markdown，三级标题：## 评估 / ## 问题与定位 / ## 改稿方向。
"""

_cache: dict = {"mtime": -1.0, "text": ""}


def load_persona() -> str:
    """读取人设提示词：按 mtime 缓存，文件改动自动失效（零额外基础设施）。"""
    try:
        mtime = PERSONA_PATH.stat().st_mtime
        if _cache["mtime"] == mtime and _cache["text"]:
            return _cache["text"]
        text = PERSONA_PATH.read_text(encoding="utf-8")
        if text.strip():
            _cache["mtime"], _cache["text"] = mtime, text
            return text
        logger.warning("人设提示词为空，使用内置最简人设: %s", PERSONA_PATH)
    except FileNotFoundError:
        logger.warning("人设提示词缺失，使用内置最简人设: %s", PERSONA_PATH)
    except Exception:
        logger.exception("人设提示词读取失败，使用内置最简人设")
    return _FALLBACK
