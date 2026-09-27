# -*- coding: utf-8 -*-
"""
ReaderAgent 内置结果自校验（模块4）：输出前自动校验业务质量。

校验维度（基础规则集）：
1. 结构要求     —— 确定性：正文字数是否达标（严重不足触发重写）
2. 内容重复     —— 确定性：与最近 N 章正文的 n-gram 重叠率
3. 输出合规     —— 确定性：明显违规内容拦截（复用 novel_gen.safety.basic_content_check）
4. 读者体验     —— LLM：复用 reader_review_prompt（代入感/爽点/钩子/阅读障碍/主角主动性）
                + 人物卡对照（人物设定一致性、剧情逻辑）

策略：校验不通过 → 触发 1 次自动重写 → 重写后复检；
仍不通过 → 标记问题（review.json + trace step_type=quality_check）并返回当前内容，不阻塞生成。
任何异常降级跳过校验，绝不影响主流程。
"""
from __future__ import annotations

import json
import time

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..store.project import ProjectStore
from ..store.schemas import Chapter
from .templates import reader_review_prompt
from .trace import TraceRecorder

# 校验阈值（确定性规则）
_MIN_WORDS_RATIO = 0.3        # 字数 < 目标 30% 视为严重不足（触发重写）
_NGRAM = 8                    # 重复检测 n-gram 长度
_DUP_RATIO = 0.25             # 与任一近章重叠率 > 25% 视为明显重复
_MAX_RECENT = 3               # 参与重复检测的最近章节数


