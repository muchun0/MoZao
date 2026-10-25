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
| `store/` | 项目落盘、状态机、断点续传、进度统计；`schemas.py` 的 `World` 含 `world_reveals`（世界观阶段揭示计划，跟随主角视角）；**P2** `ProjectStore` 新增场景落盘（`scenes.json` 分章场景清单+状态，供回看/编辑/复用）、`teardown_material.txt`（P0-3 拆书库回流素材）；**REQ-双锚** `teardown_style_anchor.txt`（题材文风锚，webapp 写/novel_gen 读）；`Chapter.user_edited`（用户编辑保存=人工修订标记，个人风格锚来源）；**REQ-借鉴webnovel A/B** `chapter_commit.json`（章节统一提交记录：pending/committed/incomplete + 失败项）+ `projection_log.jsonl`（投影一致性日志，JSONL 原子写）；**REQ-章节版本化（2026-10-16）** `chapter_versions.json` 版本索引（每章 versions/selected/history）+ `chapters/<no>/v<k>.json` 历史版本存档——`save_chapter` 追加版本语义（内容相同幂等不追加、不同则存档旧 HEAD 追加新版、每章上限 15 淘汰最旧且 HEAD 不淘汰；`quality` 质检结果写入版本 history、`promote=False` 候选版本只存档不顶 HEAD）、`load_chapter_version`/`list_versions`（透传 quality）/`select_version`（回退=复制回 HEAD，用户选择置 `user_managed`）/`_auto_select_best`（未用户管理时自动选 score 最高版）/`delete_version`（selected 不可删），`chapters/<no>.json` 继续作当前选中版本（HEAD），`load_chapter`/`list_chapters` 对外行为不变 | `project.py`（`ProjectStore`）、`schemas.py` |
| `export/` | 多格式导出 + AI 封面 | `book_formats.py`（TXT/EPUB/DOCX）、`cover.py` |
| `utils/` | 工具：章节切分、数据迁移、路径 | `chapter_splitter.py`、`migrate.py`、`paths.py` |
| `config.py` | 配置加载（YAML + 环境变量补 Key）；`AgentGuardConfig` 生产级加固配置（模块0）；**功能开关注册表 `FEATURE_FLAGS`**：开关默认值唯一事实来源，按 memory/writing/review/guard 模块分组，store 的 save/load_config 注册表驱动自动落盘 | — |
| `safety.py` | 输入防护（模块3）：Prompt 注入检测/System 清洗/输出合规校验（纯函数） | — |

> 注意：README 旧文中提到的 `novel_gen/cli.py`、`__main__.py` 已不存在；当前产品入口为 Web 端 `run_web.py`。

### 2.2 novel_gen/pipeline/（生成流水线，核心中的核心）

