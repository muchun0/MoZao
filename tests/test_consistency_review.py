# -*- coding: utf-8 -*-
"""事件重复候选确定性复核闸门专项测试（质检语义复核二期，P1）。

覆盖 TECH §11.2 清单：
- d 类中断后续写承接 → 剔除；真重演两版本 → 保留（不漏报）；
- 引文定位失败 / 信号不全 / 重演反向冲突 → fail-open 保留；
- 非事件重复类型原样放行；闸门异常整批保留；元数据标注完整。

离线纯函数，不依赖 LLM 与数据库；占位正文避开节拍词与代码特征词。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.quality.consistency_review import (
    is_event_duplicate, review_event_duplicates,
    detail_self_negates, downgrade_self_negated,
)


# ---------- 第 6 章现场形态：打断 + 递进承接（正例，必须剔除） ----------

#: 前一处：一条缝 → 中段对话/倒计时打断 → 后一处：彻底拉开、蹲下、摸到标签纸
_CONTENT_CONTINUATION = (
    "沈决把把手压到底。柜门弹开一条缝，冷气混着旧纸的味道涌出来。"
    "她伸手进去，指尖碰到一叠卷宗的硬边。"
    "“停手。”走廊尽头的皮鞋声近了。"
    "“十分钟，开始走了。”陆行舟让出半步通道。"
    "“你还有八分钟。”他看着她，看了有两秒。"
    "她把柜门彻底拉开，蹲下去，手伸进那格冷气里。"
    "指尖碰到一叠卷宗的硬边，她摸到侧脊上贴的标签纸，指腹蹭过去，胶面发涩。"
)

_DETAIL_CONTINUATION = (
    "前一处：『沈决把把手压到底。柜门弹开一条缝，冷气混着旧纸的味道涌出来。"
    "她伸手进去，指尖碰到一叠卷宗的硬边。』"
    "后一处：『她把柜门彻底拉开，蹲下去，手伸进那格冷气里。"
    "指尖碰到一叠卷宗的硬边，她摸到侧脊上贴的标签纸，指腹蹭过去，胶面发涩。』"
    "——判定为同一事件两版本"
)


def _issue(detail: str, itype: str = "自相矛盾-事件重复") -> dict:
    return {
        "type": itype,
        "detail": detail,
        "level": "大改",
        "source": "llm:consistency",
        "rule_id": "consistency.contradiction",
        "dimension": "consistency",
    }


def _fix_dups(chk, content: str, issues: list[dict]) -> tuple[str, list[dict]]:
    """调用 QualityChecker._fix_event_duplicates（未构造完整依赖，只测强制执行的装配）。"""
    from novel_gen.pipeline.quality.checker import QualityChecker
    return QualityChecker._fix_event_duplicates(
        chk, 1, "第一章", "目标", content, issues)


class TestContinuationFilter:
    def test_interrupted_continuation_filtered(self):
        """三信号同时满足（打断对话/递进新细节/未闭环）→ 识别为承接形态剔除。"""
        kept, filtered = review_event_duplicates(
            [_issue(_DETAIL_CONTINUATION)], _CONTENT_CONTINUATION)
        assert kept == []
        assert len(filtered) == 1
        hit = filtered[0]
        assert hit["review_filtered"] is True
        assert hit["review_signals"] == [
            "interruption_dialogue", "progression_new_detail", "no_closed_loop"]

    def test_is_event_duplicate_marker(self):
        assert is_event_duplicate({"type": "自相矛盾-事件重复"}) is True
        assert is_event_duplicate({"type": "自相矛盾-身体状态"}) is False


class TestRealDuplicateKept:
    def test_real_duplicate_without_interruption_kept(self):
        """真重演：两段各自闭环、中段无对话打断 → 必须保留，不漏报。"""
        content = (
            "陆沉在墟口空地冲下去，救走苏妄言，赤尉被引开，两人退到安全处。"
            "坡地上苏妄言又被赤尉围住，陆沉再次冲下去把人救了出来。"
        )
        detail = (
            "前一处：『陆沉在墟口空地冲下去，救走苏妄言，赤尉被引开。』"
            "后一处：『坡地上苏妄言又被赤尉围住，陆沉再次冲下去把人救了出来。』"
        )
        kept, filtered = review_event_duplicates([_issue(detail)], content)
        assert len(kept) == 1
        assert filtered == []

    def test_repeat_marker_forces_keep(self):
        """后窗口出现重演标志词（再次/又一次）→ 即使信号疑似满足也保留。"""
        content = (
            "他推开房门，走到桌边拿起文件。"
            "他再次推开房门，又一次走到桌边拿起文件，从头又演了一遍。"
        )
        detail = (
            "前一处：『他推开房门，走到桌边拿起文件。』"
            "后一处：『他再次推开房门，又一次走到桌边拿起文件。』"
        )
        kept, filtered = review_event_duplicates([_issue(detail)], content)
        assert len(kept) == 1
        assert filtered == []


class TestFailOpen:
    def test_citation_not_located_fails_open(self):
        """引文在正文定位不到 → fail-open 保留（兼容离线 mock 负例契约）。"""
        kept, filtered = review_event_duplicates(
            [_issue(_DETAIL_CONTINUATION)], "正文若干")
        assert len(kept) == 1
        assert filtered == []

    def test_only_interruption_without_progression_kept(self):
        """中段有对话打断，但后窗口无递进新细节 → 信号不全，保留。"""
        content = (
            "他拿起茶杯喝了一口，放在桌边。"
            "“别动。”门外有人喝了一声。"
            "他拿起茶杯喝了一口，放在桌边。"
        )
        detail = (
            "前一处：『他拿起茶杯喝了一口，放在桌边。』"
            "后一处：『他拿起茶杯喝了一口，放在桌边。』"
        )
        kept, filtered = review_event_duplicates([_issue(detail)], content)
        assert len(kept) == 1
        assert filtered == []

    def test_only_progression_without_interruption_kept(self):
        """递进新细节存在，但两段之间无对话/打断段 → 信号不全，保留。"""
        content = (
            "柜门弹开一条缝，她伸手碰到卷宗硬边。"
            "她把柜门彻底拉开，蹲下去摸到侧脊上贴的标签纸。"
        )
        detail = (
            "前一处：『柜门弹开一条缝，她伸手碰到卷宗硬边。』"
            "后一处：『她把柜门彻底拉开，蹲下去摸到侧脊上贴的标签纸。』"
        )
        kept, filtered = review_event_duplicates([_issue(detail)], content)
        assert len(kept) == 1
        assert filtered == []

    def test_non_event_duplicate_passes_through(self):
        """身体状态/方位指代等其他一致性类型不进入复核，原样保留。"""
        other = _issue("前后伤口位置不一致", "自相矛盾-身体状态")
        kept, filtered = review_event_duplicates([other], _CONTENT_CONTINUATION)
        assert kept == [other]
        assert filtered == []

    def test_malformed_issue_kept(self):
        """畸形 issue（缺 type/detail）不抛异常，整批走保留路径。"""
        kept, filtered = review_event_duplicates(
            [{}, {"type": "自相矛盾-事件重复"}], _CONTENT_CONTINUATION)
        assert len(kept) == 2
        assert filtered == []

    def test_exception_fails_open(self, monkeypatch):
        """闸门内部异常 → 整批保留、filtered 为空，不向外抛（fail-open）。"""
        from novel_gen.pipeline.quality import consistency_review as mod
        monkeypatch.setattr(mod, "_classify_candidate",
                            lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        issues = [_issue(_DETAIL_CONTINUATION)]
        kept, filtered = review_event_duplicates(issues, _CONTENT_CONTINUATION)
        assert kept == issues
        assert filtered == []


# ---------- 2026-11-10 修复⑤：v7 真实形态（未完成 → 完成 = 递进，不是重演） ----------

#: v7 原文形态：@2492 手搭门把没拧 → 退回 → 抄守则/查洗手间 → @4856 再走到门边 → @4923 拧下把手
_V7_LIKE = (
    "沈决走到门边，手搭上门把，没拧。她侧耳听了听，门外有脚步声过去。"
    "她退回来，在桌边坐下，把那张守则抄了一遍，又去洗手间洗了手。"
    "回来时菜已经上了两碟，王磊还在说话。"
    "沈决走到门边，手搭在把手上。她回头看了一眼桌上那两碟凉菜，抿了抿嘴。"
    "她拧下把手。门锁“嗒”地一声，门开了。"
)

_V7_DUP_DETAIL = (
    "前一处：沈决走到门边，手搭上门把，没拧，侧耳听门外脚步声后『退回来』；"
    "后一处：沈决再次『走到门边，手搭在把手上』，这次『拧下把手』开门放老周出去。"
    "两处均为走到包间门边、手搭门把探查，属阶段递进承接，仅作说明，不构成矛盾。"
)


class TestProgressionCompletion:
    def test_unfinished_then_complete_filtered(self):
        """同一动作第一次未完成、第二次完成 → 阶段递进，必须剔除（v7 误判根治）。"""
        kept, filtered = review_event_duplicates([_issue(_V7_DUP_DETAIL)], _V7_LIKE)
        assert kept == []
        assert filtered[0]["review_signals"] == ["unfinished_then_complete"]
        assert "阶段递进" in filtered[0]["review_note"]

    def test_true_repeat_after_completion_kept(self):
        """负例：两次都做完同一动作（无未完成标记）→ 仍是真重复，必须保留。"""
        content = (
            "沈决走到门边，手搭在把手上，拧下把手把门开了。"
            "老周出去以后，沈决回到桌边坐下，把守则重新抄了一遍。"
            "过了两页，沈决走到门边，手搭在把手上，拧下把手把门开了。"
        )
        detail = ("前一处：『走到门边，手搭在把手上』；"
                  "后一处：『走到门边，手搭在把手上』，又一次把门开了。")
        kept, filtered = review_event_duplicates([_issue(detail)], content)
        assert len(kept) == 1
        assert filtered == []


class TestSelfNegatedDowngrade:
    def test_self_denying_detail_downgraded(self):
        """detail 自述「不构成矛盾/硬伤」的大改 → 降为中改并留痕（v7 三条）。"""
        issues = [
            {"level": "大改", "detail": "动作链本身可成立（抽出即从包内取出），不构成矛盾。"},
            {"level": "大改", "detail": "三处口径一致，不构成矛盾。"},
            {"level": "大改", "detail": "比喻与场景氛围贴合，不构成硬伤。"},
        ]
        out = downgrade_self_negated(issues)
        assert [i["level"] for i in out] == ["中改", "中改", "中改"]
        assert all(i.get("level_note") for i in out)

    def test_positive_contradiction_not_downgraded(self):
        """负例：真矛盾（前后不一 / 不能同时为真）不得降级，仍按大改阻断。"""
        detail = "前一处写伤口在左臂，后一处写右臂，两处不能同时为真，前后不一。"
        assert detail_self_negates(detail) is False
        out = downgrade_self_negated([{"level": "大改", "detail": detail}])
        assert out[0]["level"] == "大改"

    def test_level_based_delta(self):
        """大改 -25 / 中改 -10：降价后的条目不再拉爆整章分数。"""
        from types import SimpleNamespace
        from novel_gen.pipeline.quality.checker import QualityChecker
        chk = QualityChecker.__new__(QualityChecker)
        chk.cfg = SimpleNamespace(high_quality_mode=True, consistency_llm_every=1)
        chk._consistency_llm_check = lambda _ch, _c: ([
            {"level": "大改", "detail": "两处不能同时为真，前后不一"},
            {"level": "大改", "detail": "三处口径一致，不构成矛盾。"},
        ], "")
        issues, delta, attempted, err = QualityChecker._check_consistency(chk, 1, "正文")
        assert attempted is True and err == ""
        assert [i["level"] for i in issues] == ["大改", "中改"]
        assert delta == -35


class TestForceExecuteDuplicate:
    """2026-11-10 修复⑤①：质检判定的「事件重复」必须被执行，不再依赖改稿器二次定位。"""

    def _checker(self):
        from novel_gen.pipeline.quality.checker import QualityChecker
        chk = QualityChecker.__new__(QualityChecker)
        chk.cfg = chk.llm = chk.store = chk.trace = chk.guard = chk.memory = None
        return chk

    def test_rewrite_applied_when_verification_passes(self, monkeypatch):
        from novel_gen.pipeline.quality import issue_rewrite as mod
        seen = []

        def fake(cfg, llm, store, ch, content, issue, **kw):
            seen.append((ch, issue["type"]))
            return {"ok": True, "content": content + "（重复段已删）", "reason": "现场叙述 2→1"}

        monkeypatch.setattr(mod, "rewrite_issue", fake)
        fixed, done = _fix_dups(self._checker(), "正文", [_issue(_V7_DUP_DETAIL)])
        assert fixed == "正文（重复段已删）"
        assert len(done) == 1
        assert seen == [(1, "自相矛盾-事件重复")]

    def test_original_kept_when_verification_fails(self, monkeypatch):
        from novel_gen.pipeline.quality import issue_rewrite as mod

        def fake(cfg, llm, store, ch, content, issue, **kw):
            return {"ok": False, "content": "", "reason": "改写未消除重复现场（现场叙述仍 2 次）"}

        monkeypatch.setattr(mod, "rewrite_issue", fake)
        fixed, done = _fix_dups(self._checker(), "正文", [_issue(_V7_DUP_DETAIL)])
        assert fixed == "正文"
        assert done == []
