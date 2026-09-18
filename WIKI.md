# 墨造 · AI 小说创作平台 — 项目 Wiki

> 本文档是项目长期知识库（Wiki），记录产品、架构、业务、部署、流程与已知问题。
> 代码更新时同步维护本文档；密钥类信息一律不入库（见 .env，已被 .gitignore 排除）。

---

## 1. 产品概览

**墨造 · AI 小说创作平台**：一个多用户 SaaS 化的 AI 自动写小说工具。

- **核心能力**：用户给定题材/方向（或上传大纲 / 已有手写正文），AI 自动生成完整小说，输出 TXT/EPUB/DOCX，并附带封面。
- **商业模式**：**按字数计费（每万字 2 元）**，余额制；注册送 10 元体验金；人工收款（站长手动充值）。
- **扣费时点**：**创建时不扣费**，真正生成文字后按 AI 实际生成字数结算扣费；未生成的项目可随时删除、不产生费用。
- **形态**：Web 应用（FastAPI 后端 + 静态前端），单机可跑，Docker 可部署。
- **当前线上**：`http://47.114.41.136`（80 端口，纯 IP 直连）
- **域名**：`mozao.click`（**备案审核中，未生效**；备案通过前被阿里云拦截，暂不可用。Caddy 配置已保留，HTTPS 待备案通过后启用）
- **HTTPS 历史**：曾尝试 `https://mozao.click`（Caddy + Let's Encrypt），因备案未生效被拦截，已回滚纯 IP 直连；安全组 443 已放行

### 关键功能清单

| 模块 | 能力 |
|---|---|
| 账号 | 邮箱注册（验证码）、登录、忘记密码（邮件重置）、会话管理 |
| 建书 | 题材可选、目标字数、自定义提示词、**三种内容类型**：① 智能生成工作流（联网调研→大纲→正文）；② AI 扩写（大纲/梗概扩写成文）；③ 续写/扩写已有内容（粘贴或上传手写正文，AI 续写或把已有章节写长） |
| 大纲 | **上传大纲文件 → 智能解析 → 自动填表**（webapp/outline_parser.py）；**三级大纲编辑器**：总纲 → 分卷（卷名/主线/矛盾/高潮）→ 章节；章节可跨分卷拖拽、分卷增删、按卷保存；章节菜单按分卷分组展示；大纲快照可保存/回滚 |
| 内容解析 | **上传/粘贴已有正文 → 自动切分章节 → 预览确认**（/api/parse-content，识别 第一章/Chapter N/数字序号/楔子等） |
| 生成 | 生成前**联网搜索热门小说**参考文风情节（博查 API）；短篇硬约束 >7000 字 |
| 质量 | 去 AI 味、防同质化（拒绝套路、人物内在矛盾、代价制、私有细节）、结局优化（人物弧光闭环、升维收尾）、**番茄写作纪律**（黄金三章/爽点闭环/章末断章/移动端排版，fanqie_style 开关注入） |
| 封面 | **硅基流动 ERNIE-Image-Turbo AI 生图一体封面**（书名/题材/文风/简介直接写进 prompt，AI 画整张封面，不再后期叠字/角标）；**每本书限生成 5 次**（项目目录 `_cover_count.txt` 计数），按钮实时显示剩余次数；失败回退程序化 Pillow 封面 |
| 作品管理 | 章节阅读/编辑、**大纲编辑、单章重写 / 从某章重生成（免费，每项目每日限 10 次）**、世界观/调研查看、下载 TXT、退款删除 |
| 计费 | **创建不扣费**；生成完成后按 **AI 实际生成字数** 结算扣费（含代理折扣快照）；余额不足扣到 0 并记录欠费；用户手写内容不重复计费 |
| 代理分销 | 一级代理：本人消费折扣 + 下级消费返佣 + 邀请码绑定 |
| 退款 | 用户申请余额退款 → 邮件通知站长 → 人工处理 |
| 站长后台 | 用户/充值/代理/退款/日志/统计/设置（仅 is_admin 可登录） |