| 文件 | 职责 |
|---|---|
| `manager.py` | `NovelPipeline`：阶段编排（调研→大纲→质检→人物→逐章→导出）、风格注入 `_with_stylist`、断点续跑；**校验合并**（REQ-token优化 P1-5）：`_run_verify` 默认 `verify_combined_prompt` 一次调用输出 consistency/reader/editor 三类（verify_combined=False 回退原 3 连调用），一致性校验频率 `verify_every` 默认 5；**P0-1**：实例化 `ReaderAgent` 为正式角色成员，`_run_verify` 把其 persona（reader.md + reader 专属技能）注入读者视角检查（不增调用数）；`_build_setting_cards` 幂等接入分章设定卡索引；**REQ-chapter-design 一期**：`_build_chapter_design`（world+outline 齐备后幂等构建逐章设计卡，失败降级不注入）+ `_backfill_pacing`（大纲复用短路/扩写路径幂等补齐 key_nodes/volume_pacing/emotion_curve；根因：扩写路径从不跑 pacing.plan_outline 致三字段真实数据全空，plan_outline 本身只补缺失不覆盖）；`_parse_json_obj` 容错解析；P2-2 全书终审 `_run_final_review`（resume/done 后，异常降级不阻塞导出结算）、编辑审稿 churn 落 trace；**REQ-借鉴webnovel A** `_commit_after_chapter`（章末 4 个分散入账收敛为一次统一提交，落基线/失败留痕/幂等/投影日志，开关 `chapter_commit`，False 回退原分散调用、行为不变）；**REQ-双锚** 章节扩写循环注入个人旧稿锚（`extract_personal_anchor`）+ 题材锚（store 文件） |
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
| `chapter_design.py` | **章级设计卡**（REQ-chapter-design 一期，纯 Python 前置确定性、零 LLM）：从章级 conflict/emotion（真未接线高价值主源，60-150 字优质文本）+ `World.conflict_matrix` 三层标尺派生逐章 A1–A7（目标/冲突核心与升级阶梯/关键转折/情绪落点/出场角色+戏份+20 种开放关系模板/对白功能），落 `projects/<id>/chapter_design.json`（唯一权威源，幂等）；stages.write 在 memory_context 组装区（仿章末钩子块）注入 `render_block`，覆盖场景/整章/回退整章三写章路径；已在 memory.build_context 注入的 theme_proposition/protagonist_arc/character_motives 等不重复造块；`reconcile_event_ledger` 事件应发生 vs 已消费对账（弱覆盖只 warning） |
| `character_state.py` | 人物动态状态快照（P0-2）：每章后确定性出场记录 + 可选 LLM 状态细节抽取（`_should_llm_extract` 按 `state_extract_frequency` 降频，默认每 3 章 1 次 LLM），失败静默降级，写入 character_states.json |
| `chapter_state.py` | **章内/章间状态账本**（REQ-章内自洽与机制账本）：章末/场景状态抽取（机制数字/人物生死/物品归属/时间锚点/已发生事实），LLM 主抽取（`_should_llm_extract` 按 `state_extract_frequency` 降频）+ 确定性面板正则兜底 + 失败降级；manager 写章循环落 `chapter_state.json`，stages 场景级生成逐场景累积「已推进状态」注入后续场景（防面板数字漏减、角色死而复生、物品归属错乱）；**P0-3 面板数字确定性闭环**（2026-10-09 生成端第三轮）：`panel_baseline_dict`/`panel_direction_violations`/`panel_numbers_sequence` 零 LLM 方向校验（次数/剩余只许递减、0→N 子时重置放行；蚀化/进度只许递增），供 stages 防线4 基线注入+带错重试（治面板账本崩坏）；**2026-10-17 机制数字软提示与通用化**：移除死亡信号词表/因果校验/蚀化跨章累计，`panel_direction_violations` 方向规则改读设定账本派生的 `mechanism_field_registry`（支持加点/升级等非死亡型机制方向），保留字段白名单 + 链式账本 + 章末 LLM 抽取作弱基准 |
| `reader_ledger.py` | **读者体验层叙事账本**（REQ-reader-ledger 二期，确定性零 LLM）：三块同构账本——①读者认知三态（`reader_knowledge.json`：known 已揭真相不得当秘密 / waiting 伏笔挂起须推进 / misbelief 反转前误认不得剧透）；②伏笔承诺状态机（`foreshadow_commitment.json`：planted→due_soon(临期)/overdue(超时)/revealed(回收)，回收判定=reveal_ch 后正文命中 name/content 子串）；③爽点配额（`payoff_quota.json`：题材 power/lite 配置爽文 3/10、非爽文 5/15，基于 outline emotion/tag/key_nodes 校验章级节奏，未达注入软提示）。stages.write 拼 outline 块后注入 `memory_context`（场景/整章两路径同吃），manager 写章循环章末钩子 `_update_reader_ledger` 推进；总开关 `reader_ledger=False` 全跳过，异常 logger.exception 降级空串不阻塞 |
| `commit.py` | **章节原子提交 + 投影一致性（REQ-借鉴webnovel A/B，2026-10-16）**：`ChapterCommitter` 把 manager 章末 4 个分散入账（人物状态/章末机制/揭示执行/读者账本）收敛为一次统一提交——先落基线 pending、逐动作执行、失败即时 incomplete 留痕、全成功 committed、同章已提交则幂等 skip、开关关闭 disabled 直接依次调用；每次提交写 `projection_log.jsonl`；`check_projection` 扫描各派生视图（chapter_commit/chapter_state/reveal_status/foreshadow_commitment/payoff_quota/summaries/scenes/beat_gate/character_states/book_memory）覆盖章号，定位"哪个账本落后于当前章号"；命令行体检 `tools/projection_check.py <项目目录> [--up-to N] [--view ...]`（lagging 退出码 2）；总开关 `chapter_commit`（默认 True，False 回退 manager 原分散调用、行为不变）；对齐 WebNovel Writer 的「CHAPTER_COMMIT 驱动投影 + doctor 体检」，写与记分离 |
| `stages.py` | `ChapterWriter` 逐章正文：**场景级生成**（拆场景→逐场景→润色，高质量模式/≥2000 字普通模式）；**REQ-scene-plan-align 拆场景规划对齐大纲人物动线**：事件清单从大纲 `character_moves` 确定性派生（`_derive_events_from_moves` 切拍+因果合并至 2-4 事件，纯字符串零 LLM，开关 `scene_plan_align` 默认 True），`split_scenes_prompt` 注入事件硬骨架（模型只补 conflict/action/final_state），派生事件覆盖模型输出（同 id 结构化字段合并保留），场景 `scene_event_ref` 必须恰好覆盖派生 id 集合（不通过回退整章，整章路径仍注入派生事件）；moves 缺失/派生失败/开关关闭完整回退原 LLM 规划路径；派生模式下事件互斥用 desc 相似度兜底（防大纲拍重复，不做槽位检查防误杀递进拍）；**REQ-scene-plan-align §7.3 正文层防重演（2026-10-09）**：场景正文节拍序列登记 `done_beat_seqs` + `beats._beats_rewrite_detected` 比对（换措辞完整循环重演拦截，命中重试至多 2 次，仍重演回退整章 `_fallback_whole_chapter` 复用）；场景 prompt 注入 `event_progress` 事件进度硬约束（只推进本场景事件、禁提前消费后续事件）；行动表措辞改为"每场景只落实本场景事件相关行动"（防全章行动逐场景重复落实）；**生成端第二轮（2026-10-09）**：①场景 prompt 新增 `done_beats` 事前约束（已发生节拍序列注入后续场景，事后比对升级事前告知）；②防线3 触发条件三重叠加——`_beat_cycle_issues`（场景自身内部循环，堵场景1盲区）+ 整序列 LCS + `_beats_local_rewrite_detected` 局部子序列重合（重试稿压缩/插序逃逸兜底）；③`_fallback_whole_chapter` 生成后即时校验（情节级重演+字数超 1.35×目标→重试至多 2 次，注入重复节拍证据+压缩字数目标），不再把重演全部押给质检重写 |
| `beats.py` | **情节节拍序列（REQ-scene-plan-align §7.3 措施3/5，2026-10-09）**：`_extract_beat_sequence` 正文→归一化节拍序列（长词优先/去连续重复）、`_lcs_len` 最长公共子序列、`_beats_rewrite_detected` 场景重演判定（LCS≥4 且占比≥0.7）、`_beats_local_rewrite_detected` 局部子序列重合判定（已完成序列 4-7 拍子序列被复现 ≥0.8→局部重演，重试稿压缩逃逸兜底）、`_beat_cycle_issues` 整章情节级重演检测；stages 与 quality 共用，零 LLM 确定性 |
| `beat_gate.py` | **三期 F1 Beat 覆盖度门禁（2026-10-14，docs/REQ-beat-gate.md）**：`_split_plan_beats`（character_moves→逐拍）/`_char_bigrams`（停用字过滤的字符二元组）/`compute_beat_coverage`（拍内 bigram≥50% 命中=覆盖）/`assess_chapter`；纯确定性零 LLM，分级 pass(≥0.6)/warn([0.4,0.6))/fail(<0.4)/skip(无计划拍)；stages.write() 落盘后接线，结果落 `beat_gate.json`；检测+记录+提示，不新起 LLM 重写循环；**REQ-超长篇攻坚 S3.2/S3.1（2026-10-18，feat/req-longform-batch1）**：`loose` 去标点归一匹配（`_norm_for_loose`，缓解标点/近义假阴性）、章型白名单（`detect_chapter_type` 对话/过渡章 → `skip_chapter_type` 不卡 fail，治误伤）、节点分级（`priorities` P0/P1/P2，P2 不计分母）；默认 `loose=True`/`chapter_type="auto"`/`priorities=None` 向后兼容；开关 `beat_loose_match`/`beat_chapter_type`/`beat_node_priority` |
| `pending_commitments.py` | **章末承诺台账（REQ-超长篇攻坚 S3.1，2026-10-18）**：beat_gate 评估后确定性生成四类清单（committed 已兑现 / missed 漏兑现 / planted 新埋 / must_carry_to_next 下章必补）落 `pending_commitments.json`；`compensation_block` 有漏兑现才生成下章补偿块文本（无则空串=零 token），由 stages 在写章 prompt **最前部**注入（S1.3 Lost in the Middle：到期必兑现节点放 prompt 首部）；与 event_progress（场景内禁抢跑）正交，前者防提前消费、后者防漏兑现；开关 `pending_commitments`（默认 True，False 不读不写不注入） |
| `scene_inspector.py` | **三期 F2 场景体检（2026-10-14，对标 Sudowrite Scene Inspector）**：`inspect_chapter` 读时实时算 字数/事件推进(对照 scenes.json)/重复度(复用 `beats._beat_cycle_issues`)/章首承接(上章尾)/章末钩子兑现 5 维度，输出 `{dim,level,detail,suggestion}`；零新 LLM、单维度 try 隔离降级 |
| `review_summary.py` | 全书质量汇总（P0-3）：改稿问题类型分布 / 质检平均分 / 校验问题统计（纯函数，供 /review 接口聚合） |
| `editor.py` | 编辑质检（大纲后 9 维审稿，<7 分自动修正）；**diff 式修正**（REQ-token优化 P1-6）：`_revise_diff` 只输出 `{"updates":[{no,title?,intro?,goal?}]}`（max_tokens 4096→1200），`_apply_updates` Python 合并回原大纲，解析失败/关闭回退 `_revise_whole` 整章重出 |
| `reviser.py` | 单章重写/审校入口 |
| `repetition.py` | 防同质化常驻提示词、防重复 |
| `style_anchor.py` | 双锚风格锚定（REQ-双锚风格锚定）：个人旧稿锚 `extract_personal_anchor`（只抽 `user_edited` 章、限段限长）+ 题材锚组装 `build_teardown_anchor`（styles/takeaways/excerpts）+ 统一包装 `wrap_style_anchor`；`ChapterWriter`（stages.py）懒提取个人锚 `_personal_anchor` + 懒读 store 题材锚 `_teardown_anchor`，经 `personal_anchor`/`teardown_anchor` 参数注入 scene/chapter prompt——**覆盖正文生成、单章重写、从第 N 章重生成全部写章路径**；**REQ-超长篇攻坚 S2.1（2026-10-18）双锚开关治理**：题材锚 `teardown_style_anchor` 全路径 gate（默认关）+ **新增个人旧稿锚开关 `personal_style_anchor`（默认关）**——stages._teardown_anchor/_personal_anchor 与 manager 扩写路径均先判开关，关时一律传空不注入（默认路径零变化；个人锚此前无条件注入需受控） |
| `trace.py` | 全链路快照（模块1）：TraceSink 接口 + TraceRecorder（trace_id/project/user/scene 上下文，step_order 自增） |
| `guard.py` | 成本防护（模块2）：AgentGuard 循环次数/场景限次/累计 Token 阈值，超限抛 `AgentGuardError` 并留痕 |
| `quality.py` | 结果自校验（模块4）：QualityChecker 确定性规则（字数/重复/合规/章内同句复读）+ 读者视角 LLM 校验（章内矛盾自检 `consistency_llm_check` 默认仅高质量模式）+ 1 次自动重写；**定点重写**（REQ-token优化 P1-4）：`_locate_problem_spans` 按 issue pos 段落→detail 原句锚点定位窗口 ±200 字，窗口覆盖 <60% 正文才局部重写并 Python 拼回，无定位/跨度过大回退 `_rewrite_whole` 整章重出；含 P1-4 AI 痕迹扣分项；`auto_revised` 真值落 review.json（P2-4）；章内同句复读（REQ-章内自洽）：叙述≥3次/长句2次/面板行2次→大改触发重写，对话口头禅≤2豁免；**2.12 情节级重演检测**（`beats._beat_cycle_issues`：同一节拍序列非重叠 ≥2 次→大改，换措辞完整循环重演兜底）；**P0-7 记忆接入（2026-10-10）**：`__init__` 可选 `memory`（BookMemory，None/关闭全跳过）、每轮 check 后 `_record_pitfalls` 入账+`escalate`、`_rewrite_whole`/`_rewrite_span` 注入 `_memory_block`（本作历史错误 Top-N + 数字修复回灌「正确应为 Z」）；**REQ-写作正向引导 5.6（2026-10-15）** `_memory_block` 叠加 `good_block`（本作高分章好片段——重写不只「避免什么」还要「按什么质感写」，参考质感非内容来源）；**REQ-门禁质检正向影响（2026-10-18）** P0-1 合规段改 structured 校验（issue 带 rule_category/rule_phrase）+ `_rewrite_once` 合规命中强制 `_rewrite_whole(skip_original=True)` 删旧稿整章重写 + `_clean_rewrite_block`（整章全新重写声明+首次合规教育，book_memory 去重）+ best 择优/兜底排除合规稿 + result 增 `compliance_rejected`；P0-3 `diag_feedback` 补 fix_suggestion/expected_gain（复用 diagnosis.suggest_for）+ `_rewrite_whole` 改 build_issue_block（reask 非盲重写）+ reader/consistency 解析 suggestion；P0-4 新增 `_style_homogeneity_issues` 跨章趋同软提示（level=小改不扣分）；P0-5 result 增 hook_score |
| `compliance.py` | **合规红线纯函数**（REQ-门禁质检正向影响 P0-1/P0-2，2026-10-18）：`has_compliance`（type 含「合规-疑似违规内容」）/`compliance_categories`（rule_category 去重，旧格式 detail 反查大类兜底）/`compliance_education_block`（首次教育文本，指向内容不指向人）；零 LLM、失败降级空串；供 quality/stages 判定合规拒绝 |
| `diagnosis.py` | **质检诊断回喂纯函数**（REQ-门禁质检正向影响 P0-3，2026-10-18）：`suggest_for`（确定性维度→fix_suggestion/expected_gain，精确表+前缀表+AI痕迹兜底，已带建议不覆盖）+ `build_issue_block`（带建议质检问题块，reask 非盲重写）；供 quality._rewrite_whole 复用 |
| `book_memory.py` | **小说级记忆系统（P0-7，2026-10-10，docs/TECH-book-memory.md）**：`BookMemory` 五账本（设定 canon/事件 events/踩坑 pitfalls/经验 taste/好文风 goods）持久化 `book_memory.json`；质检出问题→`record_pitfall`（零 LLM、大改级、同章去重跨章累加）→`escalate` 同模式≥2次升级永久纪律（排除通用类）→`decay` 3章未复发降权；注入组装 `pitfall_block`（Top-N，重写/场景双措辞）+ `canon_block`（新名词规范/永久纪律/机制基线）+ `numeric_backfill`（方向违规→确定性修复指令）+ `record_event_consumed` 跨章事件消费 + `record_taste` 章节评分（最近 20 章）+ **good 账本（REQ-写作正向引导 5.6，2026-10-15）**：`record_good` 高分章（≥85 且无大改）段落自动入账 / `record_good_manual` 用户编辑保存（webapp 触发）免质检入账，按手法确定性分类（对话-潜台词/战斗-动作链/情绪-白描+点破/环境-物象白描/叙事-节奏），每类 ≤5 条、总量 ≤50、每条 ≤300 字；`good_block` 注入「本作写得好的片段（参考质感非内容来源）」供场景生成/重写双路径；开关 `book_memory=False` 全跳过（管线行为与 P0-5 一致）；供 stages（懒加载/5 处场景注入/回退整章）/quality（质检记录+重写注入）/webapp（编辑保存入账）复用 |
| `ai_taste.py` | AI 痕迹量化检测（P1-4）：确定性规则给分（连接词/情绪词/排比/段落雷同），只扣分不阻断 |
| `skills.py` | **技能统一注入管线**（REQ-技能统一）：`SkillAssembler` 统一采集 平台全局/平台角色/用户技能/项目快照（均读统一表 `skills`，source 区分 platform/user），按角色默认阶段（STAGE_BY_ROLE）过滤（技能 applicable_stages 空=全量）、统一清洗、产出 manifest（供 `agent_trace` 归因）；**P0-2**：STAGE_BY_ROLE 补 theme_character/world_rules/pacing 三 key、移除 validator 死配置（非 BaseAgent 不参与技能注入）；`agents/base.py` 与 `templates._skill_append` 均经此管线 |
| `templates.py` | 提示词模板组装；P2-3 动态模块随机化 `_pick_dynamic_module`（seed 可复现、相邻去重）；P2-1 风格范文锚定 `_style_reference`（截 3000 字，空则不注入）；P2-2 `final_review_prompt` 终审编辑；**P1** `opening_prompt`/`ending_prompt`（开篇/结局 Agent 任务提示词）；P0-1 `verify_combined_prompt` 支持 `reader_persona` 注入；**P2** 大纲 prompt（open/expand/continue）chapters 字段新增 `cliffhanger`（章末钩子）；**REQ-双锚** scene/chapter 注入题材锚（`teardown_anchor` 参数，开关 `teardown_style_anchor` 控制）、`expand_chapter_prompt` 补风格范文+个人锚+题材锚（弱约束，空不注入）；**REQ-scene-plan-align** `split_scenes_prompt` 新增 `moves` 骨架注入（非空=事件清单已由 character_moves 派生，第一步改为补字段、禁增删改写事件；空=原规划路径）；**§7.3 措施1** `scene_prompt` 新增 `event_progress` 事件进度硬约束（只推进本场景事件、禁提前消费后续事件完整流程）+ 场景衔接规则补"后续事件留给各自场景"；**生成端第三轮（P0-1/P0-3，2026-10-09）** `scene_prompt` 新增 `consumed_events`（已消费只许回响）/`upcoming_events`（后续禁提前消费）/`panel_baseline`（机制数字基线）/`panel_violations`（带错重试说明）四参数；**P0-4** `characters_action_fill_prompt` 单字段补全（只输出行动表数组）；**P0-7（2026-10-10）** `split_scenes_prompt` 新增 `book_canon`（设定账本注入）、`scene_prompt` 新增 `book_pitfalls`（本作历史错误注入块） |

