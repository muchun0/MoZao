# -*- coding: utf-8 -*-
"""
编辑视角质检：大纲生成后，模拟资深网文编辑审稿，找出问题并自动修正。

基于 doubao-novel-writing Skill 的编辑视角规范：
- 题材标签/目标读者/一句话卖点
- 开篇钩子（前3章500字内立住主角+冲突+悬念）
- 主角目标/困境/主动行为/可识别人格
- 情绪回报节奏（爽点/虐点/悬念是否及时）
- 人物关系是否互相推动
- 前三章小闭环 / 后续持续升级
- 差异化与同质化风险
- 连载扩展性与中段疲软风险
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..store.schemas import Outline
from .templates import _hard_constraints


@dataclass
class EditorReview:
    """编辑审阅结果。"""
    passed: bool
    score: int                # 1-10 商业潜力评分
    issues: list[str] = field(default_factory=list)         # 问题清单
    suggestions: list[str] = field(default_factory=list)    # 修改建议
    priority_actions: list[str] = field(default_factory=list)  # 优先修改动作（3-5条）
    raw: str = ""            # 原始审阅文本

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "priority_actions": self.priority_actions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditorReview":
        return cls(
            passed=bool(d.get("passed", True)),
            score=int(d.get("score", 7)),
            issues=list(d.get("issues", [])),
            suggestions=list(d.get("suggestions", [])),
            priority_actions=list(d.get("priority_actions", [])),
        )


class EditorReviewer:
    """大纲编辑质检：审阅 → 不通过则自动修正一次。"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def review(self, cfg: NovelConfig, outline: Outline, research_block: str) -> EditorReview:
        """对大纲执行编辑视角审阅，返回审阅结果。"""
        system = (
            "你是一位资深网文编辑，擅长从商业潜力、读者留存和投稿表现角度审稿。"
            "你的任务是审阅一份小说大纲，找出会影响读者追更、编辑过稿和中段留存的具体问题，"
            "并给出可执行的修改建议。评价要具体，对应到章节或设定，避免空泛的'有吸引力''不够新'。"
        )
        outline_text = "\n".join(
            f"第{c.no}章《{c.title}》：{c.goal}" for c in outline.chapters
        )
        hard = _hard_constraints(cfg)
        user = (
            f"【题材】{cfg.genre}\n"
            f"【书名/简介】{outline.title}｜{outline.intro}\n"
            f"{hard}\n\n"
            f"【题材共性调研】\n{research_block}\n\n"
            f"【全书大纲】\n{outline_text}\n\n"
            f"请从以下 9 个维度审阅，输出 JSON：\n"
            f"1. 题材与读者：标签是否清晰，目标读者是否明确\n"
            f"2. 一句话卖点：核心设定能否反复制造冲突，差异化在哪\n"
            f"3. 开篇钩子：前3章是否500字内立住主角+冲突+悬念\n"
            f"4. 主角：是否有明确目标、具体困境、主动行为和可识别人格\n"
            f"5. 情绪回报：爽点/虐点/悬念是否及时，是否匹配铺垫\n"
            f"6. 人物关系：各条线是否互相推动而非平行\n"
            f"7. 节奏闭环：前3章是否形成小闭环，后续是否持续升级\n"
            f"8. 连载风险：中段是否会疲软，反派/目标是否逐级升级，是否有同质化\n"
            f"9. 防同质化：是否落入打脸/无脑爽/机械升级/完美结局等高频套路；"
            f"人物是否有内在矛盾；剧情是否有代价；结局是否允许遗憾与不完美\n\n"
            f"输出格式（严格 JSON，不要多余文字）：\n"
            f'{{"passed": true/false, "score": 1-10, "issues": ["问题1"...], '
            f'"suggestions": ["建议1"...], "priority_actions": ["优先动作1","优先动作2","优先动作3"]}}\n'
            f"判断标准：score>=7 且无致命问题则 passed=true；有开篇无力、主角无目标、节奏平铺、同质化严重、套路化打脸等问题则 passed=false。"
            f"注意：防同质化与商业化不冲突——'结局允许不完美/有遗憾'不视为缺陷。"
        )
        raw = self.llm.chat(system, user, temperature=0.3, max_tokens=2048)
        review = self._parse_review(raw)
        review.raw = raw
        return review

    def revise(self, cfg: NovelConfig, outline: Outline, review: EditorReview,
               research_block: str) -> Outline:
        """基于编辑审阅意见修正大纲，返回修正后的大纲。

        REQ-token优化 P1-6：默认 diff 式修正——只输出需改字段，Python 合并回原大纲，
        不重吐全大纲 JSON（输出 4096 → 数百 token）；解析失败/关闭开关回退整章重出。
        """
        if getattr(cfg, "editor_diff_revise", True):
            revised = self._revise_diff(cfg, outline, review, research_block)
            if revised is not None and len(revised.chapters) == len(outline.chapters):
                return revised
        return self._revise_whole(cfg, outline, review, research_block)

    def revise_candidates(self, cfg: NovelConfig, outline: Outline, review: EditorReview,
                          research_block: str, n: int = 3) -> list[Outline]:
        """P1-3-B 多版本候选（Sudowrite 式）：生成 n 个不同修订侧重的大纲候选供用户挑。

        侧重轮转：结构节奏 / 人物差异化 / 悬念钩子，diff 式（低成本）+ 递增 temperature
        制造候选差异；去重（书名+章节指纹）；全部失败回退整章重出一个。返回候选列表。
        """
        focuses = [
            "本轮优先强化结构节奏：铺垫/冲突/爽点的节拍密度与先后顺序，让推进更紧凑",
            "本轮优先强化人物差异化：人物动机、独特性格、关系张力，让角色不脸谱化",
            "本轮优先强化悬念钩子：章末钩子、伏笔密度与兑现节奏，提升追更欲",
        ]
        seen: set = set()
        out: list[Outline] = []
        import copy
        for i in range(max(1, int(n))):
            focus = focuses[i % len(focuses)]
            temp = 0.7 + (i * 0.1)
            # 每个候选基于原始大纲的独立深拷贝（_apply_updates 原地修改，须隔离候选）
            c = self._revise_diff(cfg, copy.deepcopy(outline), review, research_block,
                                  focus=focus, temperature=temp)
            if c is None or len(c.chapters) != len(outline.chapters):
                continue
            key = (c.title, tuple((x.title, x.goal) for x in c.chapters))
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        if not out:
            out.append(self._revise_whole(cfg, outline, review, research_block))
        return out

    def _revise_diff(self, cfg: NovelConfig, outline: Outline, review: EditorReview,
                     research_block: str, focus: str = "", temperature: float = 0.5) -> Outline | None:
        """diff 式修正：LLM 只输出需要改动的章节字段，Python 合并回原大纲。

        focus（P1-3-B）：本轮修订侧重指示，多版本候选时轮转；空 = 不强调侧重。
        temperature：候选多样性（默认 0.5，多版本候选时递增）。
        """
        system = (
            "你是一位网文主编。根据编辑审阅意见，修正以下大纲。"
            "必须保留用户强约束（背景/主角/预设人物/避雷），只调整结构、节奏、钩子和差异化。"
            "只输出需要改动的字段（diff），不要重新输出整个大纲。"
            + (f"当前修订重点：{focus}" if focus else "")
        )
        outline_text = "\n".join(
            f"第{c.no}章《{c.title}》：{c.goal}" for c in outline.chapters
        )
        actions = "\n".join(f"- {a}" for a in review.priority_actions)
        issues = "\n".join(f"- {i}" for i in review.issues)
        hard = _hard_constraints(cfg)
        user = (
            f"【原大纲】\n{outline_text}\n\n"
            f"【编辑指出的问题】\n{issues}\n\n"
            f"【优先修改动作】\n{actions}\n\n"
            f"{hard}\n\n"
            f"【题材共性调研】\n{research_block}\n\n"
            f"请输出需要改动的字段（未提及的章节与字段保持原样，不要输出）：\n"
            f'{{"updates": [{{"no": 0, "title"?: "全书新书名(如需)", "intro"?: "全书新简介(如需)"}}, '
            f'{{"no": 1, "title"?: "新章名(如需)", "goal"?: "新本章目标(如需)"}}]}}\n'
            f"规则：no=0 表示全书级字段（可省略）；章节字段只列出真正需要修改的；"
            f"全部字段都可省略；务必保持章节总数不变。"
        )
        raw = self.llm.chat(system, user, temperature=temperature, max_tokens=1200)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            d = json.loads(match.group())
        except (json.JSONDecodeError, TypeError):
            return None
        updates = d.get("updates") if isinstance(d, dict) else None
        if not isinstance(updates, list):
            return None
        return _apply_updates(outline, updates)

    def _revise_whole(self, cfg: NovelConfig, outline: Outline, review: EditorReview,
                      research_block: str) -> Outline:
        """整章重出（原逻辑，diff 失败/关闭时兜底）。"""
        system = (
            "你是一位网文主编。根据编辑审阅意见，修正以下大纲。"
            "必须保留用户强约束（背景/主角/预设人物/避雷），只调整结构、节奏、钩子和差异化。"
            "修正后重新输出完整大纲 JSON。"
        )
        outline_text = "\n".join(
            f"第{c.no}章《{c.title}》：{c.goal}" for c in outline.chapters
        )
        actions = "\n".join(f"- {a}" for a in review.priority_actions)
        issues = "\n".join(f"- {i}" for i in review.issues)
        hard = _hard_constraints(cfg)
        user = (
            f"【原大纲】\n{outline_text}\n\n"
            f"【编辑指出的问题】\n{issues}\n\n"
            f"【优先修改动作】\n{actions}\n\n"
            f"{hard}\n\n"
            f"【题材共性调研】\n{research_block}\n\n"
            f"请输出修正后的完整大纲 JSON（必须恰好 {len(outline.chapters)} 章）：\n"
            f'{{"title": "书名", "intro": "简介", "chapters": [{{"no":1,"title":"章名","goal":"本章目标"}},...]}}'
        )
        raw = self.llm.chat(system, user, temperature=0.5, max_tokens=4096)
        # 解析 JSON 后用 from_dict
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                d = json.loads(match.group())
                revised = Outline.from_dict(d)
            except (json.JSONDecodeError, TypeError):
                revised = None
        else:
            revised = None
        # 修正失败则返回原大纲
        if revised is None or len(revised.chapters) != len(outline.chapters):
            return outline
        return revised

    @staticmethod
    def _parse_review(raw: str) -> EditorReview:
        """从 LLM 输出中解析 JSON 审阅结果。"""
        # 尝试提取 JSON 块
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                d = json.loads(match.group())
                return EditorReview.from_dict(d)
            except (json.JSONDecodeError, TypeError):
                pass
        # 解析失败：默认通过，把原始文本作为建议
        return EditorReview(
            passed=True, score=7,
            issues=["审阅结果解析失败，已跳过自动修正"],
            suggestions=[raw[:500]],
            priority_actions=[],
        )


def _apply_updates(outline: Outline, updates: list) -> Outline | None:
    """diff 式修正合并（REQ-token优化 P1-6）。

    updates 每项形如 {"no": N, "title"?: ..., "intro"?(仅 no=0), "goal"?: ...}：
    no=0 表示全书级字段；章节字段缺失 = 不改；非法项跳过。
    返回合并后的大纲；异常返回 None（调用方回退整章重出）。
    """
    try:
        chapters = {c.no: c for c in outline.chapters}
        for u in updates:
            if not isinstance(u, dict):
                continue
            try:
                no = int(u.get("no", 0) or 0)
            except (TypeError, ValueError):
                continue
            if no == 0:
                if u.get("title"):
                    outline.title = str(u["title"]).strip()
                if u.get("intro"):
                    outline.intro = str(u["intro"]).strip()
            elif no in chapters:
                c = chapters[no]
                if u.get("title"):
                    c.title = str(u["title"]).strip()
                if u.get("goal"):
                    c.goal = str(u["goal"]).strip()
                if u.get("core_event"):
                    c.core_event = str(u["core_event"]).strip()
        return outline
    except Exception:
        return None
