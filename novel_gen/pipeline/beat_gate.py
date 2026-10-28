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
    - 对象槽：网络实体词（通用化后恒空，待从 World 动态注入）出现即命中；
    - 修饰限定槽：拍内其余 2 字实词（如"隐藏/古老/密林深处"），正文缺一则该拍
      不完整覆盖（保留"关键信息缺失则扣分"的防假阳性语义）。
  单拍覆盖 = 动作槽命中率达标 且 对象槽命中 且 修饰限定槽全部命中；
  无动作槽拍退化为"对象槽命中"，全空槽拍保守判 miss。
  由于语义点是"从拍文本自适应提取 + 同义表归一"，对任意世界观/场景通用，
  不再依赖写死的实体词清单。

设计约束（AGENTS.md §11 / REQ-beat-gate）：
- **词表路径纯确定性、零 LLM**（llm=None / use_llm=False / LLM 失败回退）；
  REQ-词表换LLM 批次一起，传入 llm 时整章一次 LLM 语义判定全部拍（成本每章 1 次），
  近义词表保留为确定性兜底；
- **检测+记录+提示，不独立重生成正文**：重写统一由既有 quality.ensure_quality 链路负责；
- 外部契约不变：`compute_beat_coverage` / `assess_chapter` 签名与返回结构
  {planned:[{beat,covered,hit,miss}], coverage, verdict, missing} 保持一致。
"""
from __future__ import annotations

import logging
import re

from ..llm.json_parse import parse_json_obj
from .templates import beat_gate_coverage_prompt

logger = logging.getLogger(__name__)

#: 拍文本中需剔除的无信息量短词（用于提取关键实词；2 字以上实词片段保留）
_STOP_CHARS = set("了的着他她它在把被是也就都和与而又再才但却向到从对为以上下中后前时这那其之等个只要会能可将，。；、（）()：:「」 \t")
#: 计划拍切分（character_moves 逐拍）
_PLAN_SPLIT_RE = re.compile(r"[→。；;]")

#: loose 匹配用：去标点/空白归一（REQ S3.2）。覆盖判定前先做归一，缓解
#: 标点差异/近义改写导致的假阴性（"清晨出门。" ↔ "天刚蒙蒙亮出门"）。零 LLM。
_PUNCT_RE = re.compile(r"[\s，。；、（）()：:「」“”‘’？！…—,.?!;:'\"~·-]+")


def _norm_for_loose(s: str) -> str:
    """去标点/空白归一，供 loose 匹配比较（保留汉字/数字/字母）。"""
    return _PUNCT_RE.sub("", s or "")


#: 对话行匹配：成对引号内的对话片段（中英文引号均计）
_QUOTE_PARTS = re.compile(r"[“\"][^”\"]{1,120}[”\"]")


def detect_chapter_type(content: str, moves: str = "") -> str:
    """正文确定性判定章型（REQ S3.2 章型白名单，零 LLM）。

    对话章：成对引号内字符占比 ≥0.35 → "对话"（对白为主的章，beat 密度天然低，
    不应用情节拍去卡 fail）；短章（<50 字）→ "过渡"；其余 → "剧情"。
    对话/过渡章由 assess 层 skip（不参与覆盖 fail），治"对话章 0% 被误判 fail"误伤。
    """
    body = content or ""
    total = max(len(body), 1)
    qlen = sum(len(p) for p in _QUOTE_PARTS.findall(body))
    if qlen / total >= 0.35:
        return "对话"
    if total < 50:
        return "过渡"
    return "剧情"


#: 网络实体词（对象槽）。通用化（2026-10-27）：书级实体词（灰卒/赤尉/小九/铁匠铺/
#: 老陈头/墟口/蚀痕等）已移除——换书后不再依赖本书实体；当前对象槽不参与覆盖判定
#: （subjects 恒空 → 视为通过），待后续从 World 人物/敌人/地点/道具卡动态注入。
_ENTITY_WORDS: tuple[str, ...] = ()

#: 动作归一表：规范动作主词 → 等价触发词。命中任一触发词（或主词本身）即归一。
#: 动作槽用它对"改写过的动作"归一到规范动作；语义点命中 = 该主词族任一写法在正文出现。
#: 通用化（2026-10-27）：书形动作族（看面板/被咬死/核外移/见血洞/药钱及弓背/钻进灰雾/
#: 回铁匠铺等书级触发词）已移除，仅保留通用行为词族。
_ACTION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "前往": ("出门", "出门前", "前往", "走向", "踏入", "走进"),
    "拾荒": ("拾荒", "翻找"),
    "遇袭": ("遭遇", "遇到", "碰上", "撞上", "扑上来"),
    "复活": ("复活", "再睁眼", "重生", "活过来", "醒过来"),
    "砸": ("砸", "砸碎", "锤", "锤砸", "击中", "敲碎"),
    "进食": ("进食", "舔", "啃", "吃"),
    "嘶吼": ("嘶吼", "低吼", "吼"),
    "逃窜": ("逃窜", "逃", "退", "转身", "缩回", "退了一步", "不见"),
    "回到": ("回到", "回来", "走回"),
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


def _slot_hit(term: str, content: str, loose: bool = True) -> bool:
    """某语义点（词）是否在正文出现：归一后按主词族的任一写法匹配。

    loose（REQ S3.2）：匹配前先做去标点/空白归一，缓解标点/近义差异导致的假阴性；
    False 保持原精确子串匹配（向后兼容）。
    """
    _m, trigs = _normalize_term(term)
    if loose:
        _n = _norm_for_loose(content)
        return any(_norm_for_loose(t) in _n for t in trigs)
    return any(t in content for t in trigs)


def _extract_slots(beat: str) -> tuple[list[str], list[str], list[str]]:
    """从拍文本提取三类语义点：动作槽 / 对象槽 / 修饰限定槽。

    返回 (actions, subjects, mods)：
      - actions：命中动作归一表的动作主词（如 前往/遇袭/砸/发现…）；
      - subjects：网络实体词（通用化后恒空，对象槽暂不参与判定）；
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


