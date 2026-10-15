# -*- coding: utf-8 -*-
"""P1-4 AI 痕迹量化评分测试（P1）。

覆盖：已知 AI 味样本检出并给分、正常正文低误报、QualityChecker 接入产出 AI痕迹 issue。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.ai_taste import score_ai_taste, ai_taste_issues
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.store.project import ProjectStore


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=200, total_words=1000)
    return ProjectStore.create(tmp_path / "ai", cfg)


_AI_HEAVY = (
    "然而，他确实感到愤怒。值得注意的是，与此同时，他的内心因此产生了深深的无奈。"
    "事实上，他震惊于这个结果，然而他不得不接受现实。总而言之，这一切显而易见地"
    "指向一个结论。此外，他的悲伤与痛苦交织在一起，他害怕失败，然而他必须前进。"
) * 2  # 连接词+情绪词堆砌样本

_NATURAL = (
    "他推开门，风灌进来。桌上那碗面还冒着热气，他坐下来，拿起筷子，却没动。"
    "手机屏幕亮了又暗，他没有看。窗外有人喊他的名字，他应了一声，声音有点哑。"
    "她把药瓶推过来，说先吃了再说。他低头看了一眼，没接，只是把外套拢了拢。"
) * 3  # 正常正文：动作/对话为主


@pytest.mark.p1
def test_heavy_sample_detected():
    """已知 AI 味样本：得分低于阈值，且命中连接词/情绪词 issue。"""
    score = score_ai_taste(_AI_HEAVY)
    assert score < 60
    issues = ai_taste_issues(_AI_HEAVY, threshold=60)
    types = {it["type"] for it in issues}
    assert "AI痕迹-连接词堆砌" in types
    assert "AI痕迹-情绪词堆砌（点破允许）" in types
    assert all(it["level"] == "中改" for it in issues)


@pytest.mark.p1
def test_natural_text_low_false_positive():
    """正常正文：得分较高，默认阈值下无 issue（低误报）。"""
    score = score_ai_taste(_NATURAL)
    assert score >= 60
    assert ai_taste_issues(_NATURAL, threshold=60) == []


@pytest.mark.p1
def test_parallel_and_para_detected():
    """排比句式与段落雷同也能检出（扣分并产出 issue）。"""
    parallel = "他望着窗。他望着门。他望着天。他望着地。" * 3
    # 排比扣 15 + 零直白扣 10（该样本无'他怕了'式点破，属演过头提示）
    assert score_ai_taste(parallel) == 75
    assert any(it["type"] == "AI痕迹-句式排比" for it in ai_taste_issues(parallel, 100))


@pytest.mark.p1
def test_simile_density_detected():
    """'像……'明喻密度超限：命中「明喻过密（警示）」，白描收敛引导（非禁词）。"""
    simile_heavy = (
        "风像刀子。夜像墨。街像一条冻僵的蛇。灯像垂死的眼。雪像盐，又像灰。"
    ) * 4
    assert any(it["type"] == "AI痕迹-明喻过密（警示）"
               for it in ai_taste_issues(simile_heavy, threshold=100))


@pytest.mark.p1
def test_zero_direct_detected_and_pointed_exempt():
    """零直白：全程无点破句式命中提示；含'他怕了'式点破则豁免。"""
    acted_only = "他攥紧拳头，指节发白，半天没说话。她低着头，把药瓶推过去。" * 3
    assert any(it["type"] == "AI痕迹-全程演过头（零直白）"
               for it in ai_taste_issues(acted_only, threshold=100))
    pointed = "他怕了。他认得这种感觉——上次腿被打断那夜，冷也是这么冷。" * 3
    assert not any(it["type"] == "AI痕迹-全程演过头（零直白）"
                   for it in ai_taste_issues(pointed, threshold=100))


@pytest.mark.p1
def test_threshold_controls(store):
    """threshold 提高后，中等文本也会被标记（阈值生效）。"""
    mid = "然而，然而，然而，然而，然而，然而。" * 2  # 连接词密度高但字数少
    assert ai_taste_issues(mid, threshold=100)  # 阈值拉满 → 必出 issue


@pytest.mark.p1
def test_quality_checker_integration(store):
    """QualityChecker.check 接入：AI 痕迹作为计分项产出 issue；正常文本无 AI痕迹 issue。"""
    llm = MagicMock()
    llm.chat.return_value = ""  # 读者视角 LLM 校验失败降级为空
    qc = QualityChecker(store.load_config(), llm, store)

    r_heavy = qc.check(1, "第一章", "目标", _AI_HEAVY)
    assert any(it["type"].startswith("AI痕迹-") for it in r_heavy["issues"])
    assert r_heavy["score"] < 100

    r_natural = qc.check(1, "第一章", "目标", _NATURAL)
    assert not any(it["type"].startswith("AI痕迹-") for it in r_natural["issues"])


@pytest.mark.p1
def test_switch_off(store):
    """ai_taste_check=False 时不产出 AI痕迹 issue。"""
    cfg = store.load_config()
    cfg.ai_taste_check = False
    llm = MagicMock()
    llm.chat.return_value = ""
    qc = QualityChecker(cfg, llm, store)
    r = qc.check(1, "第一章", "目标", _AI_HEAVY)
    assert not any(it["type"].startswith("AI痕迹-") for it in r["issues"])
