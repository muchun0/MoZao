# -*- coding: utf-8 -*-
"""踩坑账本注入对账体检命令行（REQ-踩坑账本注入对账 · T04）。

只读扫描一个项目目录下的 pitfall_reconcile.jsonl，输出「账本 Top-N 是否真的进了 prompt」
的命中率汇总与异常明细；存在 L3（本该注入的一条都没进 prompt）记录时退出码 3，
与既有 settle_audit.py=1 / version_check.py=1 / projection_check.py=2 区分，便于 CI 判别。

用法（在项目根下执行）：
    python tools/pitfall_reconcile_check.py <项目目录> [--path path_id ...]

    <项目目录>  必填：novel_gen 项目目录（含 pitfall_reconcile.jsonl）。
    --path       只统计指定 path_id（可多次），默认全部。

退出码：存在 L3 → 3；存在口径不一致（params/channel_conflict）→ 退出码仍 0，
        但单独列出「口径不一致」统计行（L3 才是阻断级信号）；无 L3 → 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel_gen.store.project import ProjectStore  # noqa: E402


def _load_jsonl(store: ProjectStore) -> list[dict]:
    try:
        return store.load_pitfall_reconcile()
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="踩坑账本注入对账体检：定位账本→注入 命中失效")
    ap.add_argument("project", help="novel_gen 项目目录")
    ap.add_argument("--path", action="append", default=None,
                    help="只统计指定 path_id（可多次）；默认全部")
    args = ap.parse_args()

    store = ProjectStore.open(args.project)
    records = _load_jsonl(store)
    if args.path:
        keep = {p for p in args.path if p}
        records = [r for r in records if r.get("path_id") in keep]

    print(f"项目：{store.project_id}")
    print(f"对账记录总数：{len(records)}")
    print("-" * 56)

    if not records:
        print("（无对账记录，可能本书尚未生成或开关关闭）")
        return 0

    # 按 path_id 聚合
    by_path: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_path[r.get("path_id")].append(r)

    level_counter = Counter()
    l3_records: list[dict] = []
    conflict_records: list[dict] = []
    for pid, recs in by_path.items():
        lv = Counter(r.get("level") for r in recs)
        level_counter.update(lv)
        info = lv.get("info", 0)
        warn = lv.get("warning", 0)
        err = lv.get("error", 0)
        unwired = lv.get("unwired", 0)
        total = len(recs)
        hit_rate = (info / total * 100) if total else 0
        print(f"[{pid}] 记录 {total} 条 | 全命中(info) {info} | 警告(warning) {warn} "
              f"| 错误(error) {err} | 未接线(unwired) {unwired} | 命中率 {hit_rate:.0f}%")
        for r in recs:
            if r.get("level") == "error":
                l3_records.append(r)
            if r.get("params_conflict") or r.get("channel_conflict"):
                conflict_records.append(r)

    # 未命中 / 重复 明细 Top
    miss_counter: Counter = Counter()
    dup_counter: Counter = Counter()
    for r in records:
        for m in (r.get("missing") or []):
            miss_counter[m] += 1
        for d in (r.get("duplicated") or []):
            dup_counter[d.get("pattern")] += d.get("times", 0)
    if miss_counter:
        print("-" * 56)
        print("未命中 pattern Top（按出现次数）：")
        for pat, n in miss_counter.most_common(10):
            print(f"  - {pat}  ×{n}")
    if dup_counter:
        print("-" * 56)
        print("重复命中 pattern Top（按累计次数）：")
        for pat, n in dup_counter.most_common(10):
            print(f"  - {pat}  ×{n}")

    # 口径不一致（独立统计行）
    print("-" * 56)
    if conflict_records:
        pc = sum(1 for r in conflict_records if r.get("params_conflict"))
        cc = sum(1 for r in conflict_records if r.get("channel_conflict"))
        print(f"口径不一致：共 {len(conflict_records)} 条（params_conflict={pc}，channel_conflict={cc}）")
        for r in conflict_records[:10]:
            print(f"  - {r.get('path_id')} ch{r.get('chapter_no')} "
                  f"call={r.get('call_params')} expected={r.get('expected_params')}")
    else:
        print("口径不一致：无（params_conflict / channel_conflict 全为 false）")

    # L3 清单
    print("-" * 56)
    if l3_records:
        chs = sorted({r.get("chapter_no") for r in l3_records})
        print(f"L3 错误：共 {len(l3_records)} 条，涉及章号 {chs}")
        for r in l3_records[:20]:
            print(f"  - {r.get('path_id')} ch{r.get('chapter_no')} "
                  f"expected_n={r.get('expected_n')} actual_distinct={r.get('actual_distinct')} "
                  f"streak={r.get('streak')}")
        print("退出码：3（存在 L3，账本写了但一条都没注入）")
        return 3
    else:
        print("L3 错误：无。退出码：0")
        return 0


if __name__ == "__main__":
    sys.exit(main())
