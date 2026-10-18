# TECH-后台开关管理（生成引擎开关进管理后台 · 技术方案）

> 状态：**核心方案已确认**（D1 拍板：`sys_config` 表为准、项目 config.json 快照不动；D2/D3 按推荐），待开发  
> 创建：2026-10-18  
> 已确认决策：REQ §3.3 D1（以表为准，config.json 快照不迁移不改写）/ D2（仅生成引擎 94 开关）/ D3（后台 > 环境变量 > 项目快照 > 注册表默认）

## 1. 总体设计

### 1.1 配置优先级链（以表中数据为准）

```
1  FEATURE_FLAGS 注册表默认值   （novel_gen/config.py，代码事实来源，仅作"表缺键"时的最终兜底）
2  项目 config.json 快照值      （projects/<id>/config.json，建书时固化，仅作"表缺键"时的次级兜底）
3  环境变量 AGENT_GUARD_*       （仅 guard 子项，部署层兜底）
4  后台全局覆盖（MySQL sys_config）  ← 唯一运行时权威：**表里有的键一律以表为准**
```

- **以表中数据为准（用户拍板）**：`sys_config` 表是运行时开关的**唯一权威**——表中有该键，生成时一律用表值；表中没有的键（仅限注册表新增后尚未 seed 的窗口）按「项目快照 → 注册表默认」回退，seed 后即恢复表权威。
- **项目 config.json 快照不动**：不迁移、不改写、不因后台保存而调用 `store.save_config`；项目文件保持权威快照，供未来 per-project 扩展。
- **不改 novel_gen**：覆盖注入全部发生在 webapp 层（依赖方向 webapp → novel_gen 不变），novel_gen 离线跑（mock/测试）不感知后台，行为确定性保持。
- **新增开关的历史一致性（用户核心动机）**：新增开关 = 在 `FEATURE_FLAGS` 注册一行 → `seed_feature_flags()` 幂等以默认值插入 `sys_config` → 所有项目（含历史项目）生成时一律遵循表值——历史项目 config.json 缺该字段不再是问题，行为与表值完全一致。

### 1.2 接线点（唯一注入管道）

`webapp/core.py::_open_pipeline(store, trace_id)` 是所有生成入口的唯一管道，已确认 15 处调用覆盖全部路径：

| 调用位置                                                      | 业务路径                             |
| --------------------------------------------------------- | -------------------------------- |
| `core.py:919` `_run_outline_only`                         | 仅大纲生成                            |
| `core.py:994` `_run_generation`                           | 完整生成 / 从第 N 章重生成                 |
| `core.py:1124` `_run_batch`                               | 批量章节                             |
| `core.py:1345`                                            | 断点续跑 / 任务重拾                      |
| `routers/projects_generation.py:24/117`                   | 生成启动 / 重跑                        |
| `routers/projects_chapters.py:38/167/234/263/285/310/604` | 单章重写 / 审校 / 扩写 / 续写 / 从第 N 章重生成等 |
| `routers/projects_base.py:387/416`                        | 克隆项目生成 / 建书后触发                   |

在 `_open_pipeline` 内 `cfg = store.load_config()` 之后、用户 BYOK 覆盖之前插入 `_apply_global_overrides(cfg)` 一处即可全部生效。

## 2. 数据模型

### 2.1 `sys_config` 表（webapp/db/base.py 新增 DDL）

```sql
CREATE TABLE IF NOT EXISTS sys_config (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  config_key  VARCHAR(128) NOT NULL COMMENT '开关 key，与 FEATURE_FLAGS.key 对齐',
  config_value VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '当前值（bool 存 true/false，数字存字符串，JSON 存 JSON 字符串）',
  config_group VARCHAR(64)  NOT NULL COMMENT '模块分组：memory/writing/review/guard',
  config_type VARCHAR(16)   NOT NULL COMMENT 'bool/int/float/str/select',
  label       VARCHAR(128)  NOT NULL COMMENT '后台显示名称（取自注册表 desc 首句，可覆盖）',
  description VARCHAR(1024) DEFAULT NULL COMMENT '完整说明（取自注册表 desc）',
  is_system   TINYINT       NOT NULL DEFAULT 0 COMMENT '系统内置开关，禁止删除',
  updated_at  DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  updated_by  VARCHAR(64)   DEFAULT NULL COMMENT '最后修改管理员用户名',
  UNIQUE KEY uk_config_key (config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全局开关配置表（后台可编辑，运行时覆盖）';
```

