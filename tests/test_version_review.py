# -*- coding: utf-8 -*-
"""版本对比的审校/评审评论视角纯函数测试（P1）。

覆盖：两视角分类、原文引用、具体改法来源（复用 diagnosis）、
跨版"已消除"事实、无质检数据时不编造评价、结构化两版镜像口径。

占位文本避开节拍词（§11.8）：不用"救/杀/转身/救人"等生成特征词。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from novel_gen.config import NovelConfig  # noqa: E402
from novel_gen.pipeline.quality.aggregate import issue_evidence  # noqa: E402
from novel_gen.pipeline.version_review import (  # noqa: E402
    AGENT_CRITIQUE,
    AGENT_PROOFREAD,
    MAX_ITEMS_PER_SECTION,
    MAX_QUOTE_LEN,
    classify_review_agent,
    review_chapter_pair,
    review_structured_pair,
)
from novel_gen.store.project import ProjectStore  # noqa: E402
from novel_gen.store.versioned import VersionedProduct  # noqa: E402
from webapp.routers import projects_versions as pv  # noqa: E402


def _quality(issues: list[dict], score: int = 80, passed: bool = True) -> dict:
    return {"passed": passed, "score": score, "issue_count": len(issues), "issues": issues}


def _item(v: int, content: str, issues: list[dict] | None = None,
          words: int = 0, quality: dict | None = None) -> dict:
    return {
        "v": v,
        "words": words or len(content),
        "quality": quality if quality is not None else _quality(issues or []),
        "content": content,
    }


class TestClassifyAgent:
    def test_prose_goes_to_proofread(self):
        for t in ("AI痕迹-排比", "文风漂移", "章内同句复读", "自相矛盾-方位", "内容重复"):
            assert classify_review_agent({"type": t}) == AGENT_PROOFREAD, t

    def test_plot_goes_to_critique(self):
        for t in ("情节-大纲核心事件未覆盖", "结构-字数偏少", "结尾完整性", "读者-追更欲过低",
                  "情节-同事件循环重演"):
            assert classify_review_agent({"type": t}) == AGENT_CRITIQUE, t

    def test_unknown_defaults_to_critique(self):
        # 未知类型默认归评审（结构/读者体验），保证不被静默吞掉
        assert classify_review_agent({"type": "自定义-未知类型"}) == AGENT_CRITIQUE
        assert classify_review_agent({}) == AGENT_CRITIQUE


class TestChapterReview:
    def _pair(self, a_issues, b_issues, a_text="", b_text="", target=1000,
              a_words=0, b_words=0):
        a = _item(1, a_text, a_issues, words=a_words)
        b = _item(2, b_text, b_issues, words=b_words)
        return review_chapter_pair(a, b, target_words=target)

    def test_two_agents_in_fixed_order(self):
        r = self._pair([], [])
        panel = r["panels"][0]
        assert [x["agent"] for x in panel["reviews"]] == [AGENT_PROOFREAD, AGENT_CRITIQUE]
        assert r["kind"] == "chapter" and (r["v1"], r["v2"]) == (1, 2)

    def test_issue_splits_by_agent(self):
        a_issues = [
            {"level": "中改", "type": "AI痕迹-排比堆砌", "detail": "第三段连用四个排比"},
            {"level": "大改", "type": "情节-同事件循环重演", "detail": "桥上对峙写了两遍"},
        ]
        r = self._pair(a_issues, [], a_text="甲段落内容。\n乙段落内容。")
        proof = r["panels"][0]["reviews"][0]["improvements"]
        crit = r["panels"][0]["reviews"][1]["improvements"]
        assert [x["type"] for x in proof] == ["AI痕迹-排比堆砌"]
        assert [x["type"] for x in crit] == ["情节-同事件循环重演"]
        # 最重的排在前面
        assert crit[0]["level"] == "大改"

    def test_improvement_carries_actionable_suggestion(self):
        issues = [{"level": "大改", "type": "情节-同事件循环重演", "detail": "同事件重演"}]
        r = self._pair(issues, [], a_text="正文内容。")
        imp = r["panels"][0]["reviews"][1]["improvements"][0]
        # 建议来自 diagnosis（与重写 prompt 同源），不是"建议优化表达"这类空话
        assert imp["suggestion"]
        assert imp["expected_gain"] > 0
        assert "重演" in imp["problem"] or imp["problem"] == "同事件重演"

    def test_quote_located_from_content(self):
        text = "开篇叙述句内容。\n桥上对峙又写了一遍，措辞换了但事件相同。\n收尾句内容。"
        issues = [{"level": "大改", "type": "情节-同事件循环重演",
                   "detail": "「桥上对峙又写了一遍，措辞换了但事件相同」重复出现"}]
        r = self._pair(issues, [], a_text=text)
        imp = r["panels"][0]["reviews"][1]["improvements"][0]
        assert "桥上对峙" in imp["quote"]
        assert len(imp["quote"]) <= MAX_QUOTE_LEN + 1

    def test_flagged_aspect_never_becomes_strength(self):
        text = "首段内容。\n中段内容。\n末段内容。"
        a_issues = [{"level": "中改", "type": "AI痕迹-排比", "detail": "排比堆砌"}]
        r = self._pair(a_issues, [], a_text=text, a_words=1000)
        proof = r["panels"][0]["reviews"][0]
        aspects = [x["aspect"] for x in proof["strengths"]]
        assert "表达自然度" not in aspects  # 被点名的方面不能再算优点
        assert "重复叙述" in aspects       # 未被点名的方面照常产出

    def test_cross_version_elimination_fact(self):
        a_issues = [{"level": "中改", "type": "章内同句复读", "detail": "整句复读"}]
        r = self._pair(a_issues, [], a_text="甲。\n乙。\n丙。", b_text="甲。\n乙。\n丙。")
        # v2：v1 被点名的"重复叙述"在本版已消除
        v2_reasons = " ".join(x["reason"] for x in r["panels"][1]["reviews"][0]["strengths"])
        assert "v1 在此项被点名 1 条，本版已消除" in v2_reasons
        # v1 自己不产出"重复叙述"优点
        v1_aspects = [x["aspect"] for x in r["panels"][0]["reviews"][0]["strengths"]]
        assert "重复叙述" not in v1_aspects

    def test_word_count_aspect_needs_target(self):
        r_with = self._pair([], [], a_text="甲。", b_text="乙。", target=1000, a_words=1000)
        r_without = self._pair([], [], a_text="甲。", b_text="乙。", target=None, a_words=1000)
        aspects_with = [x["aspect"] for x in r_with["panels"][0]["reviews"][1]["strengths"]]
        aspects_without = [x["aspect"] for x in r_without["panels"][0]["reviews"][1]["strengths"]]
        assert "篇幅与信息密度" in aspects_with
        assert "篇幅与信息密度" not in aspects_without  # 无目标字数不编造"达标"

    def test_no_quality_data_means_empty_not_fabricated(self):
        a = {"v": 1, "words": 500, "quality": None, "content": "只有正文没有质检数据。"}
        b = {"v": 2, "words": 500, "quality": None, "content": "另一版正文也无质检数据。"}
        r = review_chapter_pair(a, b, target_words=None)
        for panel in r["panels"]:
            for rv in panel["reviews"]:
                assert rv["improvements"] == []
                assert rv["strengths"] == []
                assert rv["empty_hint"]

    def test_summary_counts_only(self):
        changes = {"truncated": False, "blocks": [
            {"type": "equal", "text": "相同段。"},
            {"type": "add", "b_text": "新增段。"},
            {"type": "del", "a_text": "删除段。"},
            {"type": "mod", "a_text": "旧。", "b_text": "新。"},
        ]}
        r = review_chapter_pair(_item(1, "甲。"), _item(2, "乙。"), changes=changes)
        assert r["summary"]["changed_blocks"] == 3
        assert (r["summary"]["added_paragraphs"], r["summary"]["deleted_paragraphs"],
                r["summary"]["rewritten_paragraphs"]) == (1, 1, 1)

    def test_items_capped(self):
        issues = [{"level": "小改", "type": "AI痕迹-排比", "detail": f"第{i}处排比"}
                  for i in range(MAX_ITEMS_PER_SECTION + 3)]
        r = self._pair(issues, [], a_text="甲。\n乙。")
        assert len(r["panels"][0]["reviews"][0]["improvements"]) == MAX_ITEMS_PER_SECTION


class TestStructuredReview:
    def test_added_is_strength_for_newer_and_missing_for_older(self):
        changes = [
            {"group": "人物卡", "path": "characters[name=丙]", "type": "added",
             "name": "丙", "old": None, "new": {"name": "丙", "desc": "新角色登场"}},
            {"group": "伏笔", "path": "foreshadowing[name=铜片]", "type": "removed",
             "name": "铜片", "old": {"name": "铜片", "text": "铜片来历"}, "new": None},
        ]
        r = review_structured_pair(1, 2, changes, words_map={1: 100, 2: 120})
        v1, v2 = r["panels"]
        # v2（新）：新增人物卡 → 优点；删除伏笔 → 需改进（建议补回）
        v2_crit = next(x for x in v2["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert any("丙" in s["reason"] for s in v2_crit["strengths"])
        assert any("铜片" in i["suggestion"] for i in v2_crit["improvements"])
        # v1（旧）：同一批差异镜像 —— 缺失人物卡、持有伏笔
        v1_crit = next(x for x in v1["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert any("丙" in i["suggestion"] for i in v1_crit["improvements"])
        assert any("铜片" in s["reason"] for s in v1_crit["strengths"])

    def test_text_groups_go_to_proofread(self):
        changes = [
            {"group": "术语表", "path": "terminology[name=铜铃]", "type": "added",
             "name": "铜铃", "old": None, "new": {"name": "铜铃", "text": "雾河铜铃"}},
            {"group": "关键节点", "path": "key_nodes[0]", "type": "added",
             "name": "第3卷：铜铃现世", "old": None, "new": {"name": "第3卷：铜铃现世"}},
        ]
        r = review_structured_pair(1, 2, changes)
        v2 = r["panels"][1]
        proof = next(x for x in v2["reviews"] if x["agent"] == AGENT_PROOFREAD)
        crit = next(x for x in v2["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert [s["aspect"] for s in proof["strengths"]] == ["术语表"]
        assert [s["aspect"] for s in crit["strengths"]] == ["关键节点"]

    def test_empty_entry_flagged(self):
        changes = [{"group": "人物卡", "path": "characters[0]", "type": "added",
                    "name": "无名氏", "old": None, "new": {"name": "", "desc": ""}}]
        r = review_structured_pair(1, 2, changes)
        crit = next(x for x in r["panels"][1]["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert any("空条目" in i["type"] for i in crit["improvements"])
        assert any("补齐" in i["suggestion"] for i in crit["improvements"])

    def test_no_changes_means_empty_with_hint(self):
        r = review_structured_pair(1, 2, [])
        for panel in r["panels"]:
            for rv in panel["reviews"]:
                assert rv["strengths"] == [] and rv["improvements"] == []
                assert rv["empty_hint"]

    def test_words_carried(self):
        r = review_structured_pair(1, 2, [], words_map={1: 111, 2: 222})
        assert [p["words"] for p in r["panels"]] == [111, 222]


# ---------- 路由装配（webapp 层）：只验证取数与组装，口径仍在纯函数 ----------

@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def routed(store, monkeypatch):
    monkeypatch.setattr(pv, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pv, "_find_project", lambda pid: store)
    return store


def _chapter_dict(no: int, body: str) -> dict:
    return {"no": no, "title": f"第{no}章", "content": body}


class TestReviewRouter:
    def test_chapter_review_returns_two_panels(self, store, routed):
        vp = VersionedProduct(store, "chapter")
        vp.commit("1", _chapter_dict(1, "第一段正文内容。\n第二段正文内容。\n第三段收尾内容。"),
                  source="ai", quality={"passed": False, "score": 60, "issue_count": 1,
                                        "issues": [{"level": "大改", "type": "情节-同事件循环重演",
                                                    "detail": "桥上对峙写了两遍"}]})
        vp.commit("1", _chapter_dict(1, "第一段正文内容。\n新写的第二段。\n第三段收尾内容。"),
                  source="rewrite", quality={"passed": True, "score": 92, "issue_count": 0,
                                            "issues": []})
        body = pv.review_product_versions(store.dir.name, "chapter", 1, 2, 1, {"id": 1})
        assert body["kind"] == "chapter" and body["chapter_no"] == 1
        assert [p["v"] for p in body["panels"]] == [1, 2]
        # v1 被点名的情节问题只出现在 v1 的评审视角
        v1_crit = next(x for x in body["panels"][0]["reviews"] if x["agent"] == AGENT_CRITIQUE)
        v2_crit = next(x for x in body["panels"][1]["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert v1_crit["improvements"] and not v2_crit["improvements"]
        # 目标字数来自 config，篇幅类评价才会产出
        assert any(s["aspect"] == "篇幅与信息密度" for s in v2_crit["strengths"])

    def test_outline_review_uses_mirrored_view(self, store, routed):
        vp = VersionedProduct(store, "outline")
        vp.commit("all", {"title": "测试书", "intro": "简介甲",
                          "characters": [{"name": "甲"}]}, source="ai")
        vp.commit("all", {"title": "测试书", "intro": "简介乙",
                          "characters": [{"name": "甲"}, {"name": "丙"}]}, source="ai")
        body = pv.review_product_versions(store.dir.name, "outline", 1, 2, None, {"id": 1})
        assert body["kind"] == "outline" and body["chapter_no"] is None
        v1, v2 = body["panels"]
        v2_crit = next(x for x in v2["reviews"] if x["agent"] == AGENT_CRITIQUE)
        # v2 新增了人物「丙」→ 记为信息增量
        assert any("丙" in s["reason"] for s in v2_crit["strengths"])
        # v1 视角下「丙」是缺失项，给出补回动作
        v1_crit = next(x for x in v1["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert any("丙" in i["suggestion"] for i in v1_crit["improvements"])

    def test_missing_version_404(self, store, routed):
        VersionedProduct(store, "outline").commit("all", {"title": "测试书"}, source="ai")
        with pytest.raises(HTTPException) as ei:
            pv.review_product_versions(store.dir.name, "outline", 1, 99, None, {"id": 1})
        assert ei.value.status_code == 404

    def test_chapter_requires_chapter_no(self, store, routed):
        with pytest.raises(HTTPException) as ei:
            pv.review_product_versions(store.dir.name, "chapter", 1, 2, None, {"id": 1})
        assert ei.value.status_code == 400


# ---------- 路由装配（webapp 层）：只验证取数与组装，口径仍在纯函数 ----------

@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.fixture
def routed(store, monkeypatch):
    monkeypatch.setattr(pv, "_check_project_access", lambda *a, **k: None)
    monkeypatch.setattr(pv, "_find_project", lambda pid: store)
    return store


def _chapter_dict(no: int, body: str) -> dict:
    return {"no": no, "title": f"第{no}章", "content": body}


class TestReviewRouter:
    def test_chapter_review_returns_two_panels(self, store, routed):
        vp = VersionedProduct(store, "chapter")
        vp.commit("1", _chapter_dict(1, "第一段正文内容。\n第二段正文内容。\n第三段收尾内容。"),
                  source="ai", quality={"passed": False, "score": 60, "issue_count": 1,
                                        "issues": [{"level": "大改", "type": "情节-同事件循环重演",
                                                    "detail": "桥上对峙写了两遍"}]})
        vp.commit("1", _chapter_dict(1, "第一段正文内容。\n新写的第二段。\n第三段收尾内容。"),
                  source="rewrite", quality={"passed": True, "score": 92, "issue_count": 0,
                                            "issues": []})
        body = pv.review_product_versions(store.dir.name, "chapter", 1, 2, 1, {"id": 1})
        assert body["kind"] == "chapter" and body["chapter_no"] == 1
        assert [p["v"] for p in body["panels"]] == [1, 2]
        # v1 被点名的情节问题只出现在 v1 的评审视角
        v1_crit = next(x for x in body["panels"][0]["reviews"] if x["agent"] == AGENT_CRITIQUE)
        v2_crit = next(x for x in body["panels"][1]["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert v1_crit["improvements"] and not v2_crit["improvements"]
        # 目标字数来自 config，篇幅类评价才会产出
        assert any(s["aspect"] == "篇幅与信息密度" for s in v2_crit["strengths"])

    def test_outline_review_uses_mirrored_view(self, store, routed):
        vp = VersionedProduct(store, "outline")
        vp.commit("all", {"title": "测试书", "intro": "简介甲",
                          "characters": [{"name": "甲"}]}, source="ai")
        vp.commit("all", {"title": "测试书", "intro": "简介乙",
                          "characters": [{"name": "甲"}, {"name": "丙"}]}, source="ai")
        body = pv.review_product_versions(store.dir.name, "outline", 1, 2, None, {"id": 1})
        assert body["kind"] == "outline" and body["chapter_no"] is None
        v1, v2 = body["panels"]
        v2_crit = next(x for x in v2["reviews"] if x["agent"] == AGENT_CRITIQUE)
        # v2 新增了人物「丙」→ 记为信息增量
        assert any("丙" in s["reason"] for s in v2_crit["strengths"])
        # v1 视角下「丙」是缺失项，给出补回动作
        v1_crit = next(x for x in v1["reviews"] if x["agent"] == AGENT_CRITIQUE)
        assert any("丙" in i["suggestion"] for i in v1_crit["improvements"])

    def test_missing_version_404(self, store, routed):
        VersionedProduct(store, "outline").commit("all", {"title": "测试书"}, source="ai")
        with pytest.raises(HTTPException) as ei:
            pv.review_product_versions(store.dir.name, "outline", 1, 99, None, {"id": 1})
        assert ei.value.status_code == 404

    def test_chapter_requires_chapter_no(self, store, routed):
        with pytest.raises(HTTPException) as ei:
            pv.review_product_versions(store.dir.name, "chapter", 1, 2, None, {"id": 1})
        assert ei.value.status_code == 400


class TestIssueEvidence:
    """evidence 取舍：只认真实原文片段，回落成 detail 的一律不算（防假引用）。"""

    def test_real_evidence_kept(self):
        it = {"type": "自相矛盾-方位", "detail": "前后方位不一致",
              "evidence": "他转身向东跑去，随后又向西追去"}
        assert issue_evidence(it) == "他转身向东跑去，随后又向西追去"

    def test_detail_fallback_not_a_quote(self):
        # 评委 evidence 缺失时回落到 detail：那是问题描述，不是原文片段
        it = {"type": "内容重复", "detail": "与第3章重叠率 45%，疑似大段复用",
              "evidence": "与第3章重叠率 45%，疑似大段复用"}
        assert issue_evidence(it) == ""

    def test_missing_evidence_empty(self):
        assert issue_evidence({"type": "结构-字数偏少", "detail": "字数不足"}) == ""
        assert issue_evidence({}) == ""


class TestIssueEvidence:
    """evidence 取舍：只认真实原文片段，回落成 detail 的一律不算（防假引用）。"""

    def test_real_evidence_kept(self):
        it = {"type": "自相矛盾-方位", "detail": "前后方位不一致",
              "evidence": "他转身向东跑去，随后又向西追去"}
        assert issue_evidence(it) == "他转身向东跑去，随后又向西追去"

    def test_detail_fallback_not_a_quote(self):
        # 评委 evidence 缺失时回落到 detail：那是问题描述，不是原文片段
        it = {"type": "内容重复", "detail": "与第3章重叠率 45%，疑似大段复用",
              "evidence": "与第3章重叠率 45%，疑似大段复用"}
        assert issue_evidence(it) == ""

    def test_missing_evidence_empty(self):
        assert issue_evidence({"type": "结构-字数偏少", "detail": "字数不足"}) == ""
        assert issue_evidence({}) == ""
