# -*- coding: utf-8 -*-
"""场景体检 + 修复建议（三期 F2，对标 Sudowrite Scene Inspector）。

对已生成章节做确定性体检，输出结构化报告 + 修复建议文案。复用既有检测能力：
- beats._beat_cycle_issues（情节级循环重演）；
- scenes.json（拆场景事件是否都在正文落实）；
- 字数 / 章首承接 / 章末钩子兑现（确定性规则）。

设计约束：零新增 LLM（确定性规则给建议）；缺产物文件的维度降级为空，不报错；
全程 try 包裹 + logger 留痕。
"""
from __future__ import annotations

import logging

from . import beats as _beats

logger = logging.getLogger(__name__)

#: 章末悬念/转折词（钩子兑现的粗略信号）
_HOOK_WORDS = ("？", "！", "却", "竟", "突然", "忽然", "原来", "不料", "偏偏", "难道", "究竟")


def _frags_in(frags: list[str], text: str) -> list[str]:
    return [f for f in frags if f and f in text]


def inspect_chapter(store, chapter_no: int, target_words: int = 0) -> dict:
    """对单章做体检，返回 {chapter, words, target, dimensions:[{dim,level,detail,suggestion}]}。

    store: ProjectStore（只读产物文件）。维度 level ∈ {ok, warn, bad}。
    任何维度异常都用 try 隔离，单维度失败不影响整体体检。
    """
    dimensions: list[dict] = []
    ch = store.load_chapter(chapter_no)
    if ch is None:
        return {"chapter": chapter_no, "words": 0, "target": target_words,
                "dimensions": [{"dim": "存在性", "level": "bad",
                                "detail": f"第{chapter_no}章尚未生成", "suggestion": "先生成该章再体检"}]}
    content = ch.content or ""
    words = len(content)

    # 维度1：字数
    dimensions.append(_dim_words(words, target_words))
    # 维度2：事件推进（scenes.json 计划事件是否在正文落实）
    dimensions.append(_dim_events(store, chapter_no, content))
    # 维度3：情节重演
    dimensions.append(_dim_repetition(content))
    # 维度4：章首承接上一章
    dimensions.append(_dim_transition(store, chapter_no, content))
    # 维度5：章末钩子兑现
    dimensions.append(_dim_hook(store, chapter_no, content))
    return {"chapter": chapter_no, "words": words, "target": target_words,
            "dimensions": dimensions}


def collect_suggestions(report: dict) -> list[str]:
    """P1-5：从体检报告抽 bad/warn 维度的非空 suggestion，供下一章生成注入（闭环）。

    「纯展示」→「生成反馈」：体检发现的改进建议不再只停在报告里，而是沉淀后
    作为下一章的写作先验携带。空建议/报告异常 → []（降级安全）。
    """
    out: list[str] = []
    for dim in (report.get("dimensions") or []):
        if not isinstance(dim, dict):
            continue
        if (dim.get("level") or "") in ("bad", "warn"):
            s = (dim.get("suggestion") or "").strip()
            if s:
                out.append(f"{dim.get('dim','')}：{s}")
    return out



def _dim_words(words: int, target: int) -> dict:
    if not target or target <= 0:
        return {"dim": "字数", "level": "ok", "detail": f"{words} 字", "suggestion": ""}
    ratio = words / target
    if ratio < 0.7:
        return {"dim": "字数", "level": "bad",
                "detail": f"{words} 字，仅为目标 {target} 字的 {ratio:.0%}",
                "suggestion": "内容偏短：可在冲突处补充动作、感官细节或人物反应，把场景写充分。"}
    if ratio > 1.5:
        return {"dim": "字数", "level": "warn",
                "detail": f"{words} 字，约为目标 {target} 字的 {ratio:.0%}",
                "suggestion": "篇幅偏长：检查是否有重复段落，可精简注水对话与无效描写。"}
    return {"dim": "字数", "level": "ok", "detail": f"{words} 字（目标 {target}）", "suggestion": ""}


