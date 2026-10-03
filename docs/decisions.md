# 决策记录（docs/decisions.md）

> 目的：沉淀历史技术决策与踩坑记录（"为什么这么设计"）。**AI 助手遇到与本文冲突的"优化"想法时，先读本文确认是否已评估过，不要推翻已有设计**。
> 维护规则：每次代码合并到 master 后，把新决策/新坑追加到本文（含日期与 commit），与 README 更新记录同步。

## 一、架构级决策

| 日期 | 决策 | 原因 / 影响 |
|---|---|---|
| 2026-09-20 | `webapp/server.py`（2853 行单文件）拆分为 `core.py`（核心层）+ `routers/`（9 域 90 路由）+ `server.py`（组装壳） | 最大架构债；拆分后 openapi 路由集合 **96=96 逐字节一致**、29 pytest 全绿、端到端冒烟通过才合并。**约束：新路由按域进 routers，禁止回写 core.py/server.py 堆逻辑** |
| 2026-09-20 | `routers/projects.py`（1150 行/43 路由）再拆 5 个子模块（base/outline/chapters/world/generation） | 路径/方法/返回体不变，路由集合回归一致后合并 |
| 2026-09-20 | 结算重构：`_compute_settle` 统一口径（增量/整批共用），增量结算**先落盘结算基线再扣费** | P0 资金安全：消除"扣费后崩溃重启重复扣费"窗口。**宁可漏扣、不重扣**。改结算必须跑 `tests/test_settle.py` |
| 2026-09-20 | 生成任务持久化：线程启动落 `task.json`、结束清理，lifespan 启动自动重拾中断任务并断点续跑 | 服务重启不丢生成/大纲/批量任务 |
| 2026-09-20 | BYOK Key 加密落库：`secret_encrypt.py`（Fernet，密文前缀 `enc:v1:`）；存量明文启动自动迁移；`.llm_encrypt.key` 不入库 | DB 泄露不暴露用户自配 Key。附 P0 修复：`get_user_llm` 键名与调用方契约不符导致 BYOK 自配 key 从未生效 |
| 2026-09-20 | 项目列表页 `meta.json` 轻量统计缓存，章节未变化时零文件内容读取 | 列表页性能 |
| 2026-09-20 | `tests/` 取消 gitignore、测试入库；关键路径（结算/权限/加密/路由拆分）发版前必须本地跑通 | 回归保障；当前 44 用例全绿 |
| 长期 | 产物文件（`projects/<id>/` JSON）是项目状态**唯一权威来源**，DB 只存归属/订单 | 断点续传幂等、不重复消耗 token |
| 长期 | 计费：创建不扣费，生成完成按 AI 实际生成字数结算；目标字数封顶；生成中余额 ≤ 0 自动停止 | 商业模式既定，改动需谨慎 |
| 长期 | 前端多阶段 Docker 构建、dist 一律不入库 | 见 AGENTS.md §6；防 hash 漂移/白屏 |
| 2026-09-21 | 生成正文提示词约束口头禅：`writer.md` 限同句口头禅每章 ≤2 次、禁止章末复现；`architect.md` 禁止把口头禅写进 `core_event`/`transition` 作为"每章复现"指令 | 实测《我的地球Online日记》45 万字中"先记下来"出现 150 次（≈3.3 次/万字），口头禅从识别标签退化为复读机；`repetition.py`（>5 次/万字才禁）拦不住刻意设计的短口头禅，需在提示词层约束 |
| 2026-09-27 | Agent 生产级加固阶段1：全链路 trace（`agent_trace` 表 + LLM 层模板方法自动埋点）、成本防护（AgentGuard 循环/Token 阈值）、输入注入检测（API 400 + 拼 system 前清洗）、输出合规校验、章级 QualityChecker 自校验（1 次自动重写） | 解决「执行不可回溯/成本不可控/输入无防护/输出无校验」；mock provider 不挂 guard 保持离线测试一致；各能力独立降级开关；新增 4 测试文件 38 用例，全量 118 pytest 全绿 + mock 冒烟 + MySQL 落库验证通过。**约束：新能力默认开启，异常用 `agent_guard.*` 开关关闭，勿改回硬编码** |
| 2026-09-27 | `webapp/db.py`（1409 行单文件）拆分为 `webapp/db/` 包：`base`（连接池/表结构/init_db）+ `users`（用户/返佣/认证/余额）+ `projects`（归属/退款）+ `skills`（技能/市场）+ `style_roles` + `teardowns`（拆书库）；`__init__.py` re-export 全部符号（含 `_connect`/`_valid_email`/`DB_*`） | 数据层按业务域收敛；调用方 `from webapp import db` 零改动（routers/core/admin_cli/teardown_lib/tests 全部兼容）；**回归：80 pytest 全绿 + app 加载 108 路由 + 15 业务模块导入通过才合入**。约束：新增数据函数放对应域子模块，禁止回写 base.py |
| 2026-09-28 | 封面 AI 模型由硅基流动 `baidu/ERNIE-Image-Turbo` 更换为 `Tongyi-MAI/Z-Image-Turbo`（代码默认值 + 本地 .env COVER_MODEL 同步更新；生产可经 COVER_MODEL 环境变量覆盖） | 硅基流动官方通知 ERNIE-Image-Turbo 2026-09-30 下线；Z-Image-Turbo 兼容现有 payload（image_size=768x1024 / seed / output_format=png，官方 images API 参数已核实）；回归全量 205 pytest 全绿。merge master 8719855 |
| 2026-09-28 | 扩写大纲（expand 模式）分批生成：先出总纲（title/intro/core_conflict/main_line/volumes），再按 8 章一批生成章节；`expand_outline_prompt` 增加 batch 参数（None=总纲 / (start,end,prev_summary)=章节批），跨批传前情摘要保持连贯 | 承接 6326ac5 截断修复：即使显式 max_tokens=8192，全书大纲 JSON（几十章 × 8 字段）仍可能超限；分批把单次输出降到安全长度，避免反复重试截断。回归全量 205 pytest 全绿。merge master 8719855 |
| 2026-09-29 | 章级防「多稿拼接」加固（事前+事后双保险）：① 事前：`_output_discipline()` 注入 5 个正文输出提示词（scene/chapter/polish/revise_apply/expand），禁止多版本并列/元文字开头/中途截断；② 事后：QualityChecker 新增确定性 `_self_dup_issues`（双信号：跨大跨度重复的短收尾行 8~16字 ≥3次 + n-gram 覆盖 >25%）与 `_ending_issues`（末字非句末标点视为截断）；③ 自愈：`ensure_quality` 检出拼接先走 `_salvage_single_draft` 确定性单稿抢救（切候选段取最长且句号收尾），不依赖 LLM 重写 | 根因：管线把 LLM 原始输出当最终正文，仅化妆式清理；质量关卡重复检查只比对前几章、字数检查只查过短，故四稿拼接+截断被判满分（review.json score:100）。实测故障第1章（四稿拼接+截断，5697字/面板行6次）现正确检出并抢救为 1682 字单稿；正常章无误报（第2章 40 字长行重复 2 次不算拼接，n-gram 覆盖仅 1.7%）。**关键校准**：分界标记限短行 8~16 字，长行重复多为正常叙事复用，不能当拼接。新增 test_output_discipline.py + test_quality.py 新增 3 用例，全量 227 全绿 |
| 2026-09-29 | C 类「内容/写作级缺陷」设定层+提示词源头修复（金手指规则/细节一致性/开篇锚点/母题密度）：① `World` 新增 `mechanism_rules`（机制卡：语义无歧义/结算规则/代价递增与终局/首章兑现标题，to_dict/from_dict 兼容 dict/list）；② `world_prompt` 与 `world_from_content_prompt` 均要求产出 `mechanism_rules`；③ `Memory.build_context` 把机制卡注入强约束区（`[L1·核心锁定]`，prompt 末尾最高权重、永不裁剪）；④ 新增 `_writing_constraints()` 注入 chapter/scene 提示词（人物一致性：同一物理细节全文只一种口径、禁止左右手横跳；开篇锚点前置：首章 500 字内建立共情锚点、温度内容不得沉底；母题密度：同一动作/短句/意象单章 ≤2 次防口癖） | 批注根因（与拼接不同根）：金手指规则未定义/历史死亡史矛盾/标题未兑现/蚀痕无作用、左右手九处横跳、钩子太晚情绪落点缺失、母题复读密度过高——均为内容自洽与写作质量问题，A/B 类防拼接管线治不了，需在设定层固化 + 提示词源头约束而非仅事后校验。机制卡入强约束区保证逐章注入且不裁剪，写作约束随整章/场景正文生成注入。新增 test_writing_constraints.py 7 用例，全量 234 全绿 |
| 2026-09-29 | 分卷-章节关联完整修复：`OutlineGenerator.assign_volumes` 按 `volumes.start_ch~end_ch` 区间确定性回填 `volume_no`（重叠取较小卷/区间外就近归卷/手动 `!=0` 不覆盖）；`OutlineSaveReq` 增 `volumes`、`OutlineChapterReq` 增 `volume_no`，`save_outline` 构造 `Outline` 时完整保留分卷与卷号落盘 | 根因：生成侧 `_normalize`/分批未回填卷号 + 保存链路 Pydantic 丢弃 `volume_no`/`volumes`，导致"分卷下无对应章节、前端拖拽改卷号保存后被还原"。前端拖拽 UI 本已存在，只需修生成与保存两段后端。新增 tests/test_volume_link.py 8 用例（P0 拖拽持久化/P1 回填与保存/P2 旧数据兼容），全量 241 全绿 |
| 2026-09-29 | 世界观跟随主角视角逐步展开：`World` 新增 `world_reveals`（阶段揭示计划：`from_ch`=主角得知起始章、`content`=主角视角片段，to_dict/from_dict 兼容旧数据缺字段回退全量注入）；`world_prompt`/`world_from_content_prompt` 均要求产出 `world_reveals`（禁止一次写全）；`Memory.build_context` 逐章分层注入——已揭示进普通区、未揭示进强约束区「禁止提前透露」、无 reveals 回退原全量注入；`writer.md` 加「视角与世界观」约束（只写主角已知、未揭示禁剧透） | 根因：原每次把完整 `world_view` 全量注入强约束，写手从第 1 章全知所有设定，正文无法"跟随主角逐步发现世界"（architect.md 的"写到哪铺到哪"未落地）。未揭示设定进强约束区保证不裁剪、不剧透。新增 tests/test_world_reveal.py 7 用例（P0 分层注入/P1 序列化与提示词/P2 兼容），全量 241 全绿 |
| 2026-09-29 | 世界观揭示加「铺垫链」深化（避免"到点凭空解锁"）：`WorldReveal` 增 `foreshadow_ch`（铺垫最早章）与 `hint`（铺垫线索）；`Memory.build_context` 三段式注入——潜伏期（N<foreshadow_ch 完全不注入）→ 铺垫期（fs≤N<from_ch：hint 进普通区可写暗示、真相进强约束禁剧透）→ 揭示期（N≥from_ch：真相进普通区）；旧数据（无 foreshadow_ch）未揭示真相仍进强约束禁剧透（保持原行为） | 根因：原实现"到了 from_ch 章就凭空解锁"，缺铺垫-伏笔因果链，读者觉得突兀。现要求真相必须有前文线索层层铺垫、由具体事件触发引出并收束暗示，禁止到点摊牌。`world_prompt`/`world_from_content_prompt` 均要求产出 foreshadow_ch（必须 < from_ch，提前 2-3 章）+ hint。新增 6 用例（P0 潜伏/铺垫/揭示三段注入、P1 序列化与提示词、P2 兼容），全量 255 全绿 |
| 2026-09-29 | 多Agent协作分层优化（REQ-多Agent协作分层优化.md）：① 编排层 `Orchestrator`（阶段门禁 StageGate：产出→准出校验→不合格自动重生成≤N轮→超限仲裁放行，主题>人物>世界观），`run_world/run_outline` 接入（mock provider 默认跳过门禁）；② 校验层 `LogicValidator` + `issue_triage`（5 类专项校验：设定一致性/OOC/主线偏移/重复≤5%/伏笔完整性，问题分级 fatal/major/minor）；③ 生产层细分：ThemeCharacter/WorldRules/Pacing 专项 Agent + prompts，`Architect` 收敛为骨架生产者并编排三者；④ 数据结构：`World` 增 theme_proposition/conflict_matrix/protagonist_arc/character_motives/goldfinger_rules(五要素)/setting_taboos，`foreshadowing` 升级 `ForeshadowItem`(buried_ch/reveal_ch/level)，`Outline` 增 key_nodes/volume_pacing，全部 from_dict 兼容旧数据；⑤ 成果整合 `report.py` 落盘 4 份设计文档到 `projects/<id>/design/`；⑥ 新开关 `stage_gate`/`stage_gate_retries` | 把设计文档「总控+生产+校验」分层落地为可执行机制，解决设定吃书/主线跑偏/伏笔只埋不收/节奏失控。mock provider 跳过门禁保持离线行为一致。新增 27 用例（P1 stage_gate/validator/schemas + P2 report），全量 282 全绿。**约束：金手指五要素/伏笔回收章由 StageGate 强校验，缺失自动重生成** |
| 2026-09-29 | 技能统一注入管线（REQ-技能统一.md，阶段一）：① `writing_skills`/`user_skills`/`skill_market` 加 `applicable_stages`（适用阶段，空=全量）+`writing_skills.version`，幂等迁移（仿既有 _column_names+ALTER 模式）；② 新增 `novel_gen/pipeline/skills.py` SkillAssembler：统一采集 平台全局/平台角色/用户技能/项目快照，按角色默认阶段路由（STAGE_BY_ROLE），统一清洗，产出 manifest；③ `agents/base.py` 收敛 `_role_skill_append`/`_system_with_role`/`persona_prefix` 接入管线，注入清单经 `record_agent_step("skill_injection")` 落 agent_trace（有 recorder 才记、异常吞掉）；④ `templates._skill_append` 复用 `collect_project_blocks`；⑤ 市场安装 `install_market_skill` 透传 applicable_stages | 解决 Skill 散落 4 表+2 快照+提示词文件、注入来源双轨（实时 vs 快照）、仅靠 role 字符串硬匹配的弱联动。**兼容性关键**：阶段过滤在组装层（Python 侧）做，**不新增 DB 查询参数**（避免破坏 test_agents 对 db.list_skills 的 mock 签名）；存量技能 applicable_stages='' → 全量注入，输出与改造前逐字节一致。新增 test_skill_assembler.py 14 用例（P1 聚合/阶段过滤/manifest 落 trace + P2 匹配规则/兼容），全量 296 全绿。**阶段二（另立 REQ）**：4 表物理合并、风格角色预设化、skills_library 同步、管理 UI 收敛 |
| 2026-09-29 | 技能统一注入管线（阶段二，存储层收敛，REQ §9）：① 统一表 `skills`（source∈{platform,user,market}+owner_id 区分权限/信任，含 role/applicable_stages/version/downloads/market_id），`base.py` 新增 DDL+幂等迁移，**旧 4 表保留不 DROP**（回退）；② `deploy/migrate_skills_merge.py` 幂等迁移（skills 非空即跳过）：writing_skills→platform、user_skills→user、skill_market→market，回填 user 行 market_id（旧市场id→新id），重映射 style_role_skills.skill_id（admin→platform/user→user 新 id）；③ `db/skills.py` 重写为统一读写 `skills`（函数签名与返回字段不变：platform/user 行 `owner_id AS user_id`、market 行 `owner_id AS author_id` → 前端 API 零改动）；④ `db/style_roles.py` 挂载 JOIN/校验改经 `skills`（skill_type admin=platform/user=user）；⑤ `deploy/sync_skills_library.py` 把 writing-techniques 蒸馏为平台技能（按名幂等 upsert、文件名关键词映射角色）；⑥ 注入管线 `collect_role_blocks` 零改动（db 函数签名不变） | 根治"同一能力散落 4 表+市场外链"。**关键**：迁移保留旧表可回退；返回字段别名保住前端 `r.user_id`（我的/平台）判断与市场 `author_id`；注入逻辑/输出逐字节不变。新增 test_skill_assembler.py 统一市场安装+迁移幂等 DB 用例，全量 297 全绿。**遗留**：管理 UI 收敛（REQ 9.7）API 已统一、前端零改动，UI 结构无大改需求，视需要单独排期 |
| 2026-09-29 | 章内自洽与机制账本（REQ-章内自洽与机制账本.md）：① 新增 `chapter_state.py`——章末/场景状态抽取（机制数字/人物生死/物品归属/时间锚点/已发生事实，LLM 主抽取 + 确定性面板正则兜底 + 失败降级），store 落 `chapter_state.json`，manager 写章循环随 character_state 一起更新（开关 `chapter_state_tracking`/`chapter_state_llm`）；② `memory.build_context` 新增 L1.6「世界状态·承接上一章」（账本章号<当前章才注入），并把 `character_motives` 动机卡注入出场人物区；③ `stages._generate_chapter_scenes` 逐场景累积「本章已推进状态」注入后续场景提示词；④ 提示词：`split_scenes_prompt` 要求 time_marker/关键事件等权重/时间顺延，`scene_prompt` 注入状态承接+时间过渡+视角+内心底线 4 条硬约束，`_writing_constraints` 增时间轴/单口径/内心底线/悬念句 4 规则，`world_prompt`×2 机制卡增数字账本/时间词语义/多数字关系/历史衔接；⑤ 校验：`validator._check_mechanism` 机制卡三要素（语义/结算/代价终局）缺失→fatal，`quality._repeat_sentence_issues` 章内同句复读确定性检测（叙述≥3次/长句2次/面板行2次→大改触发重写，对话口头禅≤2次豁免） | 根因（《万墟之上》第1章 32 条批注）：模型在「场景级独立生成+静态记忆注入」结构下维护不了章内可变状态——面板数字 3→2→1→1（第3次死亡漏减）、疤脸死而复生、押金责任方前后矛盾、"找到你了"四处口径冲突、整句复读 9 处、情绪零外显。数字/生死/物品/时间是叙事内可变状态，此前既不喂回也不校验，属 C 类更深一层。新增 test_chapter_state.py 15 用例 + test_quality/test_validator/test_writing_constraints 扩展，全量 329 全绿。**约束：状态账本为弱约束提示（以直读正文为准），LLM 抽取失败静默降级；确定性面板正则优先于 LLM 数字** |
| 2026-09-29 | 事前设计卡（REQ-事前设定卡.md）：World 新增 enemies/locations/mechanism_baseline/clock/factions/style_taboos 六个结构化字段；world_prompt 要求世界观阶段产出；memory 按相关性注入（怪物/地点/势力普通区，机制初始值/时钟/语言禁忌强约束区）；validator 加缺卡门禁；前端世界观 tab 可视化增删改 | 把事后矛盾检查前置为事前设计——模型不再自行发明怪物形态/空间/数字，从源头防"灰卒无固定形状却有利齿""地下出现暮色""四数字对不上"。331 测试绿 |
| 2026-09-30 | 开发流程引入角色职责分工（AGENTS.md §9 重构为 需求/方案设计/开发/测试/发版交付 五阶段，主责角色依次为 产品经理/架构师/工程师/QA工程师/交付，每阶段设必交产出与准出关口；新增方案设计阶段，补齐架构评审与决策记录） | 小团队一人可兼任多角色，但每阶段必须有明确主责与关口，避免"需求不清直接写码 / 无方案硬开发 / 测试后置 / 发版无线上验证"。LLM 按任务所处阶段以对应角色执行。文档流程变更，无 pytest 影响 |
| 2026-09-30 | Token 消耗优化（REQ-token优化.md，P0+P1 全落地，用户拍板 6 项全按推荐）：① 前置解决优先——新增 `setting_cards.py` 纯确定性分章设定卡索引（world 后构建 `setting_cards.json`，memory 按章只注入出场人物/相关生物·地点·势力·伏笔子集；**空列表=命中但本章无项→不注入，None=索引缺失/关闭→回退全量**；匹配=名称直判（目标词含名称即命中）+关键词包含双条件）；② 调用裁剪——`scene_state_mode`（auto=普通确定性/高质量 LLM）、`state_extract_frequency`（默认 3，每 3 章 1 次 LLM 状态抽取）、`action_plan_first` 默认 False（行动表并入拆场景输出 `characters_action`，`action_plan_first=True` 保留原独立调用）、`polish_hq_only` 默认 True（普通模式关整章润色）、`consistency_llm_check` 默认 False（章内矛盾自检仅高质量模式）、`verify_combined`+`verify_every`（默认 5，原每 3 章；3 连校验合并为 1 次调用）、`editor_diff_revise`（diff 式修正只输出需改字段 max_tokens 4096→1200，Python 合并回大纲，解析失败/关闭回退整章重出）、`targeted_rewrite`（定点重写：issue pos/原句锚点定位窗口 ±200 字，窗口覆盖 <60% 正文才局部重写并拼回，无定位/跨度过大回退整章）；③ `memory_direct_read_chars` 默认 2000→1200 | 根因：09-27~09-29 密集上线的质量机制全依赖 LLM 调用，单章调用从改造前 ≈2 次膨胀到 ≈13~16 次（普通模式），约 5 倍；agent_trace 实测（847 条）in 占 73%（chapter 单次输入均值 3890、最大 9.3k）。优化方向：能前置固化（设定卡/确定性脚本）就不反复调 LLM；必须校验则降频合并裁剪输入；必须重写则定点化。**存量项目 config.json 已持久化旧默认值 → 不写迁移（用户拍板）；新增开关默认值对存量项目仅少调用方向生效（安全）**。全量 357 pytest 全绿（新增 26 用例）+ HTTP 端到端冒烟 ALL PASS + mock 整合冒烟（setting_cards 落盘验证）通过 |