---

## 2. 技术架构

### 技术栈

- 后端：Python 3.13 · FastAPI · Uvicorn · **MySQL（本地与生产均用 MySQL，库 `mozao`；本地 root/yue_426983）**
- 前端：**Vue 3 + Element Plus + Vite + Pinia + Vue Router**（`webapp/frontend/`，构建产物 `dist/` 由 FastAPI 静态挂载 + SPA fallback）；旧原生 JS 在 `webapp/webui/`（已淘汰，不再维护）
- AI：DeepSeek（文本生成，openai 库）、博查 Bocha（联网调研）、硅基流动 ERNIE-Image-Turbo（封面）
- 依赖约束：`httpx<0.28`（openai 1.x 硬依赖）；`python-multipart`（FastAPI 表单上传必需）
- 部署：Docker（python:3.13-slim + Noto CJK 字体），docker-compose 编排

### 目录结构

```
D:\PythonProjects\DouBao\
├── run_web.py                  # Web 服务入口（手写 .env 加载，环境变量优先）
├── Dockerfile / docker-compose.yml / .dockerignore
├── BRANCH_FLOW.md              # 分支与发布流程（规则）
├── WIKI.md                     # 本文档
├── config*.yaml                # 生成引擎配置（题材/文风/约束）
├── novel_gen/                  # 生成引擎（LLM 调用、调研、流水线、导出、封面）
│   ├── config.py               # NovelConfig：内容类型/content_type、expand_targets、fanqie_style 等开关
│   ├── pipeline/
│   │   ├── manager.py          # 流水线编排（委托各 agent）
│   │   ├── templates.py        # 提示词模板（含 world_from_content / continue_outline / expand_chapter）
│   │   ├── stages.py           # 大纲/世界观/章节写作器；_strip_html_tags
│   │   ├── editor.py           # 编辑视角质检
│   │   ├── reviser.py          # 高质量模式自动修订
│   │   ├── memory.py           # 章节记忆/摘要
│   │   └── agents/             # 多智能体角色（v2）
│   │       ├── base.py         # BaseAgent：从 prompts/{role}.md 加载角色+按role过滤skill
│   │       ├── researcher.py   # 调研员：题材调研+反套路改造
│   │       ├── architect.py    # 架构师：三级大纲+人物世界观+时间线
│   │       ├── writer.py      # 写手：逐章正文生成
│   │       ├── reviewer.py    # 审校：一致性/逻辑/合规
│   │       └── prompts/       # 各角色独立提示词（Markdown，可编辑）
│   │           ├── researcher.md
│   │           ├── architect.md
│   │           ├── writer.md
│   │           └── reviewer.md
│   ├── store/project.py        # ProjectStore：save_user_content/set_user_words/user_words
│   ├── store/schemas.py        # Chapter（含 source=ai|user、goal 快照）
│   ├── utils/chapter_splitter.py  # 已有正文章节切分器（split_chapters/summarize_preview）
│   └── export/cover.py         # 封面：AI(ERNIE-Image-Turbo) + 程序化兜底
├── webapp/                     # Web 应用（FastAPI）
│   ├── server.py               # 全部 API 路由（含 /api/parse-content、结算 _settle_price）
│   ├── db.py                   # MySQL 数据层（init_db 自动迁移；writing_skills 支持 role 字段）
│   ├── pricing.py              # 按字数计费（PRICE_PER_10K=2.0）
│   ├── referral.py             # 代理/分销参数
│   ├── verify_code.py          # 邮箱验证码（注册/重置）
│   ├── mailer.py               # SMTP 邮件（退款通知等）
│   ├── logger.py               # 日志
│   ├── admin_cli.py            # 命令行管理工具
│   └── webui/                  # 前端页面（index.html / admin.html / app.js / admin.js / style.css）
```

### 多智能体角色（v2 架构）

