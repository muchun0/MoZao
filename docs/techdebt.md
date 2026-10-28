# 技术债分析报告（docs/techdebt.md）

> 生成日期：2026-10-28。本文档是墨造项目的**技术债清单与证据台账**，供后续按 `AGENTS.md §11` 红线逐项偿还时引用。识别方法：实际扫描 `novel_gen/`（72 个 .py）与 `webapp/`（48 个 .py）全量源码，统计文件行数、函数体行数、print 调用、同名 def、硬编码数据；债的定义与红线以 `AGENTS.md §11`、`docs/decisions.md` 为准。
>
> **覆盖范围声明**：本报告聚焦结构/可维护性债（文件规模、重复逻辑、代码数据不分离、命名、死代码、观测性）。**安全漏洞与依赖漏洞未在本报告范围内**，需另行专项扫描（`pip-audit` / `npm audit` / OWASP 审查）。

---

## 0. 结论速览（Top 债）

1. **4 个超红线文件仍在持续膨胀，而非偿还**——`decisions.md` 早于 2026-10 已把 `stages.py`/`templates.py`/`quality.py`/`core.py` 列为"待办重构"，但至今 `stages.py` 2228→**3157**（+929）、`quality.py` 1306→**1796**（+490）、`core.py` 1207→**1377**、`templates.py` 1784→**1968**。**红线形同虚设，债在滚雪球。**
2. **核心业务代码违规使用 `print` 共 91 处**（stages 40 + manager 46 + quality 5），未走统一 logger，生产环境无法按 trace_id/场景检索，属观测性债且违反 `§11.6`。
3. **同一逻辑多份实现**（LCS 三份、JSON 健壮解析四份、鉴权两份），违反 `§11.2`"收敛单一权威源"，正是历史"排除词表分家/两套正则不同步"同款坑的复发。
4. **提示词文本仍硬编码在 `templates.py`**（1968 行主体即提示词字符串），违反 `§11.3`"提示词放 agents/prompts/*.md"。
5. **超长函数 8 个**，最严重 `memory.py:_fs` 259 行（红线 50 行的 5 倍）。

---

## 1. 文件规模债（红线对照）

`AGENTS.md §11.1`：单文件 150–500 行为宜；**>500 行须主动找拆分点；>1000 行禁止继续新增，必须重构后再加**。

### 1.1 novel_gen/（72 个文件）

| 行数 | 文件 | 红线状态 | 膨胀记录 |
|---|---|---|---|
| **3157** | `pipeline/stages.py` | 🔴 **>1000 禁新增** | decisions 记录 2228 → 现 3157（+929） |
| **1968** | `pipeline/templates.py` | 🔴 **>1000 禁新增** | 1784 → 1968（+184） |
| **1796** | `pipeline/quality.py` | 🔴 **>1000 禁新增** | 1306 → 1796（+490） |
| **1088** | `pipeline/manager.py` | 🔴 **>1000 禁新增** | — |
| **1067** | `store/project.py` | 🔴 **>1000 禁新增** | — |
| **1037** | `pipeline/book_memory.py` | 🔴 **>1000 禁新增** | — |
| 989 | `store/schemas.py` | 🟠 濒临（>500 须拆） | — |
| 733 | `pipeline/chapter_state.py` | 🟠 >500 须拆 | — |
| 583 | `pipeline/memory.py` | 🟠 >500 须拆 | — |
| 519 | `config.py` | 🟠 >500 须拆 | — |

### 1.2 webapp/（48 个文件）

| 行数 | 文件 | 红线状态 |
|---|---|---|
| **1377** | `core.py` | 🔴 **>1000 禁新增**（拆分史 2853 拆分后，又回涨到 1377） |
| 907 | `generate_service.py` | 🟠 接近 1000，单文件承载全部 Agent 触发业务 |
| 500 | `db/base.py` | 🟠 临界（DDL + seed 全在这） |
| 490 | `settle_ledger.py` | 🟠 >500 边缘（**P0 资金链路，优先关注**） |
| 486 | `routers/projects_chapters.py` | 🟠 |
| 470 | `routers/projects_base.py` | 🟠 |
| 412 | `user_memory.py` | 🟠 |
| 388 | `db/users.py` | 🟠 |
| 365 | `assistant/context.py` | 🟠 |
| 354 | `routers/admin.py` | 🟠 |

> 注：`schemas.py` 989 行承载 30+ 数据模型，每个模型手写 `to_dict`/`from_dict`（各 32 处）——大量可消除的样板代码（可用 pydantic/dataclass 序列化取代）。

