# AI 自动生成小说（novel_gen）

输入题材/创意，自动产出完整小说 txt。生成前先**调研热门小说共性**（情节/文风/节奏），再按「调研 → 大纲 → 人物世界观 → 逐章正文 → 导出」流水线推进，全程可断点续传。

## 架构

```
config.yaml ──► 配置层(config.py)         多 Provider / 题材 / 字数
                    │
                    ▼
┌────────────── 生成流水线(pipeline/manager.py) ──────────────┐
│  ①调研(research/)  ──► ②大纲 ──► ③人物/世界观 ──► ④逐章正文 ──► ⑤导出txt │
│    内置知识库+可选联网      │            │            │              │
│    → LLM提炼共性报告 ───────┴──────► 注入所有下游阶段 ◄──────────────┘
│                        （记忆系统 memory.py：设定库+最近N章摘要回填）    │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
         projects/<项目目录>/   ← JSON 产物 + 状态机，断点续传
```

**核心模块**

| 模块 | 职责 |
|---|---|
| `config.py` | 配置加载（YAML + 环境变量补 Key）、题材/字数/Provider 校验 |
| `llm/` | Provider 抽象层：DeepSeek / 豆包火山方舟 / OpenAI / mock（无 Key 测试） |
| `research/` | 调研阶段：内置知识库 + 可选联网搜索（博查/Serper）→ LLM 提炼共性报告 |
| `pipeline/` | 生成流水线：大纲/人物/逐章生成器 + 记忆系统 + 流程编排 |
| `store/` | 项目落盘、状态机、断点续传、txt 导出 |

**关键设计**
- **调研先行**：每本书生成前先对齐该题材的爆款共性（情节套路/文风混合/节奏/开篇策略），并注入到后续所有阶段，避免"闭门造车"写出过时套路。
- **长文一致性**：设定库（世界观/人物卡/伏笔，全书记忆）+ 最近 N 章摘要回填，杜绝前后矛盾。
- **断点续传**：以产物文件为唯一权威来源，中断后 `resume` 只补缺失部分，不重复生成、不浪费 token。
- **章节衔接**：每章注入上一章结尾片段，正文连续不跳戏；章节末尾模型自动产出摘要回写记忆库。

## Web 界面（推荐，无需命令行）

为不懂命令行的人提供了可视化前端，前后端分离：

- 后端：FastAPI（`webapp/server.py`），封装全部流水线为 REST API
- 前端：独立静态页（`webapp/webui/`），原生 HTML/CSS/JS，无构建依赖
- 通信：REST API（同源部署；如需跨域部署，后端已开 CORS）

### 启动

**Windows**：双击 `启动小说生成器.bat`

**任意平台**：
```bash
python run_web.py            # 默认 http://127.0.0.1:8000
python run_web.py 8080       # 指定端口
python run_web.py --host 0.0.0.0 --port 8000   # 允许局域网访问（给别人用）
```

浏览器打开 `http://127.0.0.1:8000` 即可使用。

### 多用户（SaaS）模式

本 Web 端是多用户版，支持注册/登录、余额计费、项目隔离：

- **首个注册用户自动成为管理员（站长）**，拥有全部历史项目与所有用户项目可见权
- **API Key 只放服务端**（`.env` 文件或系统环境变量），用户不需要也不可见 Key
- **按单本收费**：创建项目时按字数档位从余额扣费，余额不足会被拒绝

**API Key 配置**（在项目根目录创建 `.env`）：
```
DEEPSEEK_API_KEY=sk-你的Key
BOCHA_API_KEY=sk-你的Key      # 可选，联网调研用
```

**人工收款充值**（用户付款后站长手动加余额）：
```bash
python -m webapp.admin_cli users                       # 查看所有用户
python -m webapp.admin_cli balance <用户名>             # 查余额
python -m webapp.admin_cli recharge <用户名> <金额>      # 充值
python -m webapp.admin_cli recharges [用户名]            # 查充值记录
```

**定价**（`webapp/pricing.py` 可改）：短篇 ≤2.5 万 9.9 元 / 中篇 ≤10 万 19.9 元 / 长篇 ≤30 万 39.9 元 / 超长篇 69.9 元。注册即送 10 元体验金（可关闭）。

### 功能

- **注册 / 登录**：token 鉴权，数据存 SQLite（`webapp/novel_web.db`）
- **我的项目**：只看自己的项目，管理员可见全部
- **新建小说**：表单填写书名、题材、字数、背景、主角、配角、剧情走向、避雷等，创建即按档位扣费
- **项目详情**：实时进度条、开始/停止生成、下载 txt
- **章节阅读**：在线预览正文，支持上一章/下一章翻页
- **大纲 / 人物 / 世界观**：标签页查看（生成后可见）
- **充值**：弹层展示收款流程（扫码转账 → 备注用户名 → 站长确认到账）