| 2026-09-30 | 章节质量防线修复（REQ-章内自洽与机制账本·扩展，P0×3+P1×3+P2×1，用户拍板全做）：① **重写注入设定上下文**（P0-1）——`_rewrite_whole/_rewrite_span` prompt 增加「世界设定/金手指机制卡/机制初始值面板标准答案/人物卡/上一章机制数字」块（`_context_block`，取 world_view/mechanism_rules/mechanism_baseline/characters + chapter_state.numbers，异常降级跳过），根治"真空重写"（《万墟之上》第1章重写后复检仍 5 条的直接原因）；② **机制数字结算确定性校验**（P0-2）——`_panel_progress_issues`：同标签同数值面板行跨段（间距>150字）复现 = 结算未推进，判大改扣 30 分（零 LLM）；③ **复检失败不再静默交付**（P0-3）——`ensure_quality` 自动重写 1 次→最多 `_MAX_REWRITE=3` 轮（通过即停，重写无效提前停）；单章重写路由返回 `quality` 字段（passed/score/issue_count/auto_revised），前端 ProjectDetail 未通过弹警告（不再无声给坏章）；④ **拆场景事件边界互斥**（P1-1）——`split_scenes_prompt` 加事件互斥硬规则，`stages._scene_events_overlap` 用 event 文本相似度（>0.55 且次数标记不同才豁免）检测重叠场景→回退整章；⑤ **面板行不再作草稿分界**（P1-2）——`_draft_markers` 排除面板行（同值复现归"数字结算未推进"专检），`_salvage_single_draft` 抢救保留下限 `_SALVAGE_MIN_KEEP=0.4`（丢>60%拒绝，防误截断）；⑥ **状态抽取去截断**（P1-3）——`_norm_text` 上限 3500→12000（章末/场景 LLM 抽取覆盖全文关键状态），`_consistency_llm_check` 正文输入同步 3500→12000；⑦ **矛盾自检降频覆盖普通模式**（P2）——新开关 `consistency_llm_every`（默认 3，普通模式每 N 章 1 次，高质量模式不受限） | 实证根因链（《万墟之上》第1章单章重写，本地服务跑当前代码）：review.json `score=0, passed=false, auto_revised=true` 检出 5 条大改仍放行落盘——检测能力够（LLM 矛盾自检抓到 4 条硬矛盾），修复能力为零（重写 1 次即放行 + 重写无设定上下文 + 数字无确定性校验 + 账本 3500 截断背书错误 2/3）。**约束：①面板行重复=数字结算问题，不归拼接；②拼接抢救只对非面板行 marker 生效，找不到 marker 不抢救、走重写兜底；③新开关已注册 FEATURE_FLAGS + NovelConfig 字段，存量项目走默认值**。新增/调整 22 用例，全量 379 pytest 全绿；用真实 001.json（5825字）验证：面板 2/3 复现正确报"机制数字未结算推进"、不再误判多稿拼接、全文进入状态抽取 |

