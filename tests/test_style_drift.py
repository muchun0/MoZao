# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S2.4-L1 确定性漂移检测 专项测试。

覆盖：
- P1：正常多样文本 → drift_score 高分（无漂移信号）
- P1：模板化文本（句长趋同 + 高频口头禅 + 低 TTR）→ 漂移分显著低
- P2：metrics 字段齐全（句长均值/cv/TTR/口头禅/比喻密度）
- P2：空文本 → 100（降级安全）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.ai_taste import style_drift_metrics, drift_score


class TestStyleDriftMetrics:
    @pytest.mark.p1
    def test_metrics_fields_present(self):
        m = style_drift_metrics("少年推门而入，铁箱内躺着一枚蚀晶。他想起师父的话，心头一沉。窗外暮色四合，远处狼嚎声起，他攥紧了剑柄。")
        for key in ("sentence_count", "mean_sentence_len", "sentence_cv",
                    "ttr", "filler_count", "filler_ratio", "simile_per_1k"):
            assert key in m

    @pytest.mark.p2
    def test_empty_text(self):
        assert style_drift_metrics("") == {}
        assert drift_score("") == 100

    @pytest.mark.p2
    def test_short_text_default_high(self):
        assert drift_score("太短了。") == 100   # sentence_count<3 默认高


class TestDriftScore:
    @pytest.mark.p1
    def test_diverse_text_outranks_template(self):
        # 句长多样、用词丰富的文本漂移分明显高于模板化文本（句长趋同+口头禅+重复用词）
        diverse = (
            "少年踩着碎瓦走进庙堂，檐角的铜铃在风里晃了两下，叮当作响。"
            "老僧闭目打坐，指尖捻过一串檀木念珠，半晌才抬眼看他。"
            "他说的每句话都很轻，轻得像落在尘埃里的雪。"
            "那雪落在少年肩头，又顺着衣襟化开。"
            "少年攥紧剑柄，指节泛白，喉头滚了滚却没出声。"
            "庙外暮色四合，乌鸦掠过屋脊，叫声嘶哑而短促。"
        )
        template = "然后他向前走去，然后他看见前方，然后他停下脚步。然后他回头看去，然后他皱了皱眉，然后他继续前行。然后他又看见那扇门，然后他推开门，然后他走进去。"
        d_score = drift_score(diverse)
        t_score = drift_score(template)
        assert d_score > t_score
        assert t_score < 70          # 模板化文本有明显漂移信号
        assert d_score >= 60         # 相对多样文本不应被严重误判

    @pytest.mark.p1
    def test_template_text_scores_low(self):
        # 句长趋同 + 高频"然后"口头禅 + 重复用词 → 漂移信号明显
        text = "然后他向前走去，然后他看见前方，然后他停下脚步。然后他回头看去，然后他皱了皱眉，然后他继续前行。然后他又看见那扇门，然后他推开门，然后他走进去。"
        s = drift_score(text)
        assert s < 70
        m = style_drift_metrics(text)
        assert m["filler_ratio"] > 0.006   # 填充词占比高（口头禅）
