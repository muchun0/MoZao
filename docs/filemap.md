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
| `deploy/` | 部署辅助：`insert_skills_133.sql`（历史技能种子）；反代由服务器 nginx 承担（`/etc/nginx/conf.d/mozao.conf`），仓库内不再维护反代配置 |
| `tests/` | pytest 测试（700+ 用例，mock provider 离线跑通；含三期 `test_beat_gate.py`/`test_beat_gate_wiring.py`/`test_scene_inspector.py`；**小墨AI** `test_assistant_context.py`/`test_assistant_cards.py`/`test_assistant_service.py`，P1；**REQ-Agent用户化 阶段0/0.5（2026-10-25）** `test_settle_per_version.py`（版本级结算 P0）/`test_settle_migration.py`（v1→v2 迁移不重扣）/`test_settle_byok_stop.py`（BYOK 不被平台余额误停）/`test_settle_audit_cli.py`（对账退出码）/`test_wordcount.py`（计费字数唯一事实来源）/`test_settle_wiring.py`（生成链路接线冒烟：大纲/世界观/章节/重写真实产出账本条目）；**阶段1A** `test_versioned_product.py`（版本化门面 P0：commit 幂等/legacy 初始化/淘汰跳 selected/promote=False/quality 透传/user_managed/upstream 自动读取/run_id duck-typing/flag parity）/`test_outline_world_versions.py`（大纲/世界观版本库 P1：首版 v1、存量迁移、select 回读、一次 run 仅 1 版本=commit_head 收尾）/`test_versioned_concurrency.py`（P1：并发不丢版本、索引↔文件对账、version_check CLI 退出码）；**阶段1B（2026-10-25）** `test_agent_prompts.py`（P1：人设懒快照 v1/保存递增/sha256/白名单）/`test_agent_run_guards.py`（P0 资金守卫+P1：402/409/手写章/skill 上限/开关 503）/`test_agent_run_api.py`（P0：202 返回体/四态状态机/停止语义/resume 沿用 task_id）/`test_provenance_meta.py`（P1：run 侧写字段齐全/prompt_version 格式/人设注入不落盘/4KB 截断/200 保留剪枝/commit 自动带 run_id）；**阶段1C（2026-10-25）** `test_product_versions_api.py`（P1：版本金额反查/产物版本列表/select 回滚/rollback-impact 影响范围/agent-prompts 读写/stop 语义/rewrite 槽位 409）） |
| `tools/` | 运维/校验脚本：`projection_check.py`（投影一致性检查，lagging 退出码 2）、`settle_audit.py`（**结算三方对账**：账本内部 / MySQL 项目累计金额 / 章节已计费版本覆盖率，只读不补扣，差异退出码 1）、`version_check.py`（**版本库对账**，阶段1A：chapter/outline/world 索引↔文件 orphan/missing/selected_mismatch + chapter 孤儿存档目录，只读，差异退出码 1，支持 `--all`） |
| `skills_library/` | 写作技能库：`style-references`（style_roles 种子化运行时依赖，Dockerfile COPY 进镜像）/`writing-techniques`（已蒸馏为平台技能入库）+ 技能蒸馏文档（`INDEX.md` 有目录）；不再有仓库内蒸馏脚本（收敛已执行完成） |
| `docs/` | 设计文档（RD 方案）+ 本目录三件套（architecture/filemap/decisions） |
| `_gen_skills/`、`.workbuddy/`、`ux_report/` | 内部工具/报告产物目录，一般无需阅读 |
| `_*.py`（`_extract_routes`/`_split_server`/`_smoke_*` 等） | 一次性开发脚本（路由对比/拆分/冒烟），非运行路径 |
| `*.md`（`数据隔离改造_*`、`竞品对比分析_20260904.md`） | 历史方案/分析文档，决策背景见 `docs/decisions.md` |

## novel_gen/（生成核心库，纯 Python 无 Web 依赖）

