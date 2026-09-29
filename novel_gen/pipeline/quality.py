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
from .templates import reader_review_prompt, _output_discipline
from .trace import TraceRecorder

# 校验阈值（确定性规则）
_MIN_WORDS_RATIO = 0.3        # 字数 < 目标 30% 视为严重不足（触发重写）
_NGRAM = 8                    # 重复检测 n-gram 长度
_DUP_RATIO = 0.25             # 与任一近章重叠率 > 25% 视为明显重复
_MAX_RECENT = 3               # 参与重复检测的最近章节数

# 章内自重复（多稿拼接）检测
_SELF_NGRAM = 24              # 章内重复检测 n-gram 长度
_SELF_DUP_RATIO = 0.25        # 章内被重复 n-gram 覆盖比例 > 25% → 疑似多稿拼接
_SALVAGE_MIN_LINE = 8         # 抢救时视为「草稿分界标记」的最小行长度
_SALVAGE_MAX_LINE = 16        # 草稿分界标记的最大行长度（短促收尾/面板行；长行多为正常叙事重复）
_SALVAGE_SPREAD = 300         # 同一标记两次出现的最小字距（区分跨草稿重复 vs 章内呼应）

# 判定「正文完整收尾」的句末标点
_TERMINAL = set("。！？…”\"』」）】）…。!?"")）】")

# 章内同句复读检测（REQ-章内自洽与机制账本）：母题密度提示词的成文后兜底
_REPEAT_MIN_LEN = 8         # 参与复读统计的最短归一化句长
_REPEAT_LONG_LEN = 12       # 叙述长句出现 2 次即视为整句复读
_REPEAT_HARD_COUNT = 3      # 叙述/对话句出现 ≥3 次 → 大改（触发自动重写）
_DIALOG_ALLOWED = 2         # 对话句（口头禅）允许 2 次（writer.md：同一口头禅每章 ≤2 次）
_PANEL_KEYWORDS = ("次数", "进度", "蚀痕", "剩余", "面板")   # 数字面板行信号