- **种子化（新增开关自动入表）**：`init_db()` 时把 `FEATURE_FLAGS` 注册表全量同步进表（`INSERT ... ON DUPLICATE KEY UPDATE` 幂等），保证 94 个开关在后台可见且与代码注册表一致。**之后每新增一个开关，只要在注册表加一行，seed 即自动以默认值入表，历史项目无需任何迁移即统一遵循表值**（§1.1 用户核心动机）。删除的注册项不自动删表（防误删，保留历史）。
- **不落盘 secret**：本表只存开关/阈值，不存 API Key / 用户密钥（§11.3）。

### 2.2 `sys_config_log` 审计表

```sql
CREATE TABLE IF NOT EXISTS sys_config_log (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  config_key  VARCHAR(128) NOT NULL,
  old_value   VARCHAR(1024) DEFAULT NULL,
  new_value   VARCHAR(1024) DEFAULT NULL,
  operator    VARCHAR(64)  NOT NULL COMMENT '操作管理员用户名',
  created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
  KEY idx_key (config_key), KEY idx_time (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全局开关修改审计日志';
```

## 3. 后端实现

### 3.1 `webapp/db/config.py`（数据层，SQL 唯一入口）

```python
# -*- coding: utf-8 -*-
"""系统开关配置数据层（sys_config / sys_config_log）。SQL 只允许出现在本模块。"""
from __future__ import annotations
from typing import Any
import json
from .base import get_conn

FLAG_MODULES = ("memory", "writing", "review", "guard")   # 展示顺序


def seed_feature_flags() -> int:
    """把 novel_gen.config.FEATURE_FLAGS 全量幂等同步进 sys_config（ON DUPLICATE KEY UPDATE）。
    返回本次新增条数。guard 子项（max_loops 等）与顶层开关同表存储。"""
    from novel_gen.config import FEATURE_FLAGS
    rows = [(f.key, _json_default(f.default), f.module, f.key_desc_type(), ...) for f in FEATURE_FLAGS]
    # INSERT ... ON DUPLICATE KEY UPDATE label=VALUES(label), description=VALUES(description)
    ...


def _json_default(value: Any) -> str:
    """默认值序列化：bool→'true'/'false'，数字→str，str 原样，dict/list→JSON。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def list_configs() -> list[dict]:
    """返回全部开关行（含 group 排序），供后台渲染。"""


def get_config(key: str) -> dict | None: ...


def update_configs(updates: list[dict], operator: str) -> int:
    """批量保存：逐条 UPDATE + 写审计；返回更新条数。非法 key 跳过（应由路由层先校验）。"""


def coerce_config_type(config_type: str, raw: str):
    """按类型把存储串转回 Python 值（路由层校验用）。"""
```

- `seed_feature_flags` 在 `webapp/db/__init__.py` 的 `init_db()` 末尾调用一次（幂等），保证新环境自动有种子。
- 类型元数据：`config_type` 由注册表字段类型推导（bool/int/float/str/dict）；`select` 类（如 `scene_state_mode` auto/llm/deterministic、`pacing_density` low/medium/high）在路由层定义枚举白名单。

### 3.2 路由（`webapp/routers/admin.py` 追加，挂 `_require_admin`）

```python
@app.get("/api/admin/config")
def admin_config_list(user: dict = Depends(_require_admin)):
    """开关列表：按模块分组，含当前值/代码默认值/类型/说明/修改信息。"""
    from ..db import config as cfg_db
    rows = cfg_db.list_configs()
    defaults = {f.key: f.default for f in FEATURE_FLAGS}     # 代码默认值来源
    return {"groups": [{"module": m, "items": [...]} for m in FLAG_MODULES]}


@app.put("/api/admin/config")
def admin_config_update(req: ConfigUpdateReq, user: dict = Depends(_require_admin)):
    """批量保存：类型/范围校验 → 入库 → 审计。req.items = [{key, value}, ...]"""
```

校验规则（路由层做 HTTP 输入校验，不写业务规则进 db 层）：

- `key` 必须在注册表中存在（白名单，防注入/防伪造 key）；
- 类型必须与注册表默认值类型一致（bool 只收 true/false；int 用 `int()` 严格转换失败即 400；float 同；str 限长 ≤1024；select 类值必须在枚举内）；
- 范围约束：`max_loops ≥ 1`、`max_tokens ≥ 1000`、`ai_taste_threshold / style_drift_threshold` 0-100、`memory_window ≥ 0`、`verify_every / state_extract_frequency ≥ 1`（0 例外项按注册表 desc 标注）、`style_reanchor_every ≥ 0` 等——写一张 `RANGE_RULES` 小表（key → (min, max)），未命中则不设限。
- 全部通过后：`db.config.update_configs(items, user["username"])`；返回 `{ok, updated: n}`。

