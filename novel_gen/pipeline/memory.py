# -*- coding: utf-8 -*-
"""
记忆系统：解决长篇小说「前后矛盾」的核心组件。

两层记忆：
1. 设定库（长期）：世界观 + 人物卡 + 伏笔清单，全书记忆，永不遗忘
2. 章节摘要（短期）：已写章节的一句话摘要，只回填最近 N 章，控制上下文成本

生成当前章时，把「设定库 + 最近 N 章摘要」拼进提示词，让模型基于既定事实续写。
"""
from __future__ import annotations

from ..store.project import ProjectStore
from ..store.schemas import World
from .mechanism_types import has_type, resolve_types


def predict_appearing_chars(world: World | None, goal: str, recent_summaries: list[str],
                            char_states: dict | None, up_to_chapter: int, limit: int = 8) -> list[str]:
    """预测本章出场人物（P1-3 Lorebook 式裁剪）。

    依据：本章目标 + 近章摘要里命中的人物名；再并入近 3 章出场记录（P0-2 数据）。
    预测为空（如首章 goal 未提人名）→ 返回全部人物（兜底，保证不裁关键信息）。
    """
    if world is None or not world.characters:
        return []
    names = [c.name for c in world.characters if c.name]
    if not names:
        return []
    text = f"{goal or ''} " + " ".join(recent_summaries)
    hit = [n for n in names if n and n in text]
    for name, st in (char_states or {}).items():
        if name in names and int(st.get("last_seen", 0) or 0) >= up_to_chapter - 3:
            if name not in hit:
                hit.append(name)
    if hit:
        return hit[:limit]
    return names  # 预测失败 → 全量注入


