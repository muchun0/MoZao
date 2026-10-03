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
| `deploy/` | 部署辅助：`Caddyfile`+`caddy.service`（反代）、`migrate_sqlite_to_mysql.py`（历史数据迁移）、`insert_skills_133.sql`、`migrate_skills_merge.py`（技能表统一迁移）、`sync_skills_library.py`（skills_library 蒸馏同步到统一 skills 表） |
| `tests/` | pytest 测试（296+ 用例，mock provider 离线跑通；含 `test_volume_link.py` 分卷-章节关联、`test_world_reveal.py` 世界观逐步展开、`test_skill_assembler.py` 技能统一注入管线） |
| `skills_library/` | 写作技能库：`genre-research`/`hot-novels`/`style-references`/`writing-techniques` + 技能蒸馏文档（`INDEX.md` 有目录）；`style-references` 种子化进 style_roles，`writing-techniques` 由 `deploy/sync_skills_library.py` 蒸馏为平台技能 |
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
| `research/research.py` | 调研编排：内置知识库 + 可选联网搜索 → LLM 提炼共性报告；**P0-3** 读取 `store.load_teardown_material()`（拆书库回流素材）并入调研素材 |
| `research/sources.py` | 搜索源（博查/Serper） |
| `research/schemas.py` | 调研数据结构 |
| `research/knowledge_base.json` | 内置热门小说共性知识库 |
| `pipeline/manager.py` | **`NovelPipeline`**：整条流水线编排 + 断点续跑 + 风格注入；**校验合并**（REQ-token优化 P1-5：`_run_verify` 默认 1 次调用出 consistency/reader/editor 三类，`verify_every` 默认 5）+ `_build_setting_cards` 幂等接入；**P0-1** 实例化 `ReaderAgent`，`_run_verify` 注入其 persona 到读者视角检查；P2-2 全书终审 `_run_final_review`（resume/done 后，异常降级不阻塞导出）；编辑审稿 churn 落 trace |
| `pipeline/orchestrator.py` | 架构总控 `Orchestrator`：阶段门禁 StageGate（产出→准出校验→自动重生成≤N轮→仲裁放行）+ 冲突仲裁；`manager.run_world/run_outline` 接入（mock provider 默认跳过） |
| `pipeline/validator.py` | 独立逻辑校验 `LogicValidator`：设定一致性/OOC/主线偏移/重复≤5%/伏笔完整性 5 类校验 + 机制卡三要素（语义/结算/代价终局，REQ-章内自洽） + 分级 |
| `pipeline/issue_triage.py` | 问题分级 `IssueItem`(fatal/major/minor) + triage/has_fatal/sort 纯函数 |
| `pipeline/report.py` | 成果整合：落盘 4 份设计文档到 `projects/<id>/design/`（核心设定册/骨架大纲/节奏手册/质量报告） |
| `pipeline/stages.py` | 阶段定义/推进；场景级生成逐场景累积「本章已推进状态」注入后续场景（REQ-章内自洽）；**REQ-token优化**：行动表并入拆场景输出 `characters_action`（`action_plan_first=False` 默认无独立调用）、场景状态抽取按 `scene_state_mode` 分流（auto=普通确定性/高质量 LLM）、普通模式整章润色默认关（polish_hq_only）、场景 prompt 按 `scene_delta_inject` 分层注入（首场景全量 memory、后续场景增量块） |
| `pipeline/agents/base.py` | Agent 基类（人设+技能 → System Prompt） |
| `pipeline/agents/researcher.py` `architect.py` `writer.py` `reviewer.py` `reader.py` `stylist.py` | 六角色实现（调研/大纲/正文/审校/读者/风格师） |
| `pipeline/agents/theme_character.py` `world_rules.py` `pacing.py` | 生产层细分：主题与人物 / 世界观与规则 / 节奏与伏笔 专项 Agent（认领结构化锚点 + 确定性推导关键节点/分卷节奏/伏笔回收闭环）；**P0-2** architect 编排时把三者 persona+技能注入对应生成阶段 |
| `pipeline/agents/prompts/*.md` | 各角色专属提示词（**改角色行为改这里**） |
| `pipeline/memory.py` | 长文一致性：设定库 + 最近 N 章摘要回填 + L4.5 近章正文直读（P0-1，默认 1200 字）+ L1.5 人物状态注入（P0-2）+ L1.6 世界状态承接（REQ-章内自洽：上一章机制数字/生死/物品/时间/事实）+ 配角动机卡注入 + **分章设定卡子集注入**（REQ-token优化 P1-1：按章只注入命中人物/生物/地点/势力/伏笔；空列表=命中无项不注入、索引缺失/关闭回退全量；强约束区永不裁剪） |
| `pipeline/setting_cards.py` | **分章设定卡索引**（REQ-token优化 P1-1，纯 Python 前置确定性）：world 后构建 `setting_cards.json`，出场人物用 `memory.predict_appearing_chars` 预测，生物/地点/势力/伏笔按「名称直判+关键词包含」匹配；失败/关闭静默降级全量注入 |
| `pipeline/character_state.py` | 人物动态状态快照（P0-2）：确定性出场记录 + 可选 LLM 状态细节抽取（`_should_llm_extract` 按 `state_extract_frequency` 默认每 3 章 1 次），失败降级 |
| `pipeline/chapter_state.py` | **章内/章间状态账本**（REQ-章内自洽）：章末/场景状态抽取（机制数字/生死/物品/时间/事实），LLM 主抽取（`_should_llm_extract` 降频）+ 确定性面板正则兜底；章间承接防数字断链、场景间推进防死而复生 |
| `pipeline/review_summary.py` | 全书质量汇总（P0-3）：改稿问题/质检均分/校验问题统计（纯函数） |
| `pipeline/editor.py` | 编辑质检（9 维审稿，<7 分自动修正）；**diff 式修正**（REQ-token优化 P1-6）：`_revise_diff` 只输出需改字段（max_tokens 1200）+ `_apply_updates` Python 合并，失败/关闭回退整章重出 |
| `pipeline/reviser.py` | 章节重写/审校 |
| `pipeline/repetition.py` | 防重复/防同质化 |
| `pipeline/trace.py` | 全链路快照（模块1）：TraceSink 接口 + TraceRecorder；`record_quality` 带 score/issue_count/auto_revised（P2-4 数据底座） |
| `pipeline/guard.py` | 成本防护（模块2）：AgentGuard 循环/Token 阈值 |
| `pipeline/quality.py` | 结果自校验（模块4）：QualityChecker + 1 次自动重写；含章内同句复读检测（叙述≥3/长句2/面板行2→大改，对话口头禅≤2豁免）；章内矛盾 LLM 自检默认仅高质量模式（consistency_llm_check）；**定点重写**（REQ-token优化 P1-4：issue pos/原句锚点定位窗口 ±200 字，<60% 正文局部重写拼回，无定位回退整章）；`auto_revised` 真值落 review.json（P2-4） |
| `pipeline/ai_taste.py` | AI 痕迹量化检测（P1-4）：连接词堆砌/情绪词直白/句式排比/段落雷同（纯函数，供质检扣分） |
| `pipeline/skills.py` | 技能统一注入管线（REQ-技能统一）：`SkillAssembler` 统一采集 平台全局/平台角色/用户技能/项目快照，按角色默认阶段过滤（applicable_stages 空=全量）、清洗、产出 manifest（供 trace 归因）；**P0-2** STAGE_BY_ROLE 补 theme_character/world_rules/pacing、移除 validator 死配置 |
| `pipeline/templates.py` | 提示词模板组装；P2-3 动态模块随机化 `_pick_dynamic_module`（seed 可复现、相邻去重）；P2-1 风格范文锚定 `_style_reference`（截 3000 字，空则不注入）；P2-2 `final_review_prompt` 终审编辑 |
| `store/project.py` | **`ProjectStore`**：项目落盘、状态机、断点续传、统计；save/load_config 持久化 `style_role_reference`；`final_review.json` 读写（P2-2）；`setting_cards.json` 读写（REQ-token优化 P1-1：`setting_cards_path`/`save_setting_cards`/`load_setting_cards`）；**P0-3** `teardown_material.txt` 读写（拆书库回流素材） |
| `store/schemas.py` | 项目数据结构（NovelConfig 等） |
| `store/schemas.py` | 项目数据结构；多Agent协作扩展：`World` 新增 theme_proposition/conflict_matrix/protagonist_arc/character_motives/goldfinger_rules(五要素)/setting_taboos，`foreshadowing` 升级为结构化 `ForeshadowItem`(buried_ch/reveal_ch/level)，`Outline` 新增 key_nodes/volume_pacing；全部 from_dict 兼容旧数据 |
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
| `core.py` | **核心层**：FastAPI 实例/lifespan、鉴权、项目工具、结算、生成线程、任务持久化、登录限流；**P0-3** `_inject_teardown_material` 拆书库回流（调研前注入同题材拆书条目） |
| `db/` | **MySQL 数据层包**（拆分自原 db.py 1409 行）：`__init__.py` 统一 re-export + 末尾 init_db()；`base.py` 连接池/事务/表结构 DDL（style_roles.reference_text、skills 系列表 applicable_stages、统一表 `skills` 幂等迁移）；`users.py` 用户/密码/代理返佣/认证/会话/余额/管理统计；`projects.py` 项目归属/退款；`skills.py` 技能域（**统一读写 `skills` 表**，platform/user/market 三源 + applicable_stages/version/market_id 透传，旧表保留不回写）；`style_roles.py` 风格角色/挂载/种子化（挂载技能经 `skills` 表按 source 解析）；`teardowns.py` 拆书库；`traces.py` agent_trace 全链路快照（MysqlTraceSink 落库/按 trace·project·user 查询/聚合统计 + P2-4 `_aggregate_quality` 纯函数与 `quality_report` 报表接口） |
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
| `routers/style_roles.py` | 风格角色 + 技能挂载（reference_text 透传） |
| `routers/projects_base.py` | 项目 CRUD/退费/归档/克隆/删除/下载（建书快照 style_role_reference） |
| `routers/projects_outline.py` | 三级大纲/快照/重生成 |
| `routers/projects_chapters.py` | 章节/审校/扩写/批量（/review 返回 final_review） |
| `routers/projects_world.py` | 世界观/人物/锁定/调研/伏笔/封面 |
| `routers/projects_generation.py` | 生成控制（启动/停止/重跑） |
| `routers/admin.py` | 站长后台（9 板块，含 /api/admin/quality-report 质量报表） |

