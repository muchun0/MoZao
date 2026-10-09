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
import re
import time

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..store.project import ProjectStore
from ..store.schemas import Chapter
from .templates import reader_review_prompt, _output_discipline
from .beats import _beat_cycle_issues
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

# 机制数字结算校验（REQ-章内自洽与机制账本·扩展）：面板数字跨段同值复现 = 结算未推进
_PANEL_REPEAT_SPAN = 150    # 同一面板数字两次出现的最小字距（> 视为跨事件复现，而非紧邻强调）
_PANEL_SPAN_LABEL = 40      # 面板标签参与定位的最大长度（防误把长句当标签）
# 结算事件锚点：两次同值之间出现"死亡/复活/结算"等强结算词 = 真跨结算点
# 同值复现 → 结算未推进（大改）；无事件词 = 同一结算值的冗余重复显示（中改，不阻断）。
# 排除项（实测误命中）："死一次"（"再死一次"=预判非事件）、"咬穿"（回忆/复述他人死亡）、
# "刷新"（"面板刷新的时候"=设定陈述）——真结算错误时模型通常写"他死了/死亡/复活/结算"。
_PANEL_EVENT_ANCHORS = re.compile(r"死了|死亡|复活|苏醒|睁眼|醒来|结算|重置")
# 语境排除：锚点前 12 字内出现回忆/转述/指认/他人预判词 → 不算结算事件
# （《万墟之上》22:32 版实测 6 处误命中全部是"她看见他死了/你死了/她快死了/
# 他应该已经死了/感知到了他死亡"等回忆指认，无一是主角实时结算）
_PANEL_EVENT_RECALL = re.compile(r"看见|看过|见过|说|喊|叫|读|记得|梦见|听说|以为|感知|想起|回忆|幻觉|残留|应该|快死")

# 自动重写轮数（REQ-章内自洽与机制账本·扩展）：检出问题后最多重写 N 轮，通过即停；
# 全部失败才标记放行（不再"1 次重写不通过即静默交付"）
_MAX_REWRITE = 3

# 多稿拼接抢救保留下限：抢救稿必须保留原文 ≥ 40%（丢超过 60% 拒绝，
# 防止把"数字写错/场景重叠"的正常章节拦腰截断）
_SALVAGE_MIN_KEEP = 0.4

