# -*- coding: utf-8 -*-
"""R1「改稿建议可解释、可执行」测试。

覆盖：
- 定位：真实 evidence 优先、对齐到段落、窗口过大/无法定位时拒绝；
- 改写：窗口外文本逐字符不变（只改这一处）；
- 装配层：review.json 完整 issue 对齐（版本快照被截断也能匹配）、新版本落盘并按版本计费。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                          # noqa: E402
from novel_gen.pipeline.quality.issue_rewrite import (            # noqa: E402
    locate_issue_span, rewrite_issue)
from novel_gen.store.project import ProjectStore                  # noqa: E402
from novel_gen.store.schemas import Chapter                       # noqa: E402
from webapp import settle_ledger                                  # noqa: E402
from webapp.chapter_issue_service import (                        # noqa: E402
    apply_issue_rewrite, load_chapter_issues, resolve_issue)

PID = "p2"

_P1 = "天还没亮，墟口外的风就带着一股潮气。"
_P2 = "陆沉在界碑下蹲了一夜，膝盖早就僵了。"
_P3 = "他回头看了一眼界碑，碑上的字在雾里显得模糊。"


def _cfg(**kw) -> NovelConfig:
    cfg = NovelConfig(title="改稿测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    cfg.pitfall_reconcile = False
    cfg.book_memory = False
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def store(tmp_path):
    return ProjectStore.create(tmp_path / PID, _cfg())


def _write_review(store, no: int, issues: list[dict]) -> None:
    path = store.dir / "review.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    data = [rec for rec in data if int(rec.get("chapter", 0)) != no]
    data.append({"chapter": no, "issues": issues})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _mk_llm(replacement: str):
    llm = MagicMock()
    llm.chat.return_value = replacement
    return llm


# ---------- 定位 ----------
def test_locate_by_exact_evidence_aligned_to_para():
    content = f"{_P1}\n\n{_P2}\n\n{_P3}"
    evidence = "碑上的字在雾里显得模糊。"
    span = locate_issue_span(content, {"evidence": evidence})
    assert span is not None
    start, end = span
    # 证据只是段中一句，窗口应对齐到整段，避免拼接处断句
    assert content[start:end] == _P3
    assert content.find(evidence) > start


def test_locate_falls_back_to_pos_when_no_evidence():
    content = f"{_P1}\n\n{_P2}\n\n{_P3}"
    span = locate_issue_span(content, {"pos": "段2", "detail": "与前文重复"})
    assert span is not None
    assert content[span[0]:span[1]] == _P2


def test_locate_narrows_detail_anchor_to_single_para():
    content = f"{_P1}\n\n{_P2}\n\n{_P3}"
    span = locate_issue_span(content, {"detail": "与前文重复：“陆沉在界碑下蹲了一夜”"})
    assert span is not None
    # 句锚点窗口会带 ±200 字邻文，这里必须收窄到命中段，避免连带改邻段
    assert content[span[0]:span[1]] == _P2


def test_locate_rejects_unlocatable_issue():
    content = f"{_P1}\n\n{_P2}\n\n{_P3}"
    assert locate_issue_span(content, {"evidence": "正文里没有这句话"}) is None


def test_locate_rejects_oversized_span():
    content = "一二三四五六七八九十"
    # 证据覆盖 70% 正文：视为定位失准，拒绝而不是悄悄改整章
    assert locate_issue_span(content, {"evidence": "一二三四五六七"}) is None


# ---------- 局部改写 ----------
def test_rewrite_only_replaces_span(store):
    content = f"{_P1}\n\n{_P2}\n\n{_P3}"
    replacement = "他回头看了一眼界碑，碑上“墟”字被风磨平了半边，像一道没愈合的口子。"
    result = rewrite_issue(_cfg(), _mk_llm(replacement), store, 1, content,
                           {"evidence": _P3}, plan_title="第1章", plan_goal="下墟")
    assert result["ok"] is True
    assert result["content"].startswith(f"{_P1}\n\n{_P2}\n\n")
    assert result["content"].endswith(replacement)
    assert result["rewritten_chars"] == len(replacement)
    # 窗口外逐字符不变
    assert result["content"][: result["span"][0]] == content[: result["span"][0]]
    assert result["content"][result["span"][0] + len(replacement):] == content[result["span"][1]:]


def test_rewrite_unlocatable_keeps_original(store):
    result = rewrite_issue(_cfg(), _mk_llm("随便写"), store, 1, f"{_P1}\n\n{_P2}",
                           {"evidence": "不存在的原文"})
    assert result["ok"] is False
    assert result["content"] == ""
    assert "无法在正文中定位" in result["reason"]


def test_rewrite_empty_llm_output_keeps_original(store):
    result = rewrite_issue(_cfg(), _mk_llm(""), store, 1, f"{_P1}\n\n{_P3}",
                           {"evidence": _P3})
    assert result["ok"] is False
    assert "原文未改动" in result["reason"]


# ---------- 装配层：issue 对齐 + 落盘计费 ----------
def test_resolve_issue_matches_truncated_snapshot(store):
    full_detail = "与前文重复率偏高：两次都写了“界碑下的风带着潮气”，建议换一个感官描写角度，不要再写风。"
    _write_review(store, 1, [{"level": "中改", "type": "重复", "detail": full_detail,
                              "evidence": _P1, "fix_suggestion": "改为从声音或触觉切入。"}])
    brief = {"type": "重复", "detail": full_detail[:120], "evidence": "",
             "version": 1}
    issue = resolve_issue(store, 1, brief)
    assert issue is not None
    assert issue["fix_suggestion"] == "改为从声音或触觉切入。"


def test_resolve_issue_falls_back_to_payload(store):
    issue = resolve_issue(store, 1, {"type": "节奏", "detail": "情节推进过快"})
    assert issue is not None
    assert issue["detail"] == "情节推进过快"


def test_resolve_issue_none_when_nothing_identifiable(store):
    assert resolve_issue(store, 1, {}) is None


@pytest.fixture
def wallet(monkeypatch):
    """内存钱包（与 test_settle_per_version 同口径）：不连 MySQL。"""
    state = {"balance": 100.0}

    def _charge(uid, delta, note):
        state["balance"] = round(state["balance"] + delta, 4)

    monkeypatch.setattr(settle_ledger.db, "project_owner", lambda pid: 1)
    monkeypatch.setattr(settle_ledger.db, "project_discount", lambda pid: 1.0)
    monkeypatch.setattr(settle_ledger.db, "get_user", lambda uid: {"id": uid, "balance": state["balance"]})
    monkeypatch.setattr(settle_ledger.db, "change_balance", _charge)
    monkeypatch.setattr(settle_ledger.db, "update_project_price", lambda pid, price: None)
    monkeypatch.setattr(settle_ledger, "_grant_commission", lambda *a, **k: None)
    return state


def test_apply_rewrite_saves_new_version_and_charges(store, wallet):
    content = f"{_P1}\n\n{_P2}\n\n{_P3}"
    store.save_chapter(Chapter(no=1, title="第1章", content=content, summary="摘要"))
    _write_review(store, 1, [{"level": "中改", "type": "模糊",
                              "detail": "意象重复，画面感弱", "evidence": _P3,
                              "fix_suggestion": "换成更具体的声音细节"}])
    replacement = "他回头看了一眼界碑，碑面落满湿雾，指腹一擦，石粉混着水汽黏在手上。"
    pipeline = MagicMock()
    pipeline.cfg = _cfg()
    pipeline.llm = _mk_llm(replacement)
    pipeline.trace = None
    pipeline.guard = None

    res = apply_issue_rewrite(store, pipeline, store.load_chapter(1), 1,
                              {"type": "模糊", "detail": "意象重复", "evidence": _P3, "version": 1},
                              billing_mode="platform", project_id=PID)

    assert res["version"] == 2 and res["from_version"] == 1
    assert res["rewritten_chars"] == len(replacement)
    assert res["charged"] > 0
    current = store.load_chapter(1).content
    assert current.startswith(f"{_P1}\n\n{_P2}\n\n") and current.endswith(replacement)
    entries = json.loads((store.dir / "settle.json").read_text(encoding="utf-8"))["entries"]
    assert "chapter:1:v2" in entries
    assert load_chapter_issues(store, 1)[0]["evidence"] == _P3