## webapp/frontend/src/（Vue3 前端源码）

| 路径 | 职责 |
|---|---|
| `main.js` / `App.vue` | 应用入口/根组件 |
| `router/index.js` | 路由表 |
| `stores/auth.js` | 登录态 Pinia store |
| `stores/project.js` | 当前项目 Pinia store |
| `api/request.js` | axios 封装（token 注入/错误处理） |
| `api/auth.js` `novel.js` `project.js` `admin.js` `tools.js` `me.js` | 按域接口封装（me.js：工作台-资料/流水/返佣/退款） |
| `views/Login.vue` | 登录/注册页 |
| `views/ProjectList.vue` | 书案（作品列表/搜索/筛选） |
| `views/ProjectDetail.vue` | 项目详情（阅读器/进度/生成控制） |
| `views/Workspace.vue` | 用户工作台（概览/账户账单/推广返佣/模型配置/我的技能/风格角色 6 tab，复用 /api/me/*） |
| `views/Admin.vue` | 站长后台（9 板块，含质量报表 tab：得分分布/问题类型/弃书风险/阶段成本） |
| `components/RechargeDialog.vue` | 充值/退款弹层 |
| `components/LlmSettingsDialog.vue` | BYOK Key 设置 |
| `components/tools/ToolsPanel.vue` | 工具面板（封面/质量校验/改稿建议+全书终审/节奏分析/多方案/批量/伏笔/锁定设定；技能与风格角色管理已迁移至工作台） |
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
