# -*- coding: utf-8 -*-
"""投影一致性体检命令行（REQ-借鉴webnovel-章节提交与投影一致性，B）。

对标 WebNovel Writer 的 doctor：扫描一个项目目录的各派生视图（账本），
输出"哪个账本覆盖到第几章 / 是否落后于当前已写章号"，用于定位"哪个账本没跟上"。

用法（在项目根下执行）：
    python tools/projection_check.py <项目目录> [--up-to N] [--view name ...]

    <项目目录>  必填：novel_gen 项目目录（含 chapters/、各账本 json）。
    --up-to N   指定"当前章号"基准（默认取已写最大章节号）。
    --view      只体检指定视图（可多次），默认全部 PROJECTION_VIEWS。

退出码：存在 lagging 视图返回 2，否则 0（方便脚本判断）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本从项目根导入 novel_gen（脚本位于 tools/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel_gen.store.project import ProjectStore          # noqa: E402
from novel_gen.pipeline.commit import check_projection, PROJECTION_VIEWS  # noqa: E402

_STATUS_CN = {
    "synced": "✓ 同步",
    "lagging": "⚠ 落后",
    "ahead": "◎ 超前",
    "unknown": "… 未知(无法推断章号)",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="投影一致性体检：定位哪个账本落后于当前章号")
    ap.add_argument("project", help="novel_gen 项目目录")
    ap.add_argument("--up-to", type=int, default=None, help="当前章号基准（默认=已写最大章号）")
    ap.add_argument("--view", action="append", default=None,
                    help="只体检指定视图（可多次）；默认全部")
    args = ap.parse_args()

    store = ProjectStore.open(args.project)
    views = [v for v in args.view if v] if args.view else PROJECTION_VIEWS

    print(f"项目：{store.project_id}")
    print(f"基准章号：{args.up_to if args.up_to is not None else '已写最大章节'}")
    print("-" * 44)
    rows = [r for r in check_projection(store, up_to_chapter=args.up_to)
            if r["view"] in views]
    lagging = 0
    for r in rows:
        cn = _STATUS_CN.get(r["status"], r["status"])
        covered = r["covered_ch"] if r["covered_ch"] is not None else "-"
        flag = "  <<< 落后" if r["status"] == "lagging" else ""
        print(f"{r['view']:<20} 覆盖到第 {covered:<5} 章  {cn}{flag}")
        if r["status"] == "lagging":
            lagging += 1
    print("-" * 44)
    if lagging:
        print(f"发现 {lagging} 个视图落后：可运行生成 resume 补写，或检查对应账本写入路径。")
        return 2
    print("全部视图与基准同步（或无需跟踪）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