class QualityChecker:
    """章级质量校验器（确定性规则 + 读者视角 LLM 校验）。"""

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 trace: TraceRecorder | None = None, guard=None):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.trace = trace
        self.guard = guard

    # ---------- 对外入口 ----------
    def ensure_quality(self, chapter_no: int, plan_title: str, plan_goal: str,
                       content: str, summary: str, silent: bool = False
                       ) -> tuple[Chapter, dict]:
        """校验 → 不通过则重写 1 次 → 复检 → 返回 (最终 Chapter, 校验结果)。

        校验结果同时落 review.json（与既有格式兼容）与 trace。
        """
        result = self.check(chapter_no, plan_title, plan_goal, content)
        issues = result["issues"]

        # 不通过 → 触发 1 次自动重写
        if not result["passed"]:
            if not silent:
                print(f"  [自校验] 第{chapter_no}章未通过（{len(issues)} 个问题），触发 1 次自动重写...")
            if self.guard is not None:
                self.guard.enter_scene("revise")
            rewritten = self._rewrite_once(chapter_no, plan_title, plan_goal, content, issues)
            if rewritten and len(rewritten) > len(content) * 0.5:
                content = rewritten
                content, summary = _split_summary(content)
                # 复检
                result = self.check(chapter_no, plan_title, plan_goal, content)
                issues = result["issues"]
                if not silent:
                    print(f"  [自校验] 重写后复检：{'通过' if result['passed'] else '仍存在问题（已标记）'}")

        # 结果落库（review.json 兼容既有格式）
        self._save_review(chapter_no, issues, result)
        # 结果入 trace（模块1 快照）
        if self.trace is not None:
            self.trace.record_quality(chapter_no, result, issues)

        return Chapter(no=chapter_no, title=plan_title, content=content,
                       summary=summary, goal=plan_goal), result

    # ---------- 校验 ----------
    def check(self, chapter_no: int, plan_title: str, plan_goal: str,
              content: str) -> dict:
        """执行全部校验维度，返回 {"passed": bool, "issues": [...], "score": int}。"""
        issues: list[dict] = []
        score = 100

        # 1. 结构要求（确定性）
        target = max(self.cfg.chapter_words, 1)
        words = len(content or "")
        if words < target * _MIN_WORDS_RATIO:
            issues.append({
                "type": "结构-字数严重不足",
                "detail": f"本章仅 {words} 字，不足目标 {target} 字的 30%",
                "level": "大改",
            })
            score -= 30
        elif words < target * 0.6:
            issues.append({
                "type": "结构-字数偏少",
                "detail": f"本章 {words} 字，低于目标 {target} 字的 60%",
                "level": "中改",
            })
            score -= 10

        # 2. 内容重复（确定性，与最近 N 章对比）
        dup = self._dup_issues(chapter_no, content)
        if dup:
            issues.extend(dup)
            score -= 20

        # 3. 输出合规（模块3 基础合规校验）
        if getattr(self.cfg.agent_guard, "output_check", True):
            bad = self._content_check(content)
            if bad:
                issues.append({
                    "type": "合规-疑似违规内容",
                    "detail": "；".join(bad),
                    "level": "大改",
                })
                score -= 40

        # 4. 读者体验（LLM：代入感/爽点/钩子/主动性 + 人物一致性）
        llm_issues, hook_score = self._reader_llm_check(chapter_no, plan_title, plan_goal, content)
        for it in llm_issues:
            issues.append(it)
        if hook_score is not None:
            if hook_score <= 2:
                issues.append({
                    "type": "读者-追更欲过低",
                    "detail": f"追更欲评分 {hook_score}/5，低于 3 分阈值",
                    "level": "大改" if hook_score <= 1 else "中改",
                })
                score -= 20

        # 去重 + 排序（大改在前）
        seen, uniq = set(), []
        for it in issues:
            k = (it.get("type"), it.get("detail"))
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        uniq.sort(key=lambda x: 0 if x.get("level") == "大改" else (1 if x.get("level") == "中改" else 2))

        passed = not any(x.get("level") == "大改" for x in uniq)
        return {"passed": passed, "issues": uniq, "score": max(0, min(100, score))}

    # ---------- 确定性规则实现 ----------
    def _dup_issues(self, chapter_no: int, content: str) -> list[dict]:
        if not content:
            return []
        grams = {content[i:i + _NGRAM] for i in range(0, len(content) - _NGRAM + 1)}
        if not grams:
            return []
        issues = []
        for no in range(max(1, chapter_no - _MAX_RECENT), chapter_no):
            prev = self.store.load_chapter(no)
            if not prev or not prev.content:
                continue
            pgrams = {prev.content[i:i + _NGRAM] for i in range(0, len(prev.content) - _NGRAM + 1)}
            if not pgrams:
                continue
            ratio = len(grams & pgrams) / len(pgrams)
            if ratio > _DUP_RATIO:
                issues.append({
                    "type": "结构-与前文重复",
                    "detail": f"与第{no}章重叠率 {ratio:.0%}，疑似大段复用",
                    "level": "中改",
                })
        return issues

    def _content_check(self, content: str) -> list[str]:
        from ..safety import basic_content_check
        return basic_content_check(content)

    # ---------- LLM 校验（读者视角 + 人物一致性） ----------
    def _reader_llm_check(self, chapter_no: int, plan_title: str, plan_goal: str,
                          content: str) -> tuple[list[dict], int | None]:
        if self.guard is not None:
            self.guard.enter_scene("review")
        try:
            world = self.store.load_world()
            char_block = ""
            if world and world.characters:
                char_block = "；".join(
                    f"{c.name}({c.role or ''}：{(c.desc or '')[:60]})"
                    for c in world.characters[:12] if getattr(c, "name", None)
                )
            system, user = reader_review_prompt(
                self.cfg, chapter_no, plan_title, plan_goal, content
            )
            if char_block:
                user = (
                    user.replace("【本章正文】",
                                 "【人物卡】\n" + char_block + "\n\n【本章正文】")
                    + "\n额外校验：正文中的人物言行是否符合【人物卡】设定？有无性格/身份/称呼与人物卡冲突？"
                    "（冲突则加入 issues，type='人物一致性'）"
                )
            raw = self.llm.chat(system, user, temperature=0.3, max_tokens=1200)
            data = self._parse_json(raw)
            issues = [{
                "pos": it.get("pos", ""),
                "type": f"读者-{it.get('type', '')}",
                "detail": it.get("detail", ""),
                "level": it.get("level", "小改"),
            } for it in (data.get("issues") or []) if isinstance(it, dict)]
            hook = data.get("追更欲")
            hook_score = int(hook) if isinstance(hook, (int, float)) and 1 <= int(hook) <= 5 else None
            return issues, hook_score
        except Exception:
            return [], None

    # ---------- 自动重写（1 次） ----------
    def _rewrite_once(self, chapter_no: int, plan_title: str, plan_goal: str,
                      content: str, issues: list[dict]) -> str:
        try:
            target = max(self.cfg.chapter_words, 500)
            issue_block = "\n".join(
                f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:8]
            )
            prompt = (
                f"你是资深网文写手。以下是第{chapter_no}章《{plan_title}》的正文，"
                f"质检发现如下问题，请重写本章正文。\n"
                f"【本章目标】{plan_goal}\n"
                f"【质检问题】\n{issue_block}\n\n"
                f"重写要求：\n"
                f"1. 保留原有剧情走向、人物设定与关键对话，不推翻前文既定事实\n"
                f"2. 针对性修复上述每个问题（补足字数/去除重复/修正人物一致性/提升读者体验）\n"
                f"3. 正文（不含摘要）达到约 {target} 字\n"
                f"4. 输出格式：先输出正文，正文结束后另起一行输出【摘要】加 1-2 句剧情摘要\n\n"
                f"【当前正文】\n{content}"
            )
            text = self.llm.chat(
                "你是资深网络小说写手，擅长按质检意见重写章节。",
                prompt, temperature=0.8, max_tokens=8000,
            )
            return (text or "").strip()
        except Exception:
            return ""

    # ---------- 落库与工具 ----------
    def _save_review(self, chapter_no: int, issues: list[dict], result: dict) -> None:
        """与既有 review.json 格式兼容：该章记录合并校验问题。"""
        try:
            path = self.store.dir / "review.json"
            existing = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    existing = []
            existing = [r for r in existing if int(r.get("chapter", 0)) != chapter_no]
            existing.append({
                "chapter": chapter_no,
                "issues": issues,
                "auto_revised": False,
                "quality_check": {
                    "passed": bool(result.get("passed")),
                    "score": int(result.get("score", 0)),
                    "checked_at": time.time(),
                },
            })
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _parse_json(text: str) -> dict:
        t = (text or "").strip()
        if t.startswith("```"):
            lines = t.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            t = "\n".join(lines).strip()
        try:
            data = json.loads(t)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _split_summary(text: str) -> tuple[str, str]:
    """按『【摘要】』标记拆分正文与摘要（与 stages 保持一致）。"""
    marker = "【摘要】"
    idx = text.rfind(marker)
    if idx > 0:
        body = text[:idx].strip()
        summary = text[idx + len(marker):].strip().replace("\n", " ")
        return body, summary
    return text.strip(), ""