def _beat_covered(beat: str, content: str, loose: bool = True) -> tuple[bool, list[str], list[str]]:
    """单拍是否被正文覆盖：动作槽命中率达标 + 对象槽命中 + 修饰限定槽全部命中。

    返回 (covered, hit, miss)：hit/miss 列出命中的语义点与缺失的语义点（含"[动作]"前缀）。
    """
    actions, subjects, mods = _extract_slots(beat)
    hit: list[str] = []
    miss: list[str] = []
    # 动作槽：≥50% 命中（容忍部分动作细节省略，但主体动作必须到）
    act_ok = True
    if actions:
        act_hit_n = sum(1 for a in actions if _slot_hit(a, content, loose))
        for a in actions:
            (hit if _slot_hit(a, content, loose) else miss).append(f"[动作]{a}")
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
            if _slot_hit(m, content, loose):
                hit.append(m)
            else:
                mod_ok = False
                miss.append(m)
    # 三类槽全空（纯虚词拍）→ 保守视为未覆盖
    if not actions and not subjects and not mods:
        return False, [], [beat]
    covered = act_ok and sub_ok and mod_ok
    return covered, hit, miss


def _llm_beat_coverage(content: str, planned_beats: list[str], llm) -> list[dict] | None:
    """整章一次 LLM 判定全部计划拍是否在正文兑现（REQ-词表换LLM 批次一）。

    替代逐拍近义词表子串命中——近义改写/语序调整由 LLM 按语义判。成本纪律：每章只调
    1 次 LLM，禁止每拍 1 次。返回与 planned_beats 等长、顺序对齐的
    [{"covered":bool,"hit":[str],"miss":[str]}]；dict 为空 / 无 beats / beats 非 list /
    长度与计划拍不等 / 任一条非 dict → 返回 None，调用方逐拍回退词表（确定性兜底）。
    """
    text = content or ""
    if len(text) > 3000:
        text = text[:2500] + "……" + text[-400:]
    system, user = beat_gate_coverage_prompt(text, planned_beats)
    raw = llm.chat(system, user, temperature=0.2, max_tokens=800, json_mode=True)
    data = parse_json_obj(raw)
    if not data or not isinstance(data.get("beats"), list):
        return None
    llm_beats = data["beats"]
    if len(llm_beats) != len(planned_beats):
        return None
    result: list[dict] = []
    for item in llm_beats:
        if not isinstance(item, dict):
            return None
        hit = [str(h) for h in item["hit"]] if isinstance(item.get("hit"), list) else []
        miss = [str(m) for m in item["miss"]] if isinstance(item.get("miss"), list) else []
        result.append({"covered": bool(item.get("covered")), "hit": hit, "miss": miss})
    return result