> 字数由用户完全决定，仅按档位收费（短篇 9.9 / 中篇 19.9 / 长篇 39.9 / 超长篇 69.9 元）。模型提供商默认 DeepSeek（Key 在服务端），也可后台改 mock 测试。

## 安装

```bash
pip install -r requirements.txt
```

## 快速开始

**第一步：无 Key 冒烟跑通（mock 本地假模型）**

```bash
# 拷贝示例配置并改用 mock
cp examples/config.yaml config.yaml
# 编辑 config.yaml，把 llm.provider 改为 mock
python -m novel_gen all --config config.yaml
```

会生成 `projects/<时间戳_书名>/` 下的调研报告、大纲、6 章正文和 txt 成品。验证架构没问题后，再换真实模型。

**第二步：接入真实模型（DeepSeek 为例）**

```yaml
llm:
  provider: deepseek      # 或 doubao / openai
  api_key: ""             # 填 Key，或留空读取环境变量 DEEPSEEK_API_KEY
```

```bash
python -m novel_gen all --config config.yaml
```

## 命令一览

```bash
python -m novel_gen init      --config config.yaml        # 初始化项目
python -m novel_gen research  --config config.yaml        # 只跑「热门小说共性调研」
python -m novel_gen plan      --config config.yaml        # 生成大纲（自动含调研）
python -m novel_gen world     --config config.yaml        # 生成人物/世界观
python -m novel_gen write     --config config.yaml --from 1 --to 10   # 写指定章节
python -m novel_gen resume    --config config.yaml        # 从断点续跑
python -m novel_gen all       --config config.yaml        # 一键全流程+导出
python -m novel_gen export    --project <项目目录>         # 导出 txt
python -m novel_gen status    --project <项目目录>         # 查看进度
```

- 不填 `--project` 时默认**复用最近创建的项目**继续操作；开新书用 `--new`。
- 配置 `confirm: true` 后，`all` 会在调研/大纲/人物阶段之间暂停等你确认方向。

## 配置项（config.yaml）

| 配置 | 说明 | 默认 |
|---|---|---|
| `title` | 书名 | 未命名小说 |
| `genre` | 题材：玄幻/仙侠/都市/科幻/历史/悬疑/言情/游戏/奇幻…（可自定义） | 玄幻 |
| `total_words` | 单本目标总字数（由用户决定，按档位收费） | 100000 |
| `chapter_words` | 单章目标字数（章节数自动换算） | 2500 |
| `style` / `premise` | 文风要求 / 核心创意（可留空） | 空 |
| `background` | 故事背景/世界观（**强约束**，必须遵循） | 空 |
| `hero` | 主角设定：姓名/身份/性格/目标（**强约束**） | 空 |
| `characters` | 配角/反派预设列表（**强约束**，可留空让 AI 补） | 空 |
| `plot_direction` | 剧情走向/关键情节（**参考方向**，AI 可细化） | 空 |
| `forbidden` | 避雷/不想出现的元素列表（**强约束**） | 空 |
| `confirm` | 阶段间是否暂停人工确认 | false |
| `memory_window` | 生成当前章时回填最近 N 章摘要 | 5 |
| `llm.provider` | deepseek / doubao / openai / mock | deepseek |
| `llm.model` / `base_url` | 留空取该 Provider 默认 | 自动 |
| `search.*` | 调研阶段联网搜索（可选，见下） | 关闭 |

## 用户自定义设定（强约束 vs 参考方向）

在 `config.yaml` 中填写以下字段，AI 会按约束强度区别对待：

```yaml
background: "架空民国上海滩，1935年，租界与华界并立，百乐门是各方势力交汇点"
hero: "顾沉，32岁，前巡捕房探长，现为私家侦探，沉默寡言，随身带旧怀表"
characters:
  - "林曼：百乐门歌女，真实身份是地下党联络员"
  - "赵署长：巡捕房署长，贪腐，与黑帮勾结"
plot_direction: "码头命案牵出旧搭档假死真相，最终在百乐门设局揭露赵署长"
forbidden: ["穿越", "系统", "超自然力量", "狗血三角恋"]
```

| 字段 | 约束强度 | 注入阶段 | 说明 |
|---|---|---|---|
| `background` | **强约束** | 大纲/人物/每章正文 | 世界观必须符合，不得偏离 |
| `hero` | **强约束** | 大纲/人物/每章正文 | 主角姓名/身份/性格必须遵循 |
| `characters` | **强约束** | 大纲/人物 | 预设人物必须出现并符合设定，AI 可补充其他角色 |
| `forbidden` | **强约束** | 大纲/人物/每章正文 | 避雷元素绝对不得出现 |
| `plot_direction` | 参考方向 | 大纲 | AI 在此方向上细化发挥，不强制逐字遵循 |

强约束会在每章正文提示词中以「强约束提醒」形式重复注入，防止长篇写作中跑偏。

## 质量增强（编辑质检 + 去AI味 + 防同质化）

程序集成了网文编辑视角、去AI味、防同质化三个质量模块，默认全部开启：

