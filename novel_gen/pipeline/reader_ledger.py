# -*- coding: utf-8 -*-
"""读者体验层叙事账本（REQ-reader-ledger，二期）。

三块确定性（零 LLM）账本，写作时注入写手、章末推进状态：

1. 读者认知账本（reader_knowledge.json）——三态：
   - known：读者已知（reveal_ch <= 当前章的伏笔已揭，不得再当秘密）；
   - waiting：读者在等（buried_ch <= 当前章 < reveal_ch，悬念挂起须推进/维持）；
   - misbelief：读者误认（outline.tag 含「反转」的章前 2 章窗口，不得提前戳破）。

2. 伏笔承诺状态机（foreshadow_commitment.json）——基于既有 ForeshadowItem：
   planted(已埋) → due_soon(临期,距 reveal_ch <= lead 章) → overdue(超时未回收) / revealed(已回收)。
   回收检测：章末钩子确定性比对「reveal_ch 之后 + 正文命中 name/content 子串」。

3. 爽点配额（payoff_quota.json）——按题材 A/B：
   power(爽文类) 3 章小爽/10 章大爽/15 章里程碑；lite(非爽文类) 5/15/25。
   校验基于 outline 设计的情绪节奏（emotion/tag/key_nodes），未达成注入写章软提示。

边界（硬约束，§11）：
- 确定性优先（零 LLM）；store JSON 为唯一权威源；幂等（同章覆盖）；
- 任一异常 logger.exception 留痕、注入返回空串、不阻塞生成；
- 总开关 reader_ledger=False → 不读不写不注入（管线行为与关闭前一致）；
- 注入体积分章命中 + 上限裁剪（仿 setting_cards），空 actionable 不注入。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: 题材配额常量表（唯一事实来源，§11.3 业务数据与代码分离，禁止散落硬编码）。
#: power=爽文类（短间隔密爽）；lite=非爽文类（慢热长间隔）。
GENRE_PROFILE: dict = {
    "power": {
        "small": 3, "big": 10, "milestone": 15,
        "genres": {"玄幻", "仙侠", "都市", "游戏", "奇幻", "系统", "爽文", "修真", "武侠"},
    },
    "lite": {
        "small": 5, "big": 15, "milestone": 25,
        "genres": {"悬疑", "历史", "言情", "科幻", "军事", "体育", "现实", "职场"},
    },
}
DEFAULT_PROFILE = "power"   # 自定义/未命中题材回落（偏 medium 爽文）


def profile_for(genre: str) -> dict:
    """按题材取配额档；未命中回落 DEFAULT_PROFILE。返回 dict（含 small/big/milestone）。"""
    g = (genre or "").strip()
    for name, prof in GENRE_PROFILE.items():
        if g in prof["genres"]:
            return prof
    return GENRE_PROFILE[DEFAULT_PROFILE]


def _fs_name(fs) -> str:
    return str(getattr(fs, "name", "") or getattr(fs, "content", "") or "未命名伏笔").strip()


def _fs_blob(fs) -> str:
    """伏笔用于子串命中检测的文本（name + content）。"""
    return f"{getattr(fs, 'name', '') or ''} {getattr(fs, 'content', '') or ''}".strip()


def _extract_excerpt(content: str, fs, max_chars: int = 150) -> str:
    """REQ-超长篇攻坚 S1.1：从章正文确定性抽 1-3 句关键句（零 LLM）。

    优先返回含伏笔 name/content 关键字的句子（埋设措辞最贴近原文）；无命中则取章首句兜底。
    max_chars 控制预算上限（≤150 token ≈ 300 字符），避免回收章注入超预算。
    """
    import re
    blob = _fs_blob(fs)
    keywords = [w for w in re.split(r"[\s，。！？、；：,.!?;:]", blob) if len(w) >= 2]
    sents = [s.strip() for s in re.split(r"[。！？!?；;\n]+", content) if s.strip()]
    if not sents:
        return ""
    if keywords:
        for k in keywords:
            hit = [s for s in sents if k in s]
            if hit:
                return "；".join(hit[:3])[:max_chars]
    return "；".join(sents[:3])[:max_chars]


class ReaderLedger:
    """读者体验层叙事账本门面：写章注入 + 章末状态推进。"""

    def __init__(self, store, cfg=None):
        self.store = store
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        """总开关：reader_ledger=False 时全部跳过。"""
        if self.cfg is None:
            return True
        return bool(getattr(self.cfg, "reader_ledger", True))

    @property
    def _lead(self) -> int:
        try:
            return max(1, int(getattr(self.cfg, "foreshadow_lead", 3) or 3))
        except Exception:
            return 3

    # ---------------- 写章注入 ----------------
    def inject_block(self, chapter_no: int) -> str:
        """组装写章注入块（合并三账本 actionable 部分）；关闭/异常/空 → 空串。"""
        if not self.enabled:
            return ""
        try:
            world = self.store.load_world()
            outline = self.store.load_outline()
            if world is None and outline is None:
                return ""
            parts: list[str] = []
            kpart = self._knowledge_block(world, outline, chapter_no)
            fpart = self._foreshadow_block(world, outline, chapter_no)
            ppart = self._payoff_block(outline, chapter_no)
            if kpart:
                parts.append(kpart)
            if fpart:
                parts.append(fpart)
            if ppart:
                parts.append(ppart)
            return "\n\n".join(parts)
        except Exception:
            logger.exception("读者认知账本注入失败（降级跳过，不阻塞生成）")
            return ""

    def _knowledge_block(self, world, outline, n: int) -> str:
        """读者认知三态块（known/waiting/misbelief），上限裁剪。"""
        waiting, known, misbelief = self._derive_knowledge(world, outline, n)
        if not (waiting or known or misbelief):
            return ""
        lines: list[str] = []
        if known:
            ks = [f"- {k['name']}（第{k['reveal_ch']}章已揭示）" for k in known[:6]]
            lines.append("【读者已知（以下真相已在正文揭开，不得再当作秘密隐瞒或制造悬念式重复）】\n"
                         + "\n".join(ks))
        if waiting:
            ws = [f"- {w['name']}（第{w['buried_ch']}章埋下，预计第{w['reveal_ch']}章回收）"
                  for w in waiting[:6]]
            lines.append("【读者在等（以下悬念已埋未收，本章须推进或维持，不得遗忘）】\n"
                         + "\n".join(ws))
        if misbelief:
            ms = [f"- 反转第{m['reversal_ch']}章：读者当前被引导相信「{m['surface']}」"
                  f"——这是刻意误导，本章不得提前戳破真相" for m in misbelief[:2]]
            lines.append("【读者误认（反转前保护误导，不得剧透）】\n" + "\n".join(ms))
        return "\n".join(lines)

    def _derive_knowledge(self, world, outline, n: int):
        """确定性派生三态（零 LLM）。"""
        waiting, known, misbelief = [], [], []
        fs_list = list(getattr(world, "foreshadowing", None) or []) if world else []
        for fs in fs_list:
            reveal = getattr(fs, "reveal_ch", None)
            buried = int(getattr(fs, "buried_ch", 0) or 0)
            name = _fs_name(fs)
            if reveal is not None and int(reveal) <= n:
                known.append({"name": name, "reveal_ch": int(reveal)})
            elif buried <= n and (reveal is None or int(reveal) > n):
                waiting.append({"name": name, "buried_ch": buried,
                                "reveal_ch": int(reveal) if reveal is not None else 0})
        # 误认窗口：outline.tag 含「反转」的章，其前 [r-2, r-1] 章读者相信表面叙述
        chapters = list(getattr(outline, "chapters", None) or []) if outline else []
        for plan in chapters:
            r = int(getattr(plan, "no", 0) or 0)
            if not r or r <= n:
                continue
            if "反转" not in (getattr(plan, "tag", "") or "") \
                    and "反转" not in (getattr(plan, "core_event", "") or ""):
                continue
            if r - 2 <= n <= r - 1:
                prev_idx = r - 2 if r - 2 > 0 else 0
                prev = chapters[prev_idx] if prev_idx < len(chapters) else plan
                surface = (getattr(prev, "goal", "") or getattr(prev, "core_event", ""))[:50]
                misbelief.append({"reversal_ch": r, "surface": surface or "前文章节表面叙述"})
        return waiting, known, misbelief

    def _foreshadow_block(self, world, outline, n: int) -> str:
        """伏笔临期/超时收束提醒（只注入 actionable：due_soon + overdue）。

        REQ-超长篇攻坚 S1.1：开关 foreshadow_original_text=True 时，在提醒后附【到期伏笔原文块】——
        把埋设章存档的原文关键句逐字还给回收章，避免"只记摘要、丢细节"导致回收与埋设措辞脱节。
        """
        items = self._commitment_items(world, n)
        due = [i for i in items if i["state"] == "due_soon"]
        over = [i for i in items if i["state"] == "overdue"]
        if not (due or over):
            return ""
        lead = self._lead
        lines: list[str] = []
        for i in due[:6]:
            lines.append(f"- 伏笔「{i['name']}」计划第{i['reveal_ch']}章回收，本章临期"
                         f"（提前{lead}章），应开始收束线索/铺垫回收")
        for i in over[:6]:
            lines.append(f"- 伏笔「{i['name']}」已超过计划回收章{i['reveal_ch']}仍未回收，"
                         f"本章必须兑现，或显式推迟回收章")
        block = "【伏笔承诺·收束提醒】\n" + "\n".join(lines)
        open_text = (self.cfg is not None
                     and bool(getattr(self.cfg, "foreshadow_original_text", True)))
        if open_text:
            excerpts = []
            for i in (due + over)[:6]:
                ex = (i.get("original_excerpt") or "").strip()
                if ex:
                    excerpts.append(f"- 「{i['name']}」埋设原文：{ex}")
            if excerpts:
                block += "\n\n【到期伏笔原文（回收章必须语义呼应的关键句，逐字参考、勿写岔）】\n" \
                         + "\n".join(excerpts)
        return block

    def _commitment_items(self, world, n: int) -> list[dict]:
        """计算伏笔承诺状态机（合并持久化 revealed_ch）。n < buried 的不纳入。"""
        persisted = {}
        try:
            pc = self.store.load_foreshadow_commitment() or {}
            for it in pc.get("items", []):
                nm = str(it.get("name") or "")
                if nm:
                    persisted[nm] = it
        except Exception:
            pass
        lead = self._lead
        out: list[dict] = []
        fs_list = list(getattr(world, "foreshadowing", None) or []) if world else []
        for fs in fs_list:
            name = _fs_name(fs)
            reveal = getattr(fs, "reveal_ch", None)
            buried = int(getattr(fs, "buried_ch", 0) or 0)
            if reveal is None:
                continue  # 只埋不收（设计缺陷，validator 世界阶段已判 fatal，本账本不跟踪回收）
            reveal = int(reveal)
            if n < buried:
                continue
            prev = persisted.get(name, {})
            revealed_ch = prev.get("revealed_ch")
            if revealed_ch:
                state = "revealed"
            elif n > reveal:
                state = "overdue"
            elif reveal - lead <= n < reveal:
                state = "due_soon"
            else:
                state = "planted"
            # REQ-超长篇攻坚 S1.1：原文关键句——优先取账本持久化，其次 ForeshadowItem.original_excerpt
            excerpt = (prev.get("original_excerpt") or ""
                       or getattr(fs, "original_excerpt", "") or "").strip()
            out.append({"name": name, "buried_ch": buried, "reveal_ch": reveal,
                        "level": getattr(fs, "level", "medium") or "medium",
                        "state": state, "revealed_ch": revealed_ch,
                        "original_excerpt": excerpt})
        return out

    def _payoff_block(self, outline, n: int) -> str:
        """爽点配额缺口提示（仅未达成时出现，≤3 行）。"""
        if self.cfg is None or not bool(getattr(self.cfg, "payoff_quota", True)):
            return ""
        if outline is None or not getattr(outline, "chapters", None):
            return ""
        prof = profile_for(getattr(self.cfg, "genre", ""))
        gaps = []
        if not self._window_payoff(outline, n, prof["small"], "small"):
            gaps.append(f"- 近{prof['small']}章大纲未安排小爽点，本章可安排一次小爽"
                        f"（主角占上风/获得回报/打脸/突破），避免连续平淡")
        if not self._window_payoff(outline, n, prof["big"], "big"):
            gaps.append(f"- 近{prof['big']}章大纲未安排大爽/高潮，本章可推进一次较大的爽点或冲突升级")
        if not gaps:
            return ""
        return "【节奏提醒·爽点配额】\n" + "\n".join(gaps[:3])

    @staticmethod
    def _window_payoff(outline, n: int, window: int, kind: str) -> bool:
        """大纲设计的情绪窗口内是否有爽点（零 LLM，基于 emotion/tag/key_nodes）。"""
        lo = n - window + 1
        for plan in outline.chapters:
            no = int(getattr(plan, "no", 0) or 0)
            if no < lo or no > n:
                continue
            tag = getattr(plan, "tag", "") or ""
            emotion = getattr(plan, "emotion", "") or ""
            if kind == "small":
                if ("爽" in emotion) or ("爽" in tag) or ("高潮" in tag):
                    return True
            else:  # big
                if ("大爽" in tag) or ("高潮" in tag) or ("里程碑" in tag) or ("爽" in emotion):
                    return True
        if kind == "big":
            for kn in getattr(outline, "key_nodes", None) or []:
                kch = int(getattr(kn, "chapter_no", 0) or 0)
                if lo <= kch <= n:
                    return True
        return False

    # ---------------- 章末后处理钩子 ----------------
    def update_post_chapter(self, chapter_no: int) -> None:
        """章末钩子：伏笔回收检测 + 爽点配额 tally；幂等、失败静默留痕。"""
        if not self.enabled:
            return
        try:
            self._update_foreshadow(chapter_no)
        except Exception:
            logger.exception("伏笔承诺账本章末推进失败（降级跳过）: ch=%s", chapter_no)
        try:
            self._archive_foreshadow_excerpts(chapter_no)
        except Exception:
            logger.exception("伏笔原文句存档失败（降级跳过）: ch=%s", chapter_no)
        try:
            self._update_payoff(chapter_no)
        except Exception:
            logger.exception("爽点配额章末 tally 失败（降级跳过）: ch=%s", chapter_no)

    def _update_foreshadow(self, no: int) -> None:
        """检测本章正文是否回收了计划回收的伏笔，更新状态机落盘。

        回收判定（确定性零 LLM）：reveal_ch <= 当前章 且 本章正文命中伏笔 name 或
        content 子串 → 标记 revealed；检测不到保留原状态（注入层按 overdue 软提醒）。"""
        world = self.store.load_world()
        chapter = self.store.load_chapter(no)
        if world is None or chapter is None or not getattr(chapter, "content", ""):
            return
        content = chapter.content
        items = self._commitment_items(world, no)
        if not items:
            return
        for it in items:
            if it.get("revealed_ch"):
                continue                      # 已回收，幂等跳过
            if it["reveal_ch"] > no:
                continue                      # 尚未到回收章，正文提及不算回收
            name_hit = bool(it["name"]) and it["name"] in content
            content_hit = False
            fs = self._find_fs(world, it["name"])
            if fs:
                c = (getattr(fs, "content", "") or "").strip()
                content_hit = bool(c) and c in content
            if name_hit or content_hit:
                it["state"] = "revealed"
                it["revealed_ch"] = no
        self.store.save_foreshadow_commitment({"version": 1, "updated_ch": int(no),
                                               "items": items})

    def _find_fs(self, world, name: str):
        for fs in getattr(world, "foreshadowing", None) or []:
            if _fs_name(fs) == name:
                return fs
        return None

    def _archive_foreshadow_excerpts(self, no: int) -> None:
        """REQ-超长篇攻坚 S1.1：埋设章落盘原文句存档。

        对 buried_ch == 当前章 且尚未存档原文关键句的伏笔，从本章正文确定性抽 1-3 句
        （≤150 token/条）写进 foreshadow_commitment.json 的 items.original_excerpt，
        供 due_soon/overdue 章随收束提醒注入原文块。开关 foreshadow_original_text=False 跳过。
        """
        if self.cfg is None or not bool(getattr(self.cfg, "foreshadow_original_text", True)):
            return
        world = self.store.load_world()
        chapter = self.store.load_chapter(no)
        if world is None or chapter is None or not getattr(chapter, "content", ""):
            return
        content = chapter.content
        targets = [fs for fs in (getattr(world, "foreshadowing", None) or [])
                   if int(getattr(fs, "buried_ch", 0) or 0) == no]
        if not targets:
            return
        pc = self.store.load_foreshadow_commitment() or {}
        items = list(pc.get("items", []))
        by_name = {str(it.get("name") or ""): it for it in items}
        changed = False
        for fs in targets:
            name = _fs_name(fs)
            if not name:
                continue
            it = by_name.get(name)
            if it is None:
                it = {"name": name}
                by_name[name] = it
            if (it.get("original_excerpt") or "").strip():
                continue                      # 已存档，幂等跳过
            excerpt = _extract_excerpt(content, fs)
            if excerpt:
                it["original_excerpt"] = excerpt
                changed = True
        if changed:
            merged = [it for it in by_name.values() if it.get("name")]
            self.store.save_foreshadow_commitment({"version": 1, "updated_ch": int(no),
                                                   "items": merged})

    def _update_payoff(self, no: int) -> None:
        """爽点配额 tally：记录本章小/大/里程碑是否达成（基于大纲设计）。"""
        if self.cfg is None or not bool(getattr(self.cfg, "payoff_quota", True)):
            return
        outline = self.store.load_outline()
        if outline is None or not getattr(outline, "chapters", None):
            return
        prof = profile_for(getattr(self.cfg, "genre", ""))
        rec = {
            "ch": int(no),
            "small_met": self._window_payoff(outline, no, prof["small"], "small"),
            "big_met": self._window_payoff(outline, no, prof["big"], "big"),
            "milestone_met": self._window_payoff(outline, no, prof["milestone"], "big"),
            "small_window": prof["small"], "big_window": prof["big"],
            "milestone_window": prof["milestone"],
        }
        old = self.store.load_payoff_quota() or {}
        log = [r for r in (old.get("log") or []) if int(r.get("ch", 0)) != int(no)]
        log.append(rec)
        log.sort(key=lambda r: int(r.get("ch", 0)))
        self.store.save_payoff_quota({"version": 1, "profile": self._profile_name(),
                                      "log": log[-200:]})

    def _profile_name(self) -> str:
        g = (getattr(self.cfg, "genre", "") or "") if self.cfg else ""
        for name, prof in GENRE_PROFILE.items():
            if g in prof["genres"]:
                return name
        return DEFAULT_PROFILE
