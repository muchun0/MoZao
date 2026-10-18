# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S2.4-L1 漂移检测 接入质检链路 专项测试。

覆盖：
- P1：模板化正文（句长趋同 + 填充词堆砌）→ check 产出「文风漂移-L1」小改 issue 并轻扣分
- P1：多样正文 → 不产出漂移 issue（无误报）
- P2：style_drift_check 关闭 → 不产出
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.quality import QualityChecker

#: 模板化正文：句长趋同 + "然后/于是"填充词堆砌 + 高频重复用词 → 漂移分明显低（<60）
_TEMPLATE_TEXT = ("然后他向前走，于是他向前走，然后他向前走。于是他又向前走，"
                  "然后他看见一扇门，于是他推开那扇门，然后他走了进去。"
                  "于是屋里有一盏灯，然后他看见一盏灯，于是他看见一张桌，然后他看见一封信。"
                  "于是他拿起那封信，然后他拆开那封信，于是信上写着一个名字。"
                  "然后他愣在原地，于是他低下头，然后他把信放进怀里。"
                  "于是他走出那扇门，然后他关上门，于是他又走回雾里。"
                  "然后他看见那片雾，于是他停住脚步，然后他想了想，于是他又向前走。")

#: 多样正文：句长有变化、填充词少 → 漂移分高
_DIVERSE_TEXT = (
    "少年踩着碎瓦走进庙堂，檐角的铜铃在风里晃了两下，叮当作响。"
    "老僧闭目打坐，指尖捻过一串檀木念珠，半晌才抬眼看他。"
    "他说的每句话都很轻，轻得像落在尘埃里的雪。"
    "那雪落在少年肩头，又顺着衣襟化开。"
    "少年攥紧剑柄，指节泛白，喉头滚了滚却没出声。"
    "庙外暮色四合，乌鸦掠过屋脊，叫声嘶哑而短促。"
    "老僧终于开口，声音干涩如枯木：你还记得那枚蚀晶么。"
    "少年猛地抬头，瞳孔里倒映着跳动的烛火，半晌才哑声应道：记得。"
)


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                      chapter_words=1600)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _mk_llm():
    """reader 校验返回空 issues（其余 LLM 调用返回空），不触发重写。"""
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 5, "一句话感受": "ok"}'
    llm.chat_json.return_value = {"issues": [], "items": []}
    return llm


class TestStyleDriftWiring:
    @pytest.mark.p1
    def test_template_text_yields_drift_issue(self, store):
        qc = QualityChecker(store.load_config(), _mk_llm(), store)
        res = qc.check(1, "第一章", "推进主线", _TEMPLATE_TEXT, "摘要")
        drift = [i for i in res["issues"] if i["type"] == "文风漂移-L1（句长模板化）"]
        assert drift, "模板化正文应产出文风漂移 issue"
        assert drift[0]["level"] == "小改"
        assert "漂移分" in drift[0]["detail"]
        assert res["score"] <= 95  # 轻扣 5 分

    @pytest.mark.p1
    def test_diverse_text_no_drift_issue(self, store):
        qc = QualityChecker(store.load_config(), _mk_llm(), store)
        res = qc.check(1, "第一章", "推进主线", _DIVERSE_TEXT, "摘要")
        drift = [i for i in res["issues"] if i["type"] == "文风漂移-L1（句长模板化）"]
        assert not drift, "多样正文不应产出文风漂移 issue"

    @pytest.mark.p2
    def test_drift_check_off_disables(self, store):
        cfg = store.load_config()
        cfg.style_drift_check = False
        qc = QualityChecker(cfg, _mk_llm(), store)
        res = qc.check(1, "第一章", "推进主线", _TEMPLATE_TEXT, "摘要")
        drift = [i for i in res["issues"] if i["type"] == "文风漂移-L1（句长模板化）"]
        assert not drift
