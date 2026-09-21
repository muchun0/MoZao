# -*- coding: utf-8 -*-
"""数据迁移工具函数：把数据目录从一处迁移到另一处（复制→校验→删源）。

供根目录 _migrate_data.py（CLI）与 tests/test_paths.py 使用。

安全原则：
1. 先复制，**校验通过后才删除源目录**；校验失败保留源，可修复后重跑；
2. 目标已存在且非空时拒绝覆盖/合并（除非 --force-verify 且校验一致，续传场景）；
3. 迁移建议在服务停止状态下执行（CLI 会检查活动生成任务）。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


class MigrateError(RuntimeError):
    """迁移失败（含原因）。"""


def _file_sha256(path: Path, sample: int = 1024 * 1024) -> str:
    """文件哈希：文件 ≤ 2*sample 字节时全量，否则采样首尾各 sample 字节。"""
    size = path.stat().st_size
    h = hashlib.sha256()
    if size <= 2 * sample:
        h.update(path.read_bytes())
        return h.hexdigest()
    with path.open("rb") as f:
        h.update(f.read(sample))
        f.seek(size - sample)
        h.update(f.read(sample))
    return h.hexdigest()


def verify_dir(src: Path, dst: Path) -> tuple[bool, dict]:
    """校验 dst 与 src 一致性：文件集合、总字节数、逐文件哈希。

    返回 (ok, report)；report["mismatch"] 列出不一致的相对路径（最多 20 条）。
    """
    def _walk(root: Path) -> dict:
        root = Path(root)
        if not root.exists():
            return {}
        return {p.relative_to(root).as_posix(): p
                for p in root.rglob("*") if p.is_file()}

    sfiles = _walk(src)
    dfiles = _walk(dst)
    report = {
        "src_files": len(sfiles),
        "dst_files": len(dfiles),
        "src_bytes": sum(p.stat().st_size for p in sfiles.values()),
        "dst_bytes": sum(p.stat().st_size for p in dfiles.values()),
        "mismatch": [],
    }
    if set(sfiles) != set(dfiles):
        report["mismatch"].append("file set differs")
    for rel, sp in sfiles.items():
        dp = dfiles.get(rel)
        if dp is None:
            continue
        if sp.stat().st_size != dp.stat().st_size or _file_sha256(sp) != _file_sha256(dp):
            report["mismatch"].append(rel)
            if len(report["mismatch"]) >= 20:
                break
    return (not report["mismatch"]), report


def migrate_dir(src: Path, dst: Path, dry_run: bool = False,
                force_verify: bool = False) -> dict:
    """迁移单个目录：复制 → 校验 → 删源。

    - ``dry_run=True``：只返回计划，不产生任何写操作；
    - ``dst`` 已存在且非空且 ``force_verify=False``：抛 ``MigrateError``；
    - ``force_verify=True``：dst 已存在时先校验与源一致，一致则跳过复制直接删源（续传）。
    返回报告 dict（含 src/dst/files/bytes/mode/deleted_src）。
    """
    src, dst = Path(src), Path(dst)
    if not src.exists() or not src.is_dir():
        raise MigrateError(f"源目录不存在或不是目录: {src}")

    files = [p for p in src.rglob("*") if p.is_file()]
    report = {
        "src": str(src),
        "dst": str(dst),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }
    if dry_run:
        report["dry_run"] = True
        report["action"] = f"复制 {src} → {dst}，校验通过后删除源"
        return report

    if dst.exists() and any(dst.iterdir()):
        if not force_verify:
            raise MigrateError(
                f"目标目录已存在且非空（拒绝覆盖/合并）: {dst}。如为中断续传，请用 --force-verify")
        ok, vrep = verify_dir(src, dst)
        if not ok:
            raise MigrateError(f"续传校验失败（目标与源不一致，源已保留）: {vrep['mismatch'][:5]}")
        shutil.rmtree(src)
        report["mode"] = "resume"
        report["deleted_src"] = True
        return report

    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    ok, vrep = verify_dir(src, dst)
    if not ok:
        raise MigrateError(f"迁移校验失败（源已保留，可修复后重跑）: {vrep['mismatch'][:5]}")
    shutil.rmtree(src)
    report["mode"] = "full"
    report["deleted_src"] = True
    return report
