# 架构文档（docs/architecture.md）

> 目的：一次性沉淀项目整体架构，供 AI 助手与开发者在新会话中直接引用，**避免每次从头扫描代码推导架构**。
> 权威来源关系：本文件描述"结构/职责/依赖"；接口细节以 `API.md` 为准；业务与部署细节以 `WIKI.md`、`README.md` 为准；本文件只维护结构级信息，随代码大改同步更新。

## 1. 系统全景

**墨造 · AI 小说创作平台**：输入题材/创意，自动产出完整小说（调研 → 大纲 → 人物世界观 → 逐章正文 → 质检 → 导出），同时是**多用户 SaaS**（注册登录、按 AI 实际生成字数计费、代理分销、退款、站长后台）。

```
┌──────────────────────────── 前端 Vue3 (webapp/frontend) ────────────────────────────┐
│  views(Login/ProjectList/ProjectDetail/Admin) + components + api 封装 + stores      │
└───────────────────────────────────┬─────────────────────────────────────────────────┘
                                     │ REST API（同源部署，dist 由 FastAPI 托管）
┌───────────────────────────────────▼─────────────────────────────────────────────────┐
│                   Web 服务层 webapp/（FastAPI + MySQL）                              │
│  server.py(组装壳) ── core.py(核心层) ── routers/(按域 9 个模块)                      │
│  db.py / secret_encrypt.py / pricing.py / referral.py / mailer.py / logger.py        │
└───────────────────────────────────┬─────────────────────────────────────────────────┘
                                     │ 调用生成流水线（后台线程，任务可持久化/断点续跑）
┌───────────────────────────────────▼─────────────────────────────────────────────────┐
│                  生成核心库 novel_gen/（纯 Python，无 Web 依赖）                     │
│  llm/(Provider 抽象)  research/(调研)  pipeline/(六角色 agents + 记忆 + 编排)         │
│  store/(项目落盘/状态机)  export/(TXT/EPUB/DOCX/封面)                                │
└──────────────────────────────────────────────────────────────────────────────────────┘
        产物落盘：projects/<项目>/（JSON 中间产物，断点续传权威源）
                  output/（成品）+ MySQL mozao（用户/余额/订单/skills）
```

**依赖方向**：`webapp` → `novel_gen`（core.py 通过 `_open_pipeline` 创建 `NovelPipeline`）；`novel_gen` 内部 `pipeline` → `llm`/`research`/`store`；`export` 独立。**novel_gen 不依赖 webapp**，可独立测试（mock provider 离线跑通）。

## 2. 模块划分与职责

### 2.1 novel_gen/（生成核心库）

| 模块 | 职责 | 关键文件 |
|---|---|---|
| `llm/` | LLM Provider 抽象：DeepSeek / 豆包(火山方舟) / OpenAI / mock；工厂创建；`base.py` chat 为模板方法（自动埋点 + 成本防护挂载）；`usage.py` Token 用量统计 | `base.py`、`factory.py`、`providers.py`、`usage.py` |
| `research/` | 调研阶段：内置知识库 `knowledge_base.json` + 可选联网搜索（博查/Serper）→ LLM 提炼共性报告；**P0-3 拆书库回流**：读取 store 的 `teardown_material.txt`（webapp 调研前写入的同题材爆款拆书条目）并入调研素材 | `research.py`、`sources.py`、`schemas.py` |
| `pipeline/` | 生成流水线核心：多智能体编排 + 记忆系统 + 编辑质检 + 防重复 + 模板 | 见 2.2 |
| `store/` | 项目落盘、状态机、断点续传、进度统计；`schemas.py` 的 `World` 含 `world_reveals`（世界观阶段揭示计划，跟随主角视角）；**P2** `ProjectStore` 新增场景落盘（`scenes.json` 分章场景清单+状态，供回看/编辑/复用）、`teardown_material.txt`（P0-3 拆书库回流素材）；**REQ-双锚** `teardown_style_anchor.txt`（题材文风锚，webapp 写/novel_gen 读）；`Chapter.user_edited`（用户编辑保存=人工修订标记，个人风格锚来源） | `project.py`（`ProjectStore`）、`schemas.py` |
| `export/` | 多格式导出 + AI 封面 | `book_formats.py`（TXT/EPUB/DOCX）、`cover.py` |
| `utils/` | 工具：章节切分、数据迁移、路径 | `chapter_splitter.py`、`migrate.py`、`paths.py` |
| `config.py` | 配置加载（YAML + 环境变量补 Key）；`AgentGuardConfig` 生产级加固配置（模块0）；**功能开关注册表 `FEATURE_FLAGS`**：开关默认值唯一事实来源，按 memory/writing/review/guard 模块分组，store 的 save/load_config 注册表驱动自动落盘 | — |
| `safety.py` | 输入防护（模块3）：Prompt 注入检测/System 清洗/输出合规校验（纯函数） | — |