| 角色 | 文件 | 职责 | 专属prompt |
|---|---|---|---|
| Researcher | `agents/researcher.py` | 联网调研同题材热门、反套路改造、元素叠加 | `prompts/researcher.md` |
| Architect | `agents/architect.py` | 三级大纲（总纲/分卷/章节）、人物三层设计、世界观、时间线 | `prompts/architect.md` |
| Writer | `agents/writer.py` | 逐章正文（场景级生成、润色、字数兜底） | `prompts/writer.md` |
| Reviewer | `agents/reviewer.py` | 一致性校验、逻辑审查、合规检测、修订 | `prompts/reviewer.md` |

**Skill 按角色挂载：** `writing_skills.role` 字段空=全局（所有agent都注入），填 researcher/architect/writer/reviewer 则只注入对应agent。改风格直接编辑 `prompts/*.md` 或在管理页加专属skill，不动代码。

### 数据流（创建一本小说）

1. 前端填表（题材/字数/模式）→ `POST /api/projects` → **创建不扣费**（仅余额校验 ≥ 预估费用），登记归属与折扣快照
   - 智能生成：按目标总字数预估费用；生成完成直接交付
   - 续写/扩写已有内容：切分用户正文入库（source=user，记录 user_words），按"目标 − 已有"预估费用
2. 后台线程执行：
   - 智能生成：联网调研（博查）→ 参考文风 → 生成大纲/世界观 → 逐章生成（DeepSeek）
   - 续写模式：用户章摘要 → 基于用户正文提炼世界观/人物卡/伏笔 → AI 规划后续章 → 从断点续写（末章完整结局）
   - 扩写已有章节：保留原剧情/对话/人设，只扩充细节到单章目标字数
3. 完成后导出 TXT/EPUB/DOCX 到 output/，生成 AI 封面
4. **结算**：生成完成后按 AI 实际生成字数扣费（settle.json 幂等，全部项目统一；无 AI 文字不收费；余额不足扣到 0 + debt.json 欠费记录；消费返佣随足额扣费发放）
5. 用户可下载、阅读、编辑大纲、重写/重生成章节；未开始生成的项目可直接删除（不产生费用）

---

## 3. API 路由一览

| 分组 | 路由 |
|---|---|
| 系统 | GET /api/health · GET /api/genres · GET /api/pricing |
| 解析 | POST /api/parse-outline（上传大纲文件解析）· **POST /api/parse-content（上传/粘贴已有正文，切分预览）** |
| 账号 | POST /api/send-register-code · /api/register · /api/send-reset-code · /api/reset-password · /api/login · /api/logout |
| 站长后台 | POST /api/admin/login · GET /api/admin/stats|users|recharges|projects|settings|logs|refunds · POST /api/admin/users/{id}/recharge|agent|unagent · GET /api/admin/users/{id}/downline|commissions · POST /api/admin/refunds/{id}/handle |
| 我的 | GET /api/me · /api/me/recharges · /api/me/referral · /api/me/refundable · /api/me/refund-requests · POST /api/me/bind-referrer · /api/me/refund-request |
| 项目 | GET/POST /api/projects（**多用户并发生成，互不阻塞**）· GET /api/projects/{id} · GET /{id}/outline · **PUT /{id}/outline（三级大纲编辑：总纲/分卷 volumes/章节，章节带 volume_no）** · **GET /{id}/outline/diff（大纲改动检测）** · /{id}/chapters · /{id}/chapters/{no} · PUT /{id}/chapters/{no} · **POST /{id}/chapters/{no}/rewrite（仅重写本章+自动重摘要）** · POST /{id}/regenerate-from/{no} · POST /{id}/cover · GET /{id}/cover · /{id}/world · /{id}/research · POST /{id}/start|stop|refund · GET /{id}/download |

---

## 4. 业务规则

### 计费（webapp/pricing.py，改后重启生效）

- **按字数计费**：普通 `PRICE_PER_10K = 2.0`（每万字 2 元）；**高质量模式 3 元/万字**；`price_for(words) = round(words / 10000 * 费率, 2)`