# 章内矛盾 LLM 自检的正文输入上限（字符）：3500→12000，与状态账本抽取
# 对齐——前 3500 字符只能看到前半章，后半章（第三次死亡/次数用尽）的矛盾
# 与机制状态会漏检（REQ-章内自洽与机制账本·扩展）。
_CONSISTENCY_CHARS = 12000


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
        """校验 → 不通过则自动重写（最多 _MAX_REWRITE 轮，通过即停）→ 复检
        → 返回 (最终 Chapter, 校验结果)。

        校验结果同时落 review.json（与既有格式兼容）与 trace。
        """
        result = self.check(chapter_no, plan_title, plan_goal, content, summary)
        issues = result["issues"]
        rewritten_occurred = False

        # 事后自愈①：章内多稿拼接 → 确定性单稿抢救（不依赖 LLM，避免重写再次堆稿）
        if _has_type(issues, "章内大量自重复"):
            salvaged = self._salvage_single_draft(content)
            if salvaged and salvaged != content and len(salvaged) > len(content) * _SALVAGE_MIN_KEEP:
                content = salvaged
                nc, ns = _split_summary(content)
                if nc:
                    content = nc
                if ns:
                    summary = ns
                rewritten_occurred = True
                if not silent:
                    print(f"  [自校验] 第{chapter_no}章检测到多稿拼接，已抢救为单一草稿（{len(content)} 字）")
                result = self.check(chapter_no, plan_title, plan_goal, content, summary)
                issues = result["issues"]

        # 不通过 → 自动重写（最多 _MAX_REWRITE 轮；每轮重写后复检，通过即停；
        # 全部失败才标记 review.json 放行，不再"1 次重写不通过即静默交付"）
        rewrite_rounds = 0
        while not result["passed"] and rewrite_rounds < _MAX_REWRITE:
            rewrite_rounds += 1
            if not silent:
                print(f"  [自校验] 第{chapter_no}章未通过（{len(issues)} 个问题），触发第 {rewrite_rounds}/{_MAX_REWRITE} 次自动重写...")
            if self.guard is not None:
                self.guard.enter_scene("revise")
            rewritten = self._rewrite_once(chapter_no, plan_title, plan_goal, content, issues)
            if not rewritten or len(rewritten) <= len(content) * 0.5:
                if not silent:
                    print("  [自校验] 重写结果无效（为空或长度不足），停止重写")
                break
            content = rewritten
            nc, ns = _split_summary(content)
            if nc:
                content = nc
            if ns:
                summary = ns
            rewritten_occurred = True
            # 复检
            result = self.check(chapter_no, plan_title, plan_goal, content, summary)
            issues = result["issues"]
            if not silent:
                print(f"  [自校验] 第 {rewrite_rounds} 次重写后复检："
                      f"{'通过' if result['passed'] else f'仍有 {len(issues)} 个问题'}")

        # 结果落库（review.json 兼容既有格式，P2-4：记录实际是否触发重写）
        self._save_review(chapter_no, issues, result, auto_revised=rewritten_occurred)
        # 结果入 trace（模块1 快照，P2-4：extra 带 score/issue_count/auto_revised 供报表聚合）
        if self.trace is not None:
            self.trace.record_quality(chapter_no, result, issues, auto_revised=rewritten_occurred)

        return Chapter(no=chapter_no, title=plan_title, content=content,
                       summary=summary, goal=plan_goal), result

    # ---------- 校验 ----------
    def check(self, chapter_no: int, plan_title: str, plan_goal: str,
              content: str, summary: str = "") -> dict:
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

        # 2.8 机制数字结算校验（确定性，REQ-章内自洽与机制账本·扩展）：
        #    同一面板数字（同标签同数值）跨段复现 = 结算未推进（如"可死次数 2/3"
        #    在两次死亡后仍显示 2/3）。区别于复读检测（只检同句）、拼接检测（只检
        #    字面大段重复），这里从"数值账本"角度校验：数字必须随事件严格增减。
        for it in self._panel_progress_issues(content):
            issues.append(it)
            score -= 30

        # 2.9 对话轮重复（确定性兜底）：同一轮问答/对话序列原样重复 ≥2 次
        #    = 场景内循环拼接（"你死了？→你看错了→我看见数字了"三遍）。
        for it in self._dialogue_loop_issues(content):
            issues.append(it)
            score -= 30

        # 2.12 情节级重演检测（确定性，2026-10-09）：同一「节拍序列」（盘问→戳穿→
        #    失控→挡死→死亡→复活→面板→离场 等完整循环）在章内非重叠出现 ≥2 次
        #    = 同事件多版本重演（换措辞循环；文本 n-gram/整句复读/对话轮检测全漏检，
        #    文本相似度盲区见 REQ-scene-plan-align §7.2）。
        for it in _beat_cycle_issues(content):
            issues.append(it)
            score -= 30

        # 2.10 字数审计（确定性·仅记录）：正文远超章节目标字数 → 中改提示；
        #    不再判大改（2026-09-30：超长≠质量失败，纯字数不触发整章重写，省 token；
        #    质量硬伤由其他维度判定，重写时 prompt 字数区间自然压缩）。
        for it in self._length_audit_issues(content):
            issues.append(it)
            score -= 10

        # 2.11 摘要一致性（确定性兜底）：摘要提到的面板分数必须与正文最终结算一致
        #    （《万墟之上》21:52 版：正文两次死亡最终 1/3，摘要却写"3/3→2/3"——摘要
        #    未随正文更新，误导后续章节账本与读者）。摘要无分数 → 不报。
        if summary:
            for it in self._summary_consistency_issues(content, summary):
                issues.append(it)
                score -= 30

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
        # 高质量模式每章开启；普通模式按 consistency_llm_every 降频（默认每 3 章 1 次）。
        _hq = getattr(self.cfg, "high_quality_mode", False)
        _every = max(1, int(getattr(self.cfg, "consistency_llm_every", 3) or 3))
        if _hq or (getattr(self.cfg, "consistency_llm_check", False) and chapter_no % _every == 1):
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
            # 面板数字行不作为「草稿分界标记」：同值复现属于"数字结算未推进"
            # （_panel_progress_issues 专检），不应误判为多稿拼接而拦腰截断
            if (min_line <= len(s) <= max_line
                    and not _is_panel_line(_norm_sentence(s))):
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

        归一化句子后统计：叙述句（引号外）出现 ≥3 次、或叙述长句 ≥12 字出现 2 次
        → 大改；叙述短句（8~11 字）出现 2 次 → 中改；对话句（口头禅）≥3 次 → 大改、
        2 次放行。
        聚合为每条类型一条 issue（附最多 3 个例句），避免刷屏。

        P1-2 起数字面板行（含次数/进度/蚀痕/剩余+数字）不再纳入复读统计——面板行
        重复由 _panel_progress_issues 专检（区分"结算未推进"大改/"冗余显示"中改），
        复读再判大改会造成双重大改、误触发重写（《万墟之上》22:32 版实测）。
        """
        if not content or len(content) < 100:
            return []
        groups: dict[str, dict[str, list[str]]] = {"叙述": {}, "对话": {}}
        for sentence, in_quote in _iter_sentences(content):
            norm = _norm_sentence(sentence)
            if not norm or len(norm) < _REPEAT_MIN_LEN:
                continue
            if _is_panel_line(norm):
                continue  # 面板行归 _panel_progress_issues 专检
            bucket = "对话" if in_quote else "叙述"
            groups[bucket].setdefault(norm, []).append(sentence.strip())

        hard: list[str] = []
        soft: list[str] = []
        for bucket, norm, sentences in _iter_repeat_groups(groups):
            cnt = len(sentences)
            if cnt >= _REPEAT_HARD_COUNT:
                hard.append(f"[{bucket}×{cnt}] {sentences[0]}")
            elif bucket == "叙述" and cnt == 2:
                if len(norm) >= _REPEAT_LONG_LEN:
                    hard.append(f"[叙述×2] {sentences[0]}")
                else:
                    soft.append(f"[叙述×2] {sentences[0]}")
            # 对话 ×2 = 口头禅（writer.md 允许 ≤2 次），不报

        issues: list[dict] = []
        if hard:
            issues.append({
                "type": "写作-同句复读",
                "detail": "同一句/同一动作整句原样复读（" + "；".join(hard[:3]) + "……）；"
                          "同一母题不得原样复读，重复必须有递进",
                "level": "大改",
            })
        if soft:
            issues.append({
                "type": "写作-同句复读（轻）",
                "detail": "叙述短句原样出现 2 次（" + "；".join(soft[:3]) + "）；建议改写为不同表述",
                "level": "中改",
            })
        return issues

    def _panel_progress_issues(self, content: str) -> list[dict]:
        """机制数字结算校验（确定性）：同一面板数字（同标签同数值）跨段复现 = 结算未推进。

        如「今日可死次数：2/3」在两次死亡后仍显示「2/3」——数字未随事件递减。
        与 _repeat_sentence_issues 的区别：这里按「标签+数值」对齐（不要求整句相同），
        且要求两次出现间隔超过 _PANEL_REPEAT_SPAN（过滤"数字跳了一下"式紧邻强调）。
        正常递减（3/3→2/3→1/3）不误报。

        事件锚点（2026-09-30 修复）：两次同值之间若出现死亡/复活/刷新等结算事件词
        → 真"跨结算点同值"→ 大改；若无事件词（如复活后确认、赶路途中再看一眼、
        章末备注栏变化时数字本就不变）→ 属同一结算值的冗余重复显示 → 降级中改
        （《万墟之上》21:52 版"1/3"三次显示被判大改属误报，本规则修复）。
        """
        if not content or len(content) < _PANEL_REPEAT_SPAN * 2:
            return []
        pattern = re.compile(
            r"([\u4e00-\u9fff]{0,12}?(?:次数|进度|蚀痕|剩余))\s*[：:]\s*"
            r"([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
        )
        hits: dict[tuple[str, str], list[int]] = {}
        for m in pattern.finditer(content):
            label = (m.group(1).strip() or "")[-_PANEL_SPAN_LABEL:]
            num = (m.group(2) or "").replace(" ", "")
            if not label or not num:
                continue
            hits.setdefault((label, num), []).append(m.start())
        major: list[str] = []   # 跨结算点同值（真未推进）
        minor: list[str] = []   # 无事件冗余重复显示
        for (label, num), poss in hits.items():
            if len(poss) < 2:
                continue
            for a, b in zip(poss, poss[1:]):
                gap = b - a
                if gap <= _PANEL_REPEAT_SPAN:
                    continue
                seg = content[a:b]
                # 事件锚点须跳过回忆/转述/他人语境（"她看见他死了"不算结算事件）
                event = False
                for m in _PANEL_EVENT_ANCHORS.finditer(seg):
                    # 特判"快死了"=将死未死（非结算）：锚点"死了"前 1 字为"快"
                    if m.group() == "死了" and seg[max(0, m.start() - 1):m.start()] == "快":
                        continue
                    ctx = seg[max(0, m.start() - 16):m.start()]
                    if not _PANEL_EVENT_RECALL.search(ctx):
                        event = True
                        break
                if event:
                    major.append(f"{label}：{num}")
                else:
                    minor.append(f"{label}：{num}")
        out: list[dict] = []
        if major:
            out.append({
                "type": "写作-机制数字未结算推进",
                "detail": "面板数字「" + "；".join(major[:3]) + "」在死亡/结算事件后仍未变化，"
                          "数值未随事件结算推进（如死亡次数未递减）；"
                          "数字必须按机制卡随事件严格增减，禁止同值跨结算点复现",
                "level": "大改",
            })
        if minor:
            out.append({
                "type": "写作-面板数字重复显示（冗余）",
                "detail": "面板数字「" + "；".join(minor[:3]) + "」在无新事件时重复显示，"
                          "稀释面板的紧张感；面板数字只在结算/刷新瞬间出现一次，"
                          "后续确认状态改用叙述（如'他看了一眼面板，还剩一次'）",
                "level": "中改",
            })
        return out

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

    def _dialogue_loop_issues(self, content: str) -> list[dict]:
        """场景内对话轮重复（确定性兜底，防'同一轮问答写多遍'循环拼接）。

        抓相邻台词对（连续两句台词组成的问答序列）重复出现：同一轮对话若写成
        「你死了？→你看错了→我看见数字了」多遍（《万墟之上》21:27 版三遍循环），
        相邻台词对必然 ≥2 次重复。区别于 _repeat_sentence_issues 的单句统计：
        这里按"两句台词序列"对齐，命中即大改（场景级循环拼接）。
        """
        if not content or len(content) < 300:
            return []
        lines = [s.strip() for s, in_quote in _iter_sentences(content) if in_quote]
        norms = [_norm_sentence(s) for s in lines if _norm_sentence(s)]
        pairs: dict[tuple[str, str], int] = {}
        for a, b in zip(norms, norms[1:]):
            if a and b:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1
        bad: list[str] = []
        for (a, b), cnt in pairs.items():
            if cnt >= 2 and len(a) >= 3 and len(b) >= 3:
                bad.append(f"「{a}→{b}」×{cnt}")
        if not bad:
            return []
        return [{
            "type": "写作-对话轮重复（循环拼接）",
            "detail": "同一轮问答/对话序列原样重复（" + "；".join(bad[:3]) + "）；"
                      "同一场景的同一轮对话只允许出现一次，重复=循环拼接，必须重写合并",
            "level": "大改",
        }]

    def _length_audit_issues(self, content: str) -> list[dict]:
        """字数审计（确定性·仅记录，不判大改）：正文远超章节目标字数。

        2026-09-30 方向修正（用户确认）：超长 ≠ 质量失败，纯字数不触发整章重写
        （重写 3 轮全文 + 复检 = 数万 token 浪费，内容质量正常的超长正文应保留）。
        字数只产出中改记录：①提醒模型后续收敛；②若同时存在质量大改（截断/循环/
        拼接/矛盾），重写 prompt 已含字数区间约束，重写时自然压缩（4471 字版经此
        修正后不再为字数被拖去重写）。
        剥离【摘要】后统计正文；>1.35 倍目标 → 中改提示。
        """
        target = int(getattr(self.cfg, "chapter_words", 0) or 0)
        if target <= 0 or not content:
            return []
        import re as _re
        body = _re.split(r"【摘要】", content)[0].strip()
        if not body:
            return []
        n = len(body)
        if n > target * 1.35:
            return [{
                "type": "写作-字数超出目标",
                "detail": f"正文 {n} 字，超过本章目标字数 {target} 的 1.35 倍；"
                          f"内容质量正常时保留（不为此重写），后续章节注意收敛",
                "level": "中改",
            }]
        return []

    def _summary_consistency_issues(self, content: str, summary: str) -> list[dict]:
        """摘要一致性（确定性兜底）：摘要提到的面板分数必须与正文最终结算一致。

        《万墟之上》21:52 版：正文死亡两次、最终结算 1/3，摘要却写"面板从3/3结算
        为2/3"——摘要未随正文更新（只提取了第一次死亡）。正文无面板分数或摘要无
        分数 → 不报（不误伤无机制数字的章节）。
        """
        if not content or not summary:
            return []
        import re as _re
        panel = _re.findall(r"可死次数\s*[:：]\s*(\d+)\s*[／/]\s*(\d+)", content)
        if not panel:
            return []
        final_body = "/".join(panel[-1])
        sum_nums = _re.findall(r"(\d+)\s*[／/]\s*(\d+)", summary)
        if not sum_nums:
            return []
        final_sum = "/".join(sum_nums[-1])
        if final_sum == final_body:
            return []
        return [{
            "type": "写作-摘要与正文最终状态不符",
            "detail": f"摘要提到「{final_sum}」，而正文最终结算为「{final_body}」——"
                      f"摘要未随正文更新，应覆盖本章最终面板状态（死亡次数递减到哪）",
            "level": "大改",
        }]

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
        ④同一意象的比喻出现两个版本；⑤同一事件出现两个版本（同人物+同冲突+同结果，
        换地点/换描写重写一遍，如"救苏妄言引开赤尉"空地版后又写坡地版）。
        只报硬矛盾，报了即大改。"""
        if not content:
            return []
        try:
            sys = (
                "你是严谨的小说文字编辑，通读本章后只找「前后自相矛盾的硬伤」，不评文笔、不评节奏。\n"
                "只输出 JSON：{\"contradictions\":[{\"type\":\"身体状态/行为模式/方位指代/比喻/事件重复\",\"detail\":\"具体矛盾：前一处…后一处…\"}]}。\n"
                "判定标准：两处描写不能同时为真才算矛盾；比喻措辞不同但喻体相同不算；"
                "随剧情合理变化的伤口/状态不算矛盾；没有矛盾就输出空数组。\n"
                "特别注意⑤同一事件重复：同一人物面对同一冲突对象、实际做出同一件事（救/围/杀/逃/挡等行为动作）"
                "且结果相同，即使换了地点或改了细节，也算'同一事件两版本'（如先写'陆沉冲下去救苏妄言引开赤尉'"
                "后又写一遍'坡地上苏妄言又被赤尉围、陆沉再次冲下去救'）——报 type=事件重复，"
                "detail 必须引用两处【实际发生的行为原句】，写明共同的人物/冲突/结果。\n"
                "【事件重复排除条款·必须遵守】以下情况一律【不得】判为事件重复：\n"
                "a) 同一冲突对象下，人物的'内心决定/犹豫/退缩念头'（如'决定绕走'、'想不救'、'打算撤退'）"
                "与后续'实际行动'（如'冲出去救人'）属于'抉择→行动'的因果弧光，两者是先后承接关系、"
                "可以同时为真，不是同一事件的两个版本；\n"
                "b) 未执行的念头、计划、心理活动（未实际发生的行为）不算'事件发生'；"
                "只有实际发生的动作才构成事件；\n"
                "c) 若无法引用两处【实际发生行为】的原句，不得输出事件重复。"
            )
            user = (
                f"第{chapter_no}章正文：\n{content[:_CONSISTENCY_CHARS]}\n\n"
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

    # ---------- 自动重写（1 次；REQ-token优化 P1-4：定点化，不整章重出） ----------
    def _rewrite_once(self, chapter_no: int, plan_title: str, plan_goal: str,
                      content: str, issues: list[dict]) -> str:
        """优先只重写问题段落（省整章重出 token 60~75%）；无定位点/跨度过大 → 整章重写。"""
        try:
            if getattr(self.cfg, "targeted_rewrite", True):
                spans = _locate_problem_spans(content, issues)
                if spans:
                    spans = _align_to_paras(content, spans)
                    # 合并后仍为单一窗口且覆盖 <60% 正文 → 定点重写
                    if len(spans) == 1 and (spans[0][1] - spans[0][0]) < len(content) * 0.6:
                        rewritten = self._rewrite_span(
                            chapter_no, plan_title, plan_goal, content, spans[0], issues,
                        )
                        if rewritten and rewritten != content:
                            return rewritten
            return self._rewrite_whole(chapter_no, plan_title, plan_goal, content, issues)
        except Exception:
            return ""

    def _rewrite_whole(self, chapter_no: int, plan_title: str, plan_goal: str,
                       content: str, issues: list[dict]) -> str:
        """整章重写（原行为，无定位信息/定点失败时兜底）。

        注入设定上下文（世界观/机制卡/人物卡/章末账本）——重写不得在"信息真空"
        中进行，否则模型不知道机制结算规则，数字与矛盾修不动（实测复检仍 5 条）。
        """
        try:
            target = max(self.cfg.chapter_words, 500)
            issue_block = "\n".join(
                f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:8]
            )
            # 2026-10-01：问题含"事件重复/两版本"（矛盾自检事件重复、同句复读整段重复等）
            # 时，重写必须显式执行"每事件只写一遍"——质检问题清单只有位置描述，
            # 不注入事件序列纪律，模型会把重复版本换个写法又保留（4500 字版重写 3 轮
            # 修不掉救人二版/死亡复刻即此类）。
            has_event_dup = any(
                "事件重复" in (it.get("type") or "")
                or "两版本" in (it.get("detail") or "")
                or "整段" in (it.get("detail") or "")
                or "原样复刻" in (it.get("detail") or "")
                for it in issues
            )
            dedup_block = (
                "\n本章存在重复事件/两版本拼接，重写必须执行：\n"
                "8. 本章每个事件（死亡-复活、救援、逃离、被围等）只允许完整发生一次：\n"
                "   删除重复的事件版本，保留最完整的一个；同一事件不得以任何形式再次出现"
                "（包括换地点、换措辞、换细节重写一遍）；被删除版本中不可替代的信息"
                "（如关键对话、关键状态）合并进保留版本\n"
                "9. 事件之间必须因果推进：后一事件从上一事件的结局之后开始，"
                "禁止回到上一事件开头状态重演（连'伏看-犹豫-起手式'都禁止重复）\n"
                "10. 每个事件的结束状态（面板数字/蚀痕位置/伤势）必须比上一事件结束时有增量变化，"
                "禁止两事件结束时间一状态\n\n"
            ) if has_event_dup else ""
            ctx = self._context_block(chapter_no)
            prompt = (
                f"你是资深网文写手。以下是第{chapter_no}章《{plan_title}》的正文，"
                f"质检发现如下问题，请重写本章正文。\n"
                f"【本章目标】{plan_goal}\n"
                + (f"\n{ctx}\n" if ctx else "")
                + f"【质检问题】\n{issue_block}\n\n"
                f"重写要求：\n"
                f"1. 保留原有剧情走向、人物设定与关键对话，不推翻前文既定事实\n"
                f"2. 针对性修复上述每个问题（补足字数/去除重复/修正人物一致性/提升读者体验）\n"
                f"3. 机制数字（面板/蚀痕等）必须按【金手指机制卡】与【机制初始值/面板标准答案】"
                f"随事件严格结算推进，禁止同值跨段复现\n"
                f"4. 正文（不含摘要）控制在约 {target} 字（±15% 以内）：不足则补足，"
                f"超出则精炼删减；禁止超写注水——超长输出会被截断丢弃\n"
                f"5. 面板数字（可死次数/蚀痕进度等）只在结算/刷新瞬间显示一次："
                f"死亡/复活结算后显示新数字，其余场景改用叙述（如'他看了一眼面板，还剩一次'），"
                f"禁止同一数值重复显示；蚀痕等身体状态用累积口径（'已至小臂中段，比上次多一寸'），"
                f"禁止同一状态每处重新计量\n"
                f"6. 同一角色本章外貌/服装/代称全局唯一（不得同一角色两套描述两版本）\n"
                f"7. 输出格式：先输出正文，正文结束后另起一行输出【摘要】加 1-2 句剧情摘要；"
                f"摘要必须写本章最终结算后的面板状态（如'死亡次数减至 1/3'）\n"
                + dedup_block
                + f"【当前正文】\n{content}"
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

    def _rewrite_span(self, chapter_no: int, plan_title: str, plan_goal: str,
                      content: str, span: tuple[int, int], issues: list[dict]) -> str:
        """只重写问题窗口：上下文 + 问题段 → LLM 输出替换段 → Python 拼回（零结构破坏）。"""
        try:
            s, e = span
            ctx_before = content[max(0, s - 200):s]
            ctx_after = content[e:min(len(content), e + 200)]
            problem_seg = content[s:e]
            issue_block = "\n".join(
                f"- [{it.get('type','')}] {it.get('detail','')}" for it in issues[:5]
            )
            ctx = self._context_block(chapter_no)
            prompt = (
                f"你是资深网文写手。以下是第{chapter_no}章《{plan_title}》的一个段落，"
                f"质检发现如下问题，请只重写这个段落（不要改动上下文，不要输出其他内容）。\n"
                f"【本章目标】{plan_goal}\n"
                + (f"\n{ctx}\n" if ctx else "")
                + f"【质检问题】\n{issue_block}\n\n"
                f"【段落前文（保持衔接，不要重复）】\n{ctx_before}\n\n"
                f"【需要重写的段落】\n{problem_seg}\n\n"
                f"【段落后文（保持衔接，不要提前写）】\n{ctx_after}\n\n"
                f"重写要求：\n"
                f"1. 保留剧情走向、人物设定与关键信息，不推翻前文既定事实\n"
                f"2. 针对性修复上述问题（去重复/修正矛盾/补足内容）；机制数字必须按机制卡随事件结算\n"
                f"3. 只输出替换后的这一个段落，不要解释，不要输出【摘要】\n"
                f"4. 以句末标点完整收尾"
            )
            text = (self.llm.chat(
                "你是资深网络小说写手，擅长按质检意见局部修改章节段落。",
                prompt, temperature=0.8, max_tokens=4000,
            ) or "").strip()
            if not text:
                return ""
            return content[:s] + text + content[e:]
        except Exception:
            return ""

    def _context_block(self, chapter_no: int, max_chars: int = 2500) -> str:
        """重写时的设定上下文（防"真空重写"）：世界观/机制卡/人物卡/章末账本。

        根因（《万墟之上》第1章单章重写实测）：_rewrite_whole/_rewrite_span 原
        只给【本章目标】+【质检问题】+【当前正文】，模型不知道机制结算规则
        （如"每死一次减 1"），重写后数字与矛盾修不动，复检仍是同样的 5 条。
        """
        parts: list[str] = []
        try:
            world = self.store.load_world()
            if world:
                wv = getattr(world, "world_view", None)
                if wv:
                    parts.append("【世界设定（正文必须遵守）】" + str(wv)[:800])
                mr = getattr(world, "mechanism_rules", None)
                if mr:
                    parts.append("【金手指机制卡（机制数字结算的唯一权威）】" + str(mr)[:1000])
                mb = getattr(world, "mechanism_baseline", None)
                if mb:
                    parts.append("【机制初始值/面板标准答案（正文数字的唯一来源）】" + str(mb)[:600])
                chars = getattr(world, "characters", None) or []
                if chars:
                    blk = "；".join(
                        f"{getattr(c, 'name', '')}（{getattr(c, 'role', '')}："
                        f"{(getattr(c, 'desc', '') or '')[:50]}）"
                        for c in chars[:8] if getattr(c, "name", None)
                    )
                    if blk:
                        parts.append("【人物卡（人物言行/外貌必须一致）】" + blk)
        except Exception:
            pass
        try:
            st = self.store.load_chapter_state()
            if st:
                st_ch = int(st.get("chapter") or 0)
                # 账本注入仅限「章号 < 当前章」的既有前文状态；账本章号 >= 当前章
                # 说明是本章旧稿/旧版本的账本，不得当"上章承接"喂给模型——
                # 否则重写第 1 章会拿到旧稿的「2/3」当标准答案，数字回跳修不动
                if st_ch and st_ch < chapter_no:
                    nums = st.get("numbers") or []
                    if nums:
                        parts.append("【上一章机制数字（本章必须承接结算，不得回退）】"
                                     + "；".join(str(x) for x in nums))
        except Exception:
            pass
        joined = "\n".join(parts)
        return joined[:max_chars]

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


# ---------- 定点重写定位工具（REQ-token优化 P1-4） ----------

_CTX = 200          # 句锚点窗口半径
_MIN_FRAG = 8       # 参与匹配的 detail 片段最短归一化长度
_SPAN_GAP = 400     # 间距 ≤400 的窗口合并


def _locate_problem_spans(content: str, issues: list[dict]) -> list[tuple[int, int]]:
    """从 issue 定位问题段落（起点, 终点），供定点重写。

    定位来源优先级：
    1. issue["pos"]（如「段3」「第3段」）→ 按段落序号定位；
    2. issue["detail"] 中 ≥8 字的片段 → 归一化后在正文句子中匹配，
       以句为锚点取前后 _CTX 字符窗口；
    3. 无法定位 → 返回空列表（调用方整章重写）。
    """
    if not content or not issues:
        return []
    import re
    spans: list[tuple[int, int]] = []
    # 1) 段落定位
    para_nos = set()
    for it in issues:
        pos = (it.get("pos") or "").strip()
        if not pos:
            continue
        m = re.search(r"(\d+)", pos)
        if m:
            para_nos.add(int(m.group(1)))
    if para_nos:
        paras = content.split("\n")
        for pi in sorted(para_nos):
            if 1 <= pi <= len(paras):
                seg = paras[pi - 1]
                start = content.find(seg)
                if start >= 0:
                    spans.append((start, start + len(seg)))
        if spans:
            return _merge_spans(spans)
    # 2) 句锚点定位：detail 中 ≥8 字片段归一化匹配正文句子
    anchors: list[str] = []
    for it in issues:
        detail = it.get("detail") or ""
        for m in re.finditer(r"[“\"『]?([^”\"』，。；：]{8,40})[”\"』]?", detail):
            frag = m.group(1).strip()
            if frag:
                anchors.append(frag)
    if anchors:
        sents = list(_iter_sentences(content))
        for frag in anchors:
            nf = _norm_sentence(frag)
            if len(nf) < _MIN_FRAG:
                continue
            for sentence, _ in sents:
                ns = _norm_sentence(sentence)
                if ns and (nf in ns or ns in nf):
                    i = content.find(sentence)
                    if i >= 0:
                        spans.append((max(0, i - _CTX),
                                      min(len(content), i + len(sentence) + _CTX)))
                    break
        if spans:
            return _merge_spans(spans)
    return []


def _merge_spans(spans: list[tuple[int, int]], gap: int = _SPAN_GAP) -> list[tuple[int, int]]:
    """间距 ≤ gap 的窗口合并为一个（一次调用重写合并窗口）。"""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _align_to_paras(content: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """把窗口边界对齐到段落首尾，避免重写拼接处断句。"""
    out = []
    for s, e in spans:
        s0 = content.rfind("\n", 0, s) + 1
        e0 = content.find("\n", e)
        if e0 == -1:
            e0 = len(content)
        out.append((s0, e0))
    return out
