# -*- coding: utf-8 -*-
"""爆款拆书库沉淀逻辑：调研成果入库 + 内置知识库种子导入。

- sink_research：调研完成后把 ResearchReport 结构化沉淀到 book_teardown（幂等，
  任何异常只记日志，绝不阻塞生成主流程——沉淀是副作用）。
- seed_from_knowledge_base：把 novel_gen/research/knowledge_base.json 导入为
  「通用」种子拆书条目（幂等，服务启动时调用）。
"""
from __future__ import annotations

import json
import os
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


# ---------- 手动拆解（P1：用户粘贴/上传爆款作品文本 → AI 拆解入库） ----------

_MANUAL_MIN_WORDS = 50    # 低于此字数拆解无意义，直接报错
_MANUAL_MAX_WORDS = 20000 # 上限：控制 LLM 耗时与滥用（接口同步等待）


def _default_llm():
    """用平台默认 LLM（deepseek）跑拆解：api_key 从环境变量补齐，与生成流程一致。"""
    from novel_gen.config import PROVIDER_DEFAULTS, LLMConfig
    from novel_gen.llm.factory import create_llm
    llm_cfg = LLMConfig()
    env_key = PROVIDER_DEFAULTS.get(llm_cfg.provider, {}).get("env_key", "")
    llm_cfg.api_key = os.environ.get(env_key, "")
    return create_llm(llm_cfg)


def analyze_manuscript(text: str, genre: str = "", title: str = "",
                       user_id: int = 0, llm=None) -> dict | None:
    """用户手动拆解爆款作品文本，结构化沉淀为拆书库条目（source_type=manual）。

    参数错误（空/过短/超长）抛 ValueError（路由转 400）；LLM 或入库失败
    记日志返回 None（路由转 500），绝不写坏数据。
    返回 {id, genre, title, summary, source_type}。
    """
    text = (text or "").strip()
    n = len(text)
    if n < _MANUAL_MIN_WORDS:
        raise ValueError(f"文本太短（当前 {n} 字，至少 {_MANUAL_MIN_WORDS} 字），请粘贴更完整的章节")
    if n > _MANUAL_MAX_WORDS:
        raise ValueError(f"文本超过 {_MANUAL_MAX_WORDS} 字上限（当前 {n} 字），请截取核心章节")

    try:
        llm = llm or _default_llm()
        system = (
            "你是一位深耕网文/短篇市场的爆款拆解专家。你的任务是把用户提供的"
            "一段『爆款作品文本』拆解成可复用的写作手法清单，供 AI 写手在同类创作中"
            "借鉴（借鉴手法，不复制情节与文字）。判断要具体、可操作，避免空话套话。"
        )
        user = (
            f"【作品名】{title or '（未提供）'}\n"
            f"【用户标注题材】{genre or '（未提供，请自行判断最贴合的题材，用 1-2 个词，如：都市、玄幻、悬疑）'}\n"
            f"【待拆解文本】\n{text}\n\n"
            f"请拆解该文本并输出 JSON，字段如下：\n"
            f"- genre：题材归类（1-2 个词）\n"
            f"- summary：该作品的爆点综述（2-4 句，它为什么能留住读者）\n"
            f"- opening：开篇钩子拆解（一段，说明开头如何快速立住冲突/悬念）\n"
            f"- hooks：2-5 个悬念/冲突钩子（每个一句，说明抛出方式与回收节奏）\n"
            f"- plot_patterns：2-5 个可借鉴的情节套路（每个一句）\n"
            f"- characters：2-5 个人设亮点（每个一句，含身份/目标/性格钩子）\n"
            f"- styles：2-4 个文风与语言特点（如何混合，如『短句快节奏+口语化吐槽』）\n"
            f"- pacing：节奏规律（一句）\n"
            f"- dialogue：1-4 个对话/描写技巧（每个一句）\n"
            f"- avoid：1-3 个新手照搬时容易踩的雷区\n"
            f"- takeaways：3-6 条可直接借用的写作手法清单（这是本拆解的核心交付）\n"
            f"- excerpts：1-3 段原文关键片段引用（每段 ≤100 字，供回溯核对，不得整段照抄）\n"
            f"要求：紧扣文本给出具体判断，标注手法而非情节复制。"
        )
        data = llm.chat_json(system, user)
        if not isinstance(data, dict):
            data = {}

        def _list(v) -> list[str]:
            return [str(x).strip() for x in (v or []) if x and str(x).strip()]

        summary = str(data.get("summary") or "").strip()[:1000]
        final_genre = (genre or str(data.get("genre") or "")).strip()[:20] or "通用"
        final_title = (title or "").strip()[:120] or f"{final_genre} · 用户投稿拆解"
        content = json.dumps({
            "genre": final_genre,
            "summary": summary,
            "opening": str(data.get("opening") or "").strip(),
            "hooks": _list(data.get("hooks")),
            "plot_patterns": _list(data.get("plot_patterns")),
            "characters": _list(data.get("characters")),
            "styles": _list(data.get("styles")),
            "pacing": str(data.get("pacing") or "").strip(),
            "dialogue": _list(data.get("dialogue")),
            "avoid": _list(data.get("avoid")),
            "takeaways": _list(data.get("takeaways")),
            "excerpts": _list(data.get("excerpts")),
        }, ensure_ascii=False)
        tid = db.insert_manual_teardown(
            final_genre, final_title, summary or f"{final_genre}作品手动拆解", content, int(user_id or 0))
        logger.info("手动拆解入库: user=%s td_id=%d genre=%s 字数=%d", user_id, tid, final_genre, n)
        return {"id": tid, "genre": final_genre, "title": final_title,
                "summary": summary, "source_type": "manual"}
    except ValueError:
        raise
    except Exception:
        logger.exception("手动拆解失败: user=%s", user_id)
        return None
