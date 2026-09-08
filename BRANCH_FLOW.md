# 分支与发布流程

> 本文件是"墨造 · AI 小说创作平台"的代码管理规则，所有代码改动必须遵守。

## 分支定义

| 分支 | 用途 | 环境 |
|---|---|---|
| `devtest` | 开发与验证 | 本地开发、功能验证 |
| `master` | 生产稳定版 | **服务器运行此分支的代码** |

## 日常开发流程（每次改代码都必须走）

1. 在 `devtest` 分支上进行修改
2. 本地验证：`py -3.13 -m pytest tests/ -q` + 手动冒烟测试
3. 提交到 `devtest`：`git add <文件> && git commit -m "feat: ..."`
4. 推送：`git push origin devtest`
5. 在 devtest 上完整验证（功能、回归、数据无异常）
6. **确认无误后**合并到 master：
   ```
   git checkout master
   git merge devtest
   git push origin master
   ```
7. 发布服务器（服务器运行 master 代码）：
   ```
   git checkout master   # 必须确认当前在 master 且与 origin/master 一致
   git pull origin master
   # 打包上传 /opt/novel-gen → docker compose up -d --build
   ```

## 红线（禁止）

- **`.env` 绝不提交**（含 API Key / SMTP 密钥，已被 .gitignore 排除）
- **未验证的代码不允许合并 master**
- **服务器只运行 master 代码**；devtest 代码不得直接部署
- 数据库、日志、生成的成品（projects/ output/）不纳入版本库
- 临时脚本（`_*.py` / `_*.sh`）不提交，用完即删

## 发布前自检清单

- [ ] 当前分支是 `master`，且 `git status` 干净
- [ ] `git log devtest..master` 为空（devtest 已全部合入）
- [ ] 服务器部署包从 master 打出，包含全部最新改动
- [ ] 部署后 `curl http://<服务器>/api/health` 返回 ok