def _has_type(issues, keyword: str) -> bool:
    """issues 中是否存在 type 含指定关键词的问题。"""
    return any(keyword in (it.get("type", "") or "") for it in issues)


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
        rewritten_occurred = False

        # 事后自愈①：章内多稿拼接 → 确定性单稿抢救（不依赖 LLM，避免重写再次堆稿）
        if _has_type(issues, "章内大量自重复"):
            salvaged = self._salvage_single_draft(content)
            if salvaged and salvaged != content and len(salvaged) > len(content) * 0.25:
                content = salvaged
                nc, ns = _split_summary(content)
                if nc:
                    content = nc
                if ns:
                    summary = ns
                rewritten_occurred = True
                if not silent:
                    print(f"  [自校验] 第{chapter_no}章检测到多稿拼接，已抢救为单一草稿（{len(content)} 字）")
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
                nc, ns = _split_summary(content)
                if nc:
                    content = nc
                if ns:
                    summary = ns
                rewritten_occurred = True
                # 复检
                result = self.check(chapter_no, plan_title, plan_goal, content)
                issues = result["issues"]
                if not silent:
                    print(f"  [自校验] 重写后复检：{'通过' if result['passed'] else '仍存在问题（已标记）'}")

        # 结果落库（review.json 兼容既有格式，P2-4：记录实际是否触发重写）
        self._save_review(chapter_no, issues, result, auto_revised=rewritten_occurred)
        # 结果入 trace（模块1 快照，P2-4：extra 带 score/issue_count/auto_revised 供报表聚合）
        if self.trace is not None:
            self.trace.record_quality(chapter_no, result, issues, auto_revised=rewritten_occurred)

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

        # 2.4 章内多稿自重复（确定性）：同一章内部出现大段重复 → 疑似草稿拼接
        #    补盲：_dup_issues 只比对前几章，看不到本章内部多段草稿互相重复。
        for it in self._self_dup_issues(content):
            issues.append(it)
            score -= 30

        # 2.7 章内同句复读（确定性，REQ-章内自洽与机制账本）：同一句/同一动作
        #    整句原样复读（城墙墓碑、复活体感、铜钱、被扑倒等一字不差重复）。
        #    _self_dup_issues 只抓多稿拼接（短行+大段 n-gram），不覆盖整句复读，
        #    这里对母题密度提示词做成文后兜底：命中大改级 → 触发 1 次自动重写。
        for it in self._repeat_sentence_issues(content):
            issues.append(it)
            score -= 15

        # 2.6 结尾完整性（确定性）：正文未以句末标点收尾 → 疑似中途截断
        for it in self._ending_issues(content):
            issues.append(it)
            score -= 30

        # 2.5 AI 痕迹量化检测（P1-4，确定性规则，只扣分不单独阻断）
        if getattr(self.cfg, "ai_taste_check", True):
            try:
                from .ai_taste import ai_taste_issues
                taste_issues = ai_taste_issues(
                    content,
                    threshold=int(getattr(self.cfg, "ai_taste_threshold", 60) or 60),
                )
                if taste_issues:
                    issues.extend(taste_issues)
                    score -= 20
            except Exception:
                pass

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

        # 4.5 章内前后矛盾自检（LLM，REQ-章内自洽与机制账本·扩展）：
        # 抓确定性规则与读者视角都漏的硬矛盾——身体状态/知觉范围前后不一、
        # 同类生物行为模式不一、方位指代错误、同一意象比喻两版本。
        for it in self._consistency_llm_check(chapter_no, content):
            issues.append(it)
            score -= 25

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

    @staticmethod
    def _draft_markers(content, min_count=2, min_line=_SALVAGE_MIN_LINE,
                       max_line=_SALVAGE_MAX_LINE, spread=_SALVAGE_SPREAD) -> dict:
        """检测「同一章多稿首尾拼接」的分界标记：跨大跨度重复出现的独特短行。

        返回 {行文本: [字符偏移,...]}；无拼接返回空 dict。
        只统计「短促收尾/面板行」（长度在 min_line~max_line 之间）：多稿拼接时
        每篇草稿都会自带同一句短收尾（如"今日可死次数：2。"），而长行重复多为
        正常叙事复用（如第2章一条 40 字长行重复 2 次是正常内容），不能当标记。
        """
        if not content or len(content) < min_line * 3:
            return {}
        lines = content.split("\n")
        occ: dict[str, list[int]] = {}
        pos = 0
        for ln in lines:
            s = ln.strip()
            if min_line <= len(s) <= max_line:
                occ.setdefault(s, []).append(pos)
            pos += len(ln) + 1
        return {s: ps for s, ps in occ.items()
                if len(ps) >= min_count and ps[-1] - ps[0] >= spread}

    def _self_dup_issues(self, content: str) -> list[dict]:
        """章内自重复（确定性）：同一章正文内部出现大段重复 → 疑似多稿拼接。

        这是对「与前文重复」检查的补盲：后者只比对前几章，看不见本章内部的
        多段草稿彼此重复。两个互补信号，任一命中即视为拼接：
        A. 草稿分界标记：同一短促收尾/面板行（8~16字）跨大跨度重复 ≥3 次
           （正常单章几乎不会，多稿拼接必然多篇共用同一句短收尾）；
        B. n-gram 覆盖：被「出现≥2次的 n-gram」覆盖的字符占比过高
           （草稿之间大段内容相同但措辞略有差异，或草稿数少<3 时用）。
        """
        if not content or len(content) < _SELF_NGRAM * 3:
            return []
        # 信号 A：草稿分界标记（短收尾行重复 ≥3 次）
        if self._draft_markers(content, min_count=3):
            return [{
                "type": "结构-章内大量自重复（疑似多稿拼接）",
                "detail": "正文中出现跨大跨度重复的短促收尾句（疑似同一章多段草稿首尾拼接）；"
                          "应只保留单一完整版本",
                "level": "大改",
            }]
        # 信号 B：n-gram 覆盖比例
        grams: dict[str, list[int]] = {}
        for i in range(len(content) - _SELF_NGRAM + 1):
            grams.setdefault(content[i:i + _SELF_NGRAM], []).append(i)
        covered = [False] * len(content)
        for poss in grams.values():
            if len(poss) >= 2:
                for i in poss:
                    for j in range(i, min(i + _SELF_NGRAM, len(content))):
                        covered[j] = True
        ratio = sum(covered) / len(content)
        if ratio > _SELF_DUP_RATIO:
            return [{
                "type": "结构-章内大量自重复（疑似多稿拼接）",
                "detail": (f"正文 {len(content)} 字中约 {ratio:.0%} 由重复片段构成，"
                           "疑似同一章多段草稿被首尾拼接；应只保留单一完整版本"),
                "level": "大改",
            }]
        return []

    def _repeat_sentence_issues(self, content: str) -> list[dict]:
        """章内同句复读（确定性，REQ-章内自洽与机制账本）。

        归一化句子后统计：叙述句（引号外）出现 ≥3 次、或叙述长句 ≥12 字出现 2 次、
        或数字面板行（含次数/进度/蚀痕/剩余/面板+数字）出现 ≥2 次 → 大改；
        叙述短句（8~11 字）出现 2 次 → 中改；对话句（口头禅）≥3 次 → 大改、2 次放行。
        聚合为每条类型一条 issue（附最多 3 个例句），避免刷屏。
        """
        if not content or len(content) < 100:
            return []
        groups: dict[str, dict[str, list[str]]] = {"叙述": {}, "对话": {}}
        for sentence, in_quote in _iter_sentences(content):
            norm = _norm_sentence(sentence)
            is_panel = _is_panel_line(norm)
            if not norm:
                continue
            if len(norm) < _REPEAT_MIN_LEN and not (is_panel and len(norm) >= 5):
                continue
            bucket = "对话" if in_quote else "叙述"
            groups[bucket].setdefault(norm, []).append(sentence.strip())

        hard: list[str] = []
        soft: list[str] = []
        for bucket, norm, sentences in _iter_repeat_groups(groups):
            cnt = len(sentences)
            if cnt >= _REPEAT_HARD_COUNT:
                hard.append(f"[{bucket}×{cnt}] {sentences[0]}")
            elif bucket == "叙述" and cnt == 2:
                if len(norm) >= _REPEAT_LONG_LEN or _is_panel_line(norm):
                    hard.append(f"[叙述×2] {sentences[0]}")
                else:
                    soft.append(f"[叙述×2] {sentences[0]}")
            # 对话 ×2 = 口头禅（writer.md 允许 ≤2 次），不报

        issues: list[dict] = []
        if hard:
            issues.append({
                "type": "写作-同句复读",
                "detail": "同一句/同一动作整句原样复读（" + "；".join(hard[:3]) + "……）；"
                          "同一母题不得原样复读，重复必须有递进，数字面板行重复=数字结算未推进",
                "level": "大改",
            })
        if soft:
            issues.append({
                "type": "写作-同句复读（轻）",
                "detail": "叙述短句原样出现 2 次（" + "；".join(soft[:3]) + "）；建议改写为不同表述",
                "level": "中改",
            })
        return issues

    def _ending_issues(self, content: str) -> list[dict]:
        """结尾完整性（确定性）：正文未以句末标点收尾 → 疑似在句子中途被截断。"""
        if not content:
            return []
        last = content.rstrip()[-1]
        if last not in _TERMINAL:
            return [{
                "type": "结构-正文未完整收尾（疑似截断）",
                "detail": "本章正文未以句末标点结束，疑似在句子中途被截断；应补完结尾",
                "level": "大改",
            }]
        return []

    def _salvage_single_draft(self, content: str) -> str:
        """同章多稿首尾拼接 → 确定性抢救出最完整的单一草稿；无拼接则原样返回。

        用「草稿分界标记」（重复独特长句）切候选段，取「最长且以句末标点收尾」的一段。
        纯确定性实现，不依赖 LLM，避免重写时再次堆稿。
        """
        marks = self._draft_markers(content)
        if not marks:
            return content
        # 选「出现次数最多、行最短」的重复句作分界标记：短而独立的收尾句
        # （如面板行"今日可死次数：2。"）比长开头句更贴近"草稿收尾"，避免切出碎片。
        marker = max(marks, key=lambda s: (len(marks[s]), -len(s)))
        cuts = sorted(marks[marker])
        segs, prev = [], 0
        for c in cuts:
            nl = content.find("\n", c)
            end = nl if nl != -1 else len(content)
            segs.append(content[prev:end])
            prev = end + 1
        segs.append(content[prev:])  # 最后一稿（通常为新起笔的截断稿）

        def _score(s: str) -> int:
            t = s.strip()
            if not t:
                return -1
            return len(t) + (100000 if t[-1] in _TERMINAL else 0)

        best = max(segs, key=_score).strip()
        # 去掉开头的悬空残留行（上一稿收尾的短行，如"明天呢？"）：只去
        # ≤4字 或 ≤12字且以问号/省略号结尾的短行，避免误删真实起句（如"他走得很快。"）
        bl = best.split("\n")
        while len(bl) > 1 and bl[0].strip():
            s = bl[0].strip()
            if len(s) <= 4 or (len(s) <= 12 and s[-1] in "？…"):
                bl = bl[1:]
            else:
                break
        best = "\n".join(bl).strip()
        return best

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

    # ---------- 章内前后矛盾自检（LLM） ----------
    def _consistency_llm_check(self, chapter_no: int, content: str) -> list[dict]:
        """通读全章找硬矛盾：①主角身体状态（伤口位置/知觉范围/服饰损坏）前后不一；
        ②同一类生物/配角的行为模式前后不一；③方位/指代错误（人在A地却描述B地）；
        ④同一意象的比喻出现两个版本。只报硬矛盾，报了即大改。"""
        if not content:
            return []
        try:
            sys = (
                "你是严谨的小说文字编辑，通读本章后只找「前后自相矛盾的硬伤」，不评文笔、不评节奏。\n"
                "只输出 JSON：{\"contradictions\":[{\"type\":\"身体状态/行为模式/方位指代/比喻\",\"detail\":\"具体矛盾：前一处…后一处…\"}]}。\n"
                "判定标准：两处描写不能同时为真才算矛盾；比喻措辞不同但喻体相同不算；"
                "随剧情合理变化的伤口/状态不算矛盾；没有矛盾就输出空数组。"
            )
            user = (
                f"第{chapter_no}章正文：\n{content[:3500]}\n\n"
                "请输出上述 JSON。"
            )
            raw = self.llm.chat(sys, user, temperature=0.2, max_tokens=800)
            data = self._parse_json(raw)
            out = []
            for it in (data.get("contradictions") or []):
                if not isinstance(it, dict):
                    continue
                detail = str(it.get("detail", "")).strip()
                if not detail:
                    continue
                out.append({
                    "type": f"自相矛盾-{it.get('type', '')}",
                    "detail": detail,
                    "level": "大改",
                })
            return out
        except Exception:
            return []

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
                "你是资深网络小说写手，擅长按质检意见重写章节。\n\n"
                + _output_discipline()
                + "\n重写后必须只输出【一个】完整连贯的正文版本，并以句末标点完整收尾。",
                prompt, temperature=0.8, max_tokens=8000,
            )
            return (text or "").strip()
        except Exception:
            return ""

    # ---------- 落库与工具 ----------
    def _save_review(self, chapter_no: int, issues: list[dict], result: dict,
                      auto_revised: bool = False) -> None:
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
                "auto_revised": bool(auto_revised),
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


