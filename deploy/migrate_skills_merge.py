# -*- coding: utf-8 -*-
"""REQ-技能统一 阶段二：把 4 张旧技能表收敛到统一表 `skills`（一次性迁移，幂等）。

- writing_skills  → skills(source='platform', owner_id=0)
- user_skills     → skills(source='user', owner_id=user_id)
- skill_market    → skills(source='market', owner_id=author_id)
- user_skills.market_id（旧市场id）→ 新 skills 中对应 market 行 id
- style_role_skills.skill_id → 新 skills.id（admin→platform / user→user 分别重映射）

原则：旧表保留不 DROP（回退与存量核对）；`skills` 非空则跳过（幂等）。
用法：python -m deploy.migrate_skills_merge
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp.db.base import _connect, _SKILLS_UNIFIED  # noqa: E402


def run() -> dict:
    with _connect() as conn:
        # 确保统一表存在（init_db 已建；此处兜底）
        conn.execute(_SKILLS_UNIFIED)
        cnt = conn.execute("SELECT COUNT(*) AS c FROM skills").fetchone()["c"]
        if cnt:
            return {"skipped": True, "reason": f"skills 已存在 {cnt} 行，跳过迁移"}

        # 1) 平台技能
        plat_map = {}
        for r in conn.execute("SELECT * FROM writing_skills ORDER BY id").fetchall():
            cur = conn.execute(
                "INSERT INTO skills (source, owner_id, name, description, prompt_append, role, "
                "applicable_stages, version, is_active, sort_order, created_at) "
                "VALUES ('platform', 0, %s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r["name"], r["description"], r["prompt_append"], r["role"],
                 r.get("applicable_stages") or "", r.get("version") or "1",
                 r["is_active"], r["sort_order"], r["created_at"]))
            plat_map[r["id"]] = cur.lastrowid

        # 2) 用户技能
        user_map = {}
        for r in conn.execute("SELECT * FROM user_skills ORDER BY id").fetchall():
            cur = conn.execute(
                "INSERT INTO skills (source, owner_id, name, description, prompt_append, role, "
                "applicable_stages, is_active, market_id, created_at) "
                "VALUES ('user', %s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r["user_id"], r["name"], r["description"], r["prompt_append"], r["role"],
                 r.get("applicable_stages") or "", r["is_active"],
                 r.get("market_id") or 0, r["created_at"]))
            user_map[r["id"]] = cur.lastrowid

        # 3) 市场技能
        market_map = {}
        for r in conn.execute("SELECT * FROM skill_market ORDER BY id").fetchall():
            cur = conn.execute(
                "INSERT INTO skills (source, owner_id, name, description, prompt_append, role, "
                "applicable_stages, downloads, created_at) "
                "VALUES ('market', %s,%s,%s,%s,%s,%s,%s,%s)",
                (r["author_id"], r["name"], r["description"], r["prompt_append"], r["role"],
                 r.get("applicable_stages") or "", r["downloads"], r["created_at"]))
            market_map[r["id"]] = cur.lastrowid

        # 4) 回填用户行 market_id：旧市场 id → 新 market 行 id
        for old_mid, new_mid in market_map.items():
            conn.execute(
                "UPDATE skills SET market_id=%s WHERE source='user' AND market_id=%s",
                (new_mid, old_mid))

        # 5) 重映射风格角色挂载 skill_id → 新 skills.id
        remapped = 0
        for r in conn.execute("SELECT id, skill_type, skill_id FROM style_role_skills").fetchall():
            new_id = (plat_map if r["skill_type"] == "admin" else user_map).get(r["skill_id"])
            if new_id:
                conn.execute("UPDATE style_role_skills SET skill_id=%s WHERE id=%s",
                             (new_id, r["id"]))
                remapped += 1

        # 6) 校验各来源计数与旧表一致
        counts = {src: conn.execute(
            "SELECT COUNT(*) AS c FROM skills WHERE source=%s", (src,)).fetchone()["c"]
            for src in ("platform", "user", "market")}
        assert counts["platform"] == len(plat_map), "平台技能迁移数量不一致"
        assert counts["user"] == len(user_map), "用户技能迁移数量不一致"
        assert counts["market"] == len(market_map), "市场技能迁移数量不一致"

        return {"platform": counts["platform"], "user": counts["user"], "market": counts["market"],
                "style_role_skills_remapped": remapped}


if __name__ == "__main__":
    result = run()
    print("迁移完成:", result)
