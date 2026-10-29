# -*- coding: utf-8 -*-
"""小说级记忆系统（P0-7，docs/TECH-book-memory.md）。

背景：历轮真实重生成（v1~v5）证明所有防线（节拍词典/面板正则/dedup 关键词/LCS
阈值）都是"写死的规则"——规则覆盖的场景已能拦截，但新名词、新错误模式（第 3 章
两次下墟双战斗、第 6 章酒肆双版本、第 7 章跨章扣次错）规则不认识 → 漏检 → 每轮
只能人工定位再改代码，线性成本不可扩展。

本模块给每本书加持久化记忆：质检出问题 → 结构化写入踩坑账本 → 重写/场景 prompt
自动注入"本作历史错误+修复指令" → 同一本书越写越好；新名词/机制基线/永久纪律进
设定账本，模型不再自造错误数字。

边界（硬约束）：可计算、可验证的（数字方向/节拍循环/拼接检测）永远留在代码硬校验
（chapter_state/beats），本模块只做软约束——规则覆盖不到的开放域（新名词规范、
历史错误形态、修复指令）。写入零额外 LLM（质检 issue 本身结构化，直接落账本）。

账本 schema（落 book_memory.json）：
{
  "version": 1,
  "canon":    {"terms": {名词: {canonical, category, first_ch}},
               "disciplines": [{pattern, rule, example, escalated_ch}],
               "mechanism_baseline": {}},
  "events":   {"consumed": [{desc, ch}]},
  "pitfalls": [{pattern, trigger, fix, example, count, last_ch, recent_rounds}],
  "taste":    {"chapters": [{ch, score, issue_types}]},
  "state":    {"chapters": [{ch, deaths, erosion, scratch}], "erosion_start": 0},
}
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 升级永久纪律时排除的通用问题类（无操作性修复指令，升级无意义）
_SKIP_ESCALATE_PREFIXES = ("结构-", "合规-", "读者-")

#: 面板方向违规 detail 的解析正则（chapter_state.panel_direction_violations 输出格式）
_NUM_VIOL_RE = re.compile(
    r"面板「(.+?)」应从基线 (\d+) (递增|递减)，正文出现(?:回退|回升) (\d+)→(\d+)"
)

#: 中文数字解析（累积状态基线「已伸三十七寸」→ 37；支持 0-99 常见写法）
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_cn_number(text: str) -> int:
    """从文本中解析第一个中文/阿拉伯数字（如「已伸三十七寸」→ 37）；未解析到返回 0。"""
    if not text:
        return 0
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    # 中文数字：找「X十Y」/「十X」/单字
    m = re.search(r"([零一二两三四五六七八九]?)(十)([零一二两三四五六七八九]?)", text)
    if m:
        tens = _CN_DIGITS.get(m.group(1), 0)
        ones = _CN_DIGITS.get(m.group(3), 0)
        if tens:
            return tens * 10 + ones
        return 10 + ones
    for ch in text:
        if ch in _CN_DIGITS:
            return _CN_DIGITS[ch]
    return 0


def _empty() -> dict:
    return {
        "version": 1,
        "canon": {"terms": {}, "disciplines": [], "mechanism_baseline": {}},
        "events": {"consumed": []},
        "pitfalls": [],
        "taste": {"chapters": []},
        "state": {"chapters": [], "erosion_start": 0},
        "goods": {"samples": []},
        "compliance_educated": [],   # P0-2：本书已注入过首次合规教育的 rule_category 列表
        "hook_streak": {"current": 0, "max": 0, "freeze_used": False},  # P0-5 追更欲连续达标
        "reflections": [],           # P1-1 Reflexion：重写 >=2 轮仍未通过后 LLM 写的反思笔记
        "suggestion_lib": {},        # P1-3 EditorReviewer 建议指令库：{category: [建议,...]}（按手法分类沉淀）
    }


class BookMemory:
    """小说级记忆系统：四账本持久化 + 踩坑自动进化 + 注入组装。"""

    def __init__(self, store, cfg=None):
        self.store = store
        self.cfg = cfg
        self._data: dict | None = None   # 懒加载，一次读盘

    # ---------- 持久化 ----------
    def load(self) -> dict:
        """懒加载账本；无文件/损坏返回空账本（不抛异常，降级安全）。"""
        if self._data is None:
            try:
                d = self.store.load_book_memory()
            except Exception:
                d = None
            self._data = d if isinstance(d, dict) else _empty()
        return self._data

    def save(self) -> None:
        """整体覆盖落盘（幂等；失败静默，不阻塞生成主流程）。"""
        try:
            self.store.save_book_memory(self.load())
        except Exception:
            logger.warning("book_memory 落盘失败（跳过，不阻塞）")

    @property
    def enabled(self) -> bool:
        """开关：book_memory=False 或 cfg 缺失时全部注入为空、写入跳过。"""
        if self.cfg is None:
            return True
        return bool(getattr(self.cfg, "book_memory", True))

    # ---------- 踩坑账本（核心）----------
    def record_pitfall(self, chapter_no: int, issues: list[dict]) -> int:
        """质检出问题 → 结构化写入踩坑账本（零额外 LLM）。

        - 只记「大改」级（影响落盘决策的硬伤；中改/小改只扣分不阻断，不沉淀）；
        - pattern = issue.type（错误模式归并：第 3/6 章同 type「情节-同事件循环重演」
          → 同一 pattern → count 累加 → 升级永久纪律）；
        - 同一章同一 pattern 只递增一次（避免一轮重写多轮复检虚增 count），
          跨章真实累加；example 追加新实例（原文片段，去重）。
        返回本次新增条目数。
        """
        if not self.enabled:
            return 0
        data = self.load()
        added = 0
        for it in issues or []:
            if it.get("level") != "大改":
                continue
            pattern = str(it.get("type") or "").strip()
            if not pattern:
                continue
            pit = next((p for p in data["pitfalls"] if p.get("pattern") == pattern), None)
            if pit is None:
                data["pitfalls"].append({
                    "pattern": pattern,
                    "trigger": self._trigger_of(it),
                    "fix": self._fix_of(it),
                    "example": self._example_of(chapter_no, it),
                    "count": 1,
                    "last_ch": int(chapter_no),
                    "recent_rounds": 1,
                })
                added += 1
            else:
                # 跨章（或跨多次独立触发）才累加；同章多轮复检不虚增
                if int(pit.get("last_ch", 0)) != int(chapter_no):
                    pit["count"] = int(pit.get("count", 0)) + 1
                    pit["recent_rounds"] = int(pit.get("recent_rounds", 0)) + 1
                pit["last_ch"] = int(chapter_no)
                self._merge_example(pit, chapter_no, it)
        self.save()
        return added

    def top_pitfalls(self, top_n: int = 5, min_count: int = 1,
                     self_ch: int | None = None) -> list[dict]:
        """按 次数降序 → 最近复发降序 → 最近章降序 取 Top-N（重写/场景注入用）。

        self_ch：当前正在生成/重写的章号。当某错误的 last_ch == self_ch 时，
        说明它是「本轮刚犯的错」（正在被 numeric_backfill / 质检 issue 直接处理），
        不是跨章沉淀的「历史教训」——降权到排序尾部，避免把当前章自身的失败
        当历史教训反复回灌（P3 去污染：第1章重写循环的 self 污染根因）。跨章
        真历史教训（last_ch < self_ch）不受影响，仍按原序霸榜。
        """
        data = self.load()
        pits = [p for p in data["pitfalls"] if int(p.get("count", 0)) >= min_count]

        def _self_flag(p: dict) -> int:
            """0=跨章历史教训（正常权重）；1=当前章刚犯的错（降权到尾部）。"""
            if self_ch is None:
                return 0
            last_ch = int(p.get("last_ch", 0))
            return 0 if last_ch != int(self_ch) else 1

        pits.sort(key=lambda p: (
            _self_flag(p),
            -int(p.get("count", 0)),
            -int(p.get("recent_rounds", 0)),
            -int(p.get("last_ch", 0)),
        ))
        return pits[:max(1, int(top_n or 1))]

    def escalate(self) -> int:
        """进化：同模式 count>=2 且未在 canon → 升级为永久纪律（去重，只升一次）。

        排除通用问题类（结构-/合规-/读者-，无操作性修复指令）。返回升级条数。
        """
        if not self.enabled:
            return 0
        data = self.load()
        dis = data["canon"].setdefault("disciplines", [])
        existing = {str(d.get("pattern") or "") for d in dis}
        n = 0
        for p in data["pitfalls"]:
            pat = str(p.get("pattern") or "")
            if (int(p.get("count", 0)) >= 2 and pat not in existing
                    and not pat.startswith(_SKIP_ESCALATE_PREFIXES)):
                dis.append({
                    "pattern": pat,
                    "rule": str(p.get("fix") or ""),
                    "example": str(p.get("example") or ""),
                    "escalated_ch": int(p.get("last_ch", 0)),
                })
                existing.add(pat)
                n += 1
        if n:
            self.save()
        return n

    def decay(self, current_ch: int, inactive_rounds: int = 3) -> int:
        """进化：超过 inactive_rounds 章未复发 → recent_rounds 清零（降权，保留可追溯）。"""
        if not self.enabled:
            return 0
        data = self.load()
        n = 0
        for p in data["pitfalls"]:
            if int(current_ch) - int(p.get("last_ch", 0)) >= inactive_rounds \
                    and int(p.get("recent_rounds", 0)) > 0:
                p["recent_rounds"] = 0
                n += 1
        if n:
            self.save()
        return n

    # ---------- 设定账本（canon）----------
    def ensure_term(self, term: str, canonical: str, category: str = "",
                    chapter_no: int = 0) -> bool:
        """新名词规范表述入库（去重；同名词已存在 → 更新 canonical，不重复建条）。"""
        if not self.enabled:
            return False
        data = self.load()
        terms = data["canon"].setdefault("terms", {})
        t = str(term or "").strip()
        if not t:
            return False
        existed = t in terms
        terms[t] = {
            "canonical": str(canonical or t).strip(),
            "category": str(category or "").strip(),
            # 首次出现章只记第一次（已存在时保留旧值，更新 canonical 不重置 first_ch）
            "first_ch": int(terms.get(t, {}).get("first_ch", 0)) or int(chapter_no),
        }
        self.save()
        return not existed

    def sync_baseline(self, world) -> None:
        """P0-8：把 world.mechanism_baseline 同步进设定账本 canon.mechanism_baseline。

        背景：v6 通读实证——世界机制基线（蚀痕"左手腕内侧起每死一寸/已伸三十七寸
        距心约一寸"）从未进记忆 canon，正文 5 章蚀痕位置口径全乱（越过肘弯/小臂中段/
        逼近肘弯各说各话）。生成每章前由 stages.write 调用一次（world 已加载），
        使拆场景/场景 prompt 能注入权威基线；world 无 baseline 或格式异常 → 跳过
        （降级安全，不阻塞）。
        """
        if not self.enabled:
            return
        mb: dict[str, str] = {}
        try:
            wb = getattr(world, "mechanism_baseline", None)
            # 2026-10-10 P0-10 修复：world.mechanism_baseline 是 MechanismBaseline
            # pydantic 对象而非 dict——原实现 isinstance(wb, dict) 恒 False，
            # 基线从未同步进 canon（v7 蚀化跨章口径冲突的部分根因；此前排查
            # "plan_re 为什么少了蚀化"的答案即此）。对象 → to_dict 再遍历。
            if hasattr(wb, "to_dict"):
                wb = wb.to_dict()
            if wb is None:
                return
            if isinstance(wb, dict):
                for k, v in wb.items():
                    ks, vs = str(k or "").strip(), str(v or "").strip()
                    if ks and vs:
                        mb[ks] = vs
        except Exception:
            return
        if not mb:
            return
        data = self.load()
        cur = data["canon"].setdefault("mechanism_baseline", {})
        changed = False
        for k, v in mb.items():
            if cur.get(k) != v:
                cur[k] = v
                changed = True
        if changed:
            self.save()

    def canon_block(self, max_chars: int = 800) -> str:
        """设定账本注入块：新名词规范 + 永久纪律 + 机制基线（空则返回空串）。"""
        if not self.enabled:
            return ""
        data = self.load()
        parts: list[str] = []
        terms = data["canon"].get("terms") or {}
        if terms:
            lines = [f"- {k}：{v.get('canonical','')}"
                     + (f"（{v.get('category','')}）" if v.get("category") else "")
                     for k, v in terms.items()]
            parts.append("【本作设定账本·专有名词规范（本书已确立的称谓，正文沿用原词，"
                         "禁止换词/缩写/近义替代）】\n" + "\n".join(lines))
        dis = data["canon"].get("disciplines") or []
        if dis:
            lines = [f"- {d.get('rule','')}"
                     + (f"（本书实例：{d.get('example','')}）" if d.get("example") else "")
                     for d in dis]
            parts.append("【本作永久纪律（本书反复踩坑升级而来，正文必须遵守）】\n"
                         + "\n".join(lines))
        mb = data["canon"].get("mechanism_baseline") or {}
        if mb:
            lines = [f"- {k}：{v}" for k, v in mb.items()]
            parts.append("【本作机制基线（背景参考：机制数字与状态的当前口径，"
                         "正文按需自然呈现，不要求刻意展示）】\n" + "\n".join(lines))
        joined = "\n\n".join(parts)
        return joined[:max_chars]

    def baseline_block(self) -> str:
        """P0-8：机制基线专用注入块（拆场景已注入 canon_block 全文；场景层只注入
        机制基线，避免 terms/disciplines 重复刷屏）。空基线/关闭 → 返回空串。"""
        if not self.enabled:
            return ""
        data = self.load()
        mb = data["canon"].get("mechanism_baseline") or {}
        if not mb:
            return ""
        lines = [f"- {k}：{v}" for k, v in mb.items()]
        return ("【本作机制基线（背景参考：机制数字与状态的当前口径，"
                "正文按需自然呈现，不要求刻意展示）】\n" + "\n".join(lines))

    # ---------- 事件账本（跨章消费状态）----------
    def record_event_consumed(self, event_desc: str, chapter_no: int) -> bool:
        """登记事件已消费（跨章持久化；与 stages 章内 event_ledger 协同，零 LLM）。"""
        if not self.enabled:
            return False
        desc = str(event_desc or "").strip()
        if not desc:
            return False
        data = self.load()
        consumed = data["events"].setdefault("consumed", [])
        if any(str(c.get("desc") or "") == desc for c in consumed):
            return False
        consumed.append({"desc": desc, "ch": int(chapter_no)})
        self.save()
        return True

    # ---------- 经验账本（章节评分/问题分布；P0-8 预留规划层注入）----------
    def record_taste(self, chapter_no: int, score: int, issues: list[dict],
                     hook_score: int | None = None) -> None:
        """记录本章评分与问题类型分布（score 来自质检结果，零 LLM）。

        hook_score（P0-5）：读者审校追更欲 1-5（LLM 产出，可能为 None）；
        存于 taste.chapters[].hook_score 供 streak 与进度盘使用。
        """
        if not self.enabled:
            return
        data = self.load()
        chapters = data["taste"].setdefault("chapters", [])
        chapters = [c for c in chapters if int(c.get("ch", 0)) != int(chapter_no)]
        rec = {
            "ch": int(chapter_no),
            "score": int(score or 0),
            "issue_types": [str(it.get("type") or "") for it in (issues or [])],
        }
        if hook_score is not None:
            rec["hook_score"] = int(hook_score)
        chapters.append(rec)
        data["taste"]["chapters"] = chapters[-20:]  # 只留最近 20 章，防膨胀
        self.save()

    # ---------- P0-2 合规首次教育去重 ----------
    def compliance_educated(self) -> list[str]:
        """已注入过首次合规教育的 rule_category 列表（空=无）。"""
        if not self.enabled:
            return []
        return list(self.load().get("compliance_educated") or [])

    def mark_compliance_educated(self, categories: list[str]) -> list[str]:
        """登记本次教育过的 rule_category，返回【新增】的教育过的分类（供调用方注入）。

        幂等：已教育过的分类不重复登记；空输入返回 [] 且不落盘。
        """
        if not self.enabled or not categories:
            return []
        data = self.load()
        done = set(data.get("compliance_educated") or [])
        new_cats = [c for c in categories if c and c not in done]
        if not new_cats:
            return []
        data["compliance_educated"] = sorted(set(done) | set(new_cats))
        self.save()
        return new_cats

    # ---------- P0-5 追更欲连续达标（streak）----------
    def record_hook_streak(self, hook_score: int | None) -> dict:
        """追更欲连续达标账本（hook_score>=4 为达标）。

        - hook>=4 → current+1，max 取历史峰值；
        - hook<4 → 若 hook_streak_freeze 开关开且 freeze 未用则冻结一次不重置（记
          freeze_used=True），否则 current 重置为 0；
        - hook 为 None（LLM 失败）→ 不累计也不重置（保持现状）。
        返回 {current, max, freeze_used}。
        """
        data = self.load()
        st = data.setdefault("hook_streak",
                             {"current": 0, "max": 0, "freeze_used": False})
        freeze_enabled = bool(getattr(self.cfg, "hook_streak_freeze", True))
        if hook_score is None:
            return dict(st)
        if int(hook_score) >= 4:
            st["current"] = int(st.get("current", 0)) + 1
            st["max"] = max(int(st.get("max", 0)), st["current"])
            st["freeze_used"] = bool(st.get("freeze_used"))
        else:
            if freeze_enabled and not st.get("freeze_used"):
                st["freeze_used"] = True   # 冻结一次，不重置
            else:
                st["current"] = 0
        self.save()
        return dict(st)

    # ---------- 反思账本（P1-1 Reflexion）----------
    # 重写 >=2 轮仍未通过 → LLM 用一句话写「错在哪、下次怎么写对」入账；
    # 后续章生成时注入为先验（正反馈闭环：反复失败不是静默吞掉，而是转成下一章的先验）。
    _REFLEXION_CAP = 8   # 只保留最近 N 条反思（防账本无限膨胀）

    def record_reflection(self, chapter_no: int, note: str) -> bool:
        """P1-1：把一句反思笔记入账（失败/空文本静默返回 False，不阻塞主流程）。"""
        note = (note or "").strip()
        if not self.enabled or not note:
            return False
        data = self.load()
        refs = data.setdefault("reflections", [])
        refs.append({"chapter": int(chapter_no or 0), "note": note})
        data["reflections"] = refs[-self._REFLEXION_CAP:]
        self.save()
        return True

    def reflection_block(self, chapter_no: int, max_n: int = 2) -> str:
        """P1-1 注入块：取当前章之前最近的反思笔记作先验（空账本/关闭 → 空串）。

        章节顺序生成：chapter_no 章生成时会读到 chapter<=chapter_no 的历史教训，
        让「上一章反复没写对的地方」直接带到下一章，避免同一类错误跨章复发。
        """
        if not self.enabled:
            return ""
        refs = (self.load().get("reflections") or [])
        cur = int(chapter_no or 0)
        near = [r for r in refs if int(r.get("chapter", 0)) <= cur]
        near = near[-max(1, int(max_n or 1)):]
        lines = [f"第{r.get('chapter')}章教训：{r.get('note')}" for r in near
                 if (r.get("note") or "").strip()]
        if not lines:
            return ""
        return "【本作反思笔记（此前章节反复未过的教训，避免重蹈）】\n" + "\n".join(lines)


    # ---------- 建议指令库（P1-3 EditorReviewer 建议沉淀）----------
    # 把「编辑审稿的建议/优先动作」从 LLM 自由文本沉淀为按手法分类的正向指令库，
    # 写章生成时注入——建议不再是「审稿时的一次性展示」，而是「后续写作的复用指令」。
    # 分类框架复用 good 账本的 _GOOD_CATS（对话-潜台词 / 战斗-动作链 / 情绪-白描+点破 /
    # 环境-物象白描 / 叙事-节奏），零额外 LLM，确定性关键词分类。
    _SUGGEST_CAP = 20   # 每类指令库上限（防膨胀）

    def record_suggestions(self, texts: list) -> int:
        """把 EditorReview.suggestions/priority_actions 按手法分类沉淀进指令库。
        去重；空文本/关闭 → 0；返回实际入账条数。"""
        if not self.enabled:
            return 0
        data = self.load()
        lib = data.setdefault("suggestion_lib", {})
        n = 0
        for t in texts or []:
            t = (t or "").strip()
            if not t:
                continue
            cat = self._classify_suggestion(t)
            bucket = lib.setdefault(cat, [])
            if t not in bucket:
                bucket.append(t)
                lib[cat] = bucket[-self._SUGGEST_CAP:]
                n += 1
        if n:
            self.save()
        return n

    def _classify_suggestion(self, text: str) -> str:
        """按手法关键词把一条建议归类到 _GOOD_CATS 之一（fallback 叙事-节奏）。"""
        kw_map = {
            "对话-潜台词": ("潜台词", "对白", "台词", "言外", "对话", "口是心非", "弦外之音"),
            "战斗-动作链": ("战斗", "打斗", "动作", "招式", "搏杀", "交锋", "打戏"),
            "情绪-白描+点破": ("情绪", "情感", "白描", "点破", "内心", "心理", "感受", "心境"),
            "环境-物象白描": ("环境", "景物", "物象", "场景", "氛围", "画面", "物"),
        }
        for cat, kws in kw_map.items():
            if any(k in text for k in kws):
                return cat
        return "叙事-节奏"

    def suggestion_block(self, max_cats: int = 2) -> str:
        """P1-3 注入块：从指令库取前几个分类各 1 条正向建议（带类别标签）。空库 → 空串。"""
        if not self.enabled:
            return ""
        lib = self.load().get("suggestion_lib") or {}
        parts = []
        for cat in self._GOOD_CATS:
            bucket = lib.get(cat) or []
            if bucket:
                parts.append(f"[{cat}] {bucket[0]}")
            if len(parts) >= max(1, int(max_cats or 1)):
                break
        if not parts:
            return ""
        return "【本作编辑建议（正向写作手法指令，择优采用，勿生硬照搬）】\n" + "\n".join(parts)


    # ---------- good 账本（REQ-写作正向引导 5.6：踩坑 + 正向样例双账本）----------
    # 用户方向①「告诉 AI 怎么写是好的」的落地：只记"什么不行"（pitfalls）没有
    # "什么好"——高分章（质检 ≥85 分且无大改）的段落按手法分类沉淀为正向样例，
    # 场景生成/重写注入「本作写得好的片段（参考质感，非内容来源）」，形成
    # 写得好 → 被学习 → 写得更好 的闭环。写入零额外 LLM（分类用确定性规则）。
    # 容量约束：每类 ≤5 条、总量 ≤50 条、每条 ≤300 字（防 token 膨胀与污染）。
    _GOOD_CATS = ("对话-潜台词", "战斗-动作链", "情绪-白描+点破", "环境-物象白描", "叙事-节奏")
    #: 手法分类的确定性关键词表（粗分即可，目的是防同类刷屏，非精确文体学）
    _GOOD_ACT_VERBS = ("抓", "砸", "闪", "扑", "退", "追", "咬", "劈", "刺", "躲", "滚",
                       "跳", "踢", "绷", "攥", "掀", "拧", "扎", "抽", "撞", "挡", "俯",
                       "仰", "冲", "压", "掐", "扯", "掰", "握")
    _GOOD_EMO_WORDS = ("怕", "怒", "恨", "冷", "抖", "僵", "麻", "疼", "烫", "沉",
                       "惊", "恐", "惧", "愣", "慌")
    _GOOD_SENSE_WORDS = ("光", "影", "风", "灰", "锈", "腥", "潮", "味", "声", "静",
                         "黑", "暗", "湿", "凉", "烫", "烫")

    # demo（示范池，可前置注入）/ cand（候选池，仅重写参考）各维度容量
    _GOOD_DEMO_CAP_PER_CAT = 5     # demo 每类 ≤5
    _GOOD_DEMO_CAP_TOTAL = 50      # demo 总量 ≤50
    _GOOD_CAND_CAP_PER_CAT = 8     # cand 每类 ≤8
    _GOOD_CAND_CAP_TOTAL = 100     # cand 总量 ≤100
    # 来源隔离：demo 池中本作自产片段(book)占比上限（其余为 user/external 外部来源）
    _GOOD_BOOK_RATIO_DEMO = 0.5

    @staticmethod
    def _good_capacity(level: str) -> tuple[int, int]:
        return (BookMemory._GOOD_DEMO_CAP_PER_CAT, BookMemory._GOOD_DEMO_CAP_TOTAL) \
            if level == "demo" else (BookMemory._GOOD_CAND_CAP_PER_CAT, BookMemory._GOOD_CAND_CAP_TOTAL)

    def record_good(self, chapter_no: int, content: str, score: int,
                    issues: list[dict] | None = None,
                    hook_score: int | None = None, promote: bool | None = None) -> int:
        """好片段自动入账（零 LLM；REQ-门禁质检正向影响 P0-4）。

        - 入账条件（记录层无条件沉淀）：good_pool_layer 开 → 无「大改」级问题即入账
          （去掉 score≥85 门槛）；关 → 保持原「score≥85 且无大改」门槛。
        - 分层（注入层防平庸固化）：promote=True 或 hook_score>=4 或 score>=85 →
          demo（示范池，可前置注入）；否则 cand（候选池，仅重写/参考）。
        - 来源：本作自产片段 src_type="book"。
        - 返回本次新增条数。
        """
        if not self.enabled:
            return 0
        layer = bool(getattr(self.cfg, "good_pool_layer", True))
        if layer:
            if any(str(it.get("level") or "") == "大改" for it in (issues or [])):
                return 0
        else:
            if int(score or 0) < 85:
                return 0
            if any(str(it.get("level") or "") == "大改" for it in (issues or [])):
                return 0
        paras = self._good_paras(content)
        if not paras:
            return 0
        if layer:
            level = "demo" if (promote or int(hook_score or 0) >= 4
                               or int(score or 0) >= 85) else "cand"
        else:
            level = "demo"
        return self._ingest_good_samples(chapter_no, paras, int(score or 0),
                                         src_type="book", level=level)

    def record_good_manual(self, chapter_no: int, content: str) -> int:
        """用户人工修订/手动标记章入 good 账本（免质检条件——用户认可 = 好文风）。

        P0-4 来源隔离：用户认可片段 src_type="user"、直接入 demo 示范池。
        webapp 编辑保存时调用（ch.user_edited=True 同处）；零 LLM。
        """
        if not self.enabled:
            return 0
        paras = self._good_paras(content)
        if not paras:
            return 0
        return self._ingest_good_samples(chapter_no, paras, 100,
                                         src_type="user", level="demo")

    def ingest_external_good(self, category_key: str, text: str) -> int:
        """外部优质片段入账（P0-4 来源隔离）：拆书库/内置范文导入，src_type="external"。

        category_key 须在 _GOOD_CATS 内（否则不入账，防污染分类框架）；
        直接入 demo 示范池（外部源 = 已验证的高质量文风，可前置注入）。
        返回新增条数（0 = 未入账）。
        """
        if not self.enabled or category_key not in self._GOOD_CATS:
            return 0
        t = str(text or "").strip()
        if not (20 <= len(t) <= 300):
            return 0
        return self._ingest_good_samples(0, [t], 100,
                                         src_type="external", level="demo")

    @staticmethod
    def _good_paras(content: str) -> list[str]:
        """好片段候选：正文段落切分（20~300 字，跳过摘要行/块标题）。"""
        return [
            p.strip() for p in re.split(r"\n\s*\n|\n", content or "")
            if 20 <= len(p.strip()) <= 300
            and "【摘要】" not in p
            and "【" not in p[:2]
        ]

    def _ingest_good_samples(self, chapter_no: int, paras: list[str],
                             score: int, src_type: str = "book",
                             level: str = "demo") -> int:
        """入账主体：分类 → 去重 → 分层容量约束（demo/cand 各自每类/总量上限）。

        P0-4：容量按 level 分层独立统计；同源同类型同章去重。
        """
        data = self.load()
        samples = data.setdefault("goods", {}).setdefault("samples", [])
        cap_per_cat, cap_total = self._good_capacity(level)
        added = 0
        for p in paras:
            cat = self._classify_sample(p)
            if not cat:
                continue
            # 同章同类型已有 → 跳过（防同章刷屏）
            same_ch = [s for s in samples
                       if s.get("cat") == cat
                       and s.get("src_type") == src_type
                       and int(s.get("src_ch", 0)) == int(chapter_no)]
            if same_ch:
                continue
            # 分层容量：该 level 内每类超限 → 删该 level 最旧（保持最新优先）
            cat_items = [s for s in samples
                         if s.get("cat") == cat and s.get("level") == level]
            if len(cat_items) >= cap_per_cat:
                oldest = min(cat_items, key=lambda s: int(s.get("src_ch", 0)))
                samples.remove(oldest)
            level_items = [s for s in samples if s.get("level") == level]
            if len(level_items) >= cap_total:
                oldest = min(level_items, key=lambda s: int(s.get("src_ch", 0)))
                samples.remove(oldest)
            samples.append({
                "cat": cat,
                "text": p[:300],
                "src_ch": int(chapter_no),
                "score": int(score or 0),
                "src_type": src_type,
                "level": level,
            })
            added += 1
            if len(level_items) + added >= cap_total:
                break
        if added:
            self.save()
        return added

    def good_block(self, top_n: int = 3, max_chars: int = 900) -> str:
        """【本作写得好的片段】注入块（P0-4 采样轮换 + 来源隔离 + 软约束强化）。

        - 默认只取 demo 示范池（level=demo）；demo 空则回落 cand（兜底，行为不退化）；
        - 来源隔离：demo 中优先 user/external 外部来源，本作自产 book 限量补足
          （占比 ≤_GOOD_BOOK_RATIO_DEMO，单条 ≤200 字），防本作自引用闭环套路固化；
        - 采样轮换：每类在近窗口内按 pick_state 索引轮换（不永远锁最近几章）；
        - 软约束强化：注入措辞追加"每章限借鉴 1-2 特征、禁连续复用"多样性纪律。
        空账本/关闭 → 空串。
        """
        if not self.enabled:
            return ""
        data = self.load()
        goods = data.get("goods") or {}
        samples = goods.get("samples") or []
        if not samples:
            return ""
        # 1) 取注入候选：优先 demo，空则回落 cand
        pool = [s for s in samples if s.get("level") == "demo"]
        if not pool:
            pool = [s for s in samples if s.get("level") in (None, "cand")]
        if not pool:
            return ""
        # 2) 来源隔离：外部(user/external)优先，本作(book)限量补足
        src_isolation = bool(getattr(self.cfg, "good_src_isolation", True))
        if src_isolation:
            book_items = [s for s in pool if s.get("src_type") == "book"]
            ext_items = [s for s in pool if s.get("src_type") in ("user", "external")]
            ext_n = min(len(ext_items), max(0, int(top_n or 1)))
            max_book = max(0, int((int(top_n or 1) - ext_n) * self._GOOD_BOOK_RATIO_DEMO))
            if max_book == 0 and ext_n < int(top_n or 1):
                max_book = 1  # 外部不足时至少允许 1 条本作（防 demo 空转）
            book_n = min(len(book_items), max_book)
        else:
            ext_items, book_items = [], pool
            ext_n, book_n = 0, len(book_items)
        # 3) 采样轮换：每类窗口内按 pick_state 轮换取
        rotate = bool(getattr(self.cfg, "good_rotate", True))
        pick_state = goods.setdefault("pick_state", {})
        picked: list[dict] = []

        def _rotate_take(items: list[dict]) -> dict | None:
            if not items:
                return None
            if not rotate:
                # 不轮换：取 src_ch 最大（最新）者（回落原行为）
                return max(items, key=lambda s: (int(s.get("src_ch", 0)),
                                                 int(s.get("score", 0))))
            key = (items[0].get("cat") or "misc") + ":" + str(items[0].get("src_type") or "book")
            idx = int(pick_state.get(key, -1))
            ordered = sorted(items, key=lambda s: (int(s.get("src_ch", 0)),
                                                   int(s.get("score", 0))))
            nxt = (idx + 1) % len(ordered)
            pick_state[key] = nxt
            return ordered[nxt]

        # 每类取 1 条（外部优先补足 top_n），再按最近章排序取前 top_n
        for items, want in ((ext_items, ext_n), (book_items, book_n)):
            by_cat: dict[str, list[dict]] = {}
            for s in items:
                by_cat.setdefault(s.get("cat") or "misc", []).append(s)
            for s in sorted(by_cat.values(), key=lambda v: -max(
                    int(x.get("src_ch", 0)) for x in v)):
                if len(picked) >= want:
                    break
                cand = _rotate_take(s)
                if cand:
                    picked.append(cand)
        picked = picked[:max(1, int(top_n or 1))]
        if rotate:
            self.save()  # 持久化轮换状态
        picked.sort(key=lambda s: int(s.get("src_ch", 0)), reverse=True)
        lines = []
        for s in picked:
            src_tag = {"user": "用户修订", "external": "外部优质", "book": "本作"}.get(
                s.get("src_type"), "本作")
            lines.append(
                f"- 第{s.get('src_ch','')}章（{s.get('cat','')}·{src_tag}）："
                f"{str(s.get('text','')).strip()[:200 if s.get('src_type')=='book' else 280]}"
            )
        out = ("【本作写得好的片段（参考质感与节奏——不是内容来源，不得照抄情节；"
               "写本章时模仿这里的句式密度与白描手法）】\n"
               + "\n".join(lines)
               + "\n【多样性纪律（重要）】每章只借鉴其中 1-2 个可用的写法特征"
                 "（句式密度/白描手法之一）；禁止连续多章复用同一特征；"
                 "本章至少引入 1 个本章独有的写法或场景处理，避免套路重复。")
        return out[:max_chars]

    @classmethod
    def _classify_sample(cls, text: str) -> str:
        """段落手法分类（确定性规则，零 LLM）。

        判定顺序：对话（含引号）> 动作链（动作动词密度）> 情绪白描（情绪词+身体/
        生理反应）> 环境物象（感官词+物象）> 叙事节奏。无命中 → ""（不入账）。
        """
        t = str(text or "").strip()
        if not t:
            return ""
        if "“" in t or "”" in t or '"' in t or "「" in t:
            # 对话段落：含引号对白；引号内容占比 >40% 才算"对话为主"
            q = sum(1 for ch in t if ch in "“”\"「」")
            if q >= 2:
                return cls._GOOD_CATS[0]
        acts = sum(1 for w in cls._GOOD_ACT_VERBS if w in t)
        if acts >= 3:
            return cls._GOOD_CATS[1]
        emos = sum(1 for w in cls._GOOD_EMO_WORDS if w in t)
        if emos >= 2:
            return cls._GOOD_CATS[2]
        senses = sum(1 for w in cls._GOOD_SENSE_WORDS if w in t)
        if senses >= 3:
            return cls._GOOD_CATS[3]
        return cls._GOOD_CATS[4]

    # ---------- 机制状态账本（P0-10，确定性数字锚）----------
    # v7 第 6 章实证：正文死亡 3 次（ch1/3/4）未反映到蚀化（ch6 仍 97%），且章内
    # 「38 次到心口 vs 第 98 次蚀化满」口径冲突——LLM 对"跨章累计数字"自由发挥。
    # 本账本把每章末机制状态（死亡次数/蚀化值/蚀痕位置）确定性入账，由代码累计
    # （零 LLM），注入「当前蚀化应 = 起始基线 + 累计死亡」的确定性锚，供拆场景/
    # 场景/重写 prompt 遵守；蚀化累计校验（chapter_state.erosion_cumulative_check）
    # 用同一账本做兜底。写入时机 = 每章生成完成（stages.write 末尾）。
    def record_chapter_state(self, chapter_no: int, deaths: int = 0,
                             erosion: int | None = None, scratch: str = "",
                             numbers: dict | None = None) -> None:
        """登记本章末机制状态（死亡次数、正文蚀化值、蚀痕位置、注册字段末值）。

        同章覆盖；首次入账时若 canon 机制基线含蚀化起始值则解析为 erosion_start。
        P0-15/16 通用版（2026-10-10）：numbers = {核心字段: 末值}（ledger_extract_numbers
        产出），逐章链式入账——本章未提及的字段由 chapter_ledger 继承上一章末值，
        治 v9 ch5 正文无面板行 → erosion=null 断链。
        """
        if not self.enabled:
            return
        data = self.load()
        st = data.setdefault("state", {"chapters": [], "erosion_start": 0})
        chapters = [c for c in st["chapters"] if int(c.get("ch", 0)) != int(chapter_no)]
        chapters.append({
            "ch": int(chapter_no),
            "deaths": max(0, int(deaths or 0)),
            "erosion": int(erosion) if erosion is not None else None,
            "scratch": str(scratch or "").strip(),
            "nums": dict(numbers or {}),   # P0-15/16 通用版：注册字段末值
        })
        chapters.sort(key=lambda c: int(c.get("ch", 0)))
        st["chapters"] = chapters[-200:]  # 全书长度上限防膨胀
        # 蚀化起始值：优先已有 → 从 canon.mechanism_baseline.panel_format 解析
        if not st.get("erosion_start"):
            st["erosion_start"] = self._erosion_start_from_baseline()
        self.save()

    def chapter_ledger(self, up_to_ch: int) -> dict:
        """链式继承读取截至某章的机制字段末值（P0-15/16 通用版）。

        每章只记录正文出现的字段；本章未提及的字段由最近一章带值者继承
        （v9 ch5 正文无面板行 → ch6 校验基线仍取 ch4 末值，不断链）。
        无任何记录 → 空 dict。
        """
        if not self.enabled:
            return {}
        data = self.load()
        st = data.get("state") or {}
        out: dict = {}
        for c in sorted(
            (c for c in st.get("chapters", []) if int(c.get("ch", 0)) <= int(up_to_ch)),
            key=lambda c: int(c.get("ch", 0)),
        ):
            for k, v in (c.get("nums") or {}).items():
                out[k] = v
        return out

    def cumulative_deaths(self, up_to_ch: int) -> int:
        """截至某章的累计死亡次数（代码求和，零 LLM；账本无记录返回 0）。

        P0-14 起：账本 deaths 由调用方按「本章末蚀化 - 上一章末蚀化」反推入账
        （蚀化差驱动，不再依赖词面死亡计数），故本函数返回的累计与正文面板链一致。
        """
        data = self.load()
        st = data.get("state") or {}
        return sum(
            max(0, int(c.get("deaths", 0)))
            for c in st.get("chapters", [])
            if int(c.get("ch", 0)) <= int(up_to_ch)
        )

    # 2026-10-17 移除书级死亡信号词表（REQ-机制数字软提示与通用化）：不再做语义死亡
    # 判断，数字结算改由设定账本 + 被动方向校验 + 事后质检软提示处理。

    def erosion_anchor(self, up_to_ch: int) -> str:
        """确定性数字锚（P0-14 链式版，2026-10-10）：蚀化当前应 = 上一章末蚀化值
        （承接链，不再 97+累计死亡）。

        v8 实证根因：旧版用「起始 97 + 账本累计死亡」算期望，而账本累计被
        death_count 词面计数污染（ch1 重复段落多计、ch5/ch6 自述面板误计）→ 锚
        虚高（106/108）→ 模型跟随写虚高值 → ⑥ 校验验证"跟随"而非"正确"（正反馈
        放大）。链式版只承接上一章末面板值，杜绝跨章求和口径；上一章面板正确性由
        质检 2.8c（本章死亡必须 +1、未死亡必须不变）保证。空账本/关闭 → 空串。

        up_to_ch 传上一章号（chapter_no-1）：返回上一章末蚀化作为本章"当前应达值"；
        无账本记录 → 回落起始基线（erosion_start）。
        """
        if not self.enabled:
            return ""
        data = self.load()
        st = data.get("state") or {}
        chapters = sorted(
            (c for c in st.get("chapters", []) if int(c.get("ch", 0)) <= int(up_to_ch)),
            key=lambda c: int(c.get("ch", 0)))
        prev = None
        for c in reversed(chapters):  # 最近一章带蚀化值者（无蚀化记录的中途章跳过）
            if c.get("erosion") is not None:
                prev = c.get("erosion")
                break
        start = int(st.get("erosion_start", 0) or 0)
        if prev is None and not start:
            return ""
        base_val = int(prev) if prev is not None else start
        # 累积型字段名从设定账本声明提取（如「距离蚀化：97%」→「距离蚀化」），
        # 代码不预置单书机制词；无百分比声明 → 通用名「机制数值」。
        base = data["canon"].get("mechanism_baseline") or {}
        _pf = str(base.get("panel_format") or base.get("面板格式") or "")
        _fm = re.search(r"([\u4e00-\u9fff]{1,10}?)\s*[：:]\s*\d+\s*%", _pf)
        _field = _fm.group(1) if _fm else "机制数值"
        lines = [
            f"- {_field}：背景参考，承接上一章末值 {base_val}%（只增不减；"
            f"若本章发生结算则 +1、无结算保持；正文若出现该数字须与此一致，不要求刻意展示）"
        ]
        # 机制累积状态基线（如「已伸三十七寸」类文本）：注入基线原文口径，链式数字
        # 一致性由注册表账本（ledger）确定性校验，这里不假设单书单位/死亡联动。
        scratch_line = ""
        for _k in ("scar_current", "scar_start", "scar_end"):
            v = str(base.get(_k) or "")
            if v and re.search(r"[0-9一二三四五六七八九十百两]+", v):
                scratch_line = v
                break
        if not scratch_line:
            # 中文键兜底：任何键值含「数字+中文单位」（累积状态声明）即视为基线口径
            for v in base.values():
                vs = str(v or "")
                if re.search(r"[0-9一二三四五六七八九十百两]+\s*[\u4e00-\u9fff]", vs):
                    scratch_line = vs
                    break
        if scratch_line:
            lines.append(f"- 机制累积基线（参考口径）：{scratch_line}；"
                         "正文若提到该机制状态须与此连续，不要求刻意展示")
        return ("【机制数字·背景参考（代码按上一章末值链式承接；正文按需自然呈现，"
                "结算时为准——不要求刻意展示数字）】\n" + "\n".join(lines))

    def _erosion_start_from_baseline(self) -> int:
        """从 canon.mechanism_baseline 的 panel_format 解析累积型百分比起始值
        （如「距离蚀化：97%」→ 97）。"""
        try:
            data = self.load()
            base = data["canon"].get("mechanism_baseline") or {}
            pf = str(base.get("panel_format") or base.get("面板格式") or "")
            m = re.search(r"[：:]\s*(\d+)\s*%", pf)
            if m:
                return max(0, int(m.group(1)))
            return 0
        except Exception:
            return 0

    # ---------- 注入组装 ----------
    def pitfall_block(self, top_n: int = 5, min_count: int = 1,
                      for_rewrite: bool = False, self_ch: int | None = None) -> str:
        """【本作历史错误】注入块：Top-N 踩坑（按次数×最近频率排序）。

        for_rewrite=True：重写专用措辞（强调删除重复版本）；场景生成用 False。
        self_ch：当前章号——本错最近就在本错犯（last_ch==self_ch）时降权到尾部，
        避免把「本轮刚犯的错」当历史教训回灌（P3 去污染）。
        空账本/关闭 → 返回空串（调用方不注入）。
        """
        if not self.enabled:
            return ""
        pits = self.top_pitfalls(top_n, min_count, self_ch=self_ch)
        if not pits:
            return ""
        return self._render_pitfalls(pits, for_rewrite=for_rewrite)

    def _render_pitfalls(self, pits: list[dict], for_rewrite: bool = False) -> str:
        """把已选定的踩坑条目渲染成注入块文本（渲染体唯一事实来源）。

        `pitfall_block` 与 `pitfall_injection` 共用本方法，保证两条路径的渲染文本
        逐字一致（`pitfall_block` 输出相对改造前行为零变化，见 §11.1）。
        """
        lines = []
        for i, p in enumerate(pits, 1):
            lines.append(
                f"{i}. 错误模式「{p.get('pattern','')}」：{p.get('fix','')}。"
                f"本书实例：{p.get('example','')}（已出现 {p.get('count',1)} 次，"
                f"最近第 {p.get('last_ch',0)} 章）"
            )
        header = (
            "【本作历史错误·重写必须避免（来自本书踩坑账本——同一本书反复犯的错，"
            "本次严禁再犯；重复版本一律删除，不可替代信息合并进保留版本）】"
            if for_rewrite else
            "【本作历史错误·场景生成纪律（来自本书踩坑账本，按出现次数排序——"
            "本书已反复出现的问题，本场景严禁重犯）】"
        )
        return header + "\n" + "\n".join(lines)

    def pitfall_injection(self, top_n: int = 5, min_count: int = 1,
                          self_ch: int | None = None,
                          for_rewrite: bool = False) -> tuple[str, list[str]]:
        """踩坑注入的「单一取样」入口：返回 (渲染块, pattern 列表)。

        与 `pitfall_block` 的区别：后者只给渲染文本（供重写/只读场景），本方法额外
        把本次入选的 pattern 一起返回，供注入对账（第 3 步）取 Expected——
        两者来自**同一次 `top_pitfalls` 选择**，物理上杜绝二次取样漂移。

        patterns 已去空串/纯空白（对账侧无需再过滤）。空账本/关闭 → ("", [])。
        """
        if not self.enabled:
            return "", []
        pits = self.top_pitfalls(top_n, min_count, self_ch=self_ch)
        if not pits:
            return "", []
        block = self._render_pitfalls(pits, for_rewrite=for_rewrite)
        patterns = [str(p.get("pattern") or "").strip() for p in pits]
        return block, [p for p in patterns if p]

    def numeric_backfill(self, violations: list[str]) -> str:
        """数字修复回灌：把面板方向违规 detail 转成确定性修复指令
        『基线 X（方向规则）：你写 Y，正确应为 Z』；空输入返回空串。

        输入来自 panel_direction_violations 的 detail（含基线/错误值），
        解析失败的原样带过（不编造正确值）。
        """
        if not self.enabled or not violations:
            return ""
        lines: list[str] = []
        for v in violations:
            m = _NUM_VIOL_RE.search(str(v))
            if m:
                label, base, direction, a, b = (m.group(1), int(m.group(2)),
                                                m.group(3), int(m.group(4)), int(m.group(5)))
                correct = base + 1 if direction == "递增" else base - 1
                lines.append(
                    f"- 面板「{label}」基线 {base}（{direction}规则）：你写的 {b} 违反规则，"
                    f"正确应为 {correct}——严格从基线{direction}，禁止 {a}→{b} 这类回退/回升"
                )
            else:
                lines.append(f"- {v}")
        if not lines:
            return ""
        return ("【数字修复回灌（针对本次质检问题，逐条确定性修正指令——"
                "按此修正，禁止再犯同类数字错误）】\n" + "\n".join(lines))

    # ---------- 工具（纯函数）----------
    @staticmethod
    def _trigger_of(it: dict) -> str:
        """触发条件：从 type/detail 提炼（截断 40 字，供注入可读）。"""
        d = str(it.get("detail") or "").strip().replace("\n", " ")
        return d[:40] if d else str(it.get("type") or "")

    @staticmethod
    def _fix_of(it: dict) -> str:
        """修复指令：按 type/detail 关键词归一到可执行规则（注入重写 prompt 用）。

        2026-10-10 P0-8：分支顺序修正——「重演/循环/多版本」优先于「机制数字/面板/
        蚀化」。原实现机制数字分支在前，而循环重演类 issue 的 detail 常含「面板」
        （如「同一情节节拍序列『面板→对峙→复活→面板』在章内出现 ≥2 次」），导致
        循环重演被劫持成机制数字指令（v6 实测：canon 两条纪律的 rule 全部错标）。
        规则匹配有交集时按「具体问题（循环/拼接/复读）→ 通用（机制数字/矛盾）」
        顺序判定，避免 detail 关键词污染 type 语义。
        """
        t = str(it.get("type") or "")
        d = str(it.get("detail") or "")
        if "重演" in t or "循环" in t or "两版本" in d or "多版本" in d:
            return "同一事件/情节只写一遍，禁止换措辞/换地点/换细节重演完整流程；重复版本删除，不可替代信息合并进保留版本"
        if "拼接" in t or "自重复" in t or "草稿" in t:
            return "禁止多稿拼接：只输出单一完整连贯版本，修改在原处替换，禁止保留原文再加平行版本"
        if "复读" in t or "同句" in t or "原样" in d:
            return "同一句/同一段禁止原样复读；需要强调时用递进改写（加新信息/改变环境或代价）"
        if "机制数字" in t or "面板" in d:
            return "机制数字必须按方向规则严格结算（累积字段只增不减、次数字段只减不增、禁止同值跨结算点复现），正文从基线推进，禁止回退/回升"
        if "矛盾" in t or "一致" in t or "身份" in t:
            return "同一角色/状态/事实全书口径唯一，禁止出现两版本描述"
        if "重复" in t:
            return "本章内同一动作模式/同一对话轮/同一描写不得原样重复；第二次出现必须改变环境、代价或结果"
        return f"按质检问题清单针对性修复：{str(it.get('detail') or '')[:60]}"

    @staticmethod
    def _example_of(chapter_no: int, it: dict) -> str:
        """本作实例：章节号 + detail 摘要（原文片段锚点）。"""
        d = str(it.get("detail") or "").strip().replace("\n", " ")
        return f"第{chapter_no}章：{d[:50]}" if d else f"第{chapter_no}章"

    @classmethod
    def _merge_example(cls, pit: dict, chapter_no: int, it: dict) -> None:
        """追加新实例（同章同 detail 去重；保留最近 2 条，防膨胀）。"""
        ex = cls._example_of(chapter_no, it)
        exs = [str(x) for x in pit.get("examples") or [pit.get("example")] if str(x)]
        if ex not in exs:
            exs.append(ex)
        pit["examples"] = exs[-2:]
        pit["example"] = exs[-1]
