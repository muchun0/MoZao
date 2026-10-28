# -*- coding: utf-8 -*-
"""结算三方对账 CLI（P0 资金链路，TECH-Agent用户化与版本化 §14）。

对账三方：
1. 账本自身：`settle.json` 的 `billed_price` 与 `entries` 中 `charged` 合计是否一致；
2. 项目累计金额：MySQL `projects.price` 是否与账本 `billed_price` 一致；
3. 章节版本：版本库里 `source in {ai, rewrite}`（已计费来源）的版本是否都在账本有条目。

只读、不改数据、不补扣；发现缺口只告警列出（漏扣按决策「宁可漏扣不重扣」人工补偿）。

用法（项目根下执行）：
    python tools/settle_audit.py --project <项目ID>

退出码：0 一致；1 存在差异（便于脚本/发版检查）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本从项目根导入（脚本位于 tools/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel_gen.store.project import ProjectStore            # noqa: E402
from novel_gen.utils.paths import get_projects_dir          # noqa: E402
from webapp import settle_ledger                            # noqa: E402

# 需要计费（应在账本留条目）的章节版本来源；legacy/user 不计费
_BILLED_SOURCES = {"ai", "rewrite"}


def _issue(msg: str) -> None:
    print(f"  ✗ {msg}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _audit_ledger(ledger: dict) -> list[str]:
    """账本内部一致性：entries 合计 vs billed_price。"""
    entries = ledger.get("entries") or {}
    charged_sum = round(sum(float((e or {}).get("charged") or 0.0) for e in entries.values()), 2)
    billed = round(float(ledger.get("billed_price") or 0.0), 2)
    if abs(charged_sum - billed) > 0.005:
        _issue(f"账本内部不平：entries 合计 {charged_sum:.2f} ≠ billed_price {billed:.2f}")
        return [f"ledger internal: {charged_sum:.2f} != {billed:.2f}"]
    _ok(f"账本条目 {len(entries)} 条，合计扣费 {charged_sum:.2f} 元")
    return []


def _audit_project_price(project_id: str, billed: float) -> list[str]:
    """账本 vs MySQL 项目累计金额；数据库不可用只告警不判失败。"""
    try:
        from webapp import db
        db_price = round(float(db.project_price(project_id) or 0.0), 2)
    except Exception as exc:  # 离线/无 MySQL：不阻断对账
        print(f"  … 跳过 MySQL 比对（{type(exc).__name__}: {exc}）")
        return []
    if abs(db_price - billed) > 0.005:
        _issue(f"项目累计金额不一致：DB {db_price:.2f} ≠ 账本 {billed:.2f}")
        return [f"project_price {db_price:.2f} ≠ billed {billed:.2f}"]
    _ok(f"MySQL 项目累计金额一致：{db_price:.2f} 元")
    return []


def _audit_chapter_versions(store: ProjectStore, entries: dict) -> list[str]:
    """章节版本覆盖率：应有条目的版本是否缺失（缺 = 漏扣，只告警列出）。"""
    missing: list[str] = []
    total = 0
    for no, rec in (store.load_chapter_versions() or {}).items():
        if not isinstance(rec, dict):
            continue
        history = {h.get("v"): h for h in (rec.get("history") or []) if isinstance(h, dict)}
        for v in rec.get("versions") or []:
            source = (history.get(v) or {}).get("source") or "ai"
            if source not in _BILLED_SOURCES:
                continue
            total += 1
            key = f"chapter:{no}:v{v}"
            if key not in entries:
                missing.append(key)
    if missing:
        _issue(f"章节版本漏账 {len(missing)}/{total} 条（人工补偿，不自动补扣）：{', '.join(missing[:10])}"
               + (" …" if len(missing) > 10 else ""))
    else:
        _ok(f"章节已计费版本 {total} 条全部入账")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description="结算三方对账：账本 / 项目累计金额 / 章节版本覆盖率")
    ap.add_argument("--project", required=True, help="项目 ID（projects 目录下的项目目录名）")
    args = ap.parse_args()

    project_dir = get_projects_dir() / args.project
    if not project_dir.exists():
        print(f"项目不存在: {project_dir}")
        return 1

    store = ProjectStore.open(project_dir)
    raw = settle_ledger.read_raw(store)
    if raw is None:
        print(f"  ⚠ 无结算账本（未结算过或旧版本项目）：{args.project}")
        return 0
    ledger = settle_ledger.load_ledger(store)
    billed = round(float(ledger.get("billed_price") or 0.0), 2)

    print(f"对账项目: {args.project}")
    problems: list[str] = []
    problems += _audit_ledger(ledger)
    problems += _audit_project_price(args.project, billed)
    problems += _audit_chapter_versions(store, ledger.get("entries") or {})
    if ledger.get("legacy_billed_price"):
        print(f"  … 含 v1 迁移基线 legacy_billed_price={float(ledger['legacy_billed_price']):.2f} 元"
              "（迁移不重扣，仅展示）")

    print("结果: " + ("一致 ✓" if not problems else f"存在 {len(problems)} 项差异 ✗"))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
