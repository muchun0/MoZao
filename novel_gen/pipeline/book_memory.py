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


def _empty() -> dict:
    return {
        "version": 1,
        "canon": {"terms": {}, "disciplines": [], "mechanism_baseline": {}},
        "events": {"consumed": []},
        "pitfalls": [],
        "taste": {"chapters": []},
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

    def top_pitfalls(self, top_n: int = 5, min_count: int = 1) -> list[dict]:
        """按 次数降序 → 最近复发降序 → 最近章降序 取 Top-N（重写/场景注入用）。"""
        data = self.load()
        pits = [p for p in data["pitfalls"] if int(p.get("count", 0)) >= min_count]
        pits.sort(key=lambda p: (
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
            parts.append("【本作机制基线（机制数字的唯一来源）】\n" + "\n".join(lines))
        joined = "\n\n".join(parts)
        return joined[:max_chars]

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
    def record_taste(self, chapter_no: int, score: int, issues: list[dict]) -> None:
        """记录本章评分与问题类型分布（score 来自质检结果，零 LLM）。"""
        if not self.enabled:
            return
        data = self.load()
        chapters = data["taste"].setdefault("chapters", [])
        chapters = [c for c in chapters if int(c.get("ch", 0)) != int(chapter_no)]
        chapters.append({
            "ch": int(chapter_no),
            "score": int(score or 0),
            "issue_types": [str(it.get("type") or "") for it in (issues or [])],
        })
        data["taste"]["chapters"] = chapters[-20:]  # 只留最近 20 章，防膨胀
        self.save()

    # ---------- 注入组装 ----------
    def pitfall_block(self, top_n: int = 5, min_count: int = 1,
                      for_rewrite: bool = False) -> str:
        """【本作历史错误】注入块：Top-N 踩坑（按次数×最近频率排序）。

        for_rewrite=True：重写专用措辞（强调删除重复版本）；场景生成用 False。
        空账本/关闭 → 返回空串（调用方不注入）。
        """
        if not self.enabled:
            return ""
        pits = self.top_pitfalls(top_n, min_count)
        if not pits:
            return ""
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
        """修复指令：按 type/detail 关键词归一到可执行规则（注入重写 prompt 用）。"""
        t = str(it.get("type") or "")
        d = str(it.get("detail") or "")
        if "机制数字" in t or "面板" in d or "蚀化" in d:
            return "机制数字必须按方向规则严格结算（蚀化只增不减、次数递减、禁止同值跨结算点复现），正文从基线推进，禁止回退/回升"
        if "重演" in t or "循环" in t or "两版本" in d or "多版本" in d:
            return "同一事件/情节只写一遍，禁止换措辞/换地点/换细节重演完整流程；重复版本删除，不可替代信息合并进保留版本"
        if "拼接" in t or "自重复" in t or "草稿" in t:
            return "禁止多稿拼接：只输出单一完整连贯版本，修改在原处替换，禁止保留原文再加平行版本"
        if "复读" in t or "同句" in t or "原样" in d:
            return "同一句/同一段禁止原样复读；需要强调时用递进改写（加新信息/改变环境或代价）"
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