| 2026-10-03 | 流水线 Agent 接线补全（P0，改小但解结构问题，用户给出核查结论后执行）：① **激活 ReaderAgent**——`manager.py` 实例化 `ReaderAgent` 为正式角色成员，`_run_verify` 把其 persona（reader.md + reader 专属技能，`verify_combined_prompt` 新增 `reader_persona` 参数）注入读者视角检查，不改变输出 schema、保持单次合并调用（不增 token 调用数）；② **细分 Agent 接线**——`STAGE_BY_ROLE` 补 theme_character/world_rules/pacing 三 key（此前缺失，三 Agent 内部零 LLM 调用、persona 从未被读取即"完全不生效"）、移除 validator 死配置（LogicValidator 非 BaseAgent 不参与技能注入）；`ArchitectAgent` 编排时把 theme+rules persona 注入 WorldGenerator、pacing persona 注入 OutlineGenerator，使细分角色要求（含专属技能）真正进入 LLM 生成阶段，确定性 apply_anchor/plan_* 仍兜底；③ **拆书库回流**——拆书库此前"只沉淀不消费"（`sink_research` 仅在 webapp/core 调用，novel_gen 零引用）；新增 `ProjectStore.teardown_material` 读写 + `webapp/core._inject_teardown_material`（调研前按题材查拆书条目格式化写入 store）+ `ResearchEngine._material_text` 并入调研素材，保持 novel_gen 不依赖 webapp 的分层 | 解决"建了能力却没接线"：ReaderAgent 无独立审校执行点、三个细分 Agent 完全失效、爆款拆书库长期只进不出。**关键事实修正**：ReaderAgent 并非纯死代码（writer.py 已用它注入 persona），缺的是独立审校执行点；三个细分 Agent 连"经 architect 间接生效"都没有（architect 用自己的 persona）。**约束：拆书库回流素材为弱约束（并入调研素材供模型借鉴手法，非强约束），webapp 侧注入失败静默降级不阻塞生成；素材文件走 store 目录（产物权威源，断点续传幂等）**。新增 tests/test_p0_wiring.py 5 用例，全量 411 pytest 全绿（含 24+70 定向回归） |

