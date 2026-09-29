# -*- coding: utf-8 -*-
"""成果整合（report.py）：把扩展后的 World / Outline + 校验报告整合为 4 份结构化文档。

REQ-多Agent协作分层优化（阶段E）：落盘 projects/<id>/design/，供前端/作者查看。
- core-settings.md     《核心设定册》：主题/三层冲突/主角弧光/配角动机/世界观/金手指五要素/设定禁忌
- skeleton-outline.md  《全本骨架大纲》：三幕结构/分卷大纲/关键节点时间线
- pacing-manual.md     《节奏执行手册》：分卷节奏梯次/伏笔回收总表
- quality-report.md    《质量校验报告》：问题分级清单/风险点/优化建议

纯字符串渲染，无第三方依赖；失败可降级跳过（不阻塞主流程）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..store.schemas import Outline, World
from .issue_triage import IssueItem, SEVERITY_ORDER


def _h1(t: str) -> str:
    return f"# {t}\n\n"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "（无）\n\n"
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) or "-" for c in r) + " |")
    return "\n".join(out) + "\n\n"


# ---------- 《核心设定册》 ----------
def render_core_settings(world: World) -> str:
    s = _h1("《核心设定册》")
    s += _h1("一、核心主题与三层冲突矩阵")
    s += f"- **核心主题命题**：{world.theme_proposition or '（未设定）'}\n\n"
    s += _table(["层级", "核心冲突"], [[c.level, c.description] for c in world.conflict_matrix])
    s += _h1("二、主角弧光卡")
    arc = world.protagonist_arc
    if arc is not None:
        s += f"- **开篇信念**：{arc.belief or '（未设定）'}\n"
        s += f"- **核心缺陷**：{arc.flaw or '（未设定）'}\n"
        s += f"- **关键转变节点**：{'；'.join(arc.change_nodes) if arc.change_nodes else '（未设定）'}\n"
        s += f"- **最终认知**：{arc.final_insight or '（未设定）'}\n\n"
    else:
        s += "（未设定）\n\n"
    s += _h1("三、核心配角动机")
    s += _table(["姓名", "核心目标", "行为逻辑", "与主角关联"],
                [[m.name, m.goal, m.behavior_logic, m.link_to_protagonist] for m in world.character_motives])
    s += _h1("四、世界观核心法则")
    s += (world.world_view or "（未设定）") + "\n\n"
    s += _h1("五、金手指规则说明书（五要素）")
    gf = world.goldfinger_rules
    if gf is not None:
        s += _table(["要素", "规则"], [
            ["触发条件", gf.trigger], ["能力上限", gf.upper_limit],
            ["能力下限", gf.lower_limit], ["使用代价", gf.cost], ["成长路径", gf.growth_path]])
    else:
        s += "（未设定）\n\n"
    s += _h1("六、设定禁忌清单")
    s += "\n".join(f"- {t}" for t in world.setting_taboos) or "（无）"
    s += "\n"
    return s


# ---------- 《全本骨架大纲》 ----------
def render_skeleton(outline: Outline) -> str:
    s = _h1("《全本骨架大纲》")
    s += f"**书名**：{outline.title or '（未设定）'}\n\n"
    s += f"**核心冲突**：{outline.core_conflict or '（未设定）'}\n\n"
    s += f"**主线脉络**：{outline.main_line or '（未设定）'}\n\n"
    s += _h1("一、分卷大纲")
    s += _table(["卷", "卷名", "章区间", "主要矛盾", "卷末高潮", "人物成长"],
                [[v.no, v.name, f"{v.start_ch}-{v.end_ch}", v.main_conflict,
                  v.climax, v.character_growth] for v in outline.volumes])
    s += _h1("二、关键节点时间线")
    s += _table(["节点类型", "章号", "说明"],
                [[k.kind, k.chapter_no, k.description] for k in outline.key_nodes])
    return s


# ---------- 《节奏执行手册》 ----------
def render_pacing_manual(outline: Outline, world: World) -> str:
    s = _h1("《节奏执行手册》")
    s += _h1("一、分卷节奏梯次")
    s += _table(["卷号", "爽点密度", "高潮章", "过渡区间"],
                [[v.volume_no, v.payoff_density, v.climax_ch, v.transition_range]
                 for v in outline.volume_pacing])
    s += _h1("二、伏笔回收总表（三级）")
    rows = []
    for f in world.foreshadowing:
        lvl = {"small": "章内", "medium": "卷内", "large": "全篇"}.get(f.level, f.level)
        rows.append([f.name or f.id, lvl, f.buried_ch or "-", f.reveal_ch if f.reveal_ch else "⚠未回收", f.content])
    s += _table(["伏笔", "级别", "埋点章", "回收章", "内容"], rows)
    return s


# ---------- 《质量校验报告》 ----------
def render_quality(issues: list[IssueItem], arbitrations: list[dict] | None = None) -> str:
    s = _h1("《质量校验报告》")
    ordered = sorted(issues, key=lambda it: SEVERITY_ORDER.get(it.severity, 2))
    tag = {"fatal": "致命", "major": "严重", "minor": "一般"}
    s += _h1("一、问题分级清单")
    s += _table(["级别", "范围", "问题", "整改建议", "章节"],
                [[tag.get(i.severity, i.severity), i.scope, i.desc, i.suggestion,
                  i.chapter_no if i.chapter_no else "-"] for i in ordered]) if ordered else "（未发现问题）\n"
    if not ordered:
        s = s[: s.rfind("（未发现问题）")] + "（未发现问题）\n\n"
    s += _h1("二、总控仲裁记录")
    if arbitrations:
        for a in arbitrations:
            s += f"- **阶段 {a.get('stage')}**：{a.get('note', '')}\n"
            for it in a.get("fatal", []):
                s += f"  - 致命：{it.get('desc', '')}\n"
    else:
        s += "（无仲裁记录）\n"
    s += _h1("三、风险与建议")
    s += "1. 金手指规则模糊 / 主角工具化 / 支线偏离主线 / 伏笔只埋不收 为最高优先级风险，应在对应生产阶段修复。\n"
    s += "2. 建议在进入正文写作前，先收敛底层锚点与骨架的所有严重（major）及以上问题。\n"
    return s


def build_reports(world: World, outline: Outline,
                  issues: list[IssueItem] | None = None,
                  arbitrations: list[dict] | None = None) -> dict[str, str]:
    """生成 4 份文档内容：{文件名: markdown}。纯函数，可单测。"""
    return {
        "core-settings.md": render_core_settings(world),
        "skeleton-outline.md": render_skeleton(outline),
        "pacing-manual.md": render_pacing_manual(outline, world),
        "quality-report.md": render_quality(issues or [], arbitrations or []),
    }


def write_reports(store, world: World, outline: Outline,
                  issues: list[IssueItem] | None = None,
                  arbitrations: list[dict] | None = None) -> dict[str, Path]:
    """落盘到 projects/<id>/design/。返回 {文件名: 路径}；失败降级（返回已写部分）。"""
    out: dict[str, Path] = {}
    try:
        docs = build_reports(world, outline, issues, arbitrations)
        d = Path(getattr(store, "dir")) / "design"
        d.mkdir(parents=True, exist_ok=True)
        for name, content in docs.items():
            p = d / name
            p.write_text(content, encoding="utf-8")
            out[name] = p
    except Exception:
        pass
    return out