- **创建时不扣费**，仅余额校验（余额 < 预估费用拒绝创建）；**生成完成后**按 AI 实际生成字数 `price_for(ai_words)` 结算扣费（`settle.json` 幂等；ai_words=0 不收费；余额不足扣到 0 并写 `debt.json` 欠费记录，不阻塞交付）
- `ai_words = 全书当前总字数 − 用户手写原字数`（续写/扩写模式；普通模式即全书总字数）
- **代理折扣快照**：创建时记录折扣到 project_owner.discount，结算按快照计费
- **消费返佣随结算发放**：足额扣费时给上级代理按实付金额返佣（创建时不触发）
- **短篇小说硬约束 >7000 字**（用户偏好，代码层强制）
- 注册赠金 `FREE_REGISTER_BONUS = 10.0` 元（可设 0 关闭）
- 代理价 = 原价 × `AGENT_DISCOUNT`(0.8)

### 章节重写 / 重生成（大纲编辑配套）

- **单章重写**（POST /chapters/{no}/rewrite）：仅重写本章 + 自动重摘要，后续章节不动；**免费**
- **从某章重生成**（POST /regenerate-from/{no}）：本章起后续章节重写；**免费**
- 限额：`REGEN_DAILY_LIMIT = 10` 次/项目/日（rewrite 与 regenerate-from 共用计数，超限 429）
- 大纲编辑：改章节 title/goal、增删章、重排 no；删章后已生成正文保留并标"已废弃"；生成中禁止编辑（409）
- **用户手写章节保护**：`source == "user"` 的章节，rewrite 直接 400；regenerate-from 若起始章 ≤ 用户章节最大号 → 400（须从 user_max+1 开始）

### 代理/分销（webapp/referral.py）

| 参数 | 值 | 说明 |
|---|---|---|
| AGENT_DISCOUNT | 0.8 | 代理本人消费 8 折 |
| COMMISSION_RATE | 0.10 | 下级消费金额 10% 返佣 |
| COMMISSION_BASE | "consume" | 按下级消费返佣（防刷，推荐） |
| REFERRAL_BONUS | 0.0 | 拉新奖励默认关闭（防传销红线） |

- 邀请码 `INV{id}`；下级填邀请码注册时绑定上级；仅"上级是代理"且开启拉新奖励才发注册佣金

### 退款

- 用户可删除"未开始生成"的项目：创建不扣费的新项目（price=0）直接删除；历史已扣费项目原路退费 + 冲正返佣 + 删除作品
- 也可申请余额提现退款 → 入库 pending → **邮件通知站长**（ADMIN_EMAIL）→ 站长后台人工批准/拒绝

### 站长规则

- **首个注册用户自动成为站长**（`db.get_user(1) is None` 判定）；后台仅 is_admin 可登录
- 充值 = 人工收款：站长收到转账后后台手动加余额

---

## 5. 数据库结构（MySQL，webapp/db.py）

| 表 | 关键字段 |
|---|---|
| users | id, username(UNIQUE), pass_hash(sha256+salt), email(UNIQUE), balance, is_admin, is_agent, agent_discount, invite_code(UNIQUE), referrer_id, created_at |
| sessions | token(PK), user_id, created_at |
| project_owner | project_id(PK), user_id, **price**（结算后实扣金额，退费/统计用）, **discount**（创建时代理折扣快照，结算用）, created_at |
| recharges | id, user_id, amount, note, created_at |
| commission_log | id, agent_id, buyer_id, amount, kind(consume/recharge/register), project_id, note, created_at |
| refund_requests | id, user_id, amount, reason, status(pending/approved/rejected), admin_note, created_at, handled_at |

- `init_db()` 启动时自动建表 + 老库迁移（email/is_agent/agent_discount/invite_code/referrer_id/price 列增量补充）
- **章节不在 DB**：存于项目目录章节文件；`source=ai|user`（user=用户手写，AI 不覆盖）记录在章节文件头部
- 项目目录内辅助文件：`user_content.txt`（用户手写原文）、`user_words.json`（手写原字数，结算用）、`settle.json`（结算幂等标记）、`regen_count.json`（每日重生成计数）