### 3.3 覆盖注入（`webapp/core.py`）

```python
def _apply_global_overrides(cfg: NovelConfig) -> None:
    """把 sys_config 后台全局覆盖合并进 cfg（内存态，不落盘）。
    以表中数据为准：表有键一律覆盖（内存态）；DB 异常回退项目快照/注册表默认（§11.6 留痕）。"""
    try:
        from ..db import config as cfg_db
        overrides = {r["config_key"]: r for r in cfg_db.list_configs()}
    except Exception:
        logger.exception("读取全局开关覆盖失败，本次生成使用项目快照默认值")
        return
    for key, row in overrides.items():
        if not hasattr(cfg, key):
            continue
        try:
            value = cfg_db.coerce_config_type(row["config_type"], row["config_value"])
        except Exception:
            logger.warning("全局开关 %s 值非法已跳过: %r", key, row["config_value"])
            continue
        setattr(cfg, key, value)
    # guard 子项：cfg.agent_guard 上的同名标量字段
    guard_map = {"max_loops", "max_tokens", "input_check", "output_check", "quality_check"}
    for key in guard_map:
        row = overrides.get(key)
        if row:
            setattr(cfg.agent_guard, key, coerce(...))
    # scene_limits：JSON 解析后整段覆盖
    if overrides.get("scene_limits"):
        try:
            cfg.agent_guard.scene_limits = json.loads(...)
        except Exception:
            logger.warning(...)
```

接入点（`_open_pipeline` 内，保持单函数 <50 行）：

```python
def _open_pipeline(store, trace_id=""):
    cfg = store.load_config()
    _apply_global_overrides(cfg)                      # ← 新增：后台全局覆盖
    uid = db.project_owner(store.dir.name)
    user_llm = _user_llm_cfg(uid)                     # 用户 BYOK 覆盖（LLM 段）
    ...
    from novel_gen.config import apply_guard_env_overrides
    apply_guard_env_overrides(cfg.agent_guard)        # 环境变量兜底
    ...
```

> D3 已确认：后台（表）> 环境变量 > 项目快照 > 注册表默认。`_apply_global_overrides` 先于 `apply_guard_env_overrides` 执行——表已设置的值不被环境变量覆盖；表未设置时环境变量兜底生效。

### 3.4 不改 novel_gen

- `FEATURE_FLAGS`、`NovelConfig`、`save_config/load_config`、`apply_guard_env_overrides` 全部保持原样（§11.3：注册表仍是开关**默认值**唯一事实来源与新增开关入口；`sys_config` 表是**运行时权威**，二者由 seed 保持同步）。
- 唯一新增是 webapp 侧的 db/config.py + admin 路由 + core 注入，满足文件规模红线（每个新文件 <500 行）。

## 4. 前端实现（`webapp/frontend/src/`）

### 4.1 接口封装（`api/admin.js` 追加）

```js
export function getConfig() { return request.get('/api/admin/config') }
export function updateConfig(items) { return request.put('/api/admin/config', { items }) }
```

### 4.2 页面（`views/Admin.vue` 新增「系统设置」tab）

- `el-tabs` 内按 memory / writing / review / guard 分 4 个 `el-tab-pane`（子分组），每项一行：`label` + `description` + 输入控件：
  - `config_type=bool` → `el-switch`；
  - `int`/`float` → `el-input-number`（带 min/max 范围）；
  - `select`（scene_state_mode / pacing_density / plot_speed 等）→ `el-select` 枚举；
  - `str` → `el-input`；
- 每个 tab 底部「保存本组」按钮，改动项高亮（与当前值 diff），保存调用 `updateConfig`，成功 `ElMessage.success` 并刷新；
- 展示代码默认值（灰字）与当前值对照；有修改记录的展示 `updated_by / updated_at`；
- 数据加载 `onMounted → getConfig()`，按 `config_group` 分组渲染；
- 遵循 §11.11：组合式 API、接口走 `api/admin.js`、样式走 `style.css` 视觉语言。

## 5. 校验、降级与审计

| 关注点  | 实现                                                             |
| ---- | -------------------------------------------------------------- |
| 输入校验 | 路由层：key 白名单 + 类型严格转换 + `RANGE_RULES` 范围 + select 枚举；非法 400 带提示 |
| 降级   | DB 不可读 → 跳过覆盖走默认链，`logger.exception` 留痕（§11.6），不阻塞生成           |
| 审计   | `update_configs` 内同事务写 `sys_config_log`（旧值/新值/operator/时间）     |
| 并发   | 批量保存整体 UPDATE；一期以最后保存为准，审计可追溯                                  |
| 权限   | 所有接口 `Depends(_require_admin)`；前端 tab 仅在 is_admin 路由下渲染        |