```yaml
editor_review: true       # 大纲生成后执行编辑质检，不通过则自动修正一次
de_ai: true               # 正文生成时注入去AI味写作约束
anti_similarity: true     # 防同质化：常驻System提示词，拒绝套路
```

**编辑质检**（`editor_review`）：大纲生成后，模拟资深网文编辑从 9 个维度审稿——题材标签、一句话卖点、开篇钩子、主角目标/困境、情绪回报节奏、人物关系、前三章闭环、连载风险、防同质化。评分低于 7 分或有致命问题时，自动基于编辑建议修正大纲。审阅结果保存在项目目录 `editor_review.json`。

**去AI味**（`de_ai`）：每章正文提示词注入 8 条写作约束——避免连接词堆砌、避免强行升华和结尾金句、对白有潜台词、叙事有详略、人物有犹豫和矛盾、用具体动作代替抽象情绪、保留留白、句长有变化。实测可将 AI 腔连接词密度降到每千字 0.4 次以下。

**防同质化**（`anti_similarity`）：常驻 System 提示词注入大纲/人物/正文三阶段，核心规则：
1. 拒绝概率最高套路，剧情分叉优先选小众、次概率、反模板路径
2. 人物永远带内在矛盾（执念+软肋+阴暗面+行为悖论），由性格矛盾驱动行为
3. 剧情必须有代价与沉没成本，禁止无代价胜利、巧合救场、主角光环
4. 只展示不叙述，情绪通过动作/微表情/生理反应/细节侧面体现
5. 全网套路黑名单：打脸流、无脑爽、俗套桥段、畸形人设、机械升级、烂俗结尾、强行巧合
6. 允许失败、遗憾、不完美结局，拒绝流水线爽文闭环

关闭任一功能只需在 config 中设为 `false`。

## 联网搜索调研（可选，增强"参考热门小说"的真实性）

默认调研数据源 = 内置热门小说共性知识库 + 模型先验知识（零配置可用）。若想真实抓取网上热门小说信息，配置一个搜索 API Key：

```yaml
search:
  provider: bocha          # bocha(博查) / serper(Google)
  api_key: "你的key"        # 或留空读环境变量 BOCHA_API_KEY / SERPER_API_KEY
  count: 5                 # 每个关键词返回条数
```

配置后，调研阶段会自动搜索「题材+热门/套路/2026趋势」等关键词，把结果连同内置知识库一起交给 LLM 提炼共性。搜索服务失败或未配置时自动降级，不影响主流程。

## 字数与收费

**字数完全由用户决定**，不设下限，仅按字数档位在创建项目时从余额扣费：

| 档位 | 字数范围 | 现价 | 原价 |
|---|---|---|---|
| 极短篇 | ≤ 5000 字 | 0.99 元 | 2.99 元 |
| 短篇 | ≤ 2.5 万字 | 9.9 元 | - |
| 中篇 | ≤ 10 万字 | 19.9 元 | - |
| 长篇 | ≤ 30 万字 | 39.9 元 | - |
| 超长篇 | 30 万字以上 | 69.9 元 | - |

- 定价在 `webapp/pricing.py` 中可改，改后重启服务生效
- 档位价格以 Web 端 `webapp/pricing.py` 为准（CLI 不收费，仅本地生成）

## 目录结构

```
novel_gen/
├── config.py            # 配置层
├── cli.py / __main__.py # 命令行入口
├── llm/                 # Provider 抽象（base/factory/providers）
├── research/            # 调研阶段（schemas/sources/research + 内置知识库）
├── pipeline/            # 生成流水线（templates/stages/memory/manager）
└── store/               # 项目存储（schemas/project）
tests/                   # 18 个测试（config/store/memory/端到端 mock）
examples/config.yaml     # 示例配置
projects/<项目>/         # 中间产物：大纲/人物/章节json/摘要（断点续跑用）
output/                  # 最终成品：导出的 txt 小说（用户取用）
```

- `projects/`：每本书一个子目录，保存调研/大纲/人物/章节等中间产物，支持 `resume` 断点续跑
- `output/`：最终成品 txt 统一输出到这里，每本书一个文件；项目目录下也保留一份副本

## 测试

```bash
python -m pytest tests/ -v
```

用 `mock` provider 离线跑通全部流程，无需任何 API Key。覆盖：配置加载、字数换算、项目落盘往返、记忆上下文、断点续传幂等、正文摘要剥离、txt 导出。

## 说明与边界

- API Key 不落盘到项目目录：项目 `config.json` 只存 Provider/模型等，Key 每次从配置文件或环境变量补回。
- 输出字数是"目标值"，实际由模型决定；可在 `llm.max_tokens` 调大单次上限。
- 联网搜索需第三方搜索 API Key，未配置时调研退化为内置知识库 + 模型先验，质量仍可用。
- 本工具用于个人创作辅助，请勿用于批量生产侵权或违反平台规则的内容。
