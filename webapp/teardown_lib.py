# -*- coding: utf-8 -*-
"""爆款拆书库沉淀逻辑：调研成果入库 + 内置知识库种子导入。

- sink_research：调研完成后把 ResearchReport 结构化沉淀到 book_teardown（幂等，
  任何异常只记日志，绝不阻塞生成主流程——沉淀是副作用）。
- seed_from_knowledge_base：把 novel_gen/research/knowledge_base.json 导入为
  「通用」种子拆书条目（幂等，服务启动时调用）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import db
from .logger import get_logger

logger = get_logger("teardown")

_KB_PATH = Path(__file__).resolve().parent.parent / "novel_gen" / "research" / "knowledge_base.json"


def _build_content(report) -> dict:
    """把 ResearchReport 转成拆书库 content 的 JSON 字典。"""
    return {
        "summary": report.summary,
        "trends": list(getattr(report, "trends", []) or []),
        "plot_patterns": list(getattr(report, "plot_patterns", []) or []),
        "styles": list(getattr(report, "styles", []) or []),
        "pacing": report.pacing,
        "opening_strategy": report.opening_strategy,
        "avoid": list(getattr(report, "avoid", []) or []),
        "references": list(getattr(report, "references", []) or []),
    }


def _gen_title(genre: str) -> str:
    return f"{genre}题材 · 爆款共性拆解" if genre and genre != "通用" else "全站通用 · 爆款共性拆解"


def sink_research(report, project_id: str) -> int | None:
    """调研成果沉淀到拆书库。失败只记日志，不抛异常（绝不阻塞生成）。"""
    if report is None:
        return None
    genre = (report.genre or "").strip()
    if not genre:
        return None  # 无题材调研（扩写等）不沉淀
    try:
        content = json.dumps(_build_content(report), ensure_ascii=False)
        summary = (report.summary or "").strip()[:1000]
        return db.upsert_research_teardown(
            genre=genre,
            title=_gen_title(genre),
            summary=summary or f"{genre}题材调研共性拆解",
            content=content,
            source_project=str(project_id),
        )
    except Exception:
        logger.exception("拆书库沉淀失败: project=%s genre=%s", project_id, genre)
        return None


def seed_from_knowledge_base() -> None:
    """启动时把内置知识库导入为「通用」种子拆书条目（幂等，异常不影响启动）。"""
    try:
        if not _KB_PATH.exists():
            logger.warning("内置知识库缺失，跳过拆书库种子导入: %s", _KB_PATH)
            return
        data = json.loads(_KB_PATH.read_text(encoding="utf-8"))
        content = {
            "trends": data.get("common_trends_2026", []),
            "plot_patterns": data.get("plot_patterns", []),
            "pacing": "；".join(data.get("pacing_rules", [])),
            "opening_strategy": "；".join(data.get("opening_rules", [])),
            "styles": [s.get("name", "") for s in data.get("style_spectrum", []) if s],
            "avoid": [],
            "references": data.get("sources", []),
            "platform_notes": data.get("platform_notes", []),
        }
        tid = db.seed_teardown(
            genre="通用",
            title="全站通用 · 爆款共性拆解",
            summary="内置热门小说共性知识库（2026-08 版）：全网热门题材趋势、情节套路、节奏与开篇规则。",
            content=json.dumps(content, ensure_ascii=False),
        )
        if tid:
            logger.info("拆书库种子导入完成: td_id=%d", tid)
    except Exception:
        logger.exception("拆书库种子导入失败")