---

## 6. 配置（.env，**不入库**）

| 键 | 说明 |
|---|---|
| DEEPSEEK_API_KEY | 文本生成模型 Key |
| BOCHA_API_KEY | 联网调研（生成前搜热门小说参考）Key |
| SILICONFLOW_API_KEY | 封面 AI 生图（ERNIE-Image-Turbo）Key |
| AI_COVER_ENABLED | 封面 AI 开关，默认 1；0 = 只用程序化封面 |
| ADMIN_EMAIL | 站长收件邮箱（退款通知） |
| SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS | SMTP 发信（QQ 邮箱：smtp.qq.com:465 + 授权码） |

- run_web.py 手写解析 .env，**环境变量优先**（不会覆盖已存在的环境变量）
- 容器部署：compose `env_file: .env` 注入，镜像内不存密钥

---

## 7. 服务器与部署

### 阿里云 ECS

| 项 | 值 |
|---|---|
| 实例 ID | i-bp14oxtecrth3wbq6thc |
| 名称 | launch-advisor-20260908 |
| 地域 | cn-hangzhou |
| 规格 | ecs.e-c1m2.large（2核2G）· 40G 磁盘 |
| 系统 | Alibaba Cloud Linux 3（Docker 26.1.3 + Compose v2.27） |
| 公网 IP | 47.114.114.136（**安全组放行 TCP 80/443**） |
| 服务目录 | /opt/novel-gen |
| 部署形态 | 服务器已装 git：`cd /opt/novel-gen && git pull origin master && docker compose up -d --build novel-gen` |
| **当前运行版本** | master 最新（含按字数计费、三级大纲、续写扩写、多智能体、MySQL、Vue3 前端） |

### Docker 卷（持久化，勿 `down -v`）

| 卷 | 挂载 | 内容 |
|---|---|---|
| novel_projects | /app/projects | 生成中间产物 |
| novel_output | /app/output | 成品（TXT/EPUB/DOCX/封面） |
| novel_logs | /app/logs | 日志 |
| novel_data | /data | SQLite 数据库 novel_web.db |

### 健康检查

- 容器 HEALTHCHECK → `GET /api/health`；端口映射 `80:8000`

### 远程运维（本机 Windows）

- Workbench CLI：`C:\Users\pc\AppData\Local\Programs\workbench\workbench.exe`（凭据 `~/.workbench/config.json`，AK 模式）
- 常用：`workbench exec -i <实例ID> --command "..."` · `workbench upload <本地> <远程> -i <实例ID> -f` · `workbench list ecs`
- **注意**：服务器已装 git（GitHub token 已配）；发布 = 本地 `git push origin master` → 服务器 `git pull` → rebuild

---

## 8. 分支与发布流程（规则）

见 **BRANCH_FLOW.md**（同目录），核心：

```
devtest（开发验证）→ 确认无误 → 合并 master → 推送 → 从 master 打包部署服务器
```

红线：.env 不入库；未验证不合并；服务器只跑 master；数据库/日志/成品不入库。

---

## 9. 常用命令

```bash
# 本地测试
py -3.13 -m pytest tests/ -q          # 47 passed（26 基线 + 4 大纲编辑 + 5 续写扩写 + 6 章节名唯一 + 6 结算）
# 本地启动服务
py -3.13 run_web.py --host 0.0.0.0 --port 8000
# 命令行管理（充值/代理/查用户）
py -3.13 -m webapp.admin_cli users
# 发版（master，前端在容器内 build，无需本地 build / dist.zip）
git archive --format=zip -o "$env:TEMP\novel_master.zip" master
$wb="C:\Users\pc\AppData\Local\Programs\workbench\workbench.exe"
& $wb upload -i i-bp14oxtecrth3wbq6thc -r cn-hangzhou -f "$env:TEMP\novel_master.zip" /tmp/
& $wb exec -i i-bp14oxtecrth3wbq6thc -r cn-hangzhou --timeout 600 -c "cd /opt/novel-gen && python3 -m zipfile -e /tmp/novel_master.zip . && docker compose up -d --build"
# 服务器运维
& $wb exec -i i-bp14oxtecrth3wbq6thc -r cn-hangzhou -c "cd /opt/novel-gen && docker compose ps"
& $wb exec -i i-bp14oxtecrth3wbq6thc -r cn-hangzhou -c "cd /opt/novel-gen && docker compose logs -f novel-gen"
curl http://127.0.0.1/api/health   # 本机访问
```