| 路径 | 职责 |
|---|---|
| `config.py` | 配置加载（YAML + 环境变量补 Key）、题材/字数/Provider 校验；**功能开关注册表 `FEATURE_FLAGS`**（默认值唯一来源，按 memory/writing/review/guard/assistant 模块分组共 100 项，`python -m novel_gen.config` 可查看清单） |
| `llm/base.py` | LLM Provider 抽象基类（chat 模板方法：自动埋点 + 成本防护挂载） |
| `llm/factory.py` | Provider 工厂（deepseek/doubao/openai/mock） |
| `llm/usage.py` | Token 用量统计（模块1/2：累计 + 最近一次调用） |
| `llm/providers.py` | 各 Provider 实现 |
| `research/research.py` | 调研编排：内置知识库 + 可选联网搜索 → LLM 提炼共性报告；**P0-3** 读取 `store.load_teardown_material()`（拆书库回流素材）并入调研素材 |
| `research/sources.py` | 搜索源（博查/Serper） |
| `research/schemas.py` | 调研数据结构 |
| `research/knowledge_base.json` | 内置热门小说共性知识库 |
| `pipeline/manager.py` | **`NovelPipeline`**：整条流水线编排 + 断点续跑 + 风格注入；**校验合并**（REQ-token优化 P1-5：`_run_verify` 默认 1 次调用出 consistency/reader/editor 三类，`verify_every` 默认 5）+ `_build_setting_cards` 幂等接入；**REQ-chapter-design 一期**：`_build_chapter_design`（world+outline 齐备后幂等构建逐章设计卡）+ `_backfill_pacing`（大纲复用短路/扩写路径幂等补齐 key_nodes/volume_pacing/emotion_curve，根因=扩写路径从不跑 pacing.plan_outline 致三字段真实数据全空）；**P0-1** 实例化 `ReaderAgent`，`_run_verify` 注入其 persona 到读者视角检查；P2-2 全书终审 `_run_final_review`（resume/done 后，异常降级不阻塞导出）；编辑审稿 churn 落 trace |
| `pipeline/orchestrator.py` | 架构总控 `Orchestrator`：阶段门禁 StageGate（产出→准出校验→自动重生成≤N轮→仲裁放行）+ 冲突仲裁；`manager.run_world/run_outline` 接入（mock provider 默认跳过） |
| `pipeline/validator.py` | 独立逻辑校验 `LogicValidator`：设定一致性/OOC/主线偏移/重复≤5%/伏笔完整性 5 类校验 + 机制卡三要素（语义/结算/代价终局，REQ-章内自洽） + 分级 |
| `pipeline/issue_triage.py` | 问题分级 `IssueItem`(fatal/major/minor) + triage/has_fatal/sort 纯函数 |
| `pipeline/report.py` | 成果整合：落盘 4 份设计文档到 `projects/<id>/design/`（核心设定册/骨架大纲/节奏手册/质量报告） |
| `pipeline/stages.py` | 阶段定义/推进；场景级生成逐场景累积「本章已推进状态」注入后续场景（REQ-章内自洽）；**REQ-token优化**：行动表并入拆场景输出 `characters_action`（`action_plan_first=False` 默认无独立调用）、场景状态抽取按 `scene_state_mode` 分流（auto=普通确定性/高质量 LLM）、普通模式整章润色默认关（polish_hq_only）、场景 prompt 按 `scene_delta_inject` 分层注入（首场景全量 memory、后续场景增量块）；**P2 场景落盘**（拆场景结果+场景状态累积写入 store.scenes.json）、章末钩子注入（ChapterWriter 读取当前章 cliffhanger 并入 memory_context）；**REQ-scene-plan-align** 拆场景事件清单从大纲 character_moves 确定性派生（`_derive_events_from_moves` + 开关 `scene_plan_align`，派生事件覆盖模型输出 + 场景引用恰好覆盖派生 id 集合校验，moves 缺失/派生失败/开关关回退原 LLM 规划）；**REQ-scene-plan-align §7.3 正文层防重演（2026-10-09）**：场景正文节拍序列登记+比对（`beats.py`，换措辞完整循环重演拦截，命中重试 2 次仍重演回退整章 `_fallback_whole_chapter` 复用）、场景 prompt 事件进度硬约束（`event_progress`：只推进本场景事件、禁提前消费后续事件）、行动表按场景切片措辞（禁"逐条落实"全章行动）；**生成端第二轮（2026-10-09）**：`scene_prompt` 新增 `done_beats` 已发生节拍序列事前注入、防线3 触发条件叠加 `_beat_cycle_issues` 场景内部循环 + `_beats_local_rewrite_detected` 局部重合、`_fallback_whole_chapter` 生成后即时校验重试；**生成端第三轮（2026-10-09，P0-1/3/4）**：`event_ledger` 事件消费账本（`consumed_events` 已消费只许回响/`upcoming_events` 后续禁提前消费双块注入，治跨场景分布型重演）、防线4 面板数字方向校验（`chapter_state.panel_direction_violations`，基线注入+带错重试至多 2 次，治面板账本崩坏）、`_fill_characters_action` 单字段补全（拆场景大 JSON 截断只丢尾部 characters_action 时只输出行动表数组，仅整体解析失败触发）；**P0-7 记忆接入（2026-10-10）**：`_memory()` 懒加载 `BookMemory`（关闭/异常→None 降级安全）、拆场景注入 `book_canon`（设定账本）、场景 prompt 5 处注入 `book_pitfalls`（本作历史错误，默认 ≥2 次纪律类）、事件消费登记 `record_event_consumed` 跨章持久化、章末 `record_taste`+`decay`、回退整章拼入踩坑块 |
| `pipeline/agents/base.py` | Agent 基类（人设+技能 → System Prompt） |
| `pipeline/agents/researcher.py` `architect.py` `writer.py` `reviewer.py` `reader.py` `stylist.py` | 六角色实现（调研/大纲/正文/审校/读者/风格师） |
| `pipeline/agents/theme_character.py` `world_rules.py` `pacing.py` | 生产层细分：主题与人物 / 世界观与规则 / 节奏与伏笔 专项 Agent（认领结构化锚点 + 确定性推导关键节点/分卷节奏/伏笔回收闭环）；**P0-2** architect 编排时把三者 persona+技能注入对应生成阶段 |
| `pipeline/agents/opening.py` `ending.py` | **P1 专项 Agent**：开篇 Agent（前三章强开篇 core_event/goal 设计）/ 结局 Agent（终局设计卡写入 world.ending_card）；architect 分别于 run_outline/run_world 编排 |
| `pipeline/agents/dialogue.py` | **P2 对白 Agent**：聚合人物 speech_style 生成对话风格指南写回 world.dialogue_guideline，architect.run_world 编排，memory 强约束区注入（确定性聚合、零 LLM） |
| `pipeline/agents/pacing.py` | 节奏与伏笔 Agent：推导关键节点/分卷节奏/伏笔回收闭环；**P2** plan_outline 额外聚合全本情绪曲线卡（Outline.emotion_curve，零 LLM） |
| `pipeline/agents/prompts/*.md` | 各角色专属提示词（**改角色行为改这里**）；含小墨AI `assistant.md`（对话人设/事实纪律/输出格式/写作方向仅作背景参考） |
| `pipeline/memory.py` | 长文一致性：设定库 + 最近 N 章摘要回填 + L4.5 近章正文直读（P0-1，默认 1200 字）+ L1.5 人物状态注入（P0-2）+ L1.6 世界状态承接（REQ-章内自洽：上一章机制数字/生死/物品/时间/事实）+ 配角动机卡注入 + **分章设定卡子集注入**（REQ-token优化 P1-1：按章只注入命中人物/生物/地点/势力/伏笔；空列表=命中无项不注入、索引缺失/关闭回退全量；强约束区永不裁剪）+ **P1 物品/等级/结局卡进强约束区**；**P2 术语统一卡/对话风格进强约束区** |
| `pipeline/setting_cards.py` | **分章设定卡索引**（REQ-token优化 P1-1，纯 Python 前置确定性）：world 后构建 `setting_cards.json`，出场人物用 `memory.predict_appearing_chars` 预测，生物/地点/势力/伏笔按「名称直判+关键词包含」匹配；失败/关闭静默降级全量注入 |
| `pipeline/chapter_design.py` | **章级设计卡派生器**（REQ-chapter-design 一期，纯 Python 前置确定性，零 LLM）：从章级 conflict/emotion/goal/core_event/character_moves/cliffhanger + `World.conflict_matrix` 派生逐章 A1–A7 设计卡（冲突核心与升级阶梯/关键转折/情绪落点/出场角色+戏份+开放关系模板/对白功能），落 `chapter_design.json`；`render_block` 渲染写手注入块；`reconcile_event_ledger` 事件应发生 vs 已消费对账（弱覆盖，只 warning 不阻断） |
| `pipeline/character_state.py` | 人物动态状态快照（P0-2）：确定性出场记录 + 可选 LLM 状态细节抽取（`_should_llm_extract` 按 `state_extract_frequency` 默认每 3 章 1 次），失败降级 |
| `pipeline/chapter_state.py` | **章内/章间状态账本**（REQ-章内自洽）：章末/场景状态抽取（机制数字/生死/物品/时间/事实），LLM 主抽取（`_should_llm_extract` 降频）+ 确定性面板正则兜底；章间承接防数字断链、场景间推进防死而复生；**P0-3 面板数字确定性闭环**（2026-10-09 生成端第三轮）：`panel_baseline_dict`/`panel_direction_violations`/`panel_numbers_sequence`（零 LLM：次数/剩余只许递减（0→N 子时重置放行）、蚀化/进度只许递增），供 stages 防线4 基线注入+带错重试；**2026-10-17 机制数字软提示与通用化（REQ-机制数字软提示与通用化）**——移除死亡信号词表/因果校验/蚀化跨章累计（`death_signals`/`death_count`/`book_death_signals`/`book_death_count`/`panel_causal_violations`/`erosion_cumulative_check` 全停用），`panel_direction_violations` 方向规则改读设定账本派生的字段注册表（`mechanism_field_registry`，支持加点/升级等非死亡型机制方向）；保留字段白名单（`unknown_mechanism_fields`）+ 链式账本（`ledger_check`）+ 章末 LLM 抽取（`extract_chapter_state`）作弱基准 |
| `pipeline/reader_ledger.py` | **读者体验层叙事账本**（REQ-reader-ledger 二期，零 LLM 确定性）：①读者认知三态（known/waiting/misbelief）；②伏笔承诺状态机（planted/due_soon/overdue/revealed，回收=reveal_ch 后正文命中 name/content）；③爽点配额（题材 power/lite，校验 outline emotion/tag/key_nodes 章级节奏）。stages 写章注入 `memory_context`，manager 章末钩子 `_update_reader_ledger` 推进；开关 `reader_ledger` |
| `pipeline/book_memory.py` | **小说级记忆系统（P0-7，2026-10-10，docs/TECH-book-memory.md）**：`BookMemory` 五账本（设定 canon/事件 events/踩坑 pitfalls/经验 taste/好文风 goods）持久化 `book_memory.json`；质检出问题→`record_pitfall` 结构化入账（零 LLM、大改级、同章去重跨章累加）→`escalate` 同模式≥2次升级永久纪律→`decay` 3章未复发降权；注入组装 `pitfall_block`（Top-N 按次数×最近频率，重写/场景双措辞）+ `canon_block`（新名词规范/永久纪律/机制基线，拆场景注入）+ `numeric_backfill`（面板方向违规→确定性修复指令「正确应为 Z」回灌重写）+ `record_event_consumed` 跨章事件消费 + `record_taste` 章节评分（最近 20 章防膨胀）+ **good 账本（REQ-写作正向引导 5.6，2026-10-15）**：`record_good` 高分章（≥85 且无大改）段落自动入账 / `record_good_manual` 用户编辑保存免质检入账，按手法确定性分类（对话-潜台词/战斗-动作链/情绪-白描+点破/环境-物象白描/叙事-节奏），每类 ≤5 条、总量 ≤50、每条 ≤300 字；`good_block` 注入「本作写得好的片段（参考质感非内容来源）」供场景生成/重写双路径；`book_memory=False` 全跳过（管线行为与 P0-5 一致）；**REQ-门禁质检正向影响 P0-4/P0-5（2026-10-18）** `record_good` 增 hook_score/promote 分层（good_pool_layer 开：无大改即入账、hook≥4 或 score≥85→demo 示范池、否则 cand 候选池，关回落 score≥85）+ `ingest_external_good`/`record_good_manual` 来源隔离（demo+src_type）+ `_ingest_good_samples` 分层容量（demo≤5/50、cand≤8/100）+ `good_block` 重写为 demo 优先（空回落 cand）+ 本作来源限量≤50%且单条≤200字 + 采样轮换 pick_state 持久化 + 注入末尾多样性纪律（每章借鉴1-2特征/禁连续复用）+ `record_taste` 入账 hook_score + `record_hook_streak` 追更欲连续达标账本（≥4累计、<4冻结一次或重置）+ `mark_compliance_educated` 首次合规教育去重（P0-2） |
| `pipeline/review_summary.py` | 全书质量汇总（P0-3）：改稿问题/质检均分/校验问题统计（纯函数） |
| `pipeline/editor.py` | 编辑质检（9 维审稿，<7 分自动修正）；**diff 式修正**（REQ-token优化 P1-6）：`_revise_diff` 只输出需改字段（max_tokens 1200）+ `_apply_updates` Python 合并，失败/关闭回退整章重出 |
| `pipeline/reviser.py` | 章节重写/审校 |
| `pipeline/repetition.py` | 防重复/防同质化 |
| `pipeline/style_anchor.py` | 双锚风格锚定（REQ-双锚风格锚定）：个人旧稿锚提取 `extract_personal_anchor` + 拆书库题材锚组装 `build_teardown_anchor` + 统一包装 `wrap_style_anchor`（纯函数，供 templates/router 共用锚头常量） |
| `pipeline/trace.py` | 全链路快照（模块1）：TraceSink 接口 + TraceRecorder；`record_quality` 带 score/issue_count/auto_revised（P2-4 数据底座）+ **P0-5（2026-10-18）** extra 增 passed/hook_score（供进度盘每章色块聚合） |
| `pipeline/guard.py` | 成本防护（模块2）：AgentGuard 循环/Token 阈值 |
| `pipeline/quality.py` | 结果自校验（模块4）：QualityChecker + 1 次自动重写；含章内同句复读检测（叙述≥3/长句2/面板行2→大改，对话口头禅≤2豁免）；章内矛盾 LLM 自检默认仅高质量模式（consistency_llm_check）；**定点重写**（REQ-token优化 P1-4：issue pos/原句锚点定位窗口 ±200 字，<60% 正文局部重写拼回，无定位回退整章）；`auto_revised` 真值落 review.json（P2-4）；**2.12 情节级重演检测**（`beats._beat_cycle_issues`：同一节拍序列非重叠 ≥2 次→大改，换措辞完整循环重演兜底）；**P0-7 记忆接入（2026-10-10）**：`__init__` 可选 `memory`（None/关闭全跳过）、每轮 check 后 `_record_pitfalls` 入账+escalate、`_rewrite_whole`/`_rewrite_span` 注入 `_memory_block`（本作历史错误 Top-N + 数字修复回灌）；**REQ-门禁质检正向影响（2026-10-18）** P0-1 合规段改 structured 校验（issue 带 rule_category/rule_phrase）+ `_rewrite_once` 合规命中强制 `_rewrite_whole(skip_original=True)` 删旧稿整章重写 + `_clean_rewrite_block` 注入整章全新重写声明+首次合规教育块（compliance_education 开且 book_memory 去重）+ ensure_quality best 择优/兜底排除合规稿 + result 增 `compliance_rejected`；P0-3 diag_feedback 补 fix_suggestion/expected_gain（复用 diagnosis.suggest_for）+ `_rewrite_whole` 改用 build_issue_block（reask 非盲重写）+ `_reader_llm_check`/`_consistency_llm_check` 解析 suggestion；P0-4 新增 `_style_homogeneity_issues` 跨章趋同软提示（近3章 CV/TTR，level=小改不扣分）；P0-5 result 增 hook_score |
| `pipeline/compliance.py` | **合规红线纯函数**（REQ-门禁质检正向影响 P0-1/P0-2，2026-10-18）：`has_compliance`（type 含「合规-疑似违规内容」）/`compliance_categories`（rule_category 去重，旧格式从 detail 反查大类兜底）/`compliance_education_block`（首次教育文本，指向内容不指向人）；零 LLM、失败降级空串 |
| `pipeline/diagnosis.py` | **质检诊断回喂纯函数**（REQ-门禁质检正向影响 P0-3，2026-10-18）：`suggest_for`（确定性维度→fix_suggestion/expected_gain，精确表+前缀表+AI痕迹兜底，已带建议不覆盖）+ `build_issue_block`（带建议质检问题块，reask 非盲重写）；供 quality._rewrite_whole 复用 |
| `pipeline/beats.py` | **情节节拍序列（REQ-scene-plan-align §7.3 措施3/5，2026-10-09）**：`_extract_beat_sequence` 正文→归一化节拍序列（长词优先/去连续重复）、`_lcs_len` 最长公共子序列、`_beats_rewrite_detected` 场景重演判定（LCS≥4 且占比≥0.7）、`_beats_local_rewrite_detected` 局部子序列重合判定（重试稿压缩/插序逃逸兜底，2026-10-09 第二轮）、`_beat_cycle_issues` 质检 2.12 整章情节级重演检测；stages 与 quality 共用，零 LLM 确定性 |
| `pipeline/ai_taste.py` | AI 痕迹量化检测（P1-4）：连接词堆砌/情绪词直白/句式排比/段落雷同（纯函数，供质检扣分） |
| `pipeline/skills.py` | 技能统一注入管线（REQ-技能统一）：`SkillAssembler` 统一采集 平台全局/平台角色/用户技能/项目快照，按角色默认阶段过滤（applicable_stages 空=全量）、清洗、产出 manifest（供 trace 归因）；**P0-2/P1** STAGE_BY_ROLE 补 theme_character/world_rules/pacing/ending/opening、移除 validator 死配置；**P2** 补 dialogue |
| `pipeline/beat_gate.py` | **三期 F1** Beat 覆盖度门禁（纯确定性零 LLM）：`_split_plan_beats`/`_char_bigrams`/`compute_beat_coverage`/`assess_chapter`——正文对大纲 character_moves 逐拍的 bigram 命中覆盖率，分级 pass/warn/fail/skip，落 beat_gate.json |
| `pipeline/commit.py` | **章节原子提交 + 投影一致性（REQ-借鉴webnovel A/B，2026-10-16）**：`ChapterCommitter` 收敛 manager 章末 4 个分散入账为一次统一提交——落基线 pending→逐动作→失败 incomplete 留痕→committed，同章幂等 skip；写 `projection_log.jsonl`；`check_projection` 扫描派生视图落后章号；命令行 `tools/projection_check.py`（lagging 退出码 2）；开关 `chapter_commit`（False 回退原分散调用）。测试 `tests/test_chapter_commit.py` |
| `pipeline/scene_inspector.py` | **三期 F2** 场景体检（对标 Scene Inspector）：`inspect_chapter` 读时实时算 字数/事件推进/重复度(复用 beats._beat_cycle_issues)/章首承接/章末钩子 5 维度，输出 {dim,level,detail,suggestion}；单维度 try 隔离降级 |
| `pipeline/templates.py` | 提示词模板组装；P2-3 动态模块随机化 `_pick_dynamic_module`（seed 可复现、相邻去重）；P2-1 风格范文锚定 `_style_reference`（截 3000 字，空则不注入）；P2-2 `final_review_prompt` 终审编辑；**P1** `opening_prompt`/`ending_prompt`；P0-1 `verify_combined_prompt` reader_persona；**P2** 大纲 prompt chapters 新增 cliffhanger（open/expand/continue）；**REQ-双锚风格锚定** scene/chapter 注入题材锚（teardown_anchor 参数）、expand_chapter_prompt 补风格范文+个人锚+题材锚（弱约束，空不注入）；**REQ-scene-plan-align** `split_scenes_prompt` 新增 moves 骨架注入（非空=事件清单已由 character_moves 派生，模型只补字段+拆场景，禁增删改写事件；空=原规划路径）；**§7.3 措施1** `scene_prompt` 新增 `event_progress` 事件进度硬约束（只推进本场景事件、禁提前消费后续事件完整流程）+ 场景衔接规则补"后续事件留给各自场景"；**生成端第三轮 P0-1/P0-3**（2026-10-09）`scene_prompt` 新增 `consumed_events`（已消费事件只许回响）/`upcoming_events`（后续事件禁提前消费）/`panel_baseline`（机制数字基线）/`panel_violations`（带错重试说明）四参数；**P0-4** `characters_action_fill_prompt` 单字段补全（只输出行动表数组）；**REQ-门禁质检正向影响 P0-3（2026-10-18）** `reader_review_prompt` 输出 JSON 增 `suggestion` 字段（LLM 诊断回喂） |
| `store/project.py` | **`ProjectStore`**：项目落盘、状态机、断点续传、统计；save/load_config 持久化 `style_role_reference`；`final_review.json` 读写（P2-2）；`setting_cards.json` 读写（REQ-token优化 P1-1：`setting_cards_path`/`save_setting_cards`/`load_setting_cards`）；`chapter_design.json` 读写（REQ-chapter-design 一期：`chapter_design_path`/`save_chapter_design`/`load_chapter_design`，空 data 不落盘）；**P0-3** `teardown_material.txt` 读写（拆书库回流素材）；**P2 场景落盘**（`scenes.json` 分章场景清单+状态）；**REQ-双锚** `teardown_style_anchor.txt` 读写（题材文风锚，webapp 写/novel_gen 读）；**REQ-reader-ledger** 三块账本读写（`reader_knowledge.json`/`foreshadow_commitment.json`/`payoff_quota.json`，各 path/load/save 复用 `_write_json` 原子写）；**三期** `beat_gate.json` 读写（F1 覆盖门禁按章记录）+ `continue_drafts.json` 读写（F3 三分支续写草稿预览）；**REQ-借鉴webnovel A/B** `chapter_commit.json` 读写（章节统一提交记录）+ `projection_log.jsonl` 读写（投影一致性日志，JSONL 原子写）；**REQ-章节版本化（2026-10-16）** `chapter_versions.json` 版本索引 + `chapters/<no>/v<k>.json` 版本存档——`save_chapter(chapter, source, version_limit, quality, promote)` 追加版本（内容同 HEAD 幂等、不同追加+更新 selected、上限 15 淘汰最旧 HEAD 不淘汰；`quality` 质检结果写入该版本 history，`promote=False` 候选版本只追加存档+索引、不写 HEAD/selected）、`load_chapter_version`/`list_versions`（透传 quality）/`select_version`（回退复制回 HEAD，用户选择置 `user_managed`）、`_auto_select_best`（未被用户管理时自动把 score 最高版本设为 selected+HEAD）、`delete_version`（selected 不可删），`chapters/<no>.json` 仍作当前选中（HEAD），存量单版本章节首次保存自动建库（旧版存档为 legacy）；**阶段1A（2026-10-25）** `versioned_enabled()` 公开开关读取（False=紧急回退：save_chapter 走 `_save_chapter_legacy` 原实现、恢复 outline_snapshots 自动快照）、`save_chapter` 改门面分发器（对外签名不变）、`delete/load/select/list_versions` 委托门面（读写共用同一存储格式不分开关）、`save_outline`/`save_world` 增 `commit/source` 参数（commit=False 只写 HEAD=管线中间落盘、commit=True 经门面建版本）、`apply_outline_candidate`=source=user 提交；**阶段1B（2026-10-25）** run 上下文挂接——`begin_run(ctx)`/`current_run()`/`end_run()` 三薄方法（实例属性 `_run_context` 非线程局部，幂等；供 versioned `_resolve_run_id` duck-typing 消费） |
| `store/schemas.py` | 项目数据结构（NovelConfig 等） |
| `store/wordcount.py` | **产物字数统计（REQ-Agent用户化 阶段 0.5，2026-10-25）**：`count_words(kind, payload)` 按产物类型统计 AI 生成字符数——`chapter`=正文 / `outline`=总纲+卷+章字段 / `volume`=卷简介+卷内章规划 / `world`=递归所有字符串（字段漂移免疫），未知 kind 显式 `ValueError`；纯函数零依赖，是结算字数的**唯一事实来源**（`webapp/settle_ledger.py` 消费） |
| `store/versioned.py` | **产物版本化门面（REQ-Agent用户化 阶段 1A，2026-10-25）**：`VersionedProduct(store, kind)` 统一 chapter/outline/world 三类产物版本库（chapter 存档 `chapters/<no>/v<k>.json`+索引 `chapter_versions.json`；outline/world 存档 `<kind>/v<k>.json`+索引 `<kind>/versions.json`，key 恒 `all`）；双提交语义——`commit(content)` 比 HEAD 签名幂等（章节保存/用户编辑）、`commit_head()` 比最新存档签名幂等（run 收尾统一提交，一次 run=一次版本）；提交顺序 版本存档→索引原子写→HEAD；per-project RLock；`promote=False` 候选不顶替、`quality` 透传、`source=user` 置 user_managed 免自动选分、版本上限经 FEATURE_FLAGS（15/5/5）淘汰跳 selected；`run_id` 缺省 duck-typing 读 `store.current_run()`（1B 落地，无则空串）、`upstream=None` 自动读 chapter→{outline_v,world_v}；`reconcile` 索引↔文件对账（orphan/missing/selected_mismatch） |
| `store/run_context.py` | **run 上下文与 run 侧写（REQ-Agent用户化 阶段 1B，2026-10-25）**：`RunContext` dataclass（run_id/agent/skill_ids/user_prompt/task_id/started_at）；`write_run_file`/`read_run_file` 落读 `agent_runs/<run_id>.json`（TECH §3.3 provenance 字段：params/prompt_version/prompt_sha256/model/trace_id/billing_mode/billing/outputs/compliance_rejected 等，user_prompt ≤4KB 截断+truncated 标记，原子写）；`prune_agent_runs` 每项目保留最近 200 个（按 finished_at 删最旧、损坏文件优先删） |
| `store/schemas.py` | 项目数据结构；多Agent协作扩展：`World` 新增 theme_proposition/conflict_matrix/protagonist_arc/character_motives/goldfinger_rules(五要素)/setting_taboos，`foreshadowing` 升级为结构化 `ForeshadowItem`(buried_ch/reveal_ch/level)，`Outline` 新增 key_nodes/volume_pacing；**P1**：`World` 再增 items（ItemCard）/power_system（PowerLevelSystem）/ending_card（EndingCard）；**P2**：`World` 增 terminology（TerminologyItem）/dialogue_guideline，`Outline` 增 emotion_curve（EmotionBeat），`ChapterPlan` 增 cliffhanger；全部 from_dict 兼容旧数据 |
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
| `core.py` | **核心层**：FastAPI 实例/lifespan、鉴权、项目工具、结算、生成线程、任务持久化、登录限流；**P0-3** `_inject_teardown_material` 拆书库回流（调研前注入同题材拆书条目）；**REQ-双锚** `_inject_teardown_style_anchor` 拆书库题材文风锚（写手阶段消费，失败静默；`_run_generation` 入口统一注入，含从第 N 章重生成）；**用户级记忆库** `_inject_user_memory`（用户级画像→项目快照，接入全部生成入口）+ `_distill_user_memory`（书级→用户级蒸馏，生成 finally 触发）；**REQ-后台开关管理** `_apply_global_overrides`（sys_config 表为准内存态覆盖，_open_pipeline 内注入，config.json 快照不动）；**阶段1B（2026-10-25）** `_RunHandle` 增 `task_id` 字段、`_resume_interrupted_tasks` 增 `agent_run` 分支（局部 import generate_service 调 resume_agent_run，失败清 task+释放信号量） |
| `config_overrides.py` | **全局开关覆盖注入（REQ-后台开关管理）**：`apply_global_overrides(cfg)` 读 `sys_config` 实时值覆盖 NovelConfig（顶层 + agent_guard 标量/scene_limits），DB 异常回退默认并 logger 留痕；生成链路与小墨AI 对话共用（`core._apply_global_overrides` 保留同名入口） |
| `user_memory.py` | **用户级记忆库服务**（跨书创作偏好继承）：`UserMemoryService` 读写 `users/<uid>/user_memory.json`（偏好画像/跨书纪律/个人文风锚/stats），`record_project_signals`/`record_edits`/`distill`/`build_snapshot`/`summary`/`clear`，全部写路径零额外 LLM |
| `assistant/` | **小墨AI（REQ-小墨AI 一期，项目页写作对话）**：`prompt.py` 读取 `novel_gen/pipeline/agents/prompts/assistant.md`（mtime 缓存 + 内置降级 + warning）；`context.py` 只读项目产物组装四层上下文（人设+Bible / 事实块 / 对话窗口 / 提问），token 预算按固定档位裁剪、正文头尾保留；`cards.py` 确定性卡片（质检扣分/问题清单按大中小改排序/版本对比/正向实测），阈值全部 import 自 `pipeline/ai_taste.py`，缺口（破折号密度/面板数字）明示；`service.py` 编排（输入校验 → 章节解析 → 组装 → LLM 调用 → 失败降级留痕 → 输出合规 → 落库） |
| `db/` | **MySQL 数据层包**（拆分自原 db.py 1409 行）：`__init__.py` 统一 re-export + 末尾 init_db()；`base.py` 连接池/事务/表结构 DDL（style_roles.reference_text、skills 系列表 applicable_stages、统一表 `skills` 幂等迁移；**REQ-后台开关管理** 新增 `sys_config`/`sys_config_log` 表 + init_db 挂 seed；**小墨AI** 新增 `conversations`/`messages` 表）；`users.py` 用户/密码/代理返佣/认证/会话/余额/管理统计；`projects.py` 项目归属/退款；`skills.py` 技能域（**统一读写 `skills` 表**，platform/user/market 三源 + applicable_stages/version/market_id 透传，旧表保留不回写）；`style_roles.py` 风格角色/挂载/种子化（挂载技能经 `skills` 表按 source 解析）；`teardowns.py` 拆书库；`assistant.py` 小墨AI 会话/消息（会话 CRUD + 消息追加/分页/最近 N 轮，SQL 参数化）；`traces.py` agent_trace 全链路快照（MysqlTraceSink 落库/按 trace·project·user 查询/聚合统计 + P2-4 `_aggregate_quality` 纯函数与 `quality_report` 报表接口；**P0-5（2026-10-18）** `_aggregate_quality` 增 `chapter_status` 每章色块聚合——红=未过/绿=hook≥4/黄=自动重写/灰=通过，`quality_report` 查询带出 step_name 供解析章号）；**`config.py`（REQ-后台开关管理）** FEATURE_FLAGS→`sys_config` 幂等种子同步（新增开关自动入表）/`sys_config_log` 审计/`coerce_config_type` 类型转换/`flag_type`/`json_default`/`SELECT_ENUMS` 枚举/`list_configs`/`update_configs`，`FLAG_MODULES` 含 assistant |
| `secret_encrypt.py` | BYOK Key Fernet 加密（`enc:v1:` 前缀） |
| `pricing.py` | 定价（普通 2 元/万字、高质量 3 元/万字、赠金/封面次数） |
| `settle_ledger.py` | **v2 版本级结算账本（REQ-Agent用户化 阶段 0.5，2026-10-25，P0 资金链路）**：计费单元从「全书累计 AI 字数差 + target 封顶」反转为「一个产物版本一次结算、按该版本实际生成字数」；`settle_version`（幂等键 `{kind}:{key}:v{v}`，重启/重入不双扣）/`settle_product`（outline/world/volume 暂无版本库时的内容摘要+序号自增临时幂等）/`settle_chapter_versions`（写前快照 → 只结算新增版本，`source=user` 不计费、`legacy` 不补扣）/`settle_legacy`（存量兜底一次）/`migrate_v1`（v1 基线只读迁移不重扣）；先原子落账本再扣费（Windows `os.replace` 重试 + 直写回退）、余额不足扣到 0 并记 `debt.json` 不自动补扣、`user_llm` 应扣 0 仍写条目供审计；账本 `settle.json` 上限 500 条按插入顺序淘汰；**阶段1B（2026-10-25）** `settle_chapter_versions` 返回体补 `words` 键（run 侧写 billed_words 准确统计，加键不破坏既有结构） |
| `agent_prompts.py` | **Agent 人设提示词版本化（REQ-Agent用户化 阶段 1B，2026-10-25）**：`EDITABLE_AGENTS` 白名单（researcher/architect.outline/architect.volume/architect.world/writer.chapter）；`get_prompt_meta`→(text, `<agent>@v<n>`, sha256) 懒快照 v1（author=default，现网无静态 per-agent 模板时用内置职责描述）；`save_user_prompt` 快照 v<n+1>（author=user）；存储 `projects/<id>/agent_prompts/<agent_safe>/{v<n>.md, meta.json}` |
| `generate_service.py` | **Agent 统一触发入口（REQ-Agent用户化 阶段 1B，2026-10-25）**：`POST /api/projects/{id}/agent-runs`（202）+ `GET .../{task_id}` 四态状态机（running/resumed/done|stopped|failed/not_found，重启沿用 task_id）；守卫群纯函数（访问→计费/余额 402→agent/产物白名单→params→锁定 409→手写章→Skill≤5，判定顺序固定）；`start_agent_run`（守卫→槽位→task.json kind=agent_run→线程）/`resume_agent_run`/`get_agent_run`；执行分发 researcher/outline/world/volume/chapters（删旧产物重跑、失败恢复旧产物、章末 should_stop_now 判定、settle 接 v2 账本）；人设+user_prompt 注入 `pipeline.cfg.custom_prompt` 内存态不落盘；全局紧急回退开关 `unified_generate`（FEATURE_FLAGS 注册→sys_config 覆盖，False→503）；对 core 全部函数内延迟 import 防循环 |
| `referral.py` | 代理参数（AGENT_DISCOUNT/COMMISSION_RATE/COMMISSION_BASE/REFERRAL_BONUS） |
| `mailer.py` | 邮件（验证码/退款通知） |
| `verify_code.py` | 邮箱验证码逻辑 |
| `outline_parser.py` | 大纲文档解析（.txt/.md/.docx） |
| `logger.py` | 统一日志（logs/app.log，5MB×3 轮转） |
| `admin_cli.py` | 站长命令行管理 |
| `routers/system.py` | 系统域：health/定价/解析/邮件状态 |
| `routers/auth.py` | 账号：注册/登录/验证码/重置/绑代理 |
| `routers/me.py` | 我的：资料/余额/返佣/我的技能；**用户级记忆** `GET/POST /api/me/user-memory*`（只读摘要/清空隐私兜底） |
| `routers/skills.py` | 平台技能/技能市场 |
| `routers/style_roles.py` | 风格角色 + 技能挂载（reference_text 透传） |
| `routers/projects_base.py` | 项目 CRUD/退费/归档/克隆/删除/下载（建书快照 style_role_reference；**用户级记忆**：建书 `record_project_signals`+继承快照） |
| `routers/projects_outline.py` | 三级大纲/快照/重生成 |
| `routers/projects_chapters.py` | 章节/审校/扩写/批量（/review 返回 final_review）；**REQ-双锚** expand-selection 注入风格范文+个人旧稿锚、edit_chapter 落 `user_edited` 人工修订标记；**用户级记忆** edit_chapter 后 `record_edits` 跨书文风锚登记；**三期产品化** `/chapters/{no}/scene-inspect`（F2 场景体检）、`/chapters/{no}/chain-of-thought`（F4 计划-产出-校验聚合）、`/continue-branches`（F3 三分支候选）、`/apply-branch`（F3 草稿预览落盘，不改正文）；**REQ-章节版本化（2026-10-16）** `rewrite` 返回 `version`、`regenerate_from` 改方案 a（不再删 no 及之后章节，改为从 no 追加候选版本、旧版保留用户逐章选择）、`edit_chapter` 追加 `source=user` 版本、新增 `GET /chapters/{no}/versions` 与 `POST /chapters/{no}/versions/{v}/select`；**2026-10-17 版本质量** `rewrite` 改为以实际 HEAD 重算 kept_previous 并返回 HEAD 内容 + `candidate_version`（kept_previous 时新稿版本号）、新增 `DELETE /chapters/{no}/versions/{v}`（selected 不可删） |
| `routers/projects_world.py` | 世界观/人物/锁定/调研/伏笔/封面 |
| `routers/projects_generation.py` | 生成控制（启动/停止/重跑）；**阶段1B（2026-10-25）** `POST /api/projects/{id}/agent-runs`（202 统一触发）+ `GET .../agent-runs/{task_id}`（四态查询），业务全在 generate_service；**阶段1C（2026-10-25）** `POST .../agent-runs/{task_id}/stop`（停止）+ `GET/PUT .../agent-prompts[/{agent}]`（人设不可变版本读取/保存） |
| `routers/projects_versions.py` | **产物版本 API（阶段1C，2026-10-25）**：outline/world 的 `GET /products/{kind}/versions`（含每版计费金额）、`POST .../versions/{v}/select`（受控回滚）、`GET .../rollback-impact`（回滚影响范围：受影响章节/regenerate_from/滞后派生视图）；kind 白名单 outline/world，chapter 版本仍走 projects_chapters |
| `routers/admin.py` | 站长后台（9 板块，含 /api/admin/quality-report 质量报表；**REQ-后台开关管理** `GET/PUT /api/admin/config` 生成引擎开关管理：key 白名单 + 类型/范围/select 枚举校验 + 审计，`_RANGE_RULES` 范围表、`_validate_config_items` 校验纯函数） |
| `routers/assistant.py` | **小墨AI（REQ-小墨AI 一期）**：`POST/GET /api/projects/{id}/assistant/conversations`（建/列）、`GET .../conversations/{cid}`（详情+消息分页）、`POST .../conversations/{cid}/messages`（发消息，同步返回回复+卡片）、`DELETE .../conversations/{cid}`；全部 `_require_user` + `_check_project_access` + 会话归属校验 |