## 6. 测试计划

### 6.1 pytest（新增 `tests/test_sys_config.py`，按 P0/P1/P2 分级）


| 用例                                     | 级别 | 覆盖                                                    |
| -------------------------------------- | -- | ----------------------------------------------------- |
| `test_seed_flags_power_idempotent`     | P2 | seed 幂等（重复 seed 不报错、条数=注册表数）                          |
| `test_seed_flags_cover_registry`       | P1 | sys_config 的 key 集合 == FEATURE_FLAGS 的 key 集合         |
| `test_seed_new_flag_auto_insert`       | P1 | **模拟新增开关**：注册表加一行 → seed 后自动入表、默认值正确（验证"新增开关历史数据无问题"） |
| `test_update_and_audit`                | P1 | 更新值写入 + 审计行含旧值/新值/operator                            |
| `test_type_coerce_bool_int_float`      | P1 | bool/int/float/str/JSON 转换正确                          |
| `test_invalid_type_rejected`           | P1 | 非 bool 传给 bool 开关 → 400                               |
| `test_range_rule_rejected`             | P1 | max_loops=0 → 400；阈值越界 → 400                          |
| `test_unknown_key_rejected`            | P1 | 伪造 key → 400（防注入）                                     |
| `test_apply_global_overrides`          | P0 | 构造 store + mock db，覆盖后 cfg 字段/guard 子项被替换；DB 异常时走默认值  |
| `test_override_not_persist_to_project` | P0 | 覆盖注入后项目 config.json 内容不变                              |
| `test_table_missing_key_fallback`      | P1 | 表缺键（未 seed）时回退项目快照/注册表默认；seed 后恢复表权威                  |
| `test_admin_required`                  | P0 | 非管理员 GET/PUT → 403                                    |

回归范围：`test_settle.py`（结算不受影响）、`test_agents.py`（生成路径）、相关 P0/P1 文件；必要时全量 pytest。

### 6.2 端到端冒烟（开发完成后执行）

1. 本地起服务 → 管理后台登录 → 「系统设置」看到 94 开关；
2. 改 `state_extract_frequency` 1→5 → 建新书生成 → `projects/<id>/config.json` 未被改写、行为按 5；
3. 存量项目：改 `book_memory` True→False → 对存量项目触发章节重写/续写 → 行为按 False（book_memory 跳过）；
4. 改 guard `max_loops` → 触发超限场景验证；
5. 非管理员调用接口 → 403；普通用户页面无 tab；
6. 修改后查 `sys_config_log` 审计行。

## 7. 文档同步与开发步骤

### 7.1 文档同步（合并 master 前完成，AGENTS.md §6）

- `docs/architecture.md`：webapp 2.3/2.4 增补 db/config.py、admin 配置接口、core 覆盖注入；§5 增补"全局开关覆盖"机制行；
- `docs/filemap.md`：增 `webapp/db/config.py`、admin.py 新路由、前端 tab；
- `docs/decisions.md`：追加本期决策（优先级链、覆盖不落盘、范围边界）；
- `README.md`：发版记录；`WIKI.md`：后台开关管理使用说明（待评审确认后随开发同步）；
- 飞书需求台账：登记后状态随阶段更新（进行中 → 已完成）。

### 7.2 开发步骤（方案已确认，从 devtest 切 `feat/admin-config-switch`）

1. `webapp/db/base.py` 加 2 张表 DDL → `db/config.py` 数据层 → `db/__init__.py` 挂 seed；
2. `webapp/routers/admin.py` 加 GET/PUT 接口 + 校验；
3. `webapp/core.py` `_apply_global_overrides` + `_open_pipeline` 接线；
4. 前端 `api/admin.js` + `Admin.vue` 系统设置 tab；
5. pytest 新增 + 回归 + 端到端冒烟（§6）；
6. 文档同步（§7.1）→ 合 devtest → 验收 → 合 master → 发版（`mozao-deploy` 流程）。

## 8. 二期预留（不实现，仅留接口扩展位）

- `sys_config` 表加 `project_id VARCHAR(64) DEFAULT NULL`（NULL=全局，非 NULL=per-project 覆盖）即可扩展项目级开关；`_apply_global_overrides` 增加 project 维度合并即可，本期不加。
- webapp 业务参数（定价/返佣）二期接入同表（config_group 增加 `business`），读取点改为运行时查表 + 内存缓存，结算回归后上线。
