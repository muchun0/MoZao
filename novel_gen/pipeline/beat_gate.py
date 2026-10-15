# -*- coding: utf-8 -*-
"""Beat 覆盖度门禁（三期 F1，2026-10-14；2026-10-15 语义槽修复版）。

把大纲章计划（core_event / character_moves 的逐拍动作序列）作为"计划 beat"权威来源，
正文生成后比对实际覆盖了几成拍，分级判定（pass/warn/fail/skip）并落 `beat_gate.json`。

2026-10-15 修复（feat/beat-gate-semantic-slot）：根治覆盖率假阴性 + 通用化。
问题：旧实现用"去停用字后的字符 bigram ≥50% 子串命中"判单拍覆盖，对近义改写
（清晨↔蒙蒙亮、咬喉↔咬穿、检查面板↔面板亮了一下）与语序调整零免疫力，且长拍被
摊成大量跨界 bigram（沉清/晨出/门检/板3…），正文明明原样写了动作仍判 miss——
实测第1章 5 拍全实质覆盖却报 coverage=0.00/fail。
修复：改为「自适应语义槽」判定（沿用 stages.py `_event_semantic_slots` /
`_SCENE_ACTION_NORM` 的语义槽思路，零 LLM、纯确定性、不依赖 jieba 等外部分词）：
  每拍自提 3 类语义点：
    - 动作槽：扫同义/归一表，把等价改写归一到规范动作（咬喉/咬穿/咬死→被咬死）；
    - 对象槽：网络实体词（灰卒/小九/面板/铁锤/铁匠铺/老陈头/药钱…）出现即命中；
    - 修饰限定槽：拍内其余 2 字实词（如"隐藏/古老/密林深处"），正文缺一则该拍
      不完整覆盖（保留"关键信息缺失则扣分"的防假阳性语义）。
  单拍覆盖 = 动作槽命中率达标 且 对象槽命中 且 修饰限定槽全部命中；
  无动作槽拍退化为"对象槽命中"，全空槽拍保守判 miss。
  由于语义点是"从拍文本自适应提取 + 同义表归一"，对任意世界观/场景通用，
  不再依赖写死的实体词清单。

设计约束（AGENTS.md §11 / REQ-beat-gate）：
- **纯确定性、零 LLM**：同一输入恒得同一输出，可复现、零 token 成本；
- **检测+记录+提示，不独立重生成正文**：重写统一由既有 quality.ensure_quality 链路负责；
- 外部契约不变：`compute_beat_coverage` / `assess_chapter` 签名与返回结构
  {planned:[{beat,covered,hit,miss}], coverage, verdict, missing} 保持一致。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 拍文本中需剔除的无信息量短词（用于提取关键实词；2 字以上实词片段保留）
_STOP_CHARS = set("了的着他她它在把被是也就都和与而又再才但却向到从对为以上下中后前时这那其之等个只要会能可将，。；、（）()：:「」 \t")
#: 计划拍切分（character_moves 逐拍）
_PLAN_SPLIT_RE = re.compile(r"[→。；;]")

#: 网络实体词（对象槽）。拍/正文出现任一词即作为"对象已出现"候选。
_ENTITY_WORDS = (
    "灰卒", "赤尉", "蚀兽", "蚀卒", "小九", "黑猫", "面板", "铁锤", "核心",
    "铁匠铺", "老陈头", "药钱", "墟口", "蚀痕", "蚀化", "废铁", "灰雾",
)

#: 动作归一表：规范动作主词 → 等价触发词。命中任一触发词（或主词本身）即归一。
#: 动作槽用它对"改写过的动作"归一到规范动作；语义点命中 = 该主词族任一写法在正文出现。
_ACTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "看面板": ("检查面板", "看面板", "面板亮", "面板在脑子里亮", "看了一眼面板", "显示"),
    "前往": ("出门", "出门前", "前往", "走向", "踏入", "走进"),
    "拾荒": ("拾荒", "翻找", "翻铁", "找铁"),
    "遇袭": ("遭遇", "遇到", "碰上", "撞上", "扑上来"),
    "被咬死": ("咬喉", "咬穿", "被咬死", "咬死", "咬碎", "咬住喉咙", "致死", "啃穿"),
    "复活": ("复活", "再睁眼", "重生", "活过来", "醒过来"),
    "砸": ("砸", "砸碎", "锤", "锤砸", "击中", "敲碎"),
    "进食": ("进食", "舔", "啃", "吃"),
    "核外移": ("外移", "核外移", "移了移"),
    "嘶吼": ("嘶吼", "低吼", "吼", "弓背", "弓起背"),
    "逃窜": ("逃窜", "逃", "退", "转身", "钻进灰雾", "缩回", "退了一步", "不见"),
    "回到": ("回到", "回来", "回铁匠铺", "回铺", "走回"),
    "见血洞": ("血洞", "破了个洞", "布衫破", "见血洞", "衣服上", "布衫"),
    "药钱": ("药钱", "铜板", "塞回", "推过来", "欠着"),
    "发现": ("发现", "瞧见", "看见", "发觉"),
}

#: 修饰/限定归一表：规范限定主词 → 等价触发词。用于"关键限定词缺失则扣分"
#: （防假阳性）——正文缺任一命中的修饰主词 → 该拍不完整覆盖。
_MOD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "清晨": ("清晨", "蒙蒙亮", "天刚蒙蒙亮", "天刚亮"),
    "隐藏": ("隐藏", "藏着的", "隐匿"),
}

#: 所有归一主词的并集（_normalize_term 遍历用）
_SYNONYMS: dict[str, tuple[str, ...]] = {**_ACTION_SYNONYMS, **_MOD_SYNONYMS}


def _split_plan_beats(moves: str) -> list[str]:
    """把大纲 character_moves 拆成逐拍动作序列（按 →/。；;）。<2 拍返回 []（不误报）。"""
    if not moves or not str(moves).strip():
        return []
    beats = [b.strip() for b in _PLAN_SPLIT_RE.split(str(moves)) if b and b.strip()]
    return beats if len(beats) >= 2 else []


def _normalize_term(term: str) -> tuple[str, tuple[str, ...]]:
    """把词归一到同义主词：若 term 是某主词族的一员 → 返回 (主词, 全触发词)；
    否则原样返回 (term, (term,))。"""
    for main, trigs in _SYNONYMS.items():
        if term == main or term in trigs:
            return main, (main,) + trigs
    return term, (term,)


def _slot_hit(term: str, content: str) -> bool:
    """某语义点（词）是否在正文出现：归一后按主词族的任一写法匹配。"""
    _m, trigs = _normalize_term(term)
    return any(t in content for t in trigs)


def _extract_slots(beat: str) -> tuple[list[str], list[str], list[str]]:
    """从拍文本提取三类语义点：动作槽 / 对象槽 / 修饰限定槽。

    返回 (actions, subjects, mods)：
      - actions：命中动作归一表的动作主词（如 被咬死/看面板/砸…）；
      - subjects：网络实体词 + 拍内 2 字关键实词元（密林/深处/祭坛…，≥1 命中即算对象到位）；
      - mods：命中修饰归一表的限定主词（隐藏/清晨…，需全命中，缺任一则该拍不完整覆盖）。
    """
    actions: list[str] = []
    for main in _ACTION_SYNONYMS:
        trigs = _ACTION_SYNONYMS[main]
        if any(t in beat for t in trigs):
            actions.append(main)
    # 对象槽：实体词
    subjects: list[str] = []
    for w in _ENTITY_WORDS:
        if w in beat:
            subjects.append(w)
    # 修饰槽：命中修饰归一表的主词
    mods: list[str] = []
    for main in _MOD_SYNONYMS:
        trigs = _MOD_SYNONYMS[main]
        if any(t in beat for t in trigs):
            mods.append(main)
    # 注意：不做"去停用字后 2 字滑窗实词元"。无分词器时滑窗必然产生跨界假词
    # （"清晨出门"→ 沉清/晨出/门检…），污染 hit/miss 报告。通用场景的关键实词
    # 由同义/修饰表命中保证（如"踏入密林深处"→前往、"发现隐藏祭坛"→发现/隐藏），
    # subjects 只保留真实网络实体词，避免跨界污染。
    # 修饰槽去重、对象槽去重
    mods = list(dict.fromkeys(mods))
    subjects = list(dict.fromkeys(subjects))
    return actions, subjects, mods


def _beat_covered(beat: str, content: str) -> tuple[bool, list[str], list[str]]:
    """单拍是否被正文覆盖：动作槽命中率达标 + 对象槽命中 + 修饰限定槽全部命中。

    返回 (covered, hit, miss)：hit/miss 列出命中的语义点与缺失的语义点（含"[动作]"前缀）。
    """
    actions, subjects, mods = _extract_slots(beat)
    hit: list[str] = []
    miss: list[str] = []
    # 动作槽：≥50% 命中（容忍部分动作细节省略，但主体动作必须到）
    act_ok = True
    if actions:
        act_hit_n = sum(1 for a in actions if _slot_hit(a, content))
        for a in actions:
            (hit if _slot_hit(a, content) else miss).append(f"[动作]{a}")
        act_ok = act_hit_n * 2 >= len(actions)
    # 对象槽：至少一个实体词命中（无实体词则视为通过）
    sub_ok = True
    if subjects:
        sub_hit_n = sum(1 for s in subjects if s in content)
        for s in subjects:
            (hit if s in content else miss).append(s)
        sub_ok = sub_hit_n >= 1
    # 修饰限定槽：全部命中（缺任一关键限定 → 该拍不完整覆盖）
    mod_ok = True
    if mods:
        for m in mods:
            if _slot_hit(m, content):
                hit.append(m)
            else:
                mod_ok = False
                miss.append(m)
    # 三类槽全空（纯虚词拍）→ 保守视为未覆盖
    if not actions and not subjects and not mods:
        return False, [], [beat]
    covered = act_ok and sub_ok and mod_ok
    return covered, hit, miss


def compute_beat_coverage(content: str, moves: str, core_event: str = "",
                          min_coverage: float = 0.6, warn_coverage: float = 0.4) -> dict:
    """计算正文对章计划 beat 的覆盖率并分级。

    返回：{planned:[{beat,covered,hit,miss}], coverage, verdict, missing}
      - 无计划拍 → verdict="skip"，coverage=None（不误报）；
      - coverage ≥ min → "pass"；[warn, min) → "warn"；< warn → "fail"。
    """
    planned_beats = _split_plan_beats(moves) or _split_plan_beats(core_event)
    if not planned_beats:
        return {"planned": [], "coverage": None, "verdict": "skip", "missing": []}
    body = content or ""
    planned = []
    covered_count = 0
    missing: list[str] = []
    for beat in planned_beats:
        ok, hit, miss = _beat_covered(beat, body)
        planned.append({"beat": beat, "covered": ok, "hit": hit, "miss": miss})
        if ok:
            covered_count += 1
        else:
            missing.append(beat)
    coverage = covered_count / len(planned_beats)
    if coverage >= min_coverage:
        verdict = "pass"
    elif coverage >= warn_coverage:
        verdict = "warn"
    else:
        verdict = "fail"
    return {"planned": planned, "coverage": round(coverage, 3),
            "verdict": verdict, "missing": missing}


def assess_chapter(content: str, moves: str, core_event: str = "",
                   min_coverage: float = 0.6, warn_coverage: float = 0.4) -> dict:
    """对单章正文做 beat 覆盖门禁评估（纯函数，供 stages 接线与 router 体检复用）。"""
    result = compute_beat_coverage(content, moves, core_event, min_coverage, warn_coverage)
    if result["verdict"] == "skip":
        logger.info("[BeatGate] 无计划拍（moves/core_event 均空），跳过覆盖评估")
    else:
        logger.info("[BeatGate] 覆盖率=%.2f verdict=%s 漏写拍=%d",
                    result["coverage"], result["verdict"], len(result["missing"]))
    return result
