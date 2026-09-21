# -*- coding: utf-8 -*-
"""端到端冒烟：风格角色（StylistAgent）人设真实注入正文生成系统提示。

用录制包装的 MockClient 跑 大纲→世界观→第一章 全流程，
断言风格角色的人设与挂载技能出现在写手章节的系统提示中。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import webapp.verify_code as vc  # noqa: E402
from webapp import db  # noqa: E402
from webapp import server  # noqa: E402
from webapp import core  # noqa: E402

# ---------- 录制型 LLM ----------
from novel_gen.llm.base import LLMClient


class RecordingMock(LLMClient):
    name = "recording-mock"

    def __init__(self):
        self.calls = []  # (system, user)

    def chat(self, system, user, temperature=None, max_tokens=None,
             frequency_penalty=None, presence_penalty=None):
        self.calls.append((system, user))
        from novel_gen.llm.providers import MockClient
        return MockClient().chat(system, user, temperature=temperature,
                                 max_tokens=max_tokens,
                                 frequency_penalty=frequency_penalty,
                                 presence_penalty=presence_penalty)

_recorder = RecordingMock()
_orig_create_llm = core.create_llm


def _fake_create_llm(cfg):
    return _recorder


core.create_llm = _fake_create_llm

client = TestClient(server.app)
ts = time.strftime("%H%M%S")
username, email = f"sr_smoke_{ts}", f"sr_smoke_{ts}@test.local"
vc._codes[f"reg:{email}"] = {"code": "123456", "expires_at": time.time() + 600,
                             "attempts": 0, "last_sent": 0}

r = client.post("/api/register", json={
    "username": username, "email": email, "password": "smoke123456",
    "code": "123456", "invite_code": "",
})
assert r.status_code == 201, f"register {r.status_code}: {r.text}"
uid = r.json()["user"]["id"]
h = {"Authorization": "Bearer " + r.json()["token"]}

# 1) 建风格角色（人设带唯一标记）
marker = "克苏鲁悬疑克制风SMOKE"
r = client.post("/api/style-roles", headers=h,
                json={"name": "冒烟风格", "description": "d",
                      "role_prompt": "人设：%s，叙事克制，多用感官细节" % marker})
assert r.status_code == 201, r.text
rid = r.json()["id"]
# 挂载一个平台技能
r = client.post(f"/api/style-roles/{rid}/skills", headers=h,
                json={"skill_type": "admin", "skill_id": 1})
assert r.status_code == 200, r.text

# 2) 创建项目并选风格角色
r = client.post("/api/projects", headers=h,
                json={"title": f"风格冒烟{ts}", "genre": "玄幻", "total_words": 10000,
                      "style_role_id": rid, "llm": {"provider": "mock"}})
assert r.status_code == 201, f"create {r.status_code}: {r.text}"
pid = r.json()["id"]

# 3) 跑 大纲→世界观→第一章
core._open_pipeline  # ensure imported
from novel_gen.store.project import ProjectStore
from webapp.core import _open_pipeline

store = ProjectStore.open(Path(core.PROJECTS_DIR) / pid)
pipe = _open_pipeline(store)
pipe.run_outline()
pipe.run_world()
pipe.write_chapter(1, silent=True)

# 4) 断言：风格角色人设 + 挂载技能出现在写手章节系统提示
hit_role = hit_skill = False
for system, user in _recorder.calls:
    if marker in system:
        hit_role = True
    if "番茄爽文风" in system:
        hit_skill = True
    # 章节正文请求（mock 标记：user 含【本章】）
    if "【本章】" in user and marker in system:
        print("[OK] 第一章系统提示包含风格角色人设")
print("[OK] 风格角色人设出现在系统提示:", hit_role)
print("[OK] 挂载技能(番茄爽文风)出现在系统提示:", hit_skill)
assert hit_role and hit_skill, "风格角色人设或挂载技能未进入生成提示词"

# 5) 配置快照检查
import json as _json
cfg = _json.loads((Path(core.PROJECTS_DIR) / pid / "config.json").read_text(encoding="utf-8"))
assert cfg["style_role_id"] == rid and marker in cfg["style_role_prompt"], "config 快照缺失"
print("[OK] config.json 快照: style_role_id=%d, prompt 含标记" % cfg["style_role_id"])

# 6) 清理
core.create_llm = _orig_create_llm
import shutil
shutil.rmtree(Path(core.PROJECTS_DIR) / pid, ignore_errors=True)
db.delete_user_sessions(uid)
with db._connect() as conn:
    conn.execute("DELETE FROM user_skills WHERE user_id=%s", (uid,))
    conn.execute("DELETE FROM project_owner WHERE user_id=%s", (uid,))
    conn.execute("DELETE FROM users WHERE id=%s", (uid,))
    conn.execute("DELETE FROM style_roles WHERE id=%s", (rid,))
print("--- 端到端冒烟通过，已清理 ---")
