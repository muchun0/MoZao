# 文件索引（docs/filemap.md）

> 目的：每个目录/关键文件是干什么的，一行说明。**不需要贴完整代码**；需要细节时再按此索引定位文件精读。
> 维护规则：新增/删除/重命名文件时同步更新本文件，保持索引与仓库一致。

## 根目录

| 路径 | 职责 |
|---|---|
| `AGENTS.md` | **豆包 AI 助手长期规则**（分支/复用/部署/Agent 约束），每次新任务必读 |
| `CLAUDE.md` | Claude Code 规则镜像（与 AGENTS.md 同一套约束） |
| `README.md` | 产品功能 + 架构概览 + 部署 + 更新记录（变更史权威来源） |
| `WIKI.md` | 产品/技术/API/业务规则/数据库/配置/部署/分支/避坑/安全加固（业务权威） |
| `API.md` | REST API 接口文档（接口细节权威来源） |
| `BRANCH_FLOW.md` | 分支流程说明 |
| `run_web.py` | Web 启动入口（默认 127.0.0.1:8000，可指定端口/host） |
| `Dockerfile` / `docker-compose.yml` | 容器化部署（前端多阶段构建 + mysql-mozao） |
| `requirements.txt` | Python 依赖（注意 httpx<0.28 锁定） |
| `.env` | 环境变量（API Key/SMTP/DB，**不入库**） |
| `.llm_encrypt.key` | BYOK 加密密钥（**不入库**） |
| `启动小说生成器.bat` | Windows 一键启动 |
| `deploy/` | 部署辅助：`Caddyfile`+`caddy.service`（反代）、`migrate_sqlite_to_mysql.py`（历史数据迁移）、`insert_skills_133.sql` |
| `tests/` | pytest 测试（44 用例，mock provider 离线跑通） |
| `skills_library/` | 写作技能库：`genre-research`/`hot-novels`/`style-references`/`writing-techniques` + 技能蒸馏文档（`INDEX.md` 有目录） |
| `docs/` | 设计文档（RD 方案）+ 本目录三件套（architecture/filemap/decisions） |
| `_gen_skills/`、`.workbuddy/`、`ux_report/` | 内部工具/报告产物目录，一般无需阅读 |
| `_*.py`（`_extract_routes`/`_split_server`/`_smoke_*` 等） | 一次性开发脚本（路由对比/拆分/冒烟），非运行路径 |
| `*.md`（`数据隔离改造_*`、`竞品对比分析_20260904.md`） | 历史方案/分析文档，决策背景见 `docs/decisions.md` |

## novel_gen/（生成核心库，纯 Python 无 Web 依赖）

| 路径 | 职责 |
|---|---|
| `config.py` | 配置加载（YAML + 环境变量补 Key）、题材/字数/Provider 校验；**功能开关注册表 `FEATURE_FLAGS`**（默认值唯一来源，按 memory/writing/review/guard 模块分组，`python -m novel_gen.config` 可查看清单） |
| `llm/base.py` | LLM Provider 抽象基类（chat 模板方法：自动埋点 + 成本防护挂载） |
| `llm/factory.py` | Provider 工厂（deepseek/doubao/openai/mock） |
| `llm/usage.py` | Token 用量统计（模块1/2：累计 + 最近一次调用） |
| `llm/providers.py` | 各 Provider 实现 |
| `research/research.py` | 调研编排：内置知识库 + 可选联网搜索 → LLM 提炼共性报告 |
| `research/sources.py` | 搜索源（博查/Serper） |
| `research/schemas.py` | 调研数据结构 |
| `research/knowledge_base.json` | 内置热门小说共性知识库 |
| `pipeline/manager.py` | **`NovelPipeline`**：整条流水线编排 + 断点续跑 + 风格注入 |
| `pipeline/stages.py` | 阶段定义/推进 |
| `pipeline/agents/base.py` | Agent 基类（人设+技能 → System Prompt） |
| `pipeline/agents/researcher.py` `architect.py` `writer.py` `reviewer.py` `reader.py` `stylist.py` | 六角色实现（调研/大纲/正文/审校/读者/风格师） |
| `pipeline/agents/prompts/*.md` | 各角色专属提示词（**改角色行为改这里**） |
| `pipeline/memory.py` | 长文一致性：设定库 + 最近 N 章摘要回填 + L4.5 近章正文直读（P0-1）+ L1.5 人物状态注入（P0-2） |
| `pipeline/character_state.py` | 人物动态状态快照（P0-2）：确定性出场记录 + 可选 LLM 状态细节抽取，失败降级 |
| `pipeline/review_summary.py` | 全书质量汇总（P0-3）：改稿问题/质检均分/校验问题统计（纯函数） |
| `pipeline/editor.py` | 编辑质检（9 维审稿，<7 分自动修正） |
| `pipeline/reviser.py` | 章节重写/审校 |
| `pipeline/repetition.py` | 防重复/防同质化 |
| `pipeline/trace.py` | 全链路快照（模块1）：TraceSink 接口 + TraceRecorder |
| `pipeline/guard.py` | 成本防护（模块2）：AgentGuard 循环/Token 阈值 |
| `pipeline/quality.py` | 结果自校验（模块4）：QualityChecker + 1 次自动重写 |
| `pipeline/ai_taste.py` | AI 痕迹量化检测（P1-4）：连接词堆砌/情绪词直白/句式排比/段落雷同（纯函数，供质检扣分） |
| `pipeline/templates.py` | 提示词模板组装 |
| `store/project.py` | **`ProjectStore`**：项目落盘、状态机、断点续传、统计 |
| `store/schemas.py` | 项目数据结构（NovelConfig 等） |
| `export/book_formats.py` | TXT/EPUB/DOCX 导出 |
| `export/cover.py` | AI 封面生成（Pillow） |
| `utils/chapter_splitter.py` | 章节切分 |
| `utils/migrate.py` | 数据迁移 |
| `utils/paths.py` | 路径解析（projects/output/logs 目录） |

