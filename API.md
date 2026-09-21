# 墨造 · API 接口文档

> 本文档列出所有后端 API 接口，供接手维护参考。
> 基础路径：`http://127.0.0.1:8000`（本地）/ `https://mozao.click`（线上）
> 鉴权：除公开接口外，均需 Header `Authorization: Bearer <token>`（登录返回的 token）

---

## 一、系统

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/health | 否 | 健康检查，返回 200 |
| GET | /api/genres | 否 | 题材下拉列表 |
| GET | /api/pricing | 否 | 计费规则（普通/高质量价格） |

---

## 二、解析

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | /api/parse-outline | 是 | 上传大纲文件（.txt/.md），AI 解析成结构化大纲 |
| POST | /api/parse-content | 是 | 上传/粘贴已有正文，自动切分章节预览 |

---

## 三、账号

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | /api/send-register-code | 否 | 发送注册邮箱验证码 |
| POST | /api/register | 否 | 邮箱注册（验证码+密码） |
| POST | /api/send-reset-code | 否 | 发送重置密码邮件 |
| POST | /api/reset-password | 否 | 重置密码（验证码+新密码） |
| POST | /api/login | 否 | 登录，返回 token |
| POST | /api/logout | 是 | 登出 |

---

## 四、我的

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/me | 是 | 当前用户信息（余额、角色、邀请码） |
| GET | /api/me/recharges | 是 | 我的充值记录 |
| GET | /api/me/referral | 是 | 代理/邀请信息 |
| POST | /api/me/bind-referrer | 是 | 绑定邀请码 |
| GET | /api/me/llm | 是 | 查 BYOK 自配 Key 配置 |
| PUT | /api/me/llm | 是 | 保存 BYOK 配置（provider/key/base_url/model） |
| GET | /api/me/refundable | 是 | 可退款余额 |
| POST | /api/me/refund-request | 是 | 申请余额退款（邮件通知站长） |
| GET | /api/me/refund-requests | 是 | 我的退款申请列表 |

---

## 五、Skills

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/skills | 否 | 全局写作风格列表（admin 维护） |
| GET | /api/my-skills | 是 | 我的自定义 Skills |
| POST | /api/my-skills | 是 | 新建 Skill（name/role/description/prompt_append） |
| PUT | /api/my-skills/{id} | 是 | 编辑 Skill |
| DELETE | /api/my-skills/{id} | 是 | 删除 Skill |
| GET | /api/skills/market | 是 | Skill 市场列表（带 installed 标记） |
| POST | /api/skills/market | 是 | 发布我的 Skill 到市场（body: source_id） |
| POST | /api/skills/market/{id}/install | 是 | 安装市场 Skill 到我的（幂等） |
| DELETE | /api/skills/market/{id} | 是 | 作者下架市场 Skill |
| GET | /api/style-roles | 是 | 风格角色列表（平台内置 + 我的，建书时选） |
| POST | /api/style-roles | 是 | 新建风格角色（name/description/role_prompt） |
| PUT | /api/style-roles/{id} | 是 | 编辑我的风格角色 |
| DELETE | /api/style-roles/{id} | 是 | 删除我的风格角色 |
| POST | /api/style-roles/{id}/skills | 是 | 挂载技能到风格角色（body: skill_type=admin/user, skill_id） |
| DELETE | /api/style-roles/{id}/skills/{skill_type}/{skill_id} | 是 | 卸载风格角色技能 |

---

## 六、项目

### 项目管理

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects | 是 | 项目列表（支持排序/归档/搜索/状态筛选） |
| POST | /api/projects | 是 | 创建项目（**不扣费**，仅余额校验） |
| GET | /api/projects/{id} | 是 | 项目详情 |
| POST | /api/projects/{id}/archive | 是 | 归档项目 |
| POST | /api/projects/{id}/clone | 是 | 克隆项目 |
| DELETE | /api/projects/{id} | 是 | 删除项目（未开始生成直接删，已生成走退款） |
| POST | /api/projects/{id}/refund | 是 | 未开始项目删除退款 |

### 大纲

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/outline | 是 | 读三级大纲（总纲/分卷/章节） |
| PUT | /api/projects/{id}/outline | 是 | 保存大纲（章节带 volume_no） |
| GET | /api/projects/{id}/outline/diff | 是 | 大纲改动检测 |
| POST | /api/projects/{id}/outline/regenerate | 是 | 重新生成大纲 |
| POST | /api/projects/{id}/volumes/{no}/regenerate | 是 | 重新生成分卷 |
| GET | /api/projects/{id}/outline/snapshots | 是 | 大纲快照列表 |
| POST | /api/projects/{id}/outline/snapshots | 是 | 保存快照 |
| POST | /api/projects/{id}/outline/snapshots/{name}/restore | 是 | 回滚快照 |
| POST | /api/projects/{id}/generate-outline | 是 | 分步生成大纲入口 |