| 2026-10-03 | 开篇/结局/物品/等级卡与专项 Agent（P1，质量收益最直接，用户确认后推进）：① **开篇 Agent**（`agents/opening.py` + `prompts/opening.md` + `opening_prompt`）——黄金三章此前与普通章节共用 writer 与同一套大纲；现 architect.run_outline 后调用 `OpeningAgent.apply`，为前三章专门设计强开篇（第1章立主角+冲突点燃/第2章冲突升级+主动选择/第3章小闭环+强钩子），回填前三章 core_event/goal（已有注入机制），前 3 章 core_event 非空则幂等复用；② **结局卡 + 结局 Agent**（`schemas.EndingCard` + `agents/ending.py` + `prompts/ending.md` + `ending_prompt`）——全书此前无终局设计，末几章靠 writer 自然收束；`EndingAgent` 基于主题命题/主角弧光/大纲关键节点产出终局设计卡（终局状态/主题落点/主角最终选择/人物归宿/基调），写入 world.ending_card，architect.run_world 两个分支调用（幂等），末几章写手按此收束防烂尾；③ **物品卡 + 等级体系卡**（`schemas.ItemCard/PowerLevel/PowerLevelSystem` + world_prompt/world_from_content_prompt 输出要求 + memory 强约束区注入）——物品卡静态定义（来源/代价/限制/作用）防万能工具，等级体系卡（境界阶梯/数值区间/主角当前境界/突破规则）防长篇后期战力崩坏；两卡均为**可选**（无道具/非升级流文不产出，validator 不做 fatal，防误拦） | 解决"黄金三章决定生死却无专职设计""长篇烂尾/战力崩坏无终局锚点""道具易写成万能工具"三个质量缺口。**关键设计决策**：①开篇/结局 Agent 是「设计锚点」而非「独立正文生成」——开篇回填大纲 core_event、结局写回 ending_card，正文仍由 writer 统一生成（不破坏 token 优化/场景级生成架构），通过 memory 强约束区注入约束写手；②物品/等级卡为弱校验可选卡，只因题材需要才产出/注入，避免误拦无道具/非升级流作品；③三项幂等（core_event 非空/ending_card 非空即复用），LLM 失败静默降级不阻塞生成。**约束：新卡全部 from_dict 兼容旧数据（缺字段回退空）；强约束区新增物品/等级/结局卡，注入体积小、不破坏 token 优化的分章裁剪**。新增 tests/test_p1_cards_agents.py 8 用例，全量 419 pytest 全绿 |