### 2.3 webapp/（SaaS Web 服务）

| 文件 | 职责 |
|---|---|
| `server.py` | **组装壳**：导入注册路由 + 静态托管 `frontend/dist` + 启动入口（`python -m webapp.server` / `run_web.py`）。不写业务逻辑 |
| `core.py` | **核心层**：FastAPI 实例与 lifespan、鉴权（`_require_user`/`_require_admin`/`_check_project_access`）、项目工具（`_find_project`/`_open_pipeline`）、结算（`_compute_settle`/`_settle_incremental`/`_settle_price`）、生成线程（`_RunHandle`/信号量）、任务持久化（`_mark_task`/`_resume_interrupted_tasks`）、登录限流、项目创建/列表等公共逻辑；**P0-3 拆书库回流** `_inject_teardown_material`（调研前查同题材拆书条目写入 store 素材，供 ResearchEngine 消费）；**REQ-双锚** `_inject_teardown_style_anchor`（同题材拆书条目抽 styles/takeaways/excerpts 组装题材锚写入 store，供写手阶段消费，失败静默；`_run_generation` 入口统一注入——**含 start_from>0 从第 N 章重生成**，旧项目重生成也能拿到题材锚）；**用户级记忆库** `_inject_user_memory`（用户级画像→项目快照 `user_memory_snapshot.json`，webapp 写 / novel_gen 读，幂等+失败降级，接入全部生成入口）与 `_distill_user_memory`（书级→用户级蒸馏，生成完成/批量 finally 触发，幂等）；**后台开关管理** `_apply_global_overrides`（sys_config 表为准 → 内存态覆盖 cfg 顶层/agent_guard 子项/scene_limits，`_open_pipeline` 内 load_config 之后注入，DB 异常降级留痕） |
| `user_memory.py` | **用户级记忆库服务（跨书创作偏好继承）**：`UserMemoryService` 读写 `users/<uid>/user_memory.json`（偏好画像 preferences/跨书纪律 disciplines/个人文风锚 style_anchors/stats），`record_project_signals`（新书显式输入，按项目幂等）、`record_edits`（人工修订跨书锚，上限 30）、`distill`（书级 pitfalls/taste 蒸馏，全量重算式幂等）、`build_snapshot`（题材匹配过滤+上限裁剪→注入快照）、`summary/clear`；全部写路径零额外 LLM |
| `config_overrides.py` | **全局开关覆盖注入（REQ-后台开关管理）**：`apply_global_overrides(cfg)` 把 `sys_config` 实时值合并进 `NovelConfig`（顶层字段 + agent_guard 标量 + scene_limits），DB 异常回退快照/默认并 logger 留痕；生成链路（`core._open_pipeline`）与小墨AI 对话（`assistant/service.py`）共用，避免同一逻辑两处维护 |
| `assistant/` | **小墨AI（REQ-小墨AI 一期）**：`prompt.py`（读取 `novel_gen/pipeline/agents/prompts/assistant.md`，mtime 缓存 + 降级）、`context.py`（只读项目产物组装四层上下文：system 人设+Bible / 层2 事实块 / 层3 对话窗口 / 层4 提问；按 token 预算固定档位裁剪）、`cards.py`（确定性卡片：质检扣分/问题清单/版本对比/正向实测，正向指标与阈值全部取自 `pipeline/ai_taste.py` 常量）、`service.py`（会话编排：输入三闸 → 组装 → LLM → 失败降级 → 输出合规 → 落库） |
| `routers/` | 按域拆分路由（见 2.4） |
| `db/` | MySQL 数据层包：`base.py`（连接池/事务/表结构/init_db，含 style_roles.reference_text、skills 系列表 applicable_stages 与统一表 `skills` 幂等迁移、**小墨AI `conversations`/`messages` 建表**）、`users.py`（用户/返佣/认证/余额）、`projects.py`（项目归属/退款）、`skills.py`（技能/市场，统一读写 `skills` 表，platform/user/market 三源 + applicable_stages/version 透传）、`style_roles.py`（风格角色，reference_text 透传，挂载技能经 `skills` 表解析）、`teardowns.py`（拆书库）、`assistant.py`（小墨AI 会话/消息：会话 CRUD、消息追加与分页、最近 N 轮查询）、`traces.py`（agent_trace 全链路快照：MysqlTraceSink 落库 + 按 trace/project/user 查询 + P2-4 `_aggregate_quality`/`quality_report` 质量聚合报表）、**`config.py`（后台开关管理：FEATURE_FLAGS 注册表 → `sys_config` 表种子同步 + `sys_config_log` 审计 + 类型转换/列表/更新，SQL 唯一入口）**；`__init__.py` 统一 re-export，调用方 `from webapp import db` 不变 |
| `secret_encrypt.py` | BYOK Key 加密（Fernet，密文前缀 `enc:v1:`） |
| `pricing.py` | 定价档位（普通 2 元/万字、高质量 3 元/万字） |
| `referral.py` | 代理商业参数：`AGENT_DISCOUNT`(0.8) / `COMMISSION_RATE`(0.1) / `COMMISSION_BASE` / `REFERRAL_BONUS` |
| `mailer.py` | 邮件通知（验证码/退款通知站长） |
| `verify_code.py` | 邮箱验证码（6 位、10 分钟、60s 冷却、错 5 次作废） |
| `outline_parser.py` | 上传大纲文档解析（.txt/.md/.docx → 结构化字段） |
| `logger.py` | 统一日志（`logs/app.log`，5MB 轮转 ×3） |
| `admin_cli.py` | 站长命令行管理（users/balance/recharge/agent/...） |