> 注意：README 旧文中提到的 `novel_gen/cli.py`、`__main__.py` 已不存在；当前产品入口为 Web 端 `run_web.py`。

### 2.2 novel_gen/pipeline/（生成流水线，核心中的核心）

| 文件 | 职责 |
|---|---|
| `manager.py` | `NovelPipeline`：阶段编排（调研→大纲→质检→人物→逐章→导出）、风格注入 `_with_stylist`、断点续跑；**校验合并**（REQ-token优化 P1-5）：`_run_verify` 默认 `verify_combined_prompt` 一次调用输出 consistency/reader/editor 三类（verify_combined=False 回退原 3 连调用），一致性校验频率 `verify_every` 默认 5；**P0-1**：实例化 `ReaderAgent` 为正式角色成员，`_run_verify` 把其 persona（reader.md + reader 专属技能）注入读者视角检查（不增调用数）；`_build_setting_cards` 幂等接入分章设定卡索引；`_parse_json_obj` 容错解析；P2-2 全书终审 `_run_final_review`（resume/done 后，异常降级不阻塞导出结算）、编辑审稿 churn 落 trace；**REQ-双锚** 章节扩写循环注入个人旧稿锚（`extract_personal_anchor`）+ 题材锚（store 文件） |
| `orchestrator.py` | 架构总控 `Orchestrator`：任务拆解、**阶段门禁 StageGate**（产出→准出校验→不合格自动重生成≤N轮→超限仲裁放行）、跨模块冲突仲裁（主题>人物>世界观）；`manager.run_world/run_outline` 接入门禁（mock provider 默认跳过，保持离线行为一致） |
| `validator.py` | 独立第三方逻辑校验 Agent `LogicValidator`：5 类专项校验（设定一致性/人物OOC/主线偏移/重复情节≤5%/伏笔完整性）+ 机制卡三要素校验（语义/结算/代价终局，REQ-章内自洽），输出分级问题清单，供 StageGate 与质量报告复用 |
| `issue_triage.py` | 问题分级模型 `IssueItem`（fatal/major/minor）+ triage/has_fatal/sort 纯函数 |
| `report.py` | 成果整合：全书完成后落盘 4 份设计文档（核心设定册/全本骨架大纲/节奏执行手册/质量校验报告）到 `projects/<id>/design/`，失败降级不阻塞 |
| `agents/base.py` | Agent 基类（人设/技能注入 prompt 的统一入口） |
| `agents/researcher.py` `architect.py` `writer.py` `reviewer.py` `reader.py` `stylist.py` | 六角色：调研 / 三级大纲+人物世界观+时间线（`OutlineGenerator` 生成后按分卷区间回填 `volume_no`；世界观产出 `world_reveals` 阶段揭示计划）/ 逐章正文（叠加 Stylist 人设）/ 一致性·逻辑·合规审校 / 读者视角 / 风格师（写作风格角色：人设+挂载技能） |
| `agents/theme_character.py` `world_rules.py` `pacing.py` | 生产层细分（REQ）：主题与人物 Agent（主题命题/三层冲突/主角弧光/配角动机）/ 世界观与规则 Agent（世界观/金手指五要素/设定禁忌）/ 节奏与伏笔 Agent（关键节点/分卷节奏/伏笔回收闭环）；`ArchitectAgent` 收敛为骨架生产者并编排三者；**P0-2 接线**：architect 编排时把三者 persona（角色 prompt + 专属技能）分别注入 WorldGenerator（theme+rules）/OutlineGenerator（pacing），使细分角色要求真正进入 LLM 生成阶段（此前未读取，完全不生效） |
| `agents/opening.py` `ending.py` | **P1 新增两个专项 Agent**：开篇 Agent（黄金三章强开篇设计——第1章立主角+冲突点燃/第2章冲突升级+主动选择/第3章小闭环+强钩子，回填前三章 core_event/goal，architect.run_outline 编排）+ 结局 Agent（终局设计卡——终局状态/主题落点/主角最终选择/人物归宿/基调，写入 world.ending_card，architect.run_world 编排，末几章写手按此收束防烂尾）；二者均继承 BaseAgent 挂专属技能（opening→outline 阶段、ending→outline/world 阶段） |
| `agents/dialogue.py` | **P2 对白 Agent**：聚合全书人物 speech_style 生成对话风格指南（口吻区分+节奏规则），写回 world.dialogue_guideline，architect.run_world 编排，memory 强约束区注入正文生成；确定性聚合（零 LLM、不增 token 调用数），挂 dialogue 专属技能 |
| `agents/prompts/*.md` | 各角色专属提示词（修改角色行为改这里） |
| `memory.py` | 长文一致性：设定库（世界观/人物卡/伏笔）+ 最近 N 章摘要回填 + L4.5 近章正文直读（P0-1，默认 1200 字）+ L1.5 人物当前状态注入（P0-2）+ L1.6 世界状态承接（REQ-章内自洽：上一章机制数字/生死/物品/时间/事实，账本章号<当前章才注入）+ 配角动机卡注入出场人物区 + **分章设定卡子集注入**（REQ-token优化 P1-1：有 `setting_cards.json` 时按章只注入命中人物/生物/地点/势力/伏笔，空列表=命中无项不注入、索引缺失/关闭回退全量；强约束区机制卡/主题/弧光/金手指/禁忌/时钟永不裁剪）+ 世界观**跟随主角视角分层注入**（有 `world_reveals` 时：已揭示进普通区、未揭示进强约束区禁剧透；无则回退全量注入）+ **P1 物品卡/等级体系卡/结局卡进强约束区**（物品卡防万能工具、等级体系防战力崩坏、结局卡约束末几章收束方向）+ **P2 术语统一卡/对话风格进强约束区** |
| `setting_cards.py` | **分章设定卡索引**（REQ-token优化 P1-1，纯 Python 前置确定性脚本）：world 落盘后构建 `projects/<id>/setting_cards.json`——出场人物沿用 `memory.predict_appearing_chars` 预测，生物/地点/势力/伏笔按「名称直判（本章 goal/core/出场人物名含其名）+ 关键词包含」双条件匹配；manager 幂等接入（world 后 + 惰性两种路径），构建失败/关闭静默降级全量注入 |
| `character_state.py` | 人物动态状态快照（P0-2）：每章后确定性出场记录 + 可选 LLM 状态细节抽取（`_should_llm_extract` 按 `state_extract_frequency` 降频，默认每 3 章 1 次 LLM），失败静默降级，写入 character_states.json |
| `chapter_state.py` | **章内/章间状态账本**（REQ-章内自洽与机制账本）：章末/场景状态抽取（机制数字/人物生死/物品归属/时间锚点/已发生事实），LLM 主抽取（`_should_llm_extract` 按 `state_extract_frequency` 降频）+ 确定性面板正则兜底 + 失败降级；manager 写章循环落 `chapter_state.json`，stages 场景级生成逐场景累积「已推进状态」注入后续场景（防面板数字漏减、角色死而复生、物品归属错乱） |
| `review_summary.py` | 全书质量汇总（P0-3）：改稿问题类型分布 / 质检平均分 / 校验问题统计（纯函数，供 /review 接口聚合） |
| `editor.py` | 编辑质检（大纲后 9 维审稿，<7 分自动修正）；**diff 式修正**（REQ-token优化 P1-6）：`_revise_diff` 只输出 `{"updates":[{no,title?,intro?,goal?}]}`（max_tokens 4096→1200），`_apply_updates` Python 合并回原大纲，解析失败/关闭回退 `_revise_whole` 整章重出 |
| `reviser.py` | 单章重写/审校入口 |
| `repetition.py` | 防同质化常驻提示词、防重复 |
| `style_anchor.py` | 双锚风格锚定（REQ-双锚风格锚定）：个人旧稿锚 `extract_personal_anchor`（只抽 `user_edited` 章、限段限长）+ 题材锚组装 `build_teardown_anchor`（styles/takeaways/excerpts）+ 统一包装 `wrap_style_anchor`；`ChapterWriter`（stages.py）懒提取个人锚 `_personal_anchor` + 懒读 store 题材锚，经 `personal_anchor`/`teardown_anchor` 参数注入 scene/chapter prompt——**覆盖正文生成、单章重写、从第 N 章重生成全部写章路径** |
| `trace.py` | 全链路快照（模块1）：TraceSink 接口 + TraceRecorder（trace_id/project/user/scene 上下文，step_order 自增） |
| `guard.py` | 成本防护（模块2）：AgentGuard 循环次数/场景限次/累计 Token 阈值，超限抛 `AgentGuardError` 并留痕 |
| `quality.py` | 结果自校验（模块4）：QualityChecker 确定性规则（字数/重复/合规/章内同句复读）+ 读者视角 LLM 校验（章内矛盾自检 `consistency_llm_check` 默认仅高质量模式）+ 1 次自动重写；**定点重写**（REQ-token优化 P1-4）：`_locate_problem_spans` 按 issue pos 段落→detail 原句锚点定位窗口 ±200 字，窗口覆盖 <60% 正文才局部重写并 Python 拼回，无定位/跨度过大回退 `_rewrite_whole` 整章重出；含 P1-4 AI 痕迹扣分项；`auto_revised` 真值落 review.json（P2-4）；章内同句复读（REQ-章内自洽）：叙述≥3次/长句2次/面板行2次→大改触发重写，对话口头禅≤2豁免 |
| `ai_taste.py` | AI 痕迹量化检测（P1-4）：确定性规则给分（连接词/情绪词/排比/段落雷同），只扣分不阻断 |
| `skills.py` | **技能统一注入管线**（REQ-技能统一）：`SkillAssembler` 统一采集 平台全局/平台角色/用户技能/项目快照（均读统一表 `skills`，source 区分 platform/user），按角色默认阶段（STAGE_BY_ROLE）过滤（技能 applicable_stages 空=全量）、统一清洗、产出 manifest（供 `agent_trace` 归因）；**P0-2**：STAGE_BY_ROLE 补 theme_character/world_rules/pacing 三 key、移除 validator 死配置（非 BaseAgent 不参与技能注入）；`agents/base.py` 与 `templates._skill_append` 均经此管线 |
| `templates.py` | 提示词模板组装；P2-3 动态模块随机化 `_pick_dynamic_module`（seed 可复现、相邻去重）；P2-1 风格范文锚定 `_style_reference`（截 3000 字，空则不注入）；P2-2 `final_review_prompt` 终审编辑；**P1** `opening_prompt`/`ending_prompt`（开篇/结局 Agent 任务提示词）；P0-1 `verify_combined_prompt` 支持 `reader_persona` 注入；**P2** 大纲 prompt（open/expand/continue）chapters 字段新增 `cliffhanger`（章末钩子）；**REQ-双锚** scene/chapter 注入题材锚（`teardown_anchor` 参数，开关 `teardown_style_anchor` 控制）、`expand_chapter_prompt` 补风格范文+个人锚+题材锚（弱约束，空不注入） |