---

## 10. 已知问题与解决记录（避坑）

| 问题 | 原因 | 解决 |
|---|---|---|
| 容器启动即崩溃 Restarting | FastAPI 表单上传缺 `python-multipart` | requirements.txt 补依赖 |
| 服务器构建 apt 极慢（16KB/s） | 访问 Debian 官方源 | Dockerfile 先 sed 换阿里云镜像 `mirrors.aliyun.com` |
| 硅基流动 API 401 | key 是**国内站**的，`api.siliconflow.com` 不认 | 用 `api.siliconflow.cn`（代码已双域名兜底） |
| AI 封面出现乱码文字 | prompt 注入书名/副标题/题材字面，AI 渲染成文字 | prompt 只描述氛围并强禁文字；文字一律程序叠加 |
| 前端看不到"管理后台"入口 | 浏览器缓存旧版 app.js | 强刷 Ctrl+F5；服务器端已确认无问题 |
| 短篇缺结局 | 生成流程无结局收束 | 结局优化功能：人物弧光闭环 + 升维收尾，已并入流程 |
| 本机 push GitHub 失败（Connection reset / 443 连不上） | 本机到 github.com 间歇性网络不通 | 本地提交安全；网络恢复后重试 `git push`（devtest 待推提交先留本地） |
| Edit 工具对大文件反复 Native execution failed | 工具限制 | 写临时 `_patch_*.py` 脚本执行 + 运行后删除；脚本内 Python 源码字符串须用 `\\n` 显式转义（多行字面量会被解释坏） |
| PowerShell 执行多行/中文命令报错 | 引号转义 | 写 .py/.sh 文件上传执行，不在命令行内嵌 |
| 域名 mozao.click 打不开 | 备案未生效被阿里云拦截 | 回滚纯 IP 直连；备案通过后启用 Caddy HTTPS |
| 大纲分卷/章节刷新后丢失 | 后端 OutlineChapterReq/ChapterPlan 无 volume_no 字段，拖拽后保存被丢弃 | ChapterPlan 加 volume_no（0=未分配）并在 to/from_dict、OutlineChapterReq、save_outline 全链路序列化 |

---

## 11. 用户长期偏好（系统已记忆）

2. **代码更新流程：先推 devtest 验证 → 确认后合并 master → 服务器运行 master**
3. 部署方式：A 方案（本地 master 打包上传，服务器不装 git）
4. 收款：人工收款；定价按单本字数计费；当前 IP:端口内测
5. 生成图片/封面要求精准参数（比例/分辨率/质感），严格按指令还原

---

*最后更新：2026-09-16 · 由开发记录整理（新增三级大纲：总纲→分卷→章节、volume_no 全链路、章节按分卷分组、高质量模式 3 元/万字、MySQL 全量切换、Vue3 前端迁移完成、服务器 git 部署、多用户并发生成）*


---

## 12. 代码审查修复与安全加固（2026-09-17）

### 计费与并发
- 余额扣改：change_balance 加 SELECT ... FOR UPDATE 行锁，读改写同事务，消除并发丢更新
- 结算幂等：先落 settle 标记再扣费，避免崩溃重复扣费
- 生成防透支：每章生成前实时检查项目所有者余额，余额 <= 0 自动停止并提示充值后从断点继续（不做创建预扣冻结，仍按实际 AI 字数结算）
- 批量生成改用真 RunHandle（stop_event/error），finally 清理信号量与 RUNS，失败落盘 failed.json