def _dim_events(store, chapter_no: int, content: str) -> dict:
    try:
        scenes = store.load_scenes().get(str(chapter_no)) or {}
        scene_list = scenes.get("scenes") or []
        if not scene_list:
            return {"dim": "事件推进", "level": "ok", "detail": "无拆场景记录（整章生成路径）",
                    "suggestion": ""}
        missing = []
        for sc in scene_list:
            ev = str(sc.get("event") or sc.get("event_desc") or "")
            if not ev:
                continue
            # 事件片段是否在正文落实：取事件前 8 字 / 关键名词做粗略命中
            probe = ev.strip()[:8]
            if probe and probe not in content:
                missing.append(ev)
        if missing:
            return {"dim": "事件推进", "level": "warn",
                    "detail": f"{len(missing)} 个计划场景未在正文明显落实：{missing[0][:30]}",
                    "suggestion": "大纲计划的场景在正文里找不到对应内容，检查是否被跳过或写漏，"
                                  "可用「重写本章」按大纲重出。"}
        return {"dim": "事件推进", "level": "ok",
                "detail": f"{len(scene_list)} 个计划场景均在正文有对应", "suggestion": ""}
    except Exception as e:  # 单维度失败降级
        logger.warning("[SceneInspector] 事件推进维度失败（降级）: ch=%d err=%s", chapter_no, e)
        return {"dim": "事件推进", "level": "ok", "detail": "读取失败跳过", "suggestion": ""}


def _dim_repetition(content: str) -> dict:
    try:
        issues = _beats._beat_cycle_issues(content)
        if issues:
            det = issues[0].get("detail", "情节循环重演")
            return {"dim": "重复度", "level": "bad", "detail": det,
                    "suggestion": "同一情节序列在章内重复出现，删去冗余版本，只保留最完整的一次演绎。"}
        return {"dim": "重复度", "level": "ok", "detail": "未检出情节级循环重演", "suggestion": ""}
    except Exception as e:
        logger.warning("[SceneInspector] 重复度维度失败（降级）: err=%s", e)
        return {"dim": "重复度", "level": "ok", "detail": "读取失败跳过", "suggestion": ""}


def _dim_transition(store, chapter_no: int, content: str) -> dict:
    if chapter_no <= 1:
        return {"dim": "章首承接", "level": "ok", "detail": "首章无前置章节", "suggestion": ""}
    try:
        prev = store.load_chapter(chapter_no - 1)
        if prev is None or not (prev.content or ""):
            return {"dim": "章首承接", "level": "ok", "detail": "无上一章正文可比", "suggestion": ""}
        prev_tail = (prev.content or "")[-80:]
        opening = (content or "")[:80]
        # 粗略承接：开头 80 字与上章尾 80 字有无 4 字以上公共片段
        connected = any(prev_tail[i:i + 4] in opening for i in range(len(prev_tail) - 3))
        if not connected:
            return {"dim": "章首承接", "level": "warn",
                    "detail": "本章开头与上一章结尾无明显承接（硬切）",
                    "suggestion": "章首加一句承接上章结尾动作/状态的过渡，避免时间线跳脱。"}
        return {"dim": "章首承接", "level": "ok", "detail": "章首承接上章结尾", "suggestion": ""}
    except Exception as e:
        logger.warning("[SceneInspector] 章首承接维度失败（降级）: ch=%d err=%s", chapter_no, e)
        return {"dim": "章首承接", "level": "ok", "detail": "读取失败跳过", "suggestion": ""}


def _dim_hook(store, chapter_no: int, content: str) -> dict:
    try:
        outline = store.load_outline()
        if not outline or chapter_no - 1 >= len(outline.chapters):
            return {"dim": "章末钩子", "level": "ok", "detail": "无大纲钩子设计", "suggestion": ""}
        plan = outline.chapters[chapter_no - 1]
        cliff = (getattr(plan, "cliffhanger", "") or "").strip()
        if not cliff:
            return {"dim": "章末钩子", "level": "ok", "detail": "本章无章末钩子设计", "suggestion": ""}
        tail = (content or "")[-100:]
        if not any(w in tail for w in _HOOK_WORDS):
            return {"dim": "章末钩子", "level": "warn",
                    "detail": f"大纲设计了章末钩子「{cliff[:20]}」，但结尾未见明显悬念/转折",
                    "suggestion": "按大纲钩子在结尾收束：制造冲突一触即发/真相将揭/危险降临的追更欲。"}
        return {"dim": "章末钩子", "level": "ok", "detail": "章末有悬念/转折落点", "suggestion": ""}
    except Exception as e:
        logger.warning("[SceneInspector] 章末钩子维度失败（降级）: ch=%d err=%s", chapter_no, e)
        return {"dim": "章末钩子", "level": "ok", "detail": "读取失败跳过", "suggestion": ""}