| 2026-10-03 | 对白/情绪曲线/章末钩子/场景落盘/术语统一卡（P2，用户确认继续）：① **对白 Agent**（`agents/dialogue.py` + `prompts/dialogue.md`）——聚合全书人物 speech_style 生成对话风格指南（口吻区分+节奏规则）写回 `world.dialogue_guideline`，architect.run_world 编排，memory 强约束区注入；确定性聚合（零 LLM、不增 token 调用数），保留角色卡供后续叠加 LLM；② **情绪曲线卡**（`schemas.EmotionBeat` + `Outline.emotion_curve`）——pacing.plan_outline 基于各章 emotion/tag 确定性聚合成跨章区间（爽点/虐点/悬念/笑点/平淡），只补缺失幂等，补足"单章 emotion 无全本配比"的缺口；③ **章末钩子**（`ChapterPlan.cliffhanger`）——大纲 prompt（open/expand/continue）chapters 新增 cliffhanger 字段，ChapterWriter 读取当前章钩子注入 memory_context，writer 章末须兑现（防流水账结尾）；④ **场景落盘**（`ProjectStore.scenes_path/save_scenes/load_scenes`）——拆场景结果+场景状态累积写入 `scenes.json`，供回看/编辑/复用（此前场景不落盘，无法回看）；⑤ **术语统一卡**（`schemas.TerminologyItem` + `World.terminology`）——专有名词（译名/招式名/地名）标准写法+禁用别名，world_prompt 输出、memory 强约束区注入，与 style_taboos（禁用词）职责区分 | 补足"写作过程无专职对白/全本情绪/章末钩子/场景回看/术语统一"五个质量缺口。**关键设计决策**：①对白 Agent 取**确定性聚合版**（零 LLM、幂等、不破坏 token 优化），专职负责对话口吻区分并通过强约束区注入，而非新增一次 LLM 调用；②情绪曲线卡为**确定性聚合**（基于既有各章 emotion 字段，零额外成本），不新增 LLM；③章末钩子走**大纲事前设计 + 写章注入**（不新增钩子 Agent，复用已有 core_event 注入机制）；④场景落盘走 **store 产物文件**（权威源、可回看），后端落盘先行，前端展示接口后续接；⑤术语卡与 style_taboos 职责分明（统一写法 vs 禁用词），分别注入。**约束：新增卡/字段全部 from_dict 兼容旧数据；全部幂等（已存在即复用）；LLM 失败静默降级不阻塞生成**。新增 tests/test_p2_dialogue_emotion_scenes.py 9 用例，全量 428 pytest 全绿 |

| 2026-10-04 | review 修正（用户指出 P2 新卡无条件塞"永不裁剪"强约束区的两个缺陷）：① **物品卡/术语卡改走 setting_cards 分章命中**（REQ-token优化 P1-1）——从强约束区移除，改为普通区按 sub_items/sub_terminology 命中裁剪注入（模式同生物/地点/势力卡），`build_setting_cards` 每章索引新增 items/terminology 字段；本章只用 1 个道具不再每章注入全部卡（未命中该项则不注入，缺索引回退全量）；② **结局卡改末 5 章才注入**——从无条件强约束区改为 `up_to_chapter >= num_chapters - 5` 才注入，与 world_reveals「未揭示设定进强约束区禁剧透」逻辑一致，写第 1 章不再预知终局；num_chapters 未知时保守始终注入 | 修正"建了能力却越界注入"：新卡不应绕过分章命中与剧透控制。**关键决策**：物品/术语卡与生物/地点/势力同属"分章相关实体卡"，归 setting_cards 命中（命中才注入、命中为空不注入、缺索引回退全量）；结局卡属"未揭示高阶设定"，末 N 章才注入（N=5，配合 up_to_chapter + num_chapters 判断）；dialogue_guideline 保留强约束区（全书一致的写作纪律、每章都需要、体积小，非分章实体卡）。更新 test_memory_injects_cards（显式 100 章验证末 5 章窗口）+ 新增 test_setting_cards_items_terminology_trimming（命中裁剪），全量 429 pytest 全绿 |

