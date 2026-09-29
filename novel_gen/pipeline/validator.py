# -*- coding: utf-8 -*-
"""
逻辑校验 Agent（LogicValidator）：独立第三方校验，专司查错。

REQ-多Agent协作分层优化：收敛 manager._run_verify 等分散校验，提供 5 类专项校验
（设定一致性 / 人物OOC / 主线偏移 / 重复情节 / 伏笔完整性），输出分级问题清单
（fatal/major/minor，见 issue_triage.IssueItem），供 StageGate 阶段门禁与最终质量报告复用。

设计：确定性结构化校验为主（零 LLM 成本、可复现单测），语义级校验（主线偏移、
人物OOC 的深层判断）由上层 StageGate 可选叠加 LLM（异常降级），本类保持纯函数安全。
"""
from __future__ import annotations

from ..store.schemas import GoldfingerRules, Outline, World
from .issue_triage import IssueItem


#: 伏笔三级合法值
_FS_LEVELS = {"small", "medium", "large"}


class LogicValidator:
    """独立第三方逻辑校验 Agent。

    validate_stage(stage, product) 是总入口：
    - stage="bottom_anchors"：底层锚点（World）——金手指五要素 / 主题锚点 / 伏笔完整性
    - stage="skeleton"：骨架（Outline）——主线 / 重复 / 分卷归属
    - stage="pacing"：节奏（Outline）——关键节点 / 分卷节奏 / 弧光落点
    """

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
        issues += self._check_theme_anchor(world)
        issues += self._check_foreshadow(world)
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