def compute_beat_coverage(content: str, moves: str, core_event: str = "",
                          min_coverage: float = 0.6, warn_coverage: float = 0.4,
                          chapter_type: str = "剧情", priorities: dict | None = None,
                          loose: bool = True, far_beats: list[str] | None = None,
                          llm=None, use_llm: bool = True) -> dict:
    """计算正文对章计划 beat 的覆盖率并分级（REQ S3.2 扩展，向后兼容）。

    返回：{planned:[{beat,covered,hit,miss}], coverage, verdict, missing, chapter_type}
      - 无计划拍 → verdict="skip"，coverage=None（不误报）；
      - 对话/过渡章 → verdict="skip_chapter_type"（章型白名单：对白/过渡章 beat 密度
        天然低，不应用情节拍卡 fail，治"对话章 0% 被误判 fail"误伤；coverage=None）；
      - 提供 priorities（拍→0/1/2）时，P2 节点不计入分母（只算 P0+P1）；
      - 提供 far_beats（REQ S3.6 远期大纲粗粒度）时，这些"距当前 > 窗口的远期节点"
        不计入分母（只方向约束，不要求逐拍兑现；全为 far 时 verdict="skip_far"）；
      - coverage ≥ min → "pass"；[warn, min) → "warn"；< warn → "fail"。
    """
    planned_beats = _split_plan_beats(moves) or _split_plan_beats(core_event)
    # 章型 auto：调用方未指定时由正文确定性判定（对话/过渡章白名单）
    if chapter_type == "auto":
        chapter_type = detect_chapter_type(content or "", moves)
    base = {"chapter_type": chapter_type}
    if not planned_beats:
        return {**base, "planned": [], "coverage": None, "verdict": "skip", "missing": []}
    # 章型白名单：对话/过渡章不参与覆盖 fail（治误伤；low-beat 密度章不卡 fail）
    if chapter_type in ("对话", "过渡"):
        return {**base, "planned": [], "coverage": None, "verdict": "skip_chapter_type",
                "missing": []}
    # 节点分级：P2 氛围节点不计入覆盖率分母（分母只算 P0+P1；未提供则默认全 P1）
    if priorities:
        planned_beats = [b for b in planned_beats if priorities.get(b, 1) != 2]
        if not planned_beats:
            return {**base, "planned": [], "coverage": None,
                    "verdict": "skip_priority", "missing": []}
    # REQ S3.6 远期大纲粗粒度：距当前 > 窗口的远期节点不计入分母（方向约束，不逐拍兑现）
    if far_beats:
        far_set = {str(b).strip() for b in far_beats if str(b).strip()}
        if far_set:
            planned_beats = [b for b in planned_beats if b not in far_set]
            if not planned_beats:
                return {**base, "planned": [], "coverage": None,
                        "verdict": "skip_far", "missing": []}
    body = content or ""
    # 整章一次 LLM 语义判定（REQ-词表换LLM 批次一）：成功则逐拍直接采用 LLM 的
    # covered/hit/miss；llm 为空 / use_llm=False / 异常 / 对齐不符 → 词表逐拍兜底。
    llm_results: list[dict] | None = None
    if llm is not None and use_llm and planned_beats:
        try:
            llm_results = _llm_beat_coverage(body, planned_beats, llm)
            if llm_results is None:
                logger.warning("[BeatGate] LLM 语义判定结果不可用（对齐/格式不符），回退词表逐拍")
        except Exception:
            logger.warning("[BeatGate] LLM 语义判定异常，回退词表逐拍判定", exc_info=True)
            llm_results = None
    planned = []
    covered_count = 0
    missing: list[str] = []
    for idx, beat in enumerate(planned_beats):
        if llm_results is not None:
            r = llm_results[idx]
            ok, hit, miss = r["covered"], r["hit"], r["miss"]
        else:
            ok, hit, miss = _beat_covered(beat, body, loose)
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
    return {**base, "planned": planned, "coverage": round(coverage, 3),
            "verdict": verdict, "missing": missing}


def assess_chapter(content: str, moves: str, core_event: str = "",
                   min_coverage: float = 0.6, warn_coverage: float = 0.4,
                   chapter_type: str = "auto", priorities: dict | None = None,
                   loose: bool = True, far_beats: list[str] | None = None,
                   llm=None, use_llm: bool = True) -> dict:
    """对单章正文做 beat 覆盖门禁评估（纯函数，供 stages 接线与 router 体检复用）。

    chapter_type="auto"（默认）：由正文确定性判定章型（对话/过渡章白名单）；
    显式传"剧情"/"对话"等则按给定值。loose=False 完全回退旧精确子串匹配（向后兼容）。
    far_beats（REQ S3.6）：距当前 > 窗口的远期节点，不计入 fail 判定分母。
    llm/use_llm（REQ-词表换LLM 批次一）：传入 LLM 客户端时整章一次语义判定全部拍，
    失败自动回退近义词表兜底；llm=None 或 use_llm=False 走纯词表（存量行为不变）。
    """
    result = compute_beat_coverage(content, moves, core_event, min_coverage, warn_coverage,
                                   chapter_type, priorities, loose, far_beats,
                                   llm=llm, use_llm=use_llm)
    if result["verdict"] in ("skip", "skip_chapter_type", "skip_priority", "skip_far"):
        logger.info("[BeatGate] %s（%s）", result["verdict"],
                    "无计划拍" if result["verdict"] == "skip"
                    else "对话/过渡章不卡fail" if result["verdict"] == "skip_chapter_type"
                    else "全部P2节点不计分母" if result["verdict"] == "skip_priority"
                    else "全部远期节点不计分母(S3.6)")
    else:
        logger.info("[BeatGate] 覆盖率=%.2f verdict=%s 章型=%s 漏写拍=%d",
                    result["coverage"], result["verdict"], chapter_type, len(result["missing"]))
    return result