### 2.4 webapp/routers/（10 个路由模块，共 106 条 API 路径）

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
| `projects_generation.py` | 生成控制 | 启动/停止/重跑（5）；**Agent 统一入口（阶段1B/1C）**：`POST/GET /agent-runs`、`POST /agent-runs/{task_id}/stop`、`GET/PUT /agent-prompts[/{agent}]`（人设不可变版本），业务全在 generate_service |
| `projects_versions.py` | 产物版本 | **阶段1C**：outline/world 版本列表（含每版计费金额）/受控回滚 select/rollback-impact 影响范围（3）；chapter 版本在 projects_chapters |
| `admin.py` | 站长后台 | 用户/代理/项目/退款/流水/日志/系统参数 + `GET /api/admin/quality-report` 质量报表（P2-4）+ **`GET/PUT /api/admin/config` 生成引擎开关管理（100 开关，key 白名单+类型/范围/select 枚举校验+审计）** |
| `assistant.py` | 小墨AI | 项目内写作对话（REQ-小墨AI 一期）：会话创建/列表/详情（含消息分页）/发消息/删除 5 条；全部走 `_require_user` + `_check_project_access` + 会话归属校验；发消息同步返回回复与确定性卡片 |

> 拆分史：原 `webapp/server.py` 2853 行单文件 → `core.py` + `routers/`（2026-09-20，拆后 openapi 路由集合 96=96 逐字节一致）。**新增业务路由时按域放入对应 routers 模块，禁止回写 core.py/server.py 堆逻辑**。

