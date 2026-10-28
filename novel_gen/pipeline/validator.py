# -*- coding: utf-8 -*-
"""
逻辑校验 Agent（LogicValidator）：独立第三方校验，专司查错。

REQ-多Agent协作分层优化：收敛 manager._run_verify 等分散校验，提供 5 类专项校验
（设定一致性 / 人物OOC / 主线偏移 / 重复情节 / 伏笔完整性），输出分级问题清单
（fatal/major/minor，见 issue_triage.IssueItem），供 StageGate 阶段门禁与最终质量报告复用。

设计：确定性结构化校验为主（零 LLM 成本、可复现单测）；仅「机制卡三要素」属语义
判断，由 REQ-词表换LLM 批次一改为可选 LLM 语义判定（llm+llm_semantic 双开关），
失败/非结构化时回退原信号词表兜底，其余校验保持纯函数安全。
"""
from __future__ import annotations

import logging

from ..llm.json_parse import parse_json_obj
from ..store.schemas import GoldfingerRules, Outline, World
from .issue_triage import IssueItem
from .templates import mechanism_check_prompt

logger = logging.getLogger(__name__)


#: 伏笔三级合法值
_FS_LEVELS = {"small", "medium", "large"}

#: 机制卡三要素固定文案（LLM 路径与词表兜底产出的 missing 必须完全一致）
_MISSING_SEMANTIC = "机制语义（每个数字/状态的确切含义）"
_MISSING_SETTLE = "结算规则（何时增减/重置/与历史衔接）"
_MISSING_END = "代价与终局（代价递增/上限/触发）"
_MECH_FIXED_MISSING = (_MISSING_SEMANTIC, _MISSING_SETTLE, _MISSING_END)


def _llm_mechanism_missing(mechanism: str, llm) -> list[str] | None:
    """LLM 语义判定机制卡三要素缺失（REQ-词表换LLM 批次一，开书时每本 1 次）。

    返回：
    - None：LLM 调用失败 / 返回非结构化（dict 空、无 missing、missing 非 list）
      → 调用方回退词表兜底；
    - list[str]：LLM 判定结果（可为 [] = 三要素齐全，不回退）。
    规范化：逐条 strip，只保留三个固定文案之一（非固定文案丢弃），去重。
    """
    if not mechanism:
        return []
    try:
        system, user = mechanism_check_prompt(mechanism)
        raw = llm.chat(system, user, temperature=0.2, max_tokens=300, json_mode=True)
    except Exception:
        return None
    data = parse_json_obj(raw)
    missing = data.get("missing") if isinstance(data, dict) else None
    if not isinstance(missing, list):
        return None
    kept: list[str] = []
    for item in missing:
        text = str(item).strip()
        if text in _MECH_FIXED_MISSING and text not in kept:
            kept.append(text)
    return kept