### 2.3 webapp/（SaaS Web 服务）

| 文件 | 职责 |
|---|---|
| `server.py` | **组装壳**：导入注册路由 + 静态托管 `frontend/dist` + 启动入口（`python -m webapp.server` / `run_web.py`）。不写业务逻辑 |
| `core.py` | **核心层**：FastAPI 实例与 lifespan、鉴权（`_require_user`/`_require_admin`/`_check_project_access`）、项目工具（`_find_project`/`_open_pipeline`）、结算（`_compute_settle`/`_settle_incremental`/`_settle_price`）、生成线程（`_RunHandle`/信号量）、任务持久化（`_mark_task`/`_resume_interrupted_tasks`）、登录限流、项目创建/列表等公共逻辑；**P0-3 拆书库回流** `_inject_teardown_material`（调研前查同题材拆书条目写入 store 素材，供 ResearchEngine 消费）；**REQ-双锚** `_inject_teardown_style_anchor`（同题材拆书条目抽 styles/takeaways/excerpts 组装题材锚写入 store，供写手阶段消费，失败静默；`_run_generation` 入口统一注入——**含 start_from>0 从第 N 章重生成**，旧项目重生成也能拿到题材锚） |
| `routers/` | 按域拆分路由（见 2.4） |
| `db/` | MySQL 数据层包：`base.py`（连接池/事务/表结构/init_db，含 style_roles.reference_text、skills 系列表 applicable_stages 与统一表 `skills` 幂等迁移）、`users.py`（用户/返佣/认证/余额）、`projects.py`（项目归属/退款）、`skills.py`（技能/市场，统一读写 `skills` 表，platform/user/market 三源 + applicable_stages/version 透传）、`style_roles.py`（风格角色，reference_text 透传，挂载技能经 `skills` 表解析）、`teardowns.py`（拆书库）、`traces.py`（agent_trace 全链路快照：MysqlTraceSink 落库 + 按 trace/project/user 查询 + P2-4 `_aggregate_quality`/`quality_report` 质量聚合报表）；`__init__.py` 统一 re-export，调用方 `from webapp import db` 不变 |
| `secret_encrypt.py` | BYOK Key 加密（Fernet，密文前缀 `enc:v1:`） |
| `pricing.py` | 定价档位（普通 2 元/万字、高质量 3 元/万字） |
| `referral.py` | 代理商业参数：`AGENT_DISCOUNT`(0.8) / `COMMISSION_RATE`(0.1) / `COMMISSION_BASE` / `REFERRAL_BONUS` |
| `mailer.py` | 邮件通知（验证码/退款通知站长） |
| `verify_code.py` | 邮箱验证码（6 位、10 分钟、60s 冷却、错 5 次作废） |
| `outline_parser.py` | 上传大纲文档解析（.txt/.md/.docx → 结构化字段） |
| `logger.py` | 统一日志（`logs/app.log`，5MB 轮转 ×3） |
| `admin_cli.py` | 站长命令行管理（users/balance/recharge/agent/...） |