### 2.5 前端 webapp/frontend/src/（Vue 3 + Element Plus + Vite）

| 目录 | 职责 |
|---|---|
| `api/` | 请求封装：`request.js`（axios + token 注入）、`auth/novel/project/admin/tools/assistant` 按域分文件 |
| `router/index.js` | 路由表（登录/书案/详情/后台） |
| `stores/` | Pinia：`auth.js`（登录态）、`project.js`（当前项目） |
| `views/` | 页面：`Login` `ProjectList`（书案） `ProjectDetail`（沉浸阅读+编辑器；**新增「小墨AI」tab**） `Admin`（9 板块后台，含 P2-4 质量报表 tab：得分分布/问题类型/弃书风险/阶段成本；**REQ-后台开关管理「系统设置」tab：按 memory/writing/review/guard/assistant 分组渲染 100 个生成引擎开关（switch/input-number/select/input），保存即全局生效**） |
| `components/` | `RechargeDialog` 充值、`LlmSettingsDialog` BYOK、`tools/ToolsPanel` 工具面板（风格角色管理 + P2-1 参考文风字段 + P2-2 全书终审块）、`outline/OutlineEditor` 三级大纲、`world/CharacterGraph` 人物关系、`assistant/AssistantPanel`（小墨AI 会话列表+消息区+模式/章节切换+定位跳章）、`assistant/AssistantCard`（确定性卡片渲染）、`layouts/MainLayout` |
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
| `sys_config` / `sys_config_log` | **后台开关管理（REQ-后台开关管理）**：全局生成引擎开关唯一运行时权威（100 项由 FEATURE_FLAGS seed 幂等同步，新增开关自动入表）；`sys_config_log` 记录每次修改的旧值/新值/operator |
| `agent_trace` | Agent 全链路执行快照（LLM 调用明细/成本事件/质量校验；trace_id 串联单次任务，预留 user_id/scene/extra 支撑监控与用户分析）；P2-4 `quality_report` 据此聚合质检分布/重写率/弃书风险；小墨AI 调用以 `scene=assistant` 落表 |
| `conversations` / `messages` | **小墨AI 会话（REQ-小墨AI 一期）**：`conversations`（project_id/user_id/title/mode/时间）与 `messages`（conversation_id/role/content/extra JSON：卡片、token、章节号、降级标记）；项目产物仍是评估事实来源，会话表只存对话 |