| 日期 | 坑 | 结论 |
|---|---|---|
| 2026-09-20 | docker-compose 漏写 `novel_projects:/app/projects` 命名卷（服务器容器实际已挂载），`docker compose up` 重建容器后项目目录消失 | compose 中挂载声明必须与运行期一致；改 compose 后核对全部卷 |
| 持续 | `httpx` 升级到 0.28 会导致 LLM 客户端创建失败 | `requirements.txt` 锁定 `httpx<0.28`，勿随意升 |
| 2026-09-20 | 服务器直连 GitHub 超时；服务器无 `unzip` | 发版用本地 `git archive master` 打包经 workbench 上传；服务器端用 `python3 -m zipfile -e` 解压 |
| 2026-09-17 | 扩写模式（mode=expand）重新生成大纲后分卷为空 | 扩写提示词 `expand_outline_prompt` 必须输出 `core_conflict/main_line/volumes`（每卷 no/name/intro/main_conflict/character_growth/climax/start_ch/end_ch）；`regen_outline` 先删 outline.json 再起线程（防止"已存在则复用"跳过） |
| 持续 | 前端白屏 / `Failed to load module script ... MIME type text/html` | = index.html 引用的 assets JS 与实际文件不匹配（git checkout 还原旧 index.html / 构建后未同步）。处理：`npm run build` → 核对引用一致 → Ctrl+F5 强刷。**dist 不入库，禁止 `git add -f`** |
| 持续 | 改了后端代码但行为没变 | 本地旧 `python -m webapp.server` 进程不加载新代码；必须重启进程 |
| 持续 | 服务器 `docker compose up -d --build` 白屏 | 先 `curl` 主 JS 看 size：几百 KB 正常，几百字节说明 dist 未正确打进镜像 |
| 持续 | 改 `requirements.txt` 或前端依赖/源码 | 必须 rebuild 镜像（`--build`），不能只 restart |
| 2026-09-29 | `World.foreshadowing` 升级为 `list[ForeshadowItem]` 后，直接 `World(foreshadowing=["..."])` 构造（不经 from_dict）的旧代码/测试在 `to_dict` 崩溃（str 无 to_dict） | `World.to_dict` 的列表字段必须兼容 str/dict/对象三种元素（统一 `_list_to_dicts`）；数据类内部统一为 ForeshadowItem，from_dict/to_dict 双兼容 |
| 2026-09-28 | pymysql 参数化 SQL 中 LIKE 字面量 `%` 未转义（如 `LIKE '管理员手动充值%'`），被误当作参数占位符，抛 `TypeError: not enough arguments for format string`，导致退款/质量报表接口 500（master 5f532c5 修复） | 字面量 `%` 必须写 `%%`（mogrify 时还原为单 `%`）：`projects.py` refundable_amount/create_refund_request、`traces.py` quality_report churn 查询 3 处已修；新增 test_refund.py（P0）+ quality_report 2 个 P2 回归（模拟 mogrify 验证），全量 190 全绿。**同类隐患**：`users.py` admin_stats、`base.py` BYOK 迁移的 LIKE 目前无参数执行（pymysql 不传参不格式化 `%`）安全，将来加参数前必须转义 |
| 2026-09-28 | 大纲/世界观生成 JSON 被 max_tokens=4096 截断导致 `Unterminated string` 解析失败 | 根因：`chat_json` 调用未传 max_tokens，走 client 默认 4096，但完整大纲 JSON（总纲+分卷+每章 8 字段）轻松超限，重试 3 次每次都在同一 token 上限处截断。修复：① 大纲/世界观/扩写/续写共 7 处 `chat_json` 调用显式传 `max_tokens=8192`；② `chat_json` 重试逻辑增加截断检测（`_is_json_truncation`：pos>0 且错误消息含 Unterminated/Expecting 等信号），自动 +4096 增大 token 上限（封顶 16384）并重试；③ pos==0 的自然语言开头错误不增大 token（属格式问题非截断）。新增 test_chat_json_truncation.py 15 用例，全量 205 全绿 |
| 2026-09-28 | server.py 拆 core+routers（e1c68d1）把 `REGEN_DAILY_LIMIT = 10` 常量定义弄丢、仅保留使用处 → `_check_regen_limit` 每次调用抛 NameError（位于 try 块外、无日志），单章重写 rewrite / 从第 N 章重生成 regenerate-from 两个接口稳定 500，且 regen_count.json 永不落盘 | 已在 `webapp/core.py` 恢复常量定义；新增 tests/test_regen_limit.py 5 个 P1 用例（正常计数/超限 429/损坏容错/跨天重置），全量 210 pytest 全绿 + 真实接口重写 200。**教训：路由拆分回归不能只比对 openapi 路由集合，必须覆盖受影响接口的真实调用路径** |
| 2026-09-28 | 章节生成章节间硬切、无铺垫转折（实测《万墟之上》第1章结尾在墟底石门，第2章开头直接切登记大厅）——场景级生成路径缺「承接上一章结尾」约束：split_scenes_prompt 不知道上一章结尾、scene_prompt 无承接指令（普通整章路径反而有）、记忆上下文只注入近章 goal 从不注入大纲 transition（架构师生成的转场衔接被丢弃）、_prev_tail 仅截 120 字 | 修复：① scene_prompt 首场景必须从【上一章节结尾】直接续写、后续场景承接上一场景结尾；② split_scenes_prompt 新增 prev_tail 参数并要求首场景承接；③ memory 新增 L3.5 注入本章 transition（写手必须按架构师转场续写）；④ _prev_tail 截 300 字；⑤ writer.md 新增「章首」通用承接规则。新增 test_chapter_flow.py 5 个 P1 用例，全量 215 全绿，真实接口重写第2章验证承接成功。**决策：所有生成路径（整章/场景级）都必须有明确的章首承接约束；大纲 transition 是衔接设计的唯一权威来源，必须注入写手** |
| 2026-09-28 | 章节正文偏离大纲 core_event 导致时间线错乱（实测《万墟之上》第1章：大纲设计'跟随临时队下墟→灰卒→死而复生→墟口外醒来→混入逃出人群回城'，正文却写成'进入石门阶梯往墟底深处走'；读者看到第1章像下墟中途、第2章（登记被拒）像下墟前，时间线倒置）——写手只收到抽象的 chapter_goal，核心事件 core_event（本章具体发生什么）从未注入，模型自由发挥 | 修复：write() 内部从 outline 取本章 core_event，注入全部三条写手路径（chapter_prompt/split_scenes_prompt/scene_prompt），提示词统一标注【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】。新增 3 个 P1 用例，全量 218 全绿；真实接口重写第1、2章验证时间线理顺。**决策：写手必须同时收到 goal （方向）与 core_event（事件主线）；正文偏离大纲核心事件的场景视为生成缺陷** |
| 2026-09-29 | 点「重生成世界观」后世界观/人物档案全空：`regen_world` 先 `save_world(空World)` 再起线程，但 `load_world()` 对存在的文件一律返回对象（空壳≠None），`run_world._produce` 判定"已存在则复用"直接返回空壳、不调 LLM；空 world 又无致命问题被 StageGate 静默放行 | 重生成类接口必须**删产物文件**（与 `regen_outline` 删 outline.json 同模式），不能写空壳；并在起线程前备份旧产物，失败时恢复，异常落 `logger.exception`（原 `_do` 吞异常仅存 handle.error，排查无日志）。回归：点重生成世界观→app.log 出现 LLM 调用与阶段日志→world.json 非空 |

