# 生产部署说明：番茄热门完本拆书条目插入（teardown_fanqie_2026-10）

> 本目录供生产环境（阿里云 /opt/novel-gen 的 docker mysql-mozao，库 mozao）原样执行插入。
> 目的：为墨造 AI 小说生成建立"人味语感基线"，降低生成文本的朱雀 AI 特征（本地实测基线 76.86%）。
> 本说明只描述可执行步骤，不连接、不修改生产环境。

## 一、文件清单

| 文件 | 说明 |
|---|---|
| `insert_book_teardown_fanqie_20261013.sql` | 幂等 INSERT SQL，12 条，不含 id（走自增）；content 为 JSON 字符串已正确转义；created_at/updated_at 为 unix 时间戳 |
| `teardown_data_fanqie_20261013.json` | 与 SQL 一一对应的数据文件，每本一条（含 genre/title/summary/content 全字段），供人工核对 |

## 二、执行前核对（必读）

1. **表结构一致性**：先在本地对比生产 `SHOW CREATE TABLE book_teardown`，重点：
   - **`user_id` 列是否存在**（本 SQL 写入 `user_id=7`）。若生产表无此列，需先执行
     `ALTER TABLE book_teardown ADD COLUMN user_id INT NOT NULL DEFAULT 0;`
     或去掉 SQL 中的 `user_id` 列与对应值。
   - 库名 `mozao`、字符集 `utf8mb4`（content 是中文 JSON 字符串）。
2. **user_id 归属说明**：7 为本地 admin 账号。`book_teardown.user_id` 无外键约束，生产不存在 id=7 的用户也能插入；如需归属到生产真实账号，可把 SQL 中 `,7` 改为对应账号 id；无归属需求保留 7 即可。
3. **幂等说明**：每条 SQL 采用 `INSERT ... SELECT ... WHERE NOT EXISTS (title+genre+source_type='manual')`，重复执行不会产生重复条目。**已在本地实测**：对本库执行后 12 条全部被跳过、0 新增、总数不变。

## 三、执行步骤（任选其一）

### 方式 A：服务器命令行（推荐）

1. 将两个文件上传到服务器（如 workbench upload 至 `/tmp/teardown_fanqie_2026-10/`）。
2. 进入 mysql-mozao 容器执行（生产密码以实际为准）：

```bash
docker exec -i mysql-mozao mysql -uroot -p'<生产库密码>' mozao < /tmp/teardown_fanqie_2026-10/insert_book_teardown_fanqie_20261013.sql
```

3. 回读验证：

```bash
docker exec -i mysql-mozao mysql -uroot -p'<生产库密码>' mozao -e \
"SELECT id,genre,title,source_type,user_id FROM book_teardown WHERE source_type='manual' AND user_id=7; \
SELECT COUNT(*) FROM book_teardown WHERE source_type='manual' AND user_id=7;"
```

期望 12 条；再执行一次 SQL，条数应保持不变（幂等）。

### 方式 B：MySQL 客户端 / phpMyAdmin

选择 `mozao` 库后整体导入 SQL 文件即可（文件首行含 `USE mozao;`，亦可直接执行）。

## 四、回滚（如需撤销）

```sql
DELETE FROM book_teardown WHERE source_type='manual' AND user_id=7 AND title IN (
'十日终焉','赤心巡天','捞尸人','开局长生万古，苟到天荒地老','苟在初圣魔门当人材',
'我不是戏神','从水猴子开始成神','冬日重现','刚准备高考，离婚逆袭系统来了',
'洛阳农牧事','他的眼里雨停了','谋断镇朝野');
```

仅确需撤销时执行；生产操作前请先备份该表。

## 五、与本地一致性

- 本地 mozao 库已入库同一批 12 条（id 3440–3451，source_type='manual'，user_id=7），
  `teardown_data_fanqie_20261013.json` 与 SQL 逐条对应（同一 genre/title/summary/content/时间戳）。
- 数据文件 `content` 为解析后的 JSON 对象（styles/takeaways/excerpts），与 DB 存储的 JSON 字符串语义一致，便于核对。

## 六、题材锚消费说明（部署后生效逻辑）

- 墨造 `_inject_teardown_style_anchor` 按项目 `genre` 精确匹配 `book_teardown.genre`，
  取 `updated_at` 最新的前 5 条，把 styles/takeaways/excerpts 组装为
  `teardown_style_anchor.txt` 注入写手 prompt（scene/chapter/expand 三条正文路径，弱约束）。
- 本批 12 条题材分布：玄幻 3 / 悬疑 3 / 都市 2 / 言情 2 / 仙侠 1 / 历史 1。
- 新项目建好后，需生成/续写正文时锚才会被注入；仅入库拆书条目本身不触发正文生成。