## 4. 关键数据流

**创建项目**：前端表单（含费用预估）→ `POST /api/projects`（core 校验计费封顶）→ 落 `project_owner` + 创建 `projects/<id>/` 目录（config.json 快照，**不含 API Key**）。

**生成**：`POST /api/projects/{id}/generate` → 鉴权/访问检查 → `_acquire_slot` → 落 `task.json` → 后台线程 `_run_generation`：`NovelPipeline` 逐阶段推进（调研→大纲→编辑质检→人物→逐章），每章完成写 `chapters/<n>.json` → 完成后结算 `_settle_incremental`（增量先落基线再扣费 + 返佣）→ `_clear_task`。

**断点续传**：任何阶段中断，重启后 `_resume_interrupted_tasks` 扫描残留 `task.json` → 重新打开 pipeline → 逐阶段检查产物文件存在性 → 只补缺失部分（幂等）。

**导出**：`projects/<id>/output/` 生成 TXT/EPUB/DOCX + 封面 PNG（限 5 次），同时拷到全局 `output/`。

**小墨AI 对话**：前端 `POST /api/projects/{id}/assistant/conversations/{cid}/messages` → 鉴权/归属校验 → 输入三闸（空/超长/注入）→ 只读项目产物组装上下文（`review.json`/`chapter_design.json`/`book_memory.json`/`chapters/<no>.json`/config）→ `create_llm`（BYOK 优先，`scene=assistant` 落 `agent_trace`）→ 输出合规校验 → 确定性卡片 → `messages` 落两条（user/assistant）；LLM 失败只落 user 并返回 200 降级提示。

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
| 全局开关覆盖（后台管理） | `FEATURE_FLAGS` 注册表 = 默认值唯一事实来源；`init_db` seed 幂等同步进 `sys_config`（新增开关自动入表）；`_open_pipeline` 内 `_apply_global_overrides` 以表为准内存态覆盖 cfg（顶层/agent_guard/scene_limits），**config.json 项目快照不迁移不改写**；DB 异常降级走快照/默认并留痕 | 运行时开关收敛到管理后台、以表中数据为准；新增开关历史项目零迁移统一生效（用户核心动机） |