### 2.4 webapp/routers/（9 个路由模块，共 96 路由）

| 模块 | 域 | 说明 |
|---|---|---|
| `system.py` | 系统 | health、定价、解析、邮件状态等 |
| `auth.py` | 账号 | 注册/登录/验证码/重置密码/绑定代理 |
| `me.py` | 我的 | 个人资料、余额、返佣、我的技能 |
| `skills.py` | Skills | 平台写作技能、技能市场 |
| `style_roles.py` | 风格角色 | 风格师增删改 + 技能挂载 + `reference_text` 范文透传（P2-1） |
| `projects_base.py` | 项目管理 | 列表/创建/详情/退费/归档/克隆/删除/下载（8）；建书快照 `style_role_reference` 进 config（P2-1） |
| `projects_outline.py` | 大纲 | 三级大纲/快照/重生成（7） |
| `projects_chapters.py` | 章节 | 章节 CRUD/审校/扩写/批量（13）；/review 返回 `final_review`（P2-2）；**REQ-双锚** `expand-selection` 注入风格范文+个人旧稿锚、`edit_chapter` 落 `user_edited` 人工修订标记 |
| `projects_world.py` | 世界观 | 人物卡/世界观/锁定/调研/伏笔/封面（10） |
| `projects_generation.py` | 生成控制 | 启动/停止/重跑（5） |
| `admin.py` | 站长后台 | 用户/代理/项目/退款/流水/日志/系统参数 + `GET /api/admin/quality-report` 质量报表（P2-4） |

