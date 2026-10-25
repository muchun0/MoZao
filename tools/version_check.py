# -*- coding: utf-8 -*-
"""版本库对账 CLI（TECH-Agent用户化与版本化 §3.2，阶段 1A）。

检查三类产物（chapter/outline/world）版本索引 ↔ 版本文件一致性：
1. orphan_files：盘上存在但索引未登记的版本文件（如手工写入、索引写失败残留）；
2. missing_files：索引登记但盘上缺失的版本文件（如误删、淘汰删除异常）；
3. selected_mismatch：索引 selected 未指向任何有效版本；
4. chapter 补充：盘上存在版本存档目录（chapters/NNN/）但索引完全无该章记录。

只读、不改数据；发现差异只列出告警（修复走人工，淘汰/清理由门面 `_prune` 负责）。

用法（项目根下执行）：
    python tools/version_check.py --project <项目ID>
    python tools/version_check.py --all          # 对账 projects 目录下全部项目

退出码：0 全部一致；1 存在差异（便于脚本/发版检查）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本从项目根导入（脚本位于 tools/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel_gen.store.project import ProjectStore          # noqa: E402
from novel_gen.store.versioned import VersionedProduct    # noqa: E402
from novel_gen.utils.paths import get_projects_dir        # noqa: E402

_KINDS = ("chapter", "outline", "world")


def check_project(store: ProjectStore) -> list[str]:
    """对账单个项目，返回差异描述列表（空 = 一致）。"""
    problems: list[str] = []
    for kind in _KINDS:
        report = VersionedProduct(store, kind).reconcile()
        for item in report["orphan_files"]:
            problems.append(f"{kind} orphan: {item}")
            print(f"  ✗ {kind} 孤儿文件（索引未登记）: {item}")
        for item in report["missing_files"]:
            problems.append(f"{kind} missing: {item}")
            print(f"  ✗ {kind} 文件缺失（索引已登记）: {item}")
        if report["selected_mismatch"]:
            problems.append(f"{kind} selected_mismatch")
            print(f"  ✗ {kind} selected 未指向有效版本")
        if not any(report.values()):
            print(f"  ✓ {kind} 索引与文件一致")
    problems += _check_chapter_orphan_dirs(store)
    return problems


def _check_chapter_orphan_dirs(store: ProjectStore) -> list[str]:
    """盘上存在 chapters/NNN/ 存档目录但索引无该章记录（索引整体丢失的极端场景）。"""
    problems: list[str] = []
    if not store.chapters_dir.is_dir():
        return problems
    index_keys = set((store.load_chapter_versions() or {}).keys())
    for p in sorted(store.chapters_dir.iterdir()):
        if not p.is_dir():
            continue
        try:
            key = str(int(p.name))  # 目录名 "001" → 索引键 "1"
        except ValueError:
            continue
        if key not in index_keys and list(p.glob("v*.json")):
            problems.append(f"chapter orphan_dir: {p.name}")
            print(f"  ✗ chapter 存档目录无索引记录: chapters/{p.name}/")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="版本库对账：索引 ↔ 版本文件一致性")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", help="项目 ID（projects 目录下的项目目录名）")
    g.add_argument("--all", action="store_true", help="对账 projects 目录下全部项目")
    args = ap.parse_args(argv)

    root = get_projects_dir()
    if args.all:
        dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    else:
        dirs = [root / args.project]
    if not dirs:
        print("无项目可对账")
        return 0

    all_problems: list[str] = []
    for d in dirs:
        if not d.exists():
            print(f"项目不存在: {d}")
            all_problems.append(f"missing project: {d.name}")
            continue
        print(f"对账项目: {d.name}")
        all_problems += check_project(ProjectStore.open(d))

    print("结果: " + ("全部一致 ✓" if not all_problems else f"存在 {len(all_problems)} 项差异 ✗"))
    return 0 if not all_problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