## 6. 部署架构

- 服务器：阿里云实例 `i-bp14oxtecrth3wbq6thc`，目录 `/opt/novel-gen`，`docker compose`（`mysql-mozao` + `novel-gen` 两个容器）。
- 前端多阶段构建：Dockerfile 第一阶段 `node:20-alpine` 内 `npm ci && npm run build`，第二阶段拷 dist 进 Python 镜像（`python:3.13-slim`，内置 Noto CJK 字体、Asia/Shanghai）。**本地无需 build、dist 不入库**。
- 数据卷：`projects`（中间产物）/ `output`（成品）/ `logs` / `data`（MySQL 数据，`NOVEL_DB_PATH=/data/novel_web.db` 由环境变量控制，容器实际走 mysql-mozao 服务）。
- 反代：nginx（服务器 `/etc/nginx/conf.d/mozao.conf`，反代 `127.0.0.1:8000`，HTTP 80 跳 HTTPS 301；域名 https://mozao.click，证书 Let's Encrypt 经 certbot 自动续期）。
- 发版：`git archive master` → workbench upload → 解压 → `docker compose up -d --build`。详见 `AGENTS.md` §5。

## 7. 改代码前的指引（防迷路）

1. **新业务路由** → `webapp/routers/<对应域>.py`，数据操作走 `webapp/db/` 对应域子模块已有函数，或在该域子模块内新增（禁止回写 base.py 堆 SQL）。
2. **改生成逻辑/提示词** → `novel_gen/pipeline/`（角色行为改 `agents/prompts/*.md`，编排改 `manager.py`）。
3. **改计费/返佣** → `webapp/pricing.py`、`webapp/referral.py`、`webapp/core.py` 结算链。
4. **改前端页面** → `webapp/frontend/src/views|components/`，接口封装在 `src/api/`。
5. 不确定某文件干什么 → 先查 `docs/filemap.md`；涉及历史决策 → 查 `docs/decisions.md`。

## 8. 超长篇生成攻坚（第一批 + 第二批 + 二期已合入 devtest）

> 需求来源：`docs/REQ-超长篇生成攻坚.md`。治长文四大天花板：记忆稀释 / 文风漂移 / 大纲承诺衰减 / 成本线性爆炸。开关默认值以 `novel_gen/config.py` 为准，双锚默认全关（避免放大 AI 特征）。

### 8.1 新增模块