class Memory:
    def __init__(self, store: ProjectStore, memory_window: int = 5):
        self.store = store
        self.memory_window = max(0, memory_window)

    def build_context(self, world: World | None, up_to_chapter: int, chapter_goal: str = "") -> str:
        """为第 up_to_chapter 章构建记忆上下文。

        L1 全局设定库：按章触发注入（P1-3）——只注入预测出场人物卡与相关伏笔；
            用户强约束 / 锁定项 / 核心世界观永不裁剪，置于 prompt 末尾。
        L1.5 人物动态状态：最近出场人物的当前状态（P0-2）
        L1.6 章内/章间状态账本：上一章结束时的机制数字/人物生死/物品/时间/事实
            （REQ-章内自洽与机制账本：承接可变状态，防面板数字断链、死而复生）
        L2 卷级摘要：当前所在卷的卷目标+高潮+人物成长
        L3 近章大纲：当前章前后各2章的剧情节点
        L4 前章尾文：最近N章一句话摘要（短期）
        L4.5 近章正文直读：最近 N 章正文尾部（P0-1）
        """
        parts: list[str] = []
        key_parts: list[str] = []   # 强约束区：放 prompt 末尾（权重最高）

        # L1 全局设定库（P1-3 Lorebook 式裁剪）
        if world is not None:
            self._build_l1_world(world, up_to_chapter, chapter_goal, parts, key_parts)

        self._build_l15_states(parts, up_to_chapter)

        self._build_l16_chapter_state(parts, up_to_chapter)

        self._build_l2_and_l3(parts, up_to_chapter)

        self._build_l4_summaries(parts, up_to_chapter)

        self._build_l45_direct_read(parts, up_to_chapter)

        self._build_verify_issues(parts)

        self._build_anti_cliche(parts)

        self._build_repetition(parts, up_to_chapter)

        # 强约束区（P1-3）：用户锁定 / 强约束 / 核心世界观，置于 prompt 末尾（模型权重最高）
        if key_parts:
            parts.append("[L1·核心锁定·强约束（最高优先级，必须严格遵守）]\n" + "\n".join(key_parts))

        if not parts:
            return ""
        return "\n\n".join(parts)

    def _build_l1_world(self, world: World, up_to_chapter: int, chapter_goal: str,
                       parts: list[str], key_parts: list[str]) -> None:
        """L1 全局设定库：分章裁剪注入（人物/实体/伏笔 reveal + 核心锁定项）。

        分章设定卡子集缺失/关闭/失败时回退全量注入（保守，不裁设定）。
        """
        cfg_d, lorebook_on = self._load_lorebook_flag()
        w_normal: list[str] = []
        # REQ-token优化 P1-1：分章设定卡子集（缺失/关闭/失败 → None = 回退全量注入）
        (sc_ch, sub_chars, sub_enemies, sub_locations, sub_factions,
         sub_fs, sub_items, sub_terminology) = self._load_setting_cards(up_to_chapter)
        self._build_world_reveals(world, up_to_chapter, w_normal, key_parts)
        hit, trimmed = self._build_character_cards(
            world, sub_chars, lorebook_on, chapter_goal, up_to_chapter, w_normal)
        self._build_entity_cards(
            world, sub_enemies, sub_locations, sub_factions, sub_items,
            sub_terminology, w_normal)
        self._build_foreshadow(world, sub_fs, hit, trimmed, w_normal)
        self._build_key_settings(world, cfg_d, up_to_chapter, key_parts)
        if w_normal:
            parts.append("[L1·全局设定·本章相关]\n" + "\n".join(w_normal))

    def _build_l15_states(self, parts: list[str], up_to_chapter: int) -> None:
        """L1.5 人物动态状态快照（P0-2）：静态人物卡之外，注入最近出场人物的当前状态。"""
        try:
            from .character_state import format_state_line
            cs = self.store.load_character_states()
            if cs:
                items = sorted(
                    cs.items(),
                    key=lambda kv: int(kv[1].get("last_seen", 0) or 0),
                    reverse=True,
                )[:8]
                lines = [format_state_line(name, st) for name, st in items if st.get("last_seen")]
                if lines:
                    parts.append("[L1.5·人物当前状态·最近更新]\n" + "\n".join(lines))
        except Exception:
            pass

    def _build_l16_chapter_state(self, parts: list[str], up_to_chapter: int) -> None:
        """L1.6 章内/章间状态账本（REQ-章内自洽与机制账本）：承接上一章可变状态，防断链/死而复生。"""
        try:
            from .chapter_state import format_chapter_state
            st = self.store.load_chapter_state()
            if st and int(st.get("chapter", 0) or 0) < up_to_chapter:
                hint = format_chapter_state(st)
                if hint:
                    parts.append("[L1.6·世界状态·承接上一章]\n" + hint)
        except Exception:
            pass

    def _build_l2_and_l3(self, parts: list[str], up_to_chapter: int) -> None:
        """L2 卷级摘要 + L3 近章大纲 + L3.5 本章转场衔接。"""
        try:
            outline = self.store.load_outline()
            if outline and outline.volumes:
                cur_vol = None
                for v in outline.volumes:
                    if v.start_ch <= up_to_chapter <= v.end_ch:
                        cur_vol = v
                        break
                if cur_vol:
                    v_lines = [f"当前卷：第{cur_vol.no}卷《{cur_vol.name}》"]
                    if cur_vol.intro:
                        v_lines.append(f"卷主题：{cur_vol.intro}")
                    if cur_vol.main_conflict:
                        v_lines.append(f"本卷矛盾：{cur_vol.main_conflict}")
                    if cur_vol.character_growth:
                        v_lines.append(f"人物成长：{cur_vol.character_growth}")
                    if cur_vol.climax:
                        v_lines.append(f"卷高潮：{cur_vol.climax}")
                    parts.append("[L2·当前分卷]\n" + "\n".join(v_lines))
                # L3 近章大纲：前后各2章节点
                nearby = []
                for c in outline.chapters:
                    if abs(c.no - up_to_chapter) <= 2 and c.no < up_to_chapter:
                        nearby.append(c)
                if nearby:
                    n_lines = [f"第{c.no}章《{c.title}》：{c.goal}" for c in nearby]
                    parts.append("[L3·近期章节大纲]\n" + "\n".join(n_lines))
                # L3.5 本章转场衔接：架构师设计的 transition（上一章结尾 → 本章开头如何衔接），
                # 写手必须据此从上一章结尾续写，避免章节场景硬切
                try:
                    idx = up_to_chapter - 1
                    if 0 <= idx < len(outline.chapters):
                        cur_plan = outline.chapters[idx]
                        tr = getattr(cur_plan, "transition", "") or ""
                        if tr:
                            parts.append(
                                "[L3.5·本章转场衔接·写手必须按此从上一章结尾续写（最高优先于自由发挥）]\n" + tr
                            )
                except Exception:
                    pass
        except Exception:
            pass

    def _build_l4_summaries(self, parts: list[str], up_to_chapter: int) -> None:
        """L4 前章尾文（短期摘要）：最近 N 章一句话摘要。"""
        summaries = self.store.load_summaries()
        recent = [s for s in summaries if int(s.get("no", 0)) < up_to_chapter]
        recent = recent[-self.memory_window:] if self.memory_window else recent
        if recent:
            lines = [f"第{s['no']}章：{s['summary']}" for s in recent]
            parts.append(
                f"[L4·前章剧情摘要·最近 {len(recent)} 章]\n" + "\n".join(lines)
            )

    def _build_l45_direct_read(self, parts: list[str], up_to_chapter: int) -> None:
        """L4.5 近章正文直读（P0-1）：直读最近 N 章正文尾部作细节锚点。"""
        try:
            cfg_d = self.store.load_config()
            n_direct = int(getattr(cfg_d, "memory_direct_read_chapters", 0) or 0)
            chars_d = int(getattr(cfg_d, "memory_direct_read_chars", 2000) or 0)
            if n_direct > 0 and chars_d > 0:
                direct_lines = []
                for dno in range(up_to_chapter - 1, max(0, up_to_chapter - 1 - n_direct), -1):
                    ch = self.store.load_chapter(dno)
                    if ch and ch.content:
                        tail = ch.content.strip()[-chars_d:]
                        if tail:
                            direct_lines.append(f"第{dno}章《{ch.title}》：{tail}")
                if direct_lines:
                    parts.append(
                        "[L4.5·近章正文直读·细节锚点（以直读正文为准，摘要仅供快速回顾）]\n"
                        + "\n\n".join(direct_lines)
                    )
        except Exception:
            pass

    def _build_verify_issues(self, parts: list[str]) -> None:
        """一致性校验 issues（高质量模式）：提醒后续章节注意修正。"""
        try:
            v_issues = self.store.load_verify_issues()
            # 只保留最近一次校验（按 chapter 取最新）
            if v_issues:
                last_ch = max(int(x.get("chapter", 0)) for x in v_issues)
                recent_issues = [x for x in v_issues if int(x.get("chapter", 0)) == last_ch]
                if recent_issues:
                    v_lines = [
                        f"- {it.get('problem', '')}（建议：{it.get('suggestion', '注意保持一致')}）"
                        for it in recent_issues
                    ]
                    parts.append(
                        "[前次一致性校验·需在后续章节注意]\n" + "\n".join(v_lines)
                    )
        except Exception:
            pass

    def _build_anti_cliche(self, parts: list[str]) -> None:
        """反套路约束（常驻）：读 anti_cliche.json 注入防套路提示。"""
        try:
            ac_path = self.store.dir / "anti_cliche.json"
            if ac_path.exists():
                import json
                ac_data = json.loads(ac_path.read_text(encoding="utf-8"))
                from .templates import anti_cliche_hint
                ac_hint = anti_cliche_hint(ac_data)
                if ac_hint:
                    parts.append(ac_hint)
        except Exception:
            pass

    def _build_repetition(self, parts: list[str], up_to_chapter: int) -> None:
        """重复度检测：最近章节高频词/句式。"""
        try:
            from .repetition import RepetitionDetector
            cfg = self.store.load_config()
            detector = RepetitionDetector(cfg, self.store)
            banned_hint = detector.banned_hint(up_to_chapter)
            if banned_hint:
                parts.append(banned_hint)
        except Exception:
            pass

    def _load_lorebook_flag(self) -> tuple:
        """读取 L1 全局设定的 lorebook 裁剪开关与项目配置。"""
        cfg_d = None
        lorebook_on = True
        try:
            cfg_d = self.store.load_config()
            lorebook_on = bool(getattr(cfg_d, "lorebook_trim", True))
        except Exception:
            pass
        return cfg_d, lorebook_on

    def _load_setting_cards(self, up_to_chapter: int) -> tuple:
        """REQ-token优化 P1-1：读取本章分章设定卡子集（缺失/关闭/失败 → None = 回退全量注入）。"""
        sc_ch = None
        try:
            _cfg_d = self.store.load_config()
            if bool(getattr(_cfg_d, "setting_cards", True)):
                _sc = self.store.load_setting_cards()
                if _sc and isinstance(_sc.get("chapters"), list):
                    for _c in _sc["chapters"]:
                        if int(_c.get("no", 0)) == up_to_chapter:
                            sc_ch = _c
                            break
        except Exception:
            pass
        # 注意：空列表（[]）= 命中索引但本章无该项 → 不注入；None = 索引缺失/关闭 → 回退全量
        _scd = sc_ch or {}
        sub_chars = _scd.get("chars") if sc_ch else None
        sub_enemies = _scd.get("enemies") if sc_ch else None
        sub_locations = _scd.get("locations") if sc_ch else None
        sub_factions = _scd.get("factions") if sc_ch else None
        sub_fs = _scd.get("foreshadows") if sc_ch else None
        # P2 修正：物品/术语卡也走分章命中（如生物/地点/势力，命中才注入，不再无条件进强约束区）
        sub_items = _scd.get("items") if sc_ch else None
        sub_terminology = _scd.get("terminology") if sc_ch else None
        return (sc_ch, sub_chars, sub_enemies, sub_locations, sub_factions,
                sub_fs, sub_items, sub_terminology)

    def _build_world_reveals(self, world, up_to_chapter: int, w_normal: list[str], key_parts: list[str]) -> None:
        """L1 world_reveals 三段式（潜伏/铺垫/揭示），按主角视角逐步展开防剧透。"""
        reveals = getattr(world, "world_reveals", None) or []
        if reveals:
            # 跟随主角视角逐步展开，三段式：潜伏期 → 铺垫期 → 揭示期。
            # - 潜伏期（N < foreshadow_ch）：设定完全不注入（写早了即剧透）
            # - 铺垫期（foreshadow_ch ≤ N < from_ch）：注入 hint（可写暗示/异常），
            #   真相仍进强约束区禁剧透——保证揭示是"被线索引出来"而非凭空摊牌
            # - 揭示期（N ≥ from_ch）：真相注入普通区，并要求收束此前线索
            known: list[str] = []      # 已揭示真相 → 普通区
            hints: list[str] = []      # 铺垫线索 → 普通区
            unrevealed: list[str] = [] # 未揭示真相 → 强约束区禁剧透
            for r in sorted(reveals, key=lambda r: r.from_ch):
                fs = r.foreshadow_ch if r.foreshadow_ch is not None else r.from_ch
                if r.from_ch <= up_to_chapter:
                    known.append(r.content)
                elif fs <= up_to_chapter:
                    # 铺垫期：有显式铺垫（foreshadow_ch < from_ch）
                    if r.hint:
                        hints.append(r.hint)
                    unrevealed.append(r.content)  # 真相仍禁剧透
                elif r.foreshadow_ch is None:
                    # 旧数据（无铺垫期）：未揭示真相仍进强约束禁剧透（保持原行为）
                    unrevealed.append(r.content)
                # 否则：显式铺垫的潜伏期，不注入
            if known:
                w_normal.append(
                    "【当前阶段·主角已知世界观】\n"
                    + "\n".join(f"- {c}" for c in known)
                )
            if hints:
                w_normal.append(
                    "【当前阶段·铺垫线索（本章可写的暗示/异常/疑惑，但严禁说出真相）】\n"
                    + "\n".join(f"- {h}" for h in hints)
                )
            if unrevealed:
                key_parts.append(
                    "【尚未揭示设定·绝对禁止在正文提前透露真相，"
                    "只能按铺垫线索写暗示；留待对应章节随主角探索/剧情推进自然揭晓】\n"
                    + "\n".join(f"- {c}" for c in unrevealed)
                )
        elif world.world_view:
            # 旧数据（无揭示计划）：保持原行为，全量注入
            key_parts.append(f"世界观：{world.world_view}")

    def _build_character_cards(self, world, sub_chars, lorebook_on: bool, chapter_goal: str,
                               up_to_chapter: int, w_normal: list[str]) -> tuple:
        """L1 人物卡 + 配角动机卡；返回 (出场人物 hit, 是否裁剪 trimmed)。"""
        hit: list[str] = []
        if not world.characters:
            return hit, False
        def _char_line(c):
            return f"{c.name}（{c.role}）——{c.desc}"
        hit = [c.name for c in world.characters if c.name]
        trimmed = False
        if sub_chars:
            # 设定卡子集：只注入本章出场人物（真子集才算裁剪）
            sub_hit = [n for n in sub_chars if any(c.name == n for c in world.characters)]
            if sub_hit:
                hit, trimmed = sub_hit, len(sub_hit) < len(hit)
        elif lorebook_on and len(world.characters) > 4:
            # 预测本章出场人物；预测失败回退全量注入（不裁关键信息）
            try:
                summaries = [s.get("summary", "") for s in self.store.load_summaries()[-3:]]
                cand = predict_appearing_chars(
                    world, chapter_goal, summaries,
                    self.store.load_character_states() or {}, up_to_chapter,
                )
                if cand and len(cand) < len(hit):
                    hit, trimmed = cand, True
            except Exception:
                pass
        chars = "；".join(_char_line(c) for c in world.characters if c.name in hit)
        w_normal.append(f"本章相关人物卡：{chars}" if trimmed else f"人物卡：{chars}")
        # 配角动机卡（REQ-章内自洽与机制账本）：人物卡只有静态描述，
        # 动机卡（goal/behavior_logic/link_to_protagonist）决定行为逻辑，
        # 不注入则写手临场编动机，导致押金责任方/抢袋动机等前后漂移。
        try:
            motives = getattr(world, "character_motives", None) or []
            if motives:
                m_lines = []
                for m in motives:
                    name = getattr(m, "name", "") or ""
                    if name and name in hit:
                        bits = []
                        if getattr(m, "goal", ""):
                            bits.append(f"目标：{m.goal}")
                        if getattr(m, "behavior_logic", ""):
                            bits.append(f"行为逻辑：{m.behavior_logic}")
                        if getattr(m, "link_to_protagonist", ""):
                            bits.append(f"与主角关联：{m.link_to_protagonist}")
                        if bits:
                            m_lines.append(f"{name}（{'；'.join(bits)}）")
                if m_lines:
                    w_normal.append("本章相关人物动机卡：" + "；".join(m_lines))
        except Exception:
            pass
        return hit, trimmed

    def _build_entity_cards(self, world, sub_enemies, sub_locations, sub_factions,
                            sub_items, sub_terminology, w_normal: list[str]) -> None:
        """L1 事前设计卡：怪物/地点/势力 + 分章命中的物品/术语卡（写手不得自行发明）。"""
        self._build_creature_cards(world, sub_enemies, sub_locations, sub_factions, w_normal)
        self._build_item_term_cards(world, sub_items, sub_terminology, w_normal)

    def _build_foreshadow(self, world, sub_fs, hit: list[str], trimmed: bool,
                          w_normal: list[str]) -> None:
        """L1 伏笔：设定卡子集 / 裁剪后相关伏笔 / 全量已埋伏笔。"""
        if not world.foreshadowing:
            return
        def _fs(x):
            # ForeshadowItem（结构化）：携带埋点/回收章号，支撑正文伏笔闭环
            if hasattr(x, "buried_ch"):
                parts = []
                if x.name or x.content:
                    parts.append(x.name or x.content)
                if getattr(x, "buried_ch", 0):
                    parts.append(f"埋第{x.buried_ch}章")
                if getattr(x, "reveal_ch", None):
                    parts.append(f"收第{x.reveal_ch}章")
                return "（" + "，".join(parts) + "）" if len(parts) > 1 else (parts[0] if parts else "")
            if isinstance(x, dict):
                return " ".join(str(v) for v in x.values())
            return str(x)
        if sub_fs is not None:
            if sub_fs:
                w_normal.append("相关伏笔：" + "；".join(sub_fs))
        elif trimmed:
            related = [x for x in world.foreshadowing if any(n in _fs(x) for n in hit)]
            if related:
                w_normal.append("相关伏笔：" + "；".join(_fs(x) for x in related))
        else:
            w_normal.append("已埋伏笔：" + "；".join(_fs(x) for x in world.foreshadowing))

    def _build_key_settings(self, world, cfg_d, up_to_chapter: int, key_parts: list[str]) -> None:
        """L1 用户强约束 + 全部事前设定卡（进强约束区，永不裁剪，prompt 末尾）。"""
        self._build_user_constraints(world, cfg_d, key_parts)
        self._build_theme_anchors(world, key_parts)
        self._build_ruleset_cards(world, key_parts)
        self._build_mechanism_baseline_cards(world, key_parts)
        self._build_progression_cards(world, key_parts)
        self._build_ending_cards(world, cfg_d, up_to_chapter, key_parts)

    def _build_creature_cards(self, world, sub_enemies, sub_locations, sub_factions,
                              w_normal: list[str]) -> None:
        """L1 事前设计卡：生物图鉴 / 地点卡 / 势力卡（写手不得自行发明）。"""
        # ---- 事前设计卡（REQ-事前设定卡）：怪物/地点/势力，小而全量注入（写手不得自行发明）。
        # REQ-token优化 P1-1：有设定卡子集时按章只注入相关卡；无子集保持全量注入。 ----
        try:
            enemies = getattr(world, "enemies", None) or []
            if sub_enemies is not None:
                enemies = [e for e in enemies if getattr(e, "name", "") in sub_enemies]
            if enemies:
                e_lines = []
                for e in enemies:
                    bits = []
                    if getattr(e, "form", ""): bits.append(f"形态：{e.form}")
                    if getattr(e, "senses", ""): bits.append(f"感知：{e.senses}（正文不得矛盾）")
                    if getattr(e, "behavior", ""): bits.append(f"行为：{e.behavior}")
                    if getattr(e, "kill", ""): bits.append(f"弱点：{e.kill}")
                    if bits:
                        e_lines.append(f"{e.name}（{'；'.join(bits)}）")
                if e_lines:
                    w_normal.append("【生物图鉴·形态/感知/行为写死，不得自相矛盾】\n" + "\n".join(e_lines))
        except Exception:
            pass
        try:
            locs = getattr(world, "locations", None) or []
            if sub_locations is not None:
                locs = [lc for lc in locs if getattr(lc, "name", "") in sub_locations]
            if locs:
                l_lines = []
                for lc in locs:
                    bits = []
                    if getattr(lc, "terrain", ""): bits.append(f"地形：{lc.terrain}")
                    if getattr(lc, "light", ""): bits.append(f"光线：{lc.light}（不得矛盾）")
                    if getattr(lc, "exits", ""): bits.append(f"出口：{lc.exits}")
                    if bits:
                        l_lines.append(f"{lc.name}（{'；'.join(bits)}）")
                if l_lines:
                    w_normal.append("【地点卡·地形/光线/出口写死，不得自相矛盾】\n" + "\n".join(l_lines))
        except Exception:
            pass
        try:
            facs = getattr(world, "factions", None) or []
            if sub_factions is not None:
                facs = [f for f in facs if getattr(f, "name", "") in sub_factions]
            if facs:
                f_lines = []
                for f in facs:
                    bits = []
                    if getattr(f, "goal", ""): bits.append(f"目标：{f.goal}")
                    if getattr(f, "relation_to_mc", ""): bits.append(f"与主角：{f.relation_to_mc}")
                    if bits:
                        f_lines.append(f"{f.name}（{'；'.join(bits)}）")
                if f_lines:
                    w_normal.append("【势力卡·成员行为须符合此卡】\n" + "；".join(f_lines))
        except Exception:
            pass

    def _build_item_term_cards(self, world, sub_items, sub_terminology,
                               w_normal: list[str]) -> None:
        """L1 分章命中的物品/道具卡 + 术语统一卡（防万能工具 / 专有名词标准写法）。"""
        # P2 修正：物品卡（防万能工具）按分章命中注入（sub_items=None=索引缺失回退全量；
        # 空列表=本章无命中不注入）；不再是"永不裁剪"强约束区
        try:
            items = getattr(world, "items", None) or []
            if sub_items is not None:
                items = [it for it in items if getattr(it, "name", "") in sub_items]
            if items:
                item_lines = []
                for it in items:
                    _i = getattr(it, "name", "") or ""
                    _eff = getattr(it, "effect", "") or ""
                    _cost = getattr(it, "cost", "") or ""
                    _res = getattr(it, "restriction", "") or ""
                    if _i:
                        item_lines.append(
                            f"{_i}｜作用：{_eff}｜代价：{_cost}｜限制：{_res}"
                        )
                if item_lines:
                    w_normal.append(
                        "【本章相关物品/道具卡·静态属性不得违背，不得写成万能工具，使用须付出代价并受限制约束】\n"
                        + "\n".join(item_lines)
                    )
        except Exception:
            pass
        # P2 修正：术语统一卡按分章命中注入（命中才注入，避免每章全量塞专有名词表）
        try:
            terminology = getattr(world, "terminology", None) or []
            if sub_terminology is not None:
                terminology = [t for t in terminology if getattr(t, "name", "") in sub_terminology]
            if terminology:
                term_lines = []
                for t in terminology:
                    _n = getattr(t, "name", "") or ""
                    if not _n:
                        continue
                    _aliases = [str(x) for x in (getattr(t, "aliases", None) or []) if str(x).strip()]
                    _line = f"{_n}" + (f"（禁止写法：{'、'.join(_aliases)}）" if _aliases else "")
                    if getattr(t, "definition", ""):
                        _line += f"｜{t.definition}"
                    term_lines.append(_line)
                if term_lines:
                    w_normal.append(
                        "【本章相关术语统一·以下专有名词本章必须用标准写法，禁止出现别名/其他写法】\n"
                        + "\n".join(term_lines)
                    )
        except Exception:
            pass

    def _build_user_constraints(self, world, cfg_d, key_parts: list[str]) -> None:
        """L1 用户强约束（背景/主角/避雷 + 核心设定锁定 + 金手指机制，永不裁剪）。"""
        # 用户强约束（永不裁剪，进关键区）
        if cfg_d is not None:
            if getattr(cfg_d, "background", ""):
                key_parts.append(f"故事背景（强约束）：{cfg_d.background}")
            if getattr(cfg_d, "hero", ""):
                key_parts.append(f"主角设定（强约束）：{cfg_d.hero}")
            if getattr(cfg_d, "forbidden", None):
                key_parts.append("避雷（强约束）：" + "；".join(cfg_d.forbidden))
        if getattr(world, "locked_settings", None):
            key_parts.append("【核心设定锁定·绝对不可违背】：" + "；".join(world.locked_settings))
        # 机制体系总述卡（强约束区，永不裁剪）：语义/结算/代价/规则边界，全文必须自洽兑现
        mechanism = getattr(world, "mechanism_rules", "") or ""
        if mechanism:
            key_parts.append(
                "【机制体系卡·绝对不可违背，规则与设定全文必须自洽，首章要实际用起来并兑现标题】\n" + mechanism
            )

    def _build_theme_anchors(self, world, key_parts: list[str]) -> None:
        """L1 底层锚点：主题 / 主角弧光 / 金手指五要素 / 设定禁忌（进强约束区）。"""
        # ---- 底层锚点（REQ 多Agent协作分层优化）：主题/弧光/金手指五要素/设定禁忌进强约束区 ----
        theme = getattr(world, "theme_proposition", "") or ""
        if theme:
            key_parts.append("【核心主题·全文剧情与人物选择须最终回归此命题】\n" + theme)
        arc = getattr(world, "protagonist_arc", None)
        if arc is not None and (getattr(arc, "belief", "") or getattr(arc, "flaw", "")):
            arc_parts = [f"开篇信念：{arc.belief}", f"核心缺陷：{arc.flaw}"]
            if getattr(arc, "change_nodes", None):
                arc_parts.append("关键转变节点：" + "；".join(arc.change_nodes))
            if getattr(arc, "final_insight", ""):
                arc_parts.append(f"最终认知：{arc.final_insight}")
            key_parts.append(
                "【主角弧光卡·人物关键选择必须符合当前弧光阶段，禁止工具化行为】\n" + "\n".join(arc_parts)
            )
        # 能力规则说明书：仅 ability 类型（旧书推断命中）注入；规则怪谈/契约/无机制不硬凑五要素
        gf = getattr(world, "goldfinger_rules", None)
        types = resolve_types(world)
        if gf is not None and has_type(types, "ability"):
            gf_lines = []
            if getattr(gf, "trigger", ""):
                gf_lines.append(f"触发条件：{gf.trigger}")
            if getattr(gf, "upper_limit", ""):
                gf_lines.append(f"能力上限：{gf.upper_limit}")
            if getattr(gf, "lower_limit", ""):
                gf_lines.append(f"能力下限：{gf.lower_limit}")
            if getattr(gf, "cost", ""):
                gf_lines.append(f"使用代价：{gf.cost}")
            if getattr(gf, "growth_path", ""):
                gf_lines.append(f"成长路径：{gf.growth_path}")
            if gf_lines:
                key_parts.append(
                    "【能力规则说明书·边界与代价全文必须自洽，禁止越出上限/模糊代价】\n" + "\n".join(gf_lines)
                )
        taboos = getattr(world, "setting_taboos", []) or []
        if taboos:
            key_parts.append("【设定禁忌·明确禁止出现的吃设定行为】\n" + "；".join(str(t) for t in taboos))

    def _build_ruleset_cards(self, world, key_parts: list[str]) -> None:
        """L1 规则条款卡（rule_set 类型，强约束区）：条款一经公布在其作用域内不得改写。"""
        types = resolve_types(world)
        if not has_type(types, "rule_set"):
            return
        ruleset = getattr(world, "mechanism_ruleset", None) or []
        if not ruleset:
            return
        scope_label = {"global": "全书通用", "instance": "副本专属",
                       "character": "角色专属", "ability": "能力专属"}
        lines: list[str] = []
        for rule in ruleset:
            if getattr(rule, "status", "active") != "active":
                continue
            scope = getattr(rule, "scope", "global") or "global"
            scope_kind = scope.split(":", 1)[0]
            scope_text = scope_label.get(scope_kind, scope)
            if ":" in scope:
                scope_text += f"（{scope.split(":", 1)[1]}）"
            bits = [f"条款[{getattr(rule, 'rule_id', '')}]：{getattr(rule, 'statement', '')}",
                    f"适用范围：{scope_text}",
                    f"触发条件：{getattr(rule, 'condition', '')}",
                    f"违反后果：{getattr(rule, 'consequence', '')}"]
            policy = getattr(rule, "change_policy", "fixed")
            if policy == "fixed":
                bits.append("条款性质：一经公布不可改写、不得前后矛盾")
            elif policy == "discoverable":
                bits.append("条款性质：随剧情逐步揭示，已公布部分不可改写")
            elif policy == "negotiable":
                bits.append("条款性质：可在剧情内谈判变更，变更须走声明渠道")
            elif policy == "layered":
                bits.append("条款性质：分层揭示，已公布层不可改写")
            lines.append("｜".join(b for b in bits if b.split("：", 1)[-1].strip()))
        if lines:
            key_parts.append(
                "【规则条款卡·在其适用范围内绝对不可违背；新规则只能通过剧情声明渠道出现，"
                "禁止正文私增规则、跨副本串场】\n" + "\n".join(lines)
            )

    def _build_mechanism_baseline_cards(self, world, key_parts: list[str]) -> None:
        """L1 机制初始值 + 时钟规则 + 语言风格禁忌（强约束区，写手不得自造数字/违背时间）。"""
        # 机制初始值（仅 numeric_panel 类型）：正文里数字的唯一标准答案，写手不得自行发明数字
        mb = getattr(world, "mechanism_baseline", None)
        types = resolve_types(world)
        if mb is not None and has_type(types, "numeric_panel") and any([
                getattr(mb, f, "") for f in
                ("scar_start", "scar_per_death", "scar_current",
                 "scar_end", "panel_format", "reset_rule")]):
            mb_lines = []
            if getattr(mb, "scar_start", ""): mb_lines.append(f"标记起点：{mb.scar_start}")
            if getattr(mb, "scar_per_death", ""): mb_lines.append(f"每次触发增量：{mb.scar_per_death}")
            if getattr(mb, "scar_current", ""): mb_lines.append(f"标记当前：{mb.scar_current}")
            if getattr(mb, "scar_end", ""): mb_lines.append(f"终局：{mb.scar_end}")
            if getattr(mb, "panel_format", ""): mb_lines.append(f"面板唯一写法：{mb.panel_format}")
            if getattr(mb, "reset_rule", ""): mb_lines.append(f"重置规则：{mb.reset_rule}")
            key_parts.append(
                "【机制初始值·正文数字唯一标准答案，禁止自行发明数字/长度/比例】\n" + "\n".join(mb_lines)
            )
        # 时钟规则（REQ-事前设定卡）：每章都要遵守，防时间不推进、地下暮色
        ck = getattr(world, "clock", None)
        if ck is not None and any([getattr(ck, f, "") for f in ("day_cycle", "travel", "light_rule")]):
            ck_lines = []
            if getattr(ck, "day_cycle", ""): ck_lines.append(f"日周期：{ck.day_cycle}")
            if getattr(ck, "travel", ""): ck_lines.append(f"路程：{ck.travel}")
            if getattr(ck, "light_rule", ""): ck_lines.append(f"光线规则：{ck.light_rule}")
            key_parts.append(
                "【时钟规则·本章时间流逝、天色、路程必须一致，不得时间不推进却重复写'还有N小时'】\n" + "\n".join(ck_lines)
            )
        # 语言风格禁忌（REQ-事前设定卡）：时代/术语禁用词
        style_taboos = getattr(world, "style_taboos", []) or []
        if style_taboos:
            key_parts.append("【语言风格禁忌·以下词语/表达绝对禁止出现】\n" + "；".join(str(t) for t in style_taboos))

    def _build_progression_cards(self, world, key_parts: list[str]) -> None:
        """L1 等级/境界体系卡（防战力崩坏）：境界阶梯与主角当前境界进强约束区。"""
        # P1-3 等级/境界体系卡（防战力崩坏）：境界阶梯与主角当前境界进强约束区
        ps = getattr(world, "power_system", None)
        if ps is not None and (getattr(ps, "levels", None) or getattr(ps, "protagonist_level", "")):
            ps_lines = []
            for lv in (getattr(ps, "levels", None) or []):
                _lv_name = getattr(lv, "name", "") or ""
                if _lv_name:
                    ps_lines.append(
                        f"{_lv_name}（{getattr(lv, 'range', '') or ''}）｜突破条件：{getattr(lv, 'requirement', '') or ''}｜能力上限：{getattr(lv, 'capability', '') or ''}"
                    )
            if getattr(ps, "protagonist_level", ""):
                ps_lines.append(f"主角当前境界：{ps.protagonist_level}")
            if getattr(ps, "progression_rule", ""):
                ps_lines.append(f"突破规则：{ps.progression_rule}")
            if ps_lines:
                key_parts.append(
                    "【等级/境界体系·战力提升必须在此阶梯内递增，禁止凭空跳阶/战力崩坏】\n"
                    + "\n".join(ps_lines)
                )

    def _build_ending_cards(self, world, cfg_d, up_to_chapter: int, key_parts: list[str]) -> None:
        """L1 结局卡（末 5 章才注入）+ 对白风格指南（强约束区）。"""
        # P1-2 结局卡（末 N 章才注入，P2 修正）：终局方向只在末 5 章注入，避免写第 1 章时
        # 模型就预知结局（与 world_reveals「未揭示设定进强约束区禁剧透」逻辑一致：
        # 终局属未揭示的高阶设定，不应全程暴露）；num_chapters 未知时保守始终注入
        ec = getattr(world, "ending_card", None)
        _nc = int(getattr(cfg_d, "num_chapters", 0) or 0)
        _inject_end = (_nc <= 0) or (up_to_chapter >= _nc - 5)
        if ec is not None and _inject_end and (getattr(ec, "end_state", "") or getattr(ec, "protagonist_choice", "")):
            ec_lines = []
            if getattr(ec, "end_state", ""):
                ec_lines.append(f"终局状态：{ec.end_state}")
            if getattr(ec, "theme_landing", ""):
                ec_lines.append(f"主题落点：{ec.theme_landing}")
            if getattr(ec, "protagonist_choice", ""):
                ec_lines.append(f"主角最终选择：{ec.protagonist_choice}")
            if getattr(ec, "character_fates", None):
                ec_lines.append("人物归宿：" + "；".join(str(x) for x in ec.character_fates))
            if getattr(ec, "tone", ""):
                ec_lines.append(f"结局基调：{ec.tone}")
            if ec_lines:
                key_parts.append(
                    "【结局卡·全书终局方向，末几章必须按此收束，禁止自行发明与终局矛盾的方向】\n"
                    + "\n".join(ec_lines)
                )
        # P2 对白 Agent：对话风格指南（人物口吻区分+节奏，进强约束区）
        dlg = getattr(world, "dialogue_guideline", "") or ""
        if dlg:
            key_parts.append("【对话风格·人物口吻区分与对话节奏，正文对话必须遵守】\n" + dlg)