---

## 2. 代码与数据不分离（`§11.3` 违背）

| 债 | 证据 | 影响 |
|---|---|---|
| **提示词硬编码** | `templates.py` 1968 行主体为中文提示词字符串（如 L295"人物立体化写作约束…四技法"）。`§11.3` 要求提示词放 `agents/prompts/*.md` | 改角色行为必须翻代码；模板与 .md 双写风险 |
| **正则/词表/阈值硬编码** | `stages.py` 正则 `re.*(` 13 处、`safety.py` 8 处；排除词表/方向规则散落各 py | 正是历史"排除词表分家""两套正则不同步"坑的温床 |
| **知识双写（长 docstring）** | 47 处中文长串，其中多个为 200–300 字文件头 docstring，把 design 决策/踩坑史复制进代码（如 `reader_ledger.py` 261字、`settle_ledger.py` 242字、`versioned.py` 240字） | 架构文档 + 代码注释双维护，极易失同步 |

---

## 3. 重复逻辑 / 未收敛单一权威源（`§11.2` 违背）

| 逻辑 | 重复位置 | 说明 |
|---|---|---|
| `_lcs_len`（最长公共子序列） | `beats.py:76`、`quality.py:1632`、`stages.py:3021` **三份** | beats.py 是权威源，另两处各自实现 |
| `_parse_json_obj` / `_parse_json` | `manager.py:36`、`stages.py:38`、`quality.py:1605`、`reviser.py:104`、`reviser.py:184`（后者同文件两份） | `§11.7` 要求健壮 JSON 解析统一封装，现散落四份 |
| 鉴权 `_require_user` | `core.py:432`、`admin_cli.py:28` | 两处实现 |
| `_read_json` | `store/project.py:1061`、`user_memory.py:57` | 重复 |
| `_normalize` | `stages.py:234`、`outline_parser.py:119` | 重复 |
| `_inspector_block` | `stages.py:652`、`assistant/context.py:242` | 跨侧重复 |

> 这是 `decisions.md` 反复记录的结构性坑（"跨模块可变状态散落、同一份规则两个文件各写一份"）的现实复发，是**本次分析中仅次于文件膨胀的结构性债务**。

---

## 4. 超长函数（`§11.1`：单函数 >50 行须拆）

| 函数 | 位置 | 行数 | 备注 |
|---|---|---|---|
| `_fs`（外层 `build_context` 已拆） | `memory.py:45` | 408（原 539） | ✅ 8 个独立层级已抽 helper（§11.1 先拆函数，2026-10-28）；L1 设定库含嵌套 `_fs` 仍 300 行待下轮 |
| `init_db` | `db/base.py:392` | 92 | DDL + seed + 迁移全在这 |
| `build_context` | `memory.py:45` | 75 | |
| `build_setting_cards` | `setting_cards.py:47` | 60 | |
| `ledger_check` | `chapter_state.py:667` | 59 | 机制数字账本（核心一致性） |
| `_exec_chapters` | `generate_service.py:847` | 57 | |
| `send_message` | `assistant/service.py:124` | 53 | |
| `_beat_cycle_issues` | `beats.py:185` | 53 | |

共 8 个超长函数；`_fs` 最危险（核心路径 + 259 行难以精读/定位）。

---

## 5. 观测性债：业务代码 `print`（`§11.6` 违背）

`§11.6`：业务代码禁止 `print`，统一 logger（结构化、可诊断、trace_id），仅命令行工具例外。

- `stages.py`：**40 处** print（自校验/回退整章/拆场景/行动表补全等**核心生成路径**）
- `manager.py`：**46 处** print（大纲完成/自检/编辑审稿/全书终审/导出等）
- `quality.py`：**5 处** print（自校验路径）
- 另有 `admin_cli.py` 30 处（命令行工具，属 §11.6 例外）、`config.py` 4、`reviser.py` 4、`research.py` 6。

**影响**：核心链路输出走 stdout，生产 docker 环境无法按 trace_id/场景/项目检索，与 `agent_trace` 全链路快照体系割裂；排障只能看裸日志。**建议统一替换为 `webapp/logger.py` / novel_gen 统一 logger。**

---

## 6. 命名 / 可读性债（`§11.4` 违背）

- **单字母/晦涩命名**：`_fs`（259 行函数）、`_sys`（5 处）、`_s`（2 处）、`_qc`、`_why`、`_ok` 等。违反"命名必须表意，禁止无意义缩写/含义不明的单字母"。
- **超长行**：`templates.py` 9 行、`config.py` 3 行 >200 字符（模板字符串拼接），另有大量 >120 行宽。违反行宽 ≤120。