# ---------- 章内同句复读工具（REQ-章内自洽与机制账本） ----------

_QUOTE_OPEN = {"“": "”", "「": "」", '"': '"'}


def _iter_sentences(text: str):
    """按句末标点切句，返回 (句子原文, 是否在对话引号内)。追踪中英文引号状态。"""
    in_quote = None
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if in_quote is None and ch in _QUOTE_OPEN:
            in_quote = ch
        elif in_quote is not None and ch == _QUOTE_OPEN[in_quote]:
            in_quote = None
        if ch in "。！？…" and buf:
            yield "".join(buf), in_quote is not None
            buf = []
    tail = "".join(buf).strip()
    if tail:
        yield tail, in_quote is not None


def _norm_sentence(s: str) -> str:
    """归一化：只保留中文与字母数字，去空白标点（复读判定基准）。"""
    return "".join(ch for ch in (s or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _is_panel_line(norm: str) -> bool:
    """数字面板行：含 次数/进度/蚀痕/剩余/面板 关键词且带数字（如「今日可死次数2」）。"""
    return any(k in norm for k in _PANEL_KEYWORDS) and any(c.isdigit() for c in norm)


def _iter_repeat_groups(groups: dict):
    """遍历归一化句 → (bucket, norm, 原始句子列表)，按出现次数降序（大问题先报）。"""
    items = []
    for bucket, mp in groups.items():
        for norm, sentences in mp.items():
            items.append((bucket, norm, sentences))
    items.sort(key=lambda t: -len(t[2]))
    return items