> 拆分史：原 `webapp/server.py` 2853 行单文件 → `core.py` + `routers/`（2026-09-20，拆后 openapi 路由集合 96=96 逐字节一致）。**新增业务路由时按域放入对应 routers 模块，禁止回写 core.py/server.py 堆逻辑**。

### 2.5 前端 webapp/frontend/src/（Vue 3 + Element Plus + Vite）

| 目录 | 职责 |
|---|---|
| `api/` | 请求封装：`request.js`（axios + token 注入）、`auth/novel/project/admin/tools` 按域分文件 |
| `router/index.js` | 路由表（登录/书案/详情/后台） |
| `stores/` | Pinia：`auth.js`（登录态）、`project.js`（当前项目） |
| `views/` | 页面：`Login` `ProjectList`（书案） `ProjectDetail`（沉浸阅读+编辑器） `Admin`（9 板块后台，含 P2-4 质量报表 tab：得分分布/问题类型/弃书风险/阶段成本） |
| `components/` | `RechargeDialog` 充值、`LlmSettingsDialog` BYOK、`tools/ToolsPanel` 工具面板（风格角色管理 + P2-1 参考文风字段 + P2-2 全书终审块）、`outline/OutlineEditor` 三级大纲、`world/CharacterGraph` 人物关系、`layouts/MainLayout` |
| `style.css` | 全局样式（深夜书房 × AI 工坊视觉语言） |