### 数据与稳定性
- save_config/load_config 补落盘 8 个字段（高质量/人物深度/番茄风/节奏/剧情速度/自定义提示词等）
- 项目目录写 JSON 改临时文件 + os.replace 原子写；project_id 加短 uuid 防同秒撞名

### 安全
- 密码升级 bcrypt(cost=12)，旧 sha256 用户首登自动平滑重哈希
- CORS 由 `*`+credentials 改为环境变量 ALLOWED_ORIGINS 白名单
- 验证码改用 secrets 密码学随机数
- 登录按 IP 限流（5 分钟失败 8 次封 10 分钟）
- 上传文件 10MB 上限，超限返回 413
- config.yaml 移出 git 并加 gitignore，新增 config.example.yaml（注意：历史 key 需去厂商后台吊销重发）
- 导出文件名加 project_id 前缀，避免不同用户同名书互相覆盖

*最后更新：2026-09-17 · 代码审查报告 P0/P1/P2 修复、余额耗尽自动停止、bcrypt 密码升级*

---

## 13. 部署流程（务必遵守）

### 服务器信息
- 实例：i-bp14oxtecrth3wbq6thc（cn-hangzhou），部署目录 /opt/novel-gen
- 域名：mozao.click / www.mozao.click（均解析到 47.114.41.136），ICP 备案 冀ICP备2026038211号（2026-09-18 通过）
- HTTPS：宿主机 nginx（/etc/nginx/conf.d/mozao.conf），80 反代 127.0.0.1:8000 并 301 跳 HTTPS；443 反代同后端，证书 Let's Encrypt（certbot webroot，/var/www/certbot，自动续期，到期 2026-12-17）
- 备案刚通过时，阿里云会按域名 HTTP 拦截（带 Host: mozao.click 返回 403，IP 直连正常），属正常同步延迟，等几小时自动放行后再签证书
- 公安备案：beian.mps.gov.cn 用阿里云给的数据码办理（不影响上线，上线后 30 天内补）
- 容器：mysql-mozao + novel-gen，docker compose 管理
- 服务器直连 GitHub 常超时；本地代码用 git archive 打包后经 workbench 上传部署

### 发版步骤（多阶段 Docker 构建，2026-09-18 起）
前端已改为**容器内多阶段构建**，本地无需 `npm run build`、无需上传 dist：
- Dockerfile 阶段一 `node:20-alpine`：`npm ci` + `npm run build`；阶段二 `python:3.13-slim`：`COPY --from=builder` 拿 dist。
- `.dockerignore` 已排除 `webapp/frontend/dist/` 与 `node_modules/`。
- **为什么仍要上传代码包**：服务器直连 github.com 常超时、且仓库私有需凭证，所以不走服务器 `git pull`，改用 workbench upload（阿里云控制台通道）。多阶段构建省掉的是 dist.zip，不是代码包。

1. 本地 master：提交并 `git push origin master`（若 GitHub 暂时连不上，本地 commit 仍可先打包部署，网络恢复后补推）
2. `git archive --format=zip -o "$env:TEMP\novel_master.zip" master`
3. workbench upload 把代码包传到服务器 `/tmp/`
4. 服务器：`cd /opt/novel-gen && python3 -m zipfile -e /tmp/novel_master.zip . && docker compose up -d --build`
   - build 阶段自动在容器内编译前端（依赖层有缓存，二次发版很快）
5. 验证：首页 200，主 JS `curl -s -o /dev/null -w '%{size_download}' http://127.0.0.1/assets/index-*.js` 应 ~881KB，不是几百字节