## webapp/frontend/src/（Vue3 前端源码）

| 路径 | 职责 |
|---|---|
| `main.js` / `App.vue` | 应用入口/根组件 |
| `router/index.js` | 路由表 |
| `stores/auth.js` | 登录态 Pinia store |
| `stores/project.js` | 当前项目 Pinia store |
| `api/request.js` | axios 封装（token 注入/错误处理） |
| `api/auth.js` `novel.js` `project.js` `admin.js` `tools.js` `me.js` `assistant.js` | 按域接口封装（me.js：工作台-资料/流水/返佣/退款；admin.js 含 **`getConfig`/`updateConfig`** 系统开关接口；assistant.js：小墨AI 会话/消息 5 个接口） |
| `views/Login.vue` | 登录/注册页 |
| `views/ProjectList.vue` | 书案（作品列表/搜索/筛选） |
| `views/ProjectDetail.vue` | 项目详情（阅读器/进度/生成控制；**新增「小墨AI」tab**，透传 projectId/章节列表/当前章，接收 `locate` 事件切章打开阅读器） |
| `views/Workspace.vue` | 用户工作台（概览/账户账单/推广返佣/模型配置/我的技能/风格角色 6 tab，复用 /api/me/*） |
| `views/Admin.vue` | 站长后台（9 板块，含质量报表 tab：得分分布/问题类型/弃书风险/阶段成本；**REQ-后台开关管理「系统设置」tab**：memory/writing/review/guard/assistant 分组渲染 100 开关，改动项高亮、按组保存、展示默认值/修改人/时间） |
| `components/assistant/AssistantPanel.vue` | **小墨AI 面板（REQ-小墨AI 一期）**：会话列表/新建/删除、模式切换（章节评估/改稿建议）、章节选择、消息区（卡片渲染/降级提示/token 用量）、Ctrl+Enter 发送、「定位到第 N 章」 |
| `components/assistant/AssistantCard.vue` | 确定性卡片渲染：质检扣分（缺陷分口径）/问题清单/版本对比/正向实测（达标标签+阈值+缺口声明），不合成评分 |
| `components/RechargeDialog.vue` | 充值/退款弹层 |
| `components/LlmSettingsDialog.vue` | BYOK Key 设置 |
| `components/tools/ToolsPanel.vue` | 工具面板（封面/质量校验/改稿建议+全书终审/节奏分析/多方案/批量/伏笔/锁定设定；技能与风格角色管理已迁移至工作台；**三期** 场景体检/三分支续写/生成过程思维链三张卡片） |
| `components/outline/OutlineEditor.vue` | 三级大纲编辑器（拖拽）；**阶段1C** apply 候选成功后 `emit('open-versions')` 跳版本列表并提示候选版本同样计费 |
| `components/VersionDialog.vue` | **统一版本弹窗（阶段1C，2026-10-25）**：chapter（质检分/问题列表/申诉）与 outline/world（每版扣费金额、选择前弹回滚影响范围确认）共用；从 ProjectDetail 迁出章节版本逻辑 |
| `components/agent/AgentPanel.vue` | **Agent 面板（阶段1C，2026-10-25）**：5 角色卡片（人设版本 tag/编辑人设/参数区/user_prompt/运行停止/预估金额）+ 计费模式标识（user_llm/platform）+ 平台余额 ≤0 禁用引导充值；轮询 getAgentRun 直至终态 |
| `components/agent/SkillPicker.vue` | **技能多选器（阶段1C）**：按 role 分组展示平台技能，挂载上限 5，显示单技能注入字数 |
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