class LogicValidator:
    """独立第三方逻辑校验 Agent。

    validate_stage(stage, product) 是总入口：
    - stage="bottom_anchors"：底层锚点（World）——金手指五要素 / 主题锚点 / 伏笔完整性
    - stage="skeleton"：骨架（Outline）——主线 / 重复 / 分卷归属
    - stage="pacing"：节奏（Outline）——关键节点 / 分卷节奏 / 弧光落点
    """

    def __init__(self, llm=None, llm_semantic: bool = True):
        # REQ-词表换LLM 批次一：仅机制卡三要素可选 LLM 语义判定；
        # llm 为 None 或 llm_semantic=False 时行为与改造前完全一致（纯词表）。
        self.llm = llm
        self.llm_semantic = llm_semantic

    def validate_stage(self, stage: str, product) -> list[IssueItem]:
        try:
            if stage == "bottom_anchors":
                return self._validate_bottom_anchors(product)
            if stage == "skeleton":
                return self._validate_skeleton(product)
            if stage == "pacing":
                return self._validate_pacing(product)
        except Exception:
            # 校验自身异常不阻塞流程：返回空（视为未发现问题）
            return []
        return []

    # ---------- 阶段一：底层锚点（World） ----------
    def _validate_bottom_anchors(self, world: World) -> list[IssueItem]:
        issues: list[IssueItem] = []
        issues += self._check_goldfinger(world)
        issues += self._check_mechanism(world)
        issues += self._check_theme_anchor(world)
        issues += self._check_foreshadow(world)
        issues += self._check_design_cards(world)
        return issues

    #: 机制卡三要素关键信号词（REQ-章内自洽与机制账本）
    #: 语义：数字/状态的确切含义；结算：增减/重置的动作规则；代价：上限/终局。
    #: 刻意排除宽泛词（如"数字""今日"），避免"无数字""今日剩余复活次数"等
    #: 语义描述被误判为已覆盖对应要素。只放通用机制词，单书词（如"蚀痕/可死"）
    #: 由设定账本声明，不预置。
    _MECH_SEMANTIC = ("语义", "含义", "状态", "次数", "面板", "进度")
    _MECH_SETTLE = ("结算", "增减", "递减", "递增", "重置", "每死", "减一", "恢复")
    _MECH_END = ("代价", "上限", "终局", "触发", "成长", "封顶", "用尽", "永久")

    def _check_mechanism(self, world: World) -> list[IssueItem]:
        """机制卡完整性（REQ-章内自洽与机制账本）：非空机制卡必须覆盖
        「机制语义 / 结算规则 / 代价与终局」三要素，缺失视为致命。

        背景：机制数字前后矛盾、状态与余量口径冲突、「今日/跨天」类时间口径冲突，
        均源于机制卡只写了个名目、没有无歧义的结算规则。
        """
        issues: list[IssueItem] = []
        mechanism = (getattr(world, "mechanism_rules", "") or "").strip()
        if not mechanism:
            return issues
        # REQ-词表换LLM 批次一：开关开启且 llm 可用时走 LLM 语义判定；
        # 返回 None（失败/非结构化）留痕后回退词表兜底，issue 构造逻辑不变。
        missing: list[str] | None = None
        if self.llm is not None and self.llm_semantic:
            missing = _llm_mechanism_missing(mechanism, self.llm)
            if missing is None:
                logger.warning("[validator] 机制卡 LLM 语义判定失败/非结构化，回退词表兜底")
        if missing is None:
            missing = []
            if not any(k in mechanism for k in self._MECH_SEMANTIC):
                missing.append(_MISSING_SEMANTIC)
            if not any(k in mechanism for k in self._MECH_SETTLE):
                missing.append(_MISSING_SETTLE)
            if not any(k in mechanism for k in self._MECH_END):
                missing.append(_MISSING_END)
        if missing:
            issues.append(IssueItem(
                severity="fatal", scope="mechanism",
                desc="金手指机制卡不完整，缺失：" + "、".join(missing),
                suggestion="在主题/世界观 Agent 阶段按 world_prompt 的 mechanism_rules 八要素补全"
                          "（机制语义/结算规则/代价终局/数字账本/时间词语义/多数字关系/历史衔接/正文要求）",
            ))
        return issues

    def _check_goldfinger(self, world: World) -> list[IssueItem]:
        """金手指五要素齐全且与机制卡自洽（红线：不得模糊）。"""
        issues: list[IssueItem] = []
        gf: GoldfingerRules | None = getattr(world, "goldfinger_rules", None)
        mechanism = getattr(world, "mechanism_rules", "") or ""
        has_mechanism = bool(mechanism.strip())
        # 机制卡存在但五要素缺失 → 致命（能力边界必须明确）
        if has_mechanism and gf is None:
            issues.append(IssueItem(
                severity="fatal", scope="goldfinger",
                desc="有金手指机制卡（mechanism_rules）但缺金手指规则说明书（goldfinger_rules）",
                suggestion="在主题/世界观 Agent 阶段补齐 goldfinger_rules 五要素"))
        if gf is not None:
            missing = [k for k, v in {
                "trigger": gf.trigger, "upper_limit": gf.upper_limit,
                "lower_limit": gf.lower_limit, "cost": gf.cost, "growth_path": gf.growth_path,
            }.items() if not (v or "").strip()]
            if missing:
                issues.append(IssueItem(
                    severity="fatal", scope="goldfinger",
                    desc=f"金手指规则说明书五要素不完整，缺失：{'、'.join(missing)}",
                    suggestion="补齐全部五要素（触发/上限/下限/代价/成长路径）"))
        return issues

    def _check_theme_anchor(self, world: World) -> list[IssueItem]:
        """主题命题 / 三层冲突矩阵 / 主角弧光 / 配角动机 完整性。"""
        issues: list[IssueItem] = []
        if not (getattr(world, "theme_proposition", "") or "").strip():
            issues.append(IssueItem("major", "theme", "缺核心主题命题（theme_proposition）",
                                    "补充一句话内核，作为全书剧情回归锚点"))
        cm = getattr(world, "conflict_matrix", None) or []
        if len(cm) < 3:
            issues.append(IssueItem("major", "theme", f"三层冲突矩阵不足（当前 {len(cm)} 层，应有 personal/interpersonal/world 三层）",
                                    "补全三层冲突矩阵"))
        arc = getattr(world, "protagonist_arc", None)
        if arc is None or not (getattr(arc, "belief", "") or getattr(arc, "flaw", "")):
            issues.append(IssueItem("major", "theme", "缺主角完整弧光卡（protagonist_arc）",
                                    "补充开篇信念/核心缺陷/关键转变节点/最终认知"))
        elif len(getattr(arc, "change_nodes", []) or []) < 2:
            issues.append(IssueItem("minor", "theme", "主角弧光卡关键转变节点不足 2 个",
                                    "至少列出 2 个关键转变节点"))
        motives = getattr(world, "character_motives", None) or []
        if not motives:
            issues.append(IssueItem("minor", "theme", "缺核心配角动机卡（character_motives）",
                                    "为主要配角与反派补充 goal/behavior_logic/link_to_protagonist"))
        return issues

    def _check_foreshadow(self, world: World) -> list[IssueItem]:
        """伏笔完整性：只埋不收视为设计缺陷（红线）。"""
        issues: list[IssueItem] = []
        fs_list = getattr(world, "foreshadowing", None) or []
        for fs in fs_list:
            name = getattr(fs, "name", "") or getattr(fs, "content", "") or "（未命名伏笔）"
            reveal = getattr(fs, "reveal_ch", None)
            buried = getattr(fs, "buried_ch", 0) or 0
            if reveal is None:
                issues.append(IssueItem(
                    severity="fatal", scope="foreshadow",
                    desc=f"伏笔『{name}』只埋不收：缺回收章号（reveal_ch）",
                    suggestion="在节奏/伏笔 Agent 阶段补 reveal_ch 或删除该伏笔"))
            elif buried and reveal < buried:
                issues.append(IssueItem(
                    severity="fatal", scope="foreshadow",
                    desc=f"伏笔『{name}』回收章号({reveal})早于埋点章号({buried})",
                    suggestion="修正 reveal_ch 必须 >= buried_ch"))
            lvl = getattr(fs, "level", "medium") or "medium"
            if lvl not in _FS_LEVELS:
                issues.append(IssueItem("minor", "foreshadow",
                                        f"伏笔『{name}』level 非法：{lvl}",
                                        "level 应为 small/medium/large"))
        return issues

    def _check_design_cards(self, world: World) -> list[IssueItem]:
        """事前设计卡完整性（REQ-事前设定卡）：把怪物/地点/机制数字落成结构化卡，
        防止写手在正文自行发明形态、空间、数字。缺失降为 major（不阻塞旧项目）。"""
        issues: list[IssueItem] = []
        mechanism = (getattr(world, "mechanism_rules", "") or "").strip()
        # 机制卡有可视化数字，但缺结构化初始值 → major
        if mechanism and any(k in mechanism for k in ("次数", "进度", "面板", "状态")):
            mb = getattr(world, "mechanism_baseline", None)
            has_baseline = mb is not None and any(
                (getattr(mb, f, "") or "").strip() for f in
                ("scar_current", "panel_format", "reset_rule"))
            if not has_baseline:
                issues.append(IssueItem(
                    "major", "design_cards",
                    "机制卡含可视化数字，但缺 mechanism_baseline（数字标准答案卡）",
                    "按 world_prompt 补 mechanism_baseline：机制数字唯一写法/累积状态当前值/重置规则"))
        # 世界观散文提到怪物类生物，但没生物图鉴 → major
        wv = (getattr(world, "world_view", "") or "")
        enemies = getattr(world, "enemies", None) or []
        if wv and not enemies and any(k in wv for k in ("灰卒", "怪物", "妖兽", "魔物", "怪物", "敌")):
            issues.append(IssueItem(
                "major", "design_cards",
                "世界观提到敌对生物，但缺 enemies 生物图鉴",
                "按 world_prompt 补 enemies：形态/感知方式/行为/弱点写死"))
        return issues

    # ---------- 阶段二：骨架（Outline） ----------
    def _validate_skeleton(self, outline: Outline) -> list[IssueItem]:
        issues: list[IssueItem] = []
        issues += self._check_mainline(outline)
        issues += self._check_duplicate(outline)
        issues += self._check_volume_link(outline)
        return issues

    def _check_mainline(self, outline: Outline) -> list[IssueItem]:
        """主线：总纲冲突/主线非空；分卷齐备；关键节点覆盖激励与高潮。"""
        issues: list[IssueItem] = []
        if not (getattr(outline, "core_conflict", "") or "").strip():
            issues.append(IssueItem("fatal", "mainline", "缺全书核心冲突（core_conflict）",
                                    "补充一句话核心冲突"))
        if not (getattr(outline, "main_line", "") or "").strip():
            issues.append(IssueItem("fatal", "mainline", "缺全书主线脉络（main_line）",
                                    "补充 3-5 句主线"))
        vols = getattr(outline, "volumes", None) or []
        if not vols:
            issues.append(IssueItem("major", "mainline", "未规划分卷（volumes）",
                                    "按剧情阶段划分 3-5 卷"))
        else:
            for v in vols:
                if not (getattr(v, "main_conflict", "") or "").strip():
                    issues.append(IssueItem("major", "mainline",
                                            f"第{v.no}卷《{v.name}》缺主要矛盾（main_conflict）",
                                            "补充本卷主要矛盾"))
        k_nodes = getattr(outline, "key_nodes", None) or []
        kinds = {getattr(k, "kind", "") for k in k_nodes}
        if k_nodes and ("激励事件" not in kinds or "终极高潮" not in kinds):
            issues.append(IssueItem("minor", "mainline",
                                    "关键节点时间线未覆盖「激励事件」与「终极高潮」",
                                    "补充 key_nodes 的激励事件与终极高潮节点"))
        return issues

    def _check_duplicate(self, outline: Outline) -> list[IssueItem]:
        """重复情节：章节标题重复 / 高度同质 goal 超阈值（卷内重复率 >5% 视为问题）。"""
        issues: list[IssueItem] = []
        chapters = getattr(outline, "chapters", None) or []
        n = len(chapters)
        if not n:
            return issues
        # 标题重复
        seen_title: dict[str, int] = {}
        for c in chapters:
            t = (getattr(c, "title", "") or "").strip()
            if t:
                seen_title[t] = seen_title.get(t, 0) + 1
        dup_titles = [t for t, cnt in seen_title.items() if cnt > 1]
        if dup_titles:
            issues.append(IssueItem("major", "duplicate",
                                    f"章节标题重复：{'、'.join(dup_titles[:5])}",
                                    "章节标题必须全书唯一"))
        # goal 同质化：归一化后按词元重复度，同质对超过 5% 章节数视为问题
        goals = [self._norm_goal(getattr(c, "goal", "") or "") for c in chapters]
        same_goal_pairs = 0
        for i in range(n):
            for j in range(i + 1, n):
                if goals[i] and goals[i] == goals[j]:
                    same_goal_pairs += 1
        if same_goal_pairs > max(1, int(n * 0.05)):
            issues.append(IssueItem("major", "duplicate",
                                    f"章节 goal 同质化：{same_goal_pairs} 对相同，超过阈值",
                                    "分散各章目标，避免重复冲突桥段"))
        return issues

    @staticmethod
    def _norm_goal(goal: str) -> str:
        """goal 归一化：去空白与标点，做重复判定（确定性、可复现）。"""
        return "".join(ch for ch in goal if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    def _check_volume_link(self, outline: Outline) -> list[IssueItem]:
        """分卷归属：章节无 volume_no 归属比例超过 5% 视为结构缺失。"""
        issues: list[IssueItem] = []
        chapters = getattr(outline, "chapters", None) or []
        if not chapters:
            return issues
        unassigned = sum(1 for c in chapters if not getattr(c, "volume_no", 0))
        if unassigned > max(1, int(len(chapters) * 0.05)):
            issues.append(IssueItem(
                "major", "volume",
                f"{unassigned}/{len(chapters)} 章未归属分卷（volume_no=0）",
                "按分卷 start_ch~end_ch 区间回填 volume_no"))
        return issues

    # ---------- 阶段三：节奏 / 伏笔细化（Outline） ----------
    def _validate_pacing(self, outline: Outline) -> list[IssueItem]:
        issues: list[IssueItem] = []
        # 分卷节奏梯次
        vp = getattr(outline, "volume_pacing", None) or []
        if not vp:
            issues.append(IssueItem("minor", "pacing", "缺分卷节奏梯次（volume_pacing）",
                                    "为每卷补充爽点密度/高潮章/过渡区间"))
        # 关键节点时序递增（确定性）
        k_nodes = getattr(outline, "key_nodes", None) or []
        prev = 0
        for k in k_nodes:
            cn = int(getattr(k, "chapter_no", 0) or 0)
            if cn and cn < prev:
                issues.append(IssueItem("major", "pacing",
                                        f"关键节点『{getattr(k,'kind','')}』章号({cn})早于前一节点({prev})",
                                        "关键节点章号须全书递增"))
            if cn:
                prev = cn
        # 每卷 growth 落点
        vols = getattr(outline, "volumes", None) or []
        for v in vols:
            if not (getattr(v, "character_growth", "") or "").strip():
                issues.append(IssueItem("minor", "pacing",
                                        f"第{v.no}卷《{v.name}》缺人物成长（character_growth）",
                                        "补充本卷人物弧光成长落点"))
        return issues


def build_validator() -> LogicValidator:
    """工厂：创建独立校验 Agent。"""
    return LogicValidator()


def _reveal_keywords(r) -> list[str]:
    """reveal 的真相锚定词（strip/去空）。"""
    return [str(k).strip() for k in (getattr(r, "keywords", None) or []) if str(k).strip()]


def check_reveal_execution(world, up_to_chapter: int, status: dict) -> list[IssueItem]:
    """揭示执行跟踪校验（REQ-揭示执行跟踪）：对照 world_reveals 计划与 reveal_status
    实际命中，判定两类执行偏差。

    - **提前剧透**：正文在 from_ch 前命中真相锚定词 → fatal（红线：禁剧透）；
    - **到点未揭示**：from_ch ≤ 已写章但正文从未命中 → major（建议重写/补揭示）；
    - **旧数据兼容**：无 keywords 的 reveal 不参与确定性检测，不误报。

    纯函数、零 LLM，供 manager 全书完成后汇总报告；不阻塞生成与结算。
    """
    issues: list[IssueItem] = []
    reveals = getattr(world, "world_reveals", None) or []
    if not reveals or not status:
        return issues
    for i, r in enumerate(reveals):
        if not _reveal_keywords(r):
            continue  # 旧数据无锚定词：跳过检测
        key = str(i)
        item = status.get(key) or {}
        from_ch = int(item.get("from_ch") if item.get("from_ch") is not None
                      else getattr(r, "from_ch", 1) or 1)
        hits = [int(x) for x in (item.get("hit_ch") or []) if str(x).strip()]
        name = ((getattr(r, "content", "") or "")[:30]) or f"揭示#{i + 1}"
        if hits and min(hits) < from_ch:
            issues.append(IssueItem(
                "fatal", "reveal",
                f"世界观真相『{name}』提前剧透：计划 from_ch={from_ch}，正文第 {min(hits)} 章已出现真相锚定词",
                "重写提前泄露的章节，或调整揭示计划 from_ch",
                chapter_no=min(hits)))
        if up_to_chapter >= from_ch and not hits:
            issues.append(IssueItem(
                "major", "reveal",
                f"世界观真相『{name}』到点未揭示：from_ch={from_ch} 已过（已写至第 {up_to_chapter} 章），正文从未提及",
                "重写该章或在后续章节补揭示",
                chapter_no=from_ch))
    return issues
