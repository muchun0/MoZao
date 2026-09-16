# LLM 写作知识库（skills_library）

> 存放写作技巧、爆款案例、题材分析、风格参考等原始材料。
> 后续从这里蒸馏出可挂载到 agent 的专属 skill。

## 目录结构

```
skills_library/
├── README.md                  # 本文件
├── INDEX.md                   # 所有文档索引（维护时更新）
├── distillation-guide.md      # 蒸馏指南：怎么从原始材料提炼成 skill
├── writing-techniques/        # 写作技巧（通用）
├── hot-novels/                # 爆款小说拆解（具体作品/套路）
├── genre-research/            # 题材调研（西幻/都市/玄幻/言情等）
├── style-references/          # 风格参考（某位作者的文风/句式）
└── _drafts/                   # 待整理的草稿（原始粘贴）
```

## 文件命名规范

每个 Markdown 文件用 `中文短名-YYYYMMDD.md` 格式，例如：
- `黄金三章法则-20260916.md`
- `十日终焉拆解-20260916.md`
- `都市科幻题材调研-20260916.md`

## 文件格式（frontmatter）

每个文件开头必须有 YAML frontmatter，方便后续检索：

```yaml
---
title: 文档标题
source: 来源（番茄课堂/某本书/某公众号/自总结）
role: 适用角色（researcher/architect/writer/reviewer/all）
tags: [标签1, 标签2]
created: 2026-09-16
---
```

**role 字段**决定后续蒸馏成 skill 时默认挂到哪个 agent：
- `researcher` → 给调研员（题材调研用）
- `architect` → 给架构师（大纲/结构用）
- `writer` → 给写手（正文风格用）
- `reviewer` → 给审校（检查清单用）
- `all` → 所有 agent 都适用

## 蒸馏流程

1. 把原始材料丢进对应子目录（或 `_drafts/`）
2. 读 `distillation-guide.md`，按规则提炼
3. 产出一个精炼的 prompt 片段（300-800字）
4. 在管理后台 → 写作风格 → 新建 skill，选对应 role，粘贴 prompt
5. 测试效果，不好就改

## 注意

- 这里存**原始材料**，不是直接给 LLM 读的
- 一个文件只讲一个主题，别贪多
- 好的例子比抽象原则重要，多贴具体段落