## 3. 核心类与接口（改代码先看这里）

### 3.1 novel_gen
- `NovelPipeline`（`pipeline/manager.py`）：整条流水线的唯一编排入口；webapp 通过 `core._open_pipeline(store)` 创建。
- `ProjectStore`（`store/project.py`）：单项目门面——读/写产物文件、状态机推进、进度统计；**产物文件（JSON）是项目状态唯一权威来源**。
- Agent 基类（`pipeline/agents/base.py`）：所有角色继承，统一实现"人设 + 挂载技能 → System Prompt"注入。
- `llm/factory.py`：按 provider 字符串创建客户端；`core._resolve_llm` 决定用平台 Key 还是用户 BYOK Key。

### 3.2 webapp（core.py 关键函数）
- 鉴权链：`_require_user`（Header token）→ `_require_admin` → `_check_project_access`（项目归属/管理员全可见）。
- 结算链：`_compute_settle`（统一口径：增量/整批）→ `_settle_incremental`（**先落盘结算基线再扣费**）→ `_grant_consume_commission`（返佣）。
- 生成控制：`_acquire_slot`（全局信号量 3 并发）→ `_run_generation`/`_run_outline_only`/`_run_batch`（后台线程）→ `_mark_task`/`_clear_task`（task.json）。
- 任务持久化：`_resume_interrupted_tasks`（lifespan 启动时重拾中断任务）。

### 3.3 数据库（MySQL，库 `mozao`）
| 表 | 用途 |
|---|---|
| `users` | 用户/余额/身份(is_admin)/折扣/邀请码 |
| `sessions` | 登录会话 token |
| `project_owner` | 项目归属（项目 ID ↔ 用户） |
| `recharges` | 充值流水 |
| `commission_log` | 代理返佣明细 |
| `refund_requests` | 退款申请与处理 |
| `skills` | **统一技能表（REQ-技能统一 阶段二）**：source∈{platform,user,market} + owner_id 区分权限/信任；platform=管理员可信、user/market=用户可写（拼 system 前清洗）；含 role/applicable_stages/version/downloads/market_id；旧 `writing_skills`/`user_skills`/`skill_market` 保留不 DROP（迁移回退） |
| `style_roles` / `style_role_skills` | 风格角色（人设 + P2-1 参考文风 `reference_text`）+ 挂载技能 |
| `agent_trace` | Agent 全链路执行快照（LLM 调用明细/成本事件/质量校验；trace_id 串联单次任务，预留 user_id/scene/extra 支撑监控与用户分析）；P2-4 `quality_report` 据此聚合质检分布/重写率/弃书风险 |

## 4. 关键数据流

**创建项目**：前端表单（含费用预估）→ `POST /api/projects`（core 校验计费封顶）→ 落 `project_owner` + 创建 `projects/<id>/` 目录（config.json 快照，**不含 API Key**）。

**生成**：`POST /api/projects/{id}/generate` → 鉴权/访问检查 → `_acquire_slot` → 落 `task.json` → 后台线程 `_run_generation`：`NovelPipeline` 逐阶段推进（调研→大纲→编辑质检→人物→逐章），每章完成写 `chapters/<n>.json` → 完成后结算 `_settle_incremental`（增量先落基线再扣费 + 返佣）→ `_clear_task`。

**断点续传**：任何阶段中断，重启后 `_resume_interrupted_tasks` 扫描残留 `task.json` → 重新打开 pipeline → 逐阶段检查产物文件存在性 → 只补缺失部分（幂等）。

