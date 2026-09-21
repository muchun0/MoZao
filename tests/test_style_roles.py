# -*- coding: utf-8 -*-
"""写作风格角色（Style Role）数据层测试。

覆盖：创建/列表/读取/更新/删除、技能挂载/卸载（admin 平台技能 + user 用户技能）、
越权挂载校验、组装提示词（get_style_role_assembled）、平台角色可见性。

依赖本机 MySQL（mozao 库）；连接失败时整组跳过（CI 无 DB 场景）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp import db


def _db_ok() -> bool:
    try:
        with db._connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="本机 MySQL 不可用")


@pytest.fixture
def clean_roles():
    """清掉测试期间创建的风格角色（user_id>0 的测试数据）。"""
    yield
    with db._connect() as conn:
        conn.execute("DELETE FROM style_roles WHERE user_id>0")
        conn.execute("DELETE FROM style_role_skills WHERE role_id NOT IN (SELECT id FROM style_roles)")


def test_create_and_get(clean_roles):
    rid = db.create_style_role(user_id=7, name="测试克苏鲁风", description="d",
                               role_prompt="人设：氛围优先")
    role = db.get_style_role(rid)
    assert role["name"] == "测试克苏鲁风"
    assert role["user_id"] == 7
    assert role["is_active"] == 1


def test_update(clean_roles):
    rid = db.create_style_role(user_id=7, name="旧名", role_prompt="旧人设")
    assert db.update_style_role(rid, name="新名", role_prompt="新人设", is_active=0)
    role = db.get_style_role(rid)
    assert role["name"] == "新名" and role["role_prompt"] == "新人设" and role["is_active"] == 0


def test_list_visible_scope(clean_roles):
    rid = db.create_style_role(user_id=7, name="仅用户7可见")
    visible = db.list_style_roles(owner_id=7)
    ids = {r["id"] for r in visible}
    assert rid in ids                      # 自己的可见
    assert 1 in ids                        # 平台内置可见（种子：乌贼风）
    other = db.list_style_roles(owner_id=8)
    assert rid not in {r["id"] for r in other}   # 别人看不到


def test_mount_admin_skill(clean_roles):
    rid = db.create_style_role(user_id=7, name="挂载测试")
    db.mount_skill_to_role(rid, "admin", 1)
    skills = db.list_style_role_skills(rid)
    assert any(s["skill_type"] == "admin" and s["skill_id"] == 1 for s in skills)
    # 幂等
    again = db.mount_skill_to_role(rid, "admin", 1)
    assert again is not None
    assert len(db.list_style_role_skills(rid)) == 1


def test_mount_user_skill_requires_owner(clean_roles):
    rid = db.create_style_role(user_id=7, name="挂载用户技能")
    with pytest.raises(RuntimeError):
        db.mount_skill_to_role(rid, "user", 999999)   # 不存在的技能
    # 真建一个用户技能后挂载成功
    sid = db.create_user_skill(7, "我的短句流", "", "多用短句", role="", is_active=1)
    try:
        db.mount_skill_to_role(rid, "user", sid)
        skills = db.list_style_role_skills(rid)
        assert any(s["skill_type"] == "user" and s["skill_id"] == sid for s in skills)
    finally:
        with db._connect() as conn:
            conn.execute("DELETE FROM user_skills WHERE id=%s", (sid,))


def test_unmount(clean_roles):
    rid = db.create_style_role(user_id=7, name="卸载测试")
    db.mount_skill_to_role(rid, "admin", 1)
    db.unmount_skill_from_role(rid, "admin", 1)
    assert db.list_style_role_skills(rid) == []


def test_assembled_prompt(clean_roles):
    rid = db.create_style_role(user_id=7, name="组装测试", role_prompt="人设A")
    db.mount_skill_to_role(rid, "admin", 1)
    a = db.get_style_role_assembled(rid)
    assert a["name"] == "组装测试"
    assert "人设A" in a["assembled"]
    assert "挂载技能" in a["assembled"]       # 技能块标题
    assert a["skills_text"]


def test_assembled_missing_role():
    assert db.get_style_role_assembled(999999) is None


def test_delete_cascades_mounts(clean_roles):
    rid = db.create_style_role(user_id=7, name="级联删除")
    db.mount_skill_to_role(rid, "admin", 1)
    db.delete_style_role(rid)
    assert db.get_style_role(rid) is None
    assert db.list_style_role_skills(rid) == []