### 常见坑
- **白屏（主 JS 几百字节）**：SPA fallback，说明 dist 未正确打进镜像。多阶段改造后多为源码 index.html / title 未提交进 git，导致容器 build 用旧源码；确认 `git archive` 包里含最新前端源码后 rebuild。
- **标题/前端改动没生效**：前端改动在源码 `webapp/frontend/src` / `index.html`，必须 commit 进 git 才会被容器内 build；本地未提交的 dist 不会进镜像（已被 dockerignore）。
- **bcrypt 等新依赖**：改了 requirements.txt 或前端依赖，必须 `--build` rebuild，不能只 `up -d`。
- **服务器无 unzip**：解压用 `python3 -m zipfile -e <zip> <dir>`。
- **config.yaml**：已 gitignore，服务器上保留，不要删。

*最后更新：2026-09-18（前端多阶段 Docker 构建）*

---

## 14. 扩写模式分卷丢失修复（2026-09-17）

### 现象
- 标准模式（全自动）重新生成大纲能产出分卷（volumes），但**扩写模式**（mode=expand，用户上传大纲走 `manager._run_expand_outline`）重新生成后分卷一直为空。

### 根因（三层）
1. **提示词缺字段**：`templates.expand_outline_prompt` 的 JSON 输出要求只让模型返回 title/intro/chapters，没要求 volumes/core_conflict/main_line；`Outline.from_dict` 虽能解析 volumes，但模型压根不返回 → 分卷为空。
2. **空壳覆盖**：`server.regen_outline` 接口先写了一个空壳 Outline 占位，而 `manager.run_outline()` 有「大纲已存在则直接复用」逻辑，后台线程读到空壳就复用、永不重生成。改为：重生成前删除 outline.json（让 load_outline 返回 None）。
3. **前端无 token**：`ToolsPanel.vue` 的「重生成大纲/世界观」按钮用裸 `fetch`，不带 Authorization，HTTP 401 时 fetch 不 reject，走 success 分支误提示「已开始」。改为走 `api/novel.js` 的封装（自动带 token）。

### 修复点
- `templates.expand_outline_prompt`：JSON 输出补 `core_conflict / main_line / volumes`（每卷 no/name/intro/main_conflict/character_growth/climax/start_ch/end_ch）。
- `server.regen_outline`：改为删除 outline.json 后再起后台线程，而非先写空壳。
- `ToolsPanel.vue`：两个重生成按钮改走封装请求。

### 部署备注
- 服务器**没有 unzip**：`unzip` 命令会 exit 127。解压 dist.zip 用 `python3 -m zipfile -e /tmp/dist.zip .`。
- workbench 上传进度条走 stderr，PowerShell 会显示「命令退出码 1」，看 `Upload complete` 即成功，不要误判失败。

*最后更新：2026-09-17 · 扩写模式补分卷、regen_outline 删空壳、前端重生成按钮带 token、服务器用 python3 解压 dist*

---

## 15. 待修复问题清单（2026-09-17 记录，按优先级分批修）

| 编号 | 优先级 | 问题 | 根因/位置 | 修复方案 |
|---|---|---|---|---|
| P0-1 | 高 | 大纲分批静默失败 | `stages._run_batched` `_BATCH_SIZE=25`，一批要模型输出 25 章完整 JSON，max_tokens=8000 必截断；`chat_json` 重试 2 次同参数（同样截断）；失败后 server.py 只写日志，状态 error 字段不回填，前端 genStatus 永远空 | a) 批量 25→8，批次提示词要求字段精简；b) 失败把 error 写入状态并红色展示；c) 重试降批量/温度 |
| P0-2/3 | 高 | 裸 fetch 无 token | `ProjectDetail.saveEdit`、`ToolsPanel.loadMySkills/saveMySkill/delMySkill` 未走 axios 实例 | 统一改用 `api/request.js` |
| P1-4 | 中 | 项目列表排序无效 | filtered computed 缺 sort 分支 | computed 里补 sort |
| P1-6 | 中 | 大纲阶段无心跳文案 | 进度条只覆盖章节生成 | 轮询消费 running+state.phase，显示"大纲生成中（已X分钟）" |
| P2-9 | 低 | 项目 ID 含中文 | `_safe_name` 生成 ID 带中文 | 改 ASCII+hash 后缀 |

*最后更新：2026-09-17*

