# -*- coding: utf-8 -*-
"""数据目录隔离（DATA_DIR）测试。

覆盖：
- 路径解析（TC-P）：默认回退 / 环境变量覆盖 / 相对路径 / 空值 / ~ 展开 / 纯函数 / 循环导入 / 幂等
- 数据落点（TC-I）：建书 / 导出 / 日志随 DATA_DIR 变化；未配置时回退
- 迁移工具（TC-M）：dry-run / 完整性 / 防覆盖 / 校验失败保留源 / 续传

说明：
- 用例通过 autouse fixture 阻断 paths 从 .env 重新注入 DATA_DIR，
  使测试完全受控于 os.environ（真实 .env 的 DATA_DIR 不影响本文件用例）；
- 依赖 MySQL 模块初始化的用例沿用现有 skipif 约定（MySQL 不可用时跳过）。
"""
from __future__ import annotations

import importlib
import logging
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.utils import paths
from novel_gen.utils.migrate import MigrateError, migrate_dir


# ---------- 公共夹具 ----------

@pytest.fixture(autouse=True)
def _block_env_file_loading(monkeypatch):
    """阻断 paths 从 .env 重新注入，使本文件用例只受 os.environ 控制。"""
    monkeypatch.setattr(paths, "_env_loaded", True)


def _db_ok() -> bool:
    try:
        from webapp import db
        with db._connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


_NEED_DB = pytest.mark.skipif(not _db_ok(), reason="本机 MySQL 不可用")


# ---------- TC-P 路径解析 ----------

def test_p01_default_fallback(monkeypatch):
    """未配置 DATA_DIR：全部回退代码根（兼容红线）。"""
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert paths.get_data_dir() == paths.get_base_dir()
    assert paths.get_projects_dir() == paths.get_base_dir() / "projects"
    assert paths.get_output_dir() == paths.get_base_dir() / "output"
    assert paths.get_logs_dir() == paths.get_base_dir() / "logs"


def test_p02_env_override(monkeypatch, tmp_path):
    """配置 DATA_DIR：派生目录全部落在配置根下。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert paths.get_projects_dir() == tmp_path / "projects"
    assert paths.get_output_dir() == tmp_path / "output"
    assert paths.get_logs_dir() == tmp_path / "logs"


def test_p03_relative_path_against_base(monkeypatch, tmp_path):
    """相对路径基于代码根解析，不依赖当前工作目录。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", "data")
    assert paths.get_data_dir() == paths.get_base_dir() / "data"


def test_p04_empty_and_blank(monkeypatch):
    """空值/空白视为未设置，回退代码根。"""
    for v in ("", "   "):
        monkeypatch.setenv("DATA_DIR", v)
        assert paths.get_data_dir() == paths.get_base_dir()


def test_p05_tilde_expansion(monkeypatch):
    """~ 展开为用户主目录。"""
    monkeypatch.setenv("DATA_DIR", "~/novel_data")
    assert paths.get_data_dir() == Path.home() / "novel_data"


def test_p06_pure_function_no_side_effect(monkeypatch, tmp_path):
    """路径函数纯求值：结果确定且不隐式创建目录。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert paths.get_projects_dir() == paths.get_projects_dir()
    assert not paths.get_projects_dir().exists()


def test_p07_no_cycle_import():
    """webapp 与 novel_gen 互引无循环导入。"""
    import webapp.core  # noqa: F401
    import webapp.logger  # noqa: F401
    import webapp.server  # noqa: F401
    from novel_gen.pipeline.manager import NovelPipeline  # noqa: F401


def test_p08_env_loading_idempotent(monkeypatch, tmp_path):
    """load_env_once 幂等；系统环境变量优先于 .env。"""
    monkeypatch.setattr(paths, "_env_loaded", False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    paths.load_env_once()
    paths.load_env_once()  # 第二次调用无副作用
    assert paths.get_data_dir() == tmp_path
    assert paths._env_loaded is True


# ---------- TC-I 数据落点 ----------

@_NEED_DB
def test_i01_project_created_under_configured_dir(monkeypatch, tmp_path):
    """建书落点在配置的 PROJECTS_DIR，代码根无新增。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cfg = NovelConfig(title=f"落点测试{time.strftime('%H%M%S')}", genre="玄幻", total_words=10000)
    store = ProjectStore.create(paths.get_projects_dir(), cfg)
    try:
        assert store.dir.parent == paths.get_projects_dir()
        assert (store.dir / "config.json").exists()
        assert not (paths.get_base_dir() / "projects" / store.dir.name).exists()
    finally:
        shutil.rmtree(store.dir, ignore_errors=True)