---

## 7. 死代码 / 注释掉的逻辑（`§11.5` 自查项）

- 连续 ≥10 行注释块（含代码特征）共 5+ 处：`stages.py`、`quality.py`、`chapter_state.py`、`config.py`。
- 典型：`quality.py:421` 整段"ch4 蚀化 106…P0-14 改为链式"方案注释；`stages.py:1100` "方案4（落盘分流）"注释；`chapter_state.py:448` "做通用机制 # ①②③"整段方案。
- 影响：增加认知负担、误导后续维护者；建议按 `§11.5` 清理或收敛进 `decisions.md`。

---

## 8. 结构 / 分层债

- **`core.py` 1377 行回涨**：拆分史 2853 → 拆分后，现又逼近 1400。鉴权、结算、生成线程、任务持久化、记忆注入全部堆回 core。
- **`generate_service.py` 907 行单文件**：阶段 1B/1C 新增的 Agent 统一入口业务全塞一个文件，含守卫群/状态机/执行分发/进度更新多内聚概念。
- **疑似分层越界（需复核）**：`apply_outline_candidate` 同时出现在 `store/project.py:346`（数据层）与 `routers/projects_generation.py:94`（router 层）——router 是否绕过 service 直调 store，需结合 `§11.2` 三层约束复核。
- **`config.py` 519 行 + FEATURE_FLAGS 100 项**：开关治理是正确方向，但 100 个开关本身是复杂度信号——大量"攻防迭代临时开关"，靠开关灰度而非设计收敛，长期是认知债。

---

## 9. 流程债（元问题，优先级最高）

`decisions.md`（2026-10）已明确把 `stages.py`/`templates.py`/`quality.py`/`core.py` 列为"**待办（已识别债，非本次范围）**"并给出行为零变化拆分标准（server/db 拆分史成功标准），但此后：

- `stages.py` +929、`quality.py` +490、`core.py` +170、`templates.py` +184；
- 期间 `manager.py`、`store/project.py`、`book_memory.py`、`generate_service.py` 陆续越过 1000/900 线。

**根因**：`§11.1`"超 1000 行禁止继续新增"作为**评审准出闸**（`§11.5`）未被真正执行——新功能仍往超红线大文件里堆。**单独立会按行为零变化标准偿还大文件债，是偿还全部债务的前提**；否则本报告其余债会继续叠加。

---

## 10. 处置建议（按「影响面 × 修复成本」排序，参照 `§10`）

| 优先级 | 债项 | 建议 | 成本 |
|---|---|---|---|
| **P0** | 大文件重构流程失效 | 单独立会，按 §11.1 行为零变化标准拆 `stages.py`/`quality.py`/`core.py`/`manager.py`；拆分前**冻结向这些文件新增** | 高（跨模块，需全量 pytest + openapi 一致 + 端到端冒烟） |
| **P0** | 核心链路 print → logger | 将 stages/manager/quality 的 91 处 print 替换为结构化 logger（带 trace_id/场景） | 低（机械替换，低风险） |
| **P1** | 重复逻辑收敛 | 抽 `_lcs_len`/`_parse_json_obj`/`_read_json`/鉴权 到单一权威模块，其余改为 import | 低–中（行为零变化） |
| **P1** | 提示词外置 | `templates.py` 提示词迁往 `agents/prompts/*.md` / 独立模板文件 | 中（涉及大量回归） |
| **P2** | 超长函数拆分 | 拆分 8 个超长函数（helper 留原文件） | 低–中 |
| **P2** | 死代码/长 docstring/超长行 | 清理注释块、收敛 docstring 到 decisions、行宽 ≤120 | 低 |
| **P2** | 命名表意化 | `_fs`/`_sys`/`_s`/`_qc` 等改名 | 低 |
| **P2** | schemas 样板去重 | 引入 pydantic/dataclass 序列化，消除 32 份手写 to_dict/from_dict | 中（需先选型记 decisions） |

---

## 11. 后续专项（本报告未覆盖，需另行扫描）

- 安全与依赖漏洞：`pip-audit` / `npm audit` / OWASP（SQL 注入、XSS、CSRF、文件上传、密钥泄漏）。
- 性能债：慢查询与索引、LLM 重复调用、串行等待、热点缓存。
- 前端债：`ProjectDetail.vue`/`Admin.vue`/`ToolsPanel.vue` 单组件规模与逻辑堆积（本次未扫 src/）。