> 注：README 旧目录结构中的 `cli.py` / `__main__.py` 已不存在，当前入口为 Web 端。

## webapp/（FastAPI SaaS 服务）

| 路径 | 职责 |
|---|---|
| `server.py` | **组装壳**：导入注册路由 + 静态托管 dist + 启动（无业务逻辑） |
| `core.py` | **核心层**：FastAPI 实例/lifespan、鉴权、项目工具、结算、生成线程、任务持久化、登录限流 |
| `db/` | **MySQL 数据层包**（拆分自原 db.py 1409 行）：`__init__.py` 统一 re-export + 末尾 init_db()；`base.py` 连接池/事务/表结构 DDL；`users.py` 用户/密码/代理返佣/认证/会话/余额/管理统计；`projects.py` 项目归属/退款；`skills.py` 平台技能/用户技能/技能市场；`style_roles.py` 风格角色/挂载/种子化；`teardowns.py` 拆书库；`traces.py` agent_trace 全链路快照（MysqlTraceSink 落库/按 trace·project·user 查询/聚合统计） |
| `secret_encrypt.py` | BYOK Key Fernet 加密（`enc:v1:` 前缀） |
| `pricing.py` | 定价（普通 2 元/万字、高质量 3 元/万字、赠金/封面次数） |
| `referral.py` | 代理参数（AGENT_DISCOUNT/COMMISSION_RATE/COMMISSION_BASE/REFERRAL_BONUS） |
| `mailer.py` | 邮件（验证码/退款通知） |
| `verify_code.py` | 邮箱验证码逻辑 |
| `outline_parser.py` | 大纲文档解析（.txt/.md/.docx） |
| `logger.py` | 统一日志（logs/app.log，5MB×3 轮转） |
| `admin_cli.py` | 站长命令行管理 |
| `routers/system.py` | 系统域：health/定价/解析/邮件状态 |
| `routers/auth.py` | 账号：注册/登录/验证码/重置/绑代理 |
| `routers/me.py` | 我的：资料/余额/返佣/我的技能 |
| `routers/skills.py` | 平台技能/技能市场 |
| `routers/style_roles.py` | 风格角色 + 技能挂载 |
| `routers/projects_base.py` | 项目 CRUD/退费/归档/克隆/删除/下载 |
| `routers/projects_outline.py` | 三级大纲/快照/重生成 |
| `routers/projects_chapters.py` | 章节/审校/扩写/批量 |
| `routers/projects_world.py` | 世界观/人物/锁定/调研/伏笔/封面 |
| `routers/projects_generation.py` | 生成控制（启动/停止/重跑） |
| `routers/admin.py` | 站长后台（8 板块） |

## webapp/frontend/src/（Vue3 前端源码）

| 路径 | 职责 |
|---|---|
| `main.js` / `App.vue` | 应用入口/根组件 |
| `router/index.js` | 路由表 |
| `stores/auth.js` | 登录态 Pinia store |
| `stores/project.js` | 当前项目 Pinia store |
| `api/request.js` | axios 封装（token 注入/错误处理） |
| `api/auth.js` `novel.js` `project.js` `admin.js` `tools.js` | 按域接口封装 |
| `views/Login.vue` | 登录/注册页 |
| `views/ProjectList.vue` | 书案（作品列表/搜索/筛选） |
| `views/ProjectDetail.vue` | 项目详情（阅读器/进度/生成控制） |
| `views/Admin.vue` | 站长后台（8 板块） |
| `components/RechargeDialog.vue` | 充值/退款弹层 |
| `components/LlmSettingsDialog.vue` | BYOK Key 设置 |
| `components/tools/ToolsPanel.vue` | 工具面板（风格角色管理/技能挂载） |
| `components/outline/OutlineEditor.vue` | 三级大纲编辑器（拖拽） |
| `components/world/CharacterGraph.vue` | 人物关系图 |
| `layouts/MainLayout.vue` | 主布局（顶栏/侧栏） |
| `style.css` | 全局样式（深夜书房视觉语言） |
| `vite.config.js` / `package.json` | 构建配置/依赖 |

> 构建产物 `webapp/frontend/dist/` **不入库**（见 AGENTS.md §6）。

## 关联文档速查

| 问题 | 看哪里 |
|---|---|
| 某接口的路径/参数/返回 | `API.md` |
| 计费/返佣/退款/封面规则 | `WIKI.md` §4、`README.md` 计费节 |
| 数据库表结构 | `WIKI.md` §5、`webapp/db/base.py`（含 agent_trace） |
| 部署/发版/已知问题 | `WIKI.md` §7-9、`AGENTS.md` §5 |
| 为什么这么设计/踩过的坑 | `docs/decisions.md` |
| 模块职责/依赖/改哪里 | `docs/architecture.md` |