### 世界观/人物

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/world | 是 | 读世界观/人物设定 |
| PUT | /api/projects/{id}/world | 是 | 保存世界观 |
| POST | /api/projects/{id}/world/regenerate | 是 | 重新生成世界观 |

### 章节

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/chapters | 是 | 章节列表（按分卷分组） |
| GET | /api/projects/{id}/chapters/{no} | 是 | 读单章内容 |
| PUT | /api/projects/{id}/chapters/{no} | 是 | 编辑单章 |
| POST | /api/projects/{id}/chapters/{no}/rewrite | 是 | 重写单章（免费，每日限次） |
| POST | /api/projects/{id}/chapters/batch | 是 | 批量生成章节 |
| POST | /api/projects/{id}/regenerate-from/{no} | 是 | 从某章开始重生成 |
| POST | /api/projects/{id}/expand-selection | 是 | 扩写选中片段 |

### 生成控制

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | /api/projects/{id}/start | 是 | 开始生成 |
| POST | /api/projects/{id}/stop | 是 | 停止生成 |
| GET | /api/projects/{id}/status | 是 | 生成状态（轮询用，3秒一次） |

### 质量与审校

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/stats/rhythm | 是 | 节奏分析（每章字数分布） |
| GET | /api/projects/{id}/review | 是 | 改稿+读者审校记录 |
| POST | /api/projects/{id}/verify/consistency | 是 | 设定一致性校验 |
| POST | /api/projects/{id}/verify/logic | 是 | 逻辑校验 |
| POST | /api/projects/{id}/verify/compliance | 是 | 合规校验 |
| POST | /api/projects/{id}/generate/alternatives | 是 | 多方案生成 |

### 伏笔与锁定

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/foreshadowing | 是 | 伏笔列表 |
| POST | /api/projects/{id}/foreshadowing | 是 | 添加伏笔 |
| DELETE | /api/projects/{id}/foreshadowing/{idx} | 是 | 删除伏笔 |
| GET | /api/projects/{id}/lock | 是 | 核心设定锁定 |
| PUT | /api/projects/{id}/lock | 是 | 保存锁定 |

### 封面与下载

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/cover | 是 | 读封面图 |
| POST | /api/projects/{id}/cover | 是 | 生成封面（限5次/本） |
| GET | /api/projects/{id}/download | 是 | 下载成品（TXT/EPUB/DOCX） |

### 调研

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /api/projects/{id}/research | 是 | 读联网调研结果 |

---

## 七、站长后台（仅 is_admin）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/admin/login | 站长登录 |
| GET | /api/admin/stats | 全站统计 |
| GET | /api/admin/users | 用户列表 |
| POST | /api/admin/users/{id}/recharge | 手动充值 |
| POST | /api/admin/users/{id}/agent | 设为代理 |
| POST | /api/admin/users/{id}/unagent | 取消代理 |
| GET | /api/admin/users/{id}/downline | 下级列表 |
| GET | /api/admin/users/{id}/commissions | 佣金记录 |
| GET | /api/admin/recharges | 充值流水 |
| GET | /api/admin/projects | 全部项目 |
| GET | /api/admin/settings | 系统设置 |
| GET | /api/admin/logs | 操作日志 |
| GET | /api/admin/refunds | 退款申请列表 |
| POST | /api/admin/refunds/{id}/handle | 处理退款 |
| GET | /api/admin/skills | 全局 Skills 管理 |
| POST | /api/admin/skills | 新建全局 Skill |
| PUT | /api/admin/skills/{id} | 编辑全局 Skill |
| DELETE | /api/admin/skills/{id} | 删除全局 Skill |
| GET | /api/admin/style-roles | 全部风格角色（含用户自建） |

---

## 八、关键业务规则

### 计费
- 创建不扣费，生成完成后按 AI 实际生成字数结算
- 普通 2 元/万字，高质量 3 元/万字
- BYOK 模式（用户自配 Key）完全不扣费
- 余额 ≤ 0 自动停止生成

### 重写限额
- 单章重写 + 从某章重生成，共用 10 次/项目/日
- 超限返回 429

### 封面
- 每本书限 5 次 AI 封面生成
- 项目目录 `_cover_count.txt` 计数
- `GET /cover` 是图片接口，`<img>` 标签直接引用，鉴权用 query 参数 `?token=xxx`（不用 Authorization header）

---

*最后更新：2026-09-20*