@_NEED_DB
def test_i02_export_under_configured_dir(monkeypatch, tmp_path):
    """导出 txt 落点在配置的 OUTPUT_DIR，项目内副本保留，代码根无新增。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cfg = NovelConfig(title=f"导出测试{time.strftime('%H%M%S')}", genre="玄幻", total_words=10000)
    store = ProjectStore.create(paths.get_projects_dir(), cfg)
    try:
        store.save_chapter(Chapter(no=1, title="第一章", content="测试内容" * 100))
        out = store.export_txt()
        assert out.parent == paths.get_output_dir()
        assert out.exists()
        assert (store.dir / out.name).exists()  # 项目内副本保留（自包含）
        assert not (paths.get_base_dir() / "output" / out.name).exists()
    finally:
        shutil.rmtree(store.dir, ignore_errors=True)


@_NEED_DB
def test_i03_logs_under_configured_dir(monkeypatch, tmp_path):
    """日志目录随 DATA_DIR：重载模块后 LOG_FILE 指向配置目录并真实写入。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import webapp.logger as lg
    importlib.reload(lg)
    try:
        assert lg.LOGS_DIR == tmp_path / "logs"
        assert lg.LOG_FILE == tmp_path / "logs" / "app.log"
        lg.setup_logging()
        lg.get_logger("paths_test").info("data-dir-test-%s", time.time())
        assert lg.LOG_FILE.exists()
    finally:
        # 清理测试写入的 file handler，避免污染后续日志
        root = logging.getLogger()
        root.handlers = [h for h in root.handlers
                         if not (isinstance(h, logging.FileHandler)
                                 and Path(getattr(h, "baseFilename", "")) == lg.LOG_FILE)]


@_NEED_DB
def test_i05_unchanged_without_config(monkeypatch):
    """未配置 DATA_DIR：重载 logger 后 LOGS_DIR 回退代码根。"""
    monkeypatch.delenv("DATA_DIR", raising=False)
    import webapp.logger as lg
    importlib.reload(lg)
    try:
        assert lg.LOGS_DIR == paths.get_base_dir() / "logs"
    finally:
        root = logging.getLogger()
        root.handlers = [h for h in root.handlers
                         if not (isinstance(h, logging.FileHandler)
                                 and Path(getattr(h, "baseFilename", "")) == lg.LOG_FILE)]


# ---------- TC-M 迁移工具 ----------

def _make_tree(root: Path, files: dict[str, bytes]) -> None:
    """按 {相对路径: 内容} 构造目录树。"""
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def test_m01_dry_run_no_write(tmp_path):
    """dry-run：只返回计划，不产生任何写操作。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_tree(src, {"a.txt": b"hello", "sub/b.txt": b"world"})
    res = migrate_dir(src, dst, dry_run=True)
    assert res["dry_run"] is True
    assert res["files"] == 2
    assert not dst.exists()
    assert (src / "a.txt").exists()
    assert (src / "sub" / "b.txt").exists()


def test_m02_full_migrate_integrity(tmp_path):
    """正常迁移：文件数/字节/内容一致，源被删除。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_tree(src, {
        "a.txt": b"hello world",
        "bin.dat": bytes(range(256)) * 100,          # 二进制
        "sub/c.txt": "你好，数据迁移".encode("utf-8"),
    })
    (src / "empty_dir").mkdir()
    res = migrate_dir(src, dst)
    assert res["deleted_src"] is True
    assert res["mode"] == "full"
    assert not src.exists()
    assert (dst / "a.txt").read_bytes() == b"hello world"
    assert (dst / "bin.dat").read_bytes() == bytes(range(256)) * 100
    assert (dst / "sub" / "c.txt").read_bytes() == "你好，数据迁移".encode("utf-8")
    assert (dst / "empty_dir").is_dir()


def test_m03_refuse_existing_target(tmp_path):
    """目标已存在且非空：拒绝覆盖/合并，源与目标均保持原样。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_tree(src, {"a.txt": b"new"})
    _make_tree(dst, {"a.txt": b"old"})
    with pytest.raises(MigrateError):
        migrate_dir(src, dst)
    assert (dst / "a.txt").read_bytes() == b"old"   # 目标未被改动
    assert (src / "a.txt").read_bytes() == b"new"   # 源保留


def test_m04_verify_fail_keeps_src_then_resume(tmp_path):
    """校验失败保留源；修复后 --force-verify 续传成功。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_tree(src, {"a.txt": b"AAA", "b.txt": b"BBB"})
    migrate_dir(src, dst)   # 第一次完整迁移（src 已删）

    # 模拟「复制完成但未删源」的中断：重建源，并破坏目标（缺 b.txt）
    _make_tree(src, {"a.txt": b"AAA", "b.txt": b"BBB"})
    (dst / "b.txt").unlink()
    with pytest.raises(MigrateError):
        migrate_dir(src, dst)   # 目标已存在且校验不一致 → 拒绝，源保留
    assert src.exists()
    assert (dst / "b.txt").exists() is False

    # 修复目标后 force_verify 续传：跳过复制，直接删源
    (dst / "b.txt").write_bytes(b"BBB")
    res = migrate_dir(src, dst, force_verify=True)
    assert res["deleted_src"] is True
    assert res["mode"] == "resume"
    assert not src.exists()