## 三、文档漂移记录

- **2026-09-21**：修复 `README.md` 目录结构中的过期条目——`novel_gen/cli.py`、`__main__.py` 实际不存在（当前产品入口为 Web 端 `run_web.py`），已删除该行；README 中「快速开始（命令行）/ 命令一览」两节仍描述 `python -m novel_gen ...` CLI 用法，**与现状不符，待确认是否清理**。
- **2026-09-21**：`.gitignore` 现状——`docs/`（需求分析文档）与 `AGENTS.md`（注释为 "AI agent rules (local only)"）均不入库。`docs/architecture.md` / `filemap.md` / `decisions.md` 三件套默认同受 `docs/` 忽略；**若要跨机器复用 AI 知识库，需在 .gitignore 中为其加白名单（如 `!docs/architecture.md` 等），由用户拍板，勿擅自改动**。

## 四、新增决策的写法

追加时按以下模板，保持一行一条、可回溯：

```
| YYYY-MM-DD | <决策一句话> | <原因/影响，含回归验证方式> |
```

## 2026-09-30 章节质量防线·第三轮：面板事件锚点 + 摘要一致性
- 背景：21:52 版《万墟之上》第1章（3574字）质检 6 大改中，"机制数字未结算推进"实为误报——"1/3"三次显示（第二次死亡结算/通道确认/章末备注栏变化）数字本就不该变，属冗余显示而非结算错误；另发现摘要"3/3→2/3"与正文最终"1/3"不符（摘要未随正文更新）。
- 决策：
  1. _panel_progress_issues 加结算事件锚点（死了/死亡/复活/苏醒/睁眼/醒来/结算/重置）：两次同值间有强结算词 → 真未推进（大改）；无 → 降级"面板数字重复显示（冗余）"中改。锚点排除"死一次"（预判）、"咬穿"（回忆复述）、"刷新"（"面板刷新的时候"设定陈述）——实测误命中。
  2. 新增 _summary_consistency_issues：正文最终面板分数 vs 摘要分数，不一致 → 大改（摘要未随正文更新）。
  3. 事前 prompt（scene_prompt 19-22）：面板显示纪律（数字只在结算瞬间出现一次）、状态度量单一口径（蚀痕累积口径禁多处重新计量）、角色身份全局锁定（同一角色不得两套描述）、摘要覆盖最终结算状态。
  4. 重写 prompt（_rewrite_whole）同步注入面板显示纪律 + 摘要必须写最终面板数字。
- 验证：392 全绿；真实 3574 字正文回验：1/3 冗余→中改（误报消除）、摘要 2/3 vs 1/3→大改。

## 2026-09-30 章节质量防线·第四轮：锚点语境排除 + 面板行移出复读统计
- 背景：22:32 版第1章（4471字）质检 2 条大改"机制数字未结算推进"经上下文核查全部误报——6 处锚点全是回忆/指认/他人语境（"她看见他死了""你死了""她快死了""他应该已经死了""感知到了他死亡的情绪印记"），面板 2/3 未变是正确行为；另有"同句复读"大改实为面板行"2/3"×3（面板行已由专检归类，复读再判大改 = 双重大改误触发重写）。
- 决策：
  1. _panel_progress_issues 锚点加语境排除（_PANEL_EVENT_RECALL，前 16 字含 看见/说/读/感知/应该/快死 等回忆转述词 → 跳过），特判"快死了"（前 1 字"快"）——回忆不算结算事件。
  2. _repeat_sentence_issues 不再统计面板行（_is_panel_line 直接 continue）——面板行重复唯一归属 _panel_progress_issues（跨结算大改/冗余中改），杜绝双重大改。
- 验证：393 全绿；真实 4471 字正文回验：面板→冗余中改（误报消除）、复读→空、字数→中改（4471 字 1.79 倍，真问题）、对话轮/结尾→无。

## 2026-09-30 章节质量防线·第五轮：跨场景事件重复 + 字数收紧（事前+事后）
- 背景：22:32 版第1章（4471字）真问题——①"救苏妄言"事件写了空地/坡地两个版本（场景间"已推进状态"只含数字/生死/物品，不含事件，场景3不知道事件已发生）；②字数 4471=1.79 倍仅中改不阻断；③蚀痕"一寸"与"三指"单位混用。
- 决策：
  1. 事前·事件清单注入：stages.py 场景循环按拆场景定义的 event 累积【本章已发生事件】清单（确定性、零 LLM），拼入 scene_state_hint 注入后续场景；scene_prompt 新增 23 条"已发生事件禁止重复"（同人物+同冲突+同结果=重复，换地点重写也算）。
  2. 事前·蚀痕单位锁定：scene_prompt 20 条细化"增长量只用'寸'，禁止'三指/一指宽'"。
  3. 事后·矛盾自检加"同一事件两版本"：_consistency_llm_check prompt 增加第⑤类检查（事件重复），type 枚举含 事件重复。
  4. 事后·字数审计收紧：>1.6 倍 → 大改（触发重写压缩，4471=1.79 触发）、>1.35 倍 → 中改。
- 验证：395 全绿（新增 场景事件注入/矛盾自检事件重复/字数阈值 用例）；真实 4471 字回验：字数→大改。

## 2026-09-30 章节质量防线·第六轮：字数审计方向修正（超长≠质量失败）
- 用户确认：超长还触发整章重写浪费 token；正确做法是看生成质量，而非完全看生成字数。
- 决策：_length_audit_issues 不再产出大改——>1.35 倍目标仅中改记录（不阻断、不触发重写）；质量硬伤（截断/循环/拼接/矛盾/数字错误/摘要不符）仍由各自维度判大改触发重写，重写 prompt 已含字数区间约束会自然压缩。
- 验证：395 全绿；真实 4471 字回验：中改（不再大改）；6202 字版因含循环/截断/拼接大改仍会重写（合理——质量问题触发，非字数触发）。