**导出**：`projects/<id>/output/` 生成 TXT/EPUB/DOCX + 封面 PNG（限 5 次），同时拷到全局 `output/`。

## 5. 关键机制（为什么这么设计）

| 机制 | 设计 | 目的 |
|---|---|---|
| 产物文件 = 唯一权威 | 项目状态完全由 `projects/<id>/` 下 JSON 决定，DB 只存归属/订单 | 断点续传幂等、不重复扣 token |
| 先落结算基线再扣费 | `_settle_incremental` 先写结算基线后扣余额 | 消除"扣费后崩溃重启重复扣费"窗口（宁可漏扣不重扣） |
| 任务持久化 | 线程启动落 `task.json`，lifespan 重拾 | 服务重启不丢生成任务 |
| `meta.json` 轻量缓存 | 项目列表页统计走 meta 缓存，章节未变化零文件读取 | 列表页性能 |
| BYOK 加密落库 | Fernet，密文前缀 `enc:v1:`；`.llm_encrypt.key` 不入库 | DB 泄露不暴露用户自配 Key |
| 登录限流 | `_LOGIN_MAX=8` / 5 分钟窗口 | 防爆破 |
| 全链路 trace（模块1） | LLM 层模板方法自动埋点（Prompt/返回/Token/耗时/错误栈）→ MySQL `agent_trace`；trace_id 串联单次任务；pipeline 阶段级 `agent_step` 语义记录 | 问题回溯、阶段2 监控/成本报表数据底座；sink 失败自动降级不阻塞 |
| 成本防护（模块2） | LLM 调用计数 + 累计 Token 阈值，超限抛 `AgentGuardError`；生成线程捕获后保留产物 + 超限提示（可断点继续）；事件写 trace | 防死循环/成本爆炸；mock 不挂 guard；`agent_guard.*` 可开关 |
| 输入/输出防护（模块3） | API 输入注入检测（命中 400）+ 拼 system 前清洗（custom_prompt/skill/用户技能）+ 输出基础合规校验 | 防 Prompt 注入篡改系统约束；正常创作输入零误报 |
| 结果自校验（模块4） | 每章保存前 QualityChecker（字数/重复/合规确定性规则 + 读者视角 LLM）→ 1 次自动重写 → 仍不通过标记 review.json + trace | 减少人工返工；异常降级不阻塞 |
| 全局并发信号量 | `_GEN_SEMAPHORE = Semaphore(3)` | 防过载 |

## 6. 部署架构

- 服务器：阿里云实例 `i-bp14oxtecrth3wbq6thc`，目录 `/opt/novel-gen`，`docker compose`（`mysql-mozao` + `novel-gen` 两个容器）。
- 前端多阶段构建：Dockerfile 第一阶段 `node:20-alpine` 内 `npm ci && npm run build`，第二阶段拷 dist 进 Python 镜像（`python:3.13-slim`，内置 Noto CJK 字体、Asia/Shanghai）。**本地无需 build、dist 不入库**。
- 数据卷：`projects`（中间产物）/ `output`（成品）/ `logs` / `data`（MySQL 数据，`NOVEL_DB_PATH=/data/novel_web.db` 由环境变量控制，容器实际走 mysql-mozao 服务）。
- 反代：Caddy（`deploy/Caddyfile`，域名 https://mozao.click）。
- 发版：`git archive master` → workbench upload → 解压 → `docker compose up -d --build`。详见 `AGENTS.md` §5。

## 7. 改代码前的指引（防迷路）

1. **新业务路由** → `webapp/routers/<对应域>.py`，数据操作走 `webapp/db/` 对应域子模块已有函数，或在该域子模块内新增（禁止回写 base.py 堆 SQL）。
2. **改生成逻辑/提示词** → `novel_gen/pipeline/`（角色行为改 `agents/prompts/*.md`，编排改 `manager.py`）。
3. **改计费/返佣** → `webapp/pricing.py`、`webapp/referral.py`、`webapp/core.py` 结算链。
4. **改前端页面** → `webapp/frontend/src/views|components/`，接口封装在 `src/api/`。
5. 不确定某文件干什么 → 先查 `docs/filemap.md`；涉及历史决策 → 查 `docs/decisions.md`。