| 文件 | 职责 |
|---|---|
| `pipeline/pending_commitments.py` | 章末承诺台账：`build_commitment_ledger`（committed/missed/planted/must_carry_to_next 四类清单）、`compensation_block`（L1 上章补偿块）、`l2_hangover_block`（S3.3 L2 连续漏兑现→下章挂账镜头，章型豁免） |

### 8.2 关键落点

- **beat 门禁扩展（S3.2）**：`pipeline/beat_gate.py` —— loose 去标点匹配 `_norm_for_loose`/`_slot_hit(loose)`、章型白名单 `detect_chapter_type`（对话=引号占比≥0.35、过渡=<50字）、节点优先级 P0/P1/P2（`beat_node_priority`）。
- **承诺台账接线（S3.1）**：`store/project.py` 增 `pending_commitments_path/load/save/prev`；`stages.py` write() 落盘 + `_pending_compensation_block`（L1+L2 合并）注入 memory_context 前段（S1.3 前置）。
- **双锚（S2.1）**：`stages.py` `_teardown_anchor`/`_personal_anchor`，开关默认关返回空串，不注入。
- **降本（S4.3/S4.1）**：`llm/base.py`+`providers.py` `json_mode`→`response_format={"type":"json_object"}` + `prompt_cache_hit_tokens` 埋点；`templates.py:1644 chapter_prompt` 在 `prefix_cache_aligned` 下把稳定块（字数·硬指标+constraints+题材参考）前置 user 最前；`config.state_extract_frequency` 3→5。
- **伏笔原文存档（S1.1）**：`store/schemas.py ForeshadowItem.original_excerpt`；`pipeline/reader_ledger.py` `_archive_foreshadow_excerpts`（埋设章落盘时抽原文句）、`_foreshadow_block` 附【到期伏笔原文】。
- **周期再锚定（S2.2）**：`stages.py` `ChapterWriter._rolling_style_anchor(chapter_no)`（`chapter_no%style_reanchor_every==0` 时读 `BookMemory.good_block(top_n=3, max_chars=900)` 注入 system 前段）。
- **漂移检测（S2.4-L1）**：`pipeline/ai_taste.py` `style_drift_metrics`（句长均值/cv/TTR/填充词占比/明喻密度）+ `drift_score`，纯确定性每章可跑，供与前期章基线对比。
- **伏笔 stalled + 语义双路（S1.4）**：`reader_ledger.py` 状态机新增 `stalled`（planted 且埋后超 `ledger_stall_after` 章无推进、不在回收窗口 → 注入"冷却"提醒，防长文伏笔弃笔）；模块级 `semantic_foreshadow_check`（字面未命中时 LLM 判语义推进，默认关省成本）。
- **固定锚双轨（S2.3）**：`stages.py` `_fixed_style_anchor`（开头 1-2 章原文 + 个人旧稿作风格原点，永不替换，默认关防放大 AI 特征）；整章/场景两路径滚动锚+固定锚双轨合并注入。
- **L3 风格裁判（S2.5）**：`manager.py` `_run_verify`（每 verify_every 章）末尾接 `_style_judge_l3`——LLM 比对最近章 vs 风格原点输出六维差异，漂移维度≥阈值写 verify_issues（触发再锚定信号）。
- **远期大纲粗粒度（S3.6）**：`beat_gate.py` `compute_beat_coverage/assess_chapter` 增 `far_beats` 参数，远期节点不计 fail 分母（全远期 verdict=skip_far）；`stages.py` `_far_outline_beats` 从 key_nodes 取距当前>窗口节点，`outline_rolling_detail` 默认关。
- **fail 章二级复核（S3.4）**：`stages.py` `_beat_fail_judge`（仅 beat_gate=fail 且开关开时 1 次 pointwise judge，anti-length、judge 异模型族，确认未兑现才进 L3）；模块级 `_parse_json_obj` 健壮 JSON 解析（REQ 11.7）。默认关（启用待人工标注集验证>80%）。

### 8.3 开关清单

- 第一批：`beat_loose_match`/`beat_chapter_type`/`beat_node_priority`/`pending_commitments` = True；`teardown_style_anchor`/`personal_style_anchor` = False（双锚默认关）。
- 第二批：`json_schema_output`/`prefix_cache_aligned`/`foreshadow_original_text`/`style_anchor_rolling`/`style_drift_check` = True；`style_reanchor_every` = 5（0=关）；`state_extract_frequency` = 5（原 3）。
- 二期：`ledger_stalled`/`style_judge_l3` = True（默认开）；`ledger_stall_after` = 8；`style_judge_drift_threshold` = 2；`foreshadow_semantic_check`/`fixed_style_anchor`/`outline_rolling_detail`/`beat_fail_judge` = False（默认关，启用条件见 §8.4）。

### 8.4 二期项（已开发，部分默认关）

二期全部开发并合 devtest：S1.4（语义双路回收判定，stalled 默认开、语义核对默认关）/ S2.3（固定锚双轨，默认关）/ S2.5（L3 裁判，默认开）/ S3.6（远期大纲，默认关）/ S3.4（fail 章 judge，默认关——启用待自建 20-50 章人工标注集验证一致率>80%）；滚动细化大纲生成（S3.6 近窗分批细化）为后续项；S4.2（小模型静态路由）/ S4.4（润色分层）待各自 A/B 验证后另开。