## 2026-09-30 章节质量防线·第七轮：拆场景设计重构（事件清单先行，治本而非打补丁）
- 用户指出：一直在打补丁（事件清单注入/同句复读/段落重复检测），根因在场景拆分设计——模型把同一事件拆成多个场景（6316 字版"苏妄言被围"5 版、"灰卒咬喉死"2 版），旧 _scene_events_overlap 只做文本相似度、措辞一变全放行，且不校验场景数。
- 决策：
  1. split_scenes_prompt 重构：事件清单先行（events，1 事件 = 1 场景，事件互斥+因果推进），场景 scene_event_ref 逐字引用事件编号（机制上杜绝换措辞把同一事件塞进两场景）；场景数 = 事件数（2-4，由总字数反推）。
  2. 结构化校验 _validate_scene_plan（零 LLM）：场景数 ≤4（>4=凑数拆重复场景→回退整章）、多场景 target_words 和 ∈±10%、事件引用唯一性（同编号被 ≥2 场景引用=重叠）；旧格式降级文本相似度。
  3. 场景衔接强约束：scene_prompt 加【本场景唯一事件】+ 后续场景必须从上一场景"结局之后"续写（禁止重演上一场景情节，连起手式都禁）；force_new_progression 重试块。
  4. 生成端兜底（拼接发生点拦截）：相邻场景全文相似度 >0.55 → 重试本场景 1 次（强制从结局后续写），不整章重写。
- 验证：398 全绿（新增 场景数超限/事件引用唯一/引用展开/相邻场景重试 用例）；旧校验函数保留兼容别名。

## 2026-10-01 章节质量防线·第八轮：事件语义互斥 + 局部复刻拦截 + 重写事件纪律
- 背景：4500 字版质检 4 条大改全命中但重写 3 轮修不掉——①"救苏妄言"石殿版/墟口外版（事件 desc 措辞不同，文本相似度 <0.55 放行）；②死亡复刻段约 200 字嵌进场景 2（相邻场景全文相似度 <0.55 漏检）；③蚀痕状态两次相同无增量。
- 决策：
  1. 事件语义槽位互斥（零 LLM）：_scene_events_semantically_overlap——事件输出结构化 conflict+action，归一化动作词表（救/围/杀/逃/追/挡…）；两事件（冲突对象, 动作）相同 = 重复事件，拆场景阶段回退整章。desc 兜底提取冲突对象/动作。
  2. 相邻场景长段复刻率：_long_sentence_overlap_ratio——当前场景中与上一场景完全相同的 ≥15 字句子占比 >20% → 重试本场景（全文相似度对局部复刻漏检）；重试仍复用全文+长段双指标择优。
  3. 事件状态增量规划：拆场景 events 新增 final_state（事件结束时的面板/蚀痕/伤势增量），scene_prompt 注入【本场景结束时的机制状态】硬约束（禁止与上一场景结束状态相同）。
  4. 重写事件纪律：_rewrite_whole 在质检含事件重复/两版本时注入 8-10 条（删除重复版本保留最完整者、事件间因果推进禁止重演起手式、每事件结束状态必须有增量）——补"检测到位但修复无锚点"缺口。
- 验证：401 全绿；真实回验：救人二版语义互斥=True（拆场景拦）、死亡复刻长段重叠率=0.82（场景生成重试）。

## 2026-10-01 章节质量防线·第九轮：拆场景 JSON 解析健壮化 + 回退整章事件注入
- 背景：实机重生成暴露真正根因——拆场景 LLM 输出三块（events+scenes+characters_action）较长，多次在结尾处 JSON 语法断裂（'Expecting value: line 46 col 17'），json.loads 失败直接 scenes=[] 回退整章 → 事件清单（含救苏妄言核心事件）整条丢失/事前防线（语义互斥/长段复刻/final_state）全部没机会执行：①6649 字版整章自我重复 3 遍救人+死亡复刻（质检 6 大改全命中）；②4874 字版拆场景失败回退整章、救人事件整体丢失、score=45 仍 3 问题。
- 决策：
  1. _strip_json_block 健壮化：剥代码块 → 逐级回溯最近可解析 } 前缀 → 修尾逗号（,\s*[}\]] → 闭合符）。
  2. 字段级兜底 _extract_json_field：整体解析失败时用正则分别救回 events/scenes/characters_action 数组（events 在前，截断通常只丢尾部，events 常可救回）。
  3. 拆场景失败重试 1 次（温度 0.4→0.5，prompt 追加"只输出合法 JSON，禁代码块/注释/省略号/尾逗号"），仍失败才回退整章。
  4. 回退整章注入事件序列：chapter_prompt 新增 chapter_events 参数，拆场景失败但 events 可救回时把事件清单（含 final_state）拼入整章 prompt，并附"每事件只写一遍、按编号顺序推进、禁重演"纪律；整章 prompt 无条件新增 3.5 条通用防自我重复纪律。
- 验证：404 全绿（新增 _strip_json_block 健壮/字段级截断救回/回退事件注入/拆场景失败重试用例）。

## 2026-10-01 章节质量防线·第十轮：场景正文事件级复写检测（治本，非打补丁）
- 背景：第三版 3395 字（score=0，4 大改）——救人事件坡顶版/墟口外版两处完整重演（质检 LLM 抓"事件重复"），但语义互斥只校验拆场景 events 清单：两个场景各自引用唯一事件、conflict 措辞'三条赤尉'vs'巨型赤尉'不同→文本相似度与事件清单双漏检。根因=场景生成时模型无视【本场景唯一事件】软约束，把已发生事件换措辞又写一遍。
- 决策：
  1. _scene_text_rewrites_event（零 LLM）：对每个刚生成的场景正文提取事件槽位（冲突对象 _event_conflict 优先敌人词 赤尉/灰卒/蚀兽，其次人物），正文含救援专属动词（救/引开/砸/挡/扑/迎）+ 冲突对象与任一已完成事件同现 → 判复写（"冲/拽/拖"太通用会误判撤离场景，不放宽）。
  2. 场景循环接入：场景 N 生成后与全部已完成事件比对（不只相邻场景），命中 → 重试本场景（scene_prompt 新增 forbid_event 参数：明确"上版把已发生事件【原文】重演了，禁止以任何形式再次发生，须从该事件结局之后续写"）；重试版二次检测通过才采用。
- 验证：406 全绿；真实回验：第三版场景3(墟口救人)复写场景2(坡顶救人) 检测=True（生成端即拦），场景1(灰卒死亡) 不误报。
