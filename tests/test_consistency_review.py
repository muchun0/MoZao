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
