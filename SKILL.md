---
name: code-check
description: 增量代码隐患检查。基于 git 提交记录做增量检查，SQLite 记录已检查的提交/文件，只检查新增改动，跳过已检查过的。触发词：代码检查、检查代码、隐患检查、code-check、增量检查。
author: 胡志伟
motto: "增量检查不是偷懒——查过的记下来，只碰新代码，改动越多查得越稳。"
---

# code-check — 增量代码隐患检查

## 核心机制：SQLite 记录 → 增量差集

每次只检查**自上次之后新出现**的改动，已检查过的自动跳过，不重复劳动。

```
git log 全量 ──┐
               ├─→ 差集（新提交）→ 提取改动 → AI 检查隐患 → 写回 SQLite
SQLite 已查记录 ┘
```

两张表，区分「已提交」和「未提交工作区改动」：

| 表 | 去重键 | 对应改动 |
|----|--------|---------|
| `checked_commits` | `repo + commit_hash` | 已提交的新 commit |
| `checked_files` | `repo + file_path + content_hash` | 未提交工作区改动（按内容指纹） |

**仓库区分**：`repo` 字段存 git remote 地址（无 remote 用绝对路径），多项目各自独立进度。

**库位置**：被检查项目 git 根目录下 `.code-check.db`（已加入该 skill 的 .gitignore，不入库）。

---

## 脚本

`{技能目录}/scripts/code_check.py`，Python 标准库 sqlite3，零第三方依赖。

```bash
python "{技能目录}/scripts/code_check.py" scan [--author 姓名] [--json]   # 扫描待检查改动
python "{技能目录}/scripts/code_check.py" mark --commit <hash> ...        # 标记 commit 已检查
python "{技能目录}/scripts/code_check.py" mark --file <路径>:<内容hash> ... # 标记文件已检查
python "{技能目录}/scripts/code_check.py" status                          # 查看进度
```

---

## 流程

### 第 1 步：确认在 git 仓库内

`git rev-parse --show-toplevel` 确认当前目录是仓库根或子目录。

### 第 2 步：扫描增量改动

```bash
python "{技能目录}/scripts/code_check.py" scan
```

**可选**：只查自己提交加 `--author="胡志伟"`（对照 `git log --format="%an"` 里的实际作者名）。

脚本输出三块：
1. 已提交的新 commit（hash、时间、作者、说明、改动文件）
2. 未提交工作区改动（标注「新增 / 已查过」）
3. 每个待查项对应的 mark 命令

**全部标「已查过」且无新 commit → 直接报告"无新改动"，结束。**

### 第 3 步：逐项检查隐患 🔴 核心

对每个新 commit / 新增工作区文件，读改动内容（`git show <hash>` / `git diff`），按下面检查清单逐维度检查。**循环外先批量提取所有改动，内存里查，禁止循环内重复执行 git/查库。**

#### 检查清单（按改动类型套用）

| 维度 | 检查点 | 典型隐患 |
|------|--------|---------|
| **性能** | 循环内查库/HTTP、N+1 查询、大表无索引、全表扫描 | 逐条查库、无分页、子查询失控 |
| **注入安全** | SQL 拼接 `${}`、未用 `#{}`、字符串拼 SQL、路径拼 | SQL 注入、路径遍历 |
| **健壮性** | 空指针、类型转换、资源未关闭、异常吞掉、边界值 | NPE、连接泄漏、空集合 |
| **并发** | 共享可变状态、线程安全、锁范围 | 静态变量并发、并发修改 |
| **方言兼容** | 达梦/国产库 ROWNUM、分页、关键字、双引号、与 Oracle/MySQL 差异 | 分页写法不兼容、保留字当列名 |
| **前端** | XSS、危险标签、v-html、无 key、内存泄漏 | 注入 HTML、循环无 key |
| **事务** | 批量操作无事务、提交过早、脏读 | 半途失败数据不一致 |

**每种隐患必须给出**：`文件:行号` + 隐患类型 + 为什么是问题 + 改法建议。**拿不准的标注「待确认」，不许胡编。**

### 第 4 步：输出检查报告

对话中按以下格式列出（不写文件，除非用户要求）：

```
## code-check 报告 — {repo}

### 🆕 新 commit（N 个）
- [{short_hash}] {时间} {说明}
  - [性能] {文件}:{行号} 循环内逐条查库 → 改批量 IN 查询
  - [注入] {文件}:{行号} ${xxx} 拼接 → 改 #{xxx}
  - ✅ {文件}:{行号} 无问题

### 📝 工作区新增改动（N 个）
- {文件}
  - ...

### 结论
- 发现 X 个隐患（Y 个需立即处理 / Z 个待确认）
- 建议：...
```

### 第 5 步：标记已检查 → 写回 SQLite 🔴 必须

检查完**当场**把本次查过的项标记写回，下次才不会再查：

```bash
# 逐 commit 标记
python "{技能目录}/scripts/code_check.py" mark --commit <hash1> --commit <hash2>

# 逐文件标记（内容 hash 从 scan 输出复制）
python "{技能目录}/scripts/code_check.py" mark --file "src/a.java:<hash>"
```

写回后再 `scan` 一次，应显示"没有新改动"。

---

## 回滚 / 重置进度

想强制重新检查某 commit：`mark` 前先删库或手动删对应记录：

```bash
python -c "import sqlite3;c=sqlite3.connect('.code-check.db');c.execute('DELETE FROM checked_commits');c.execute('DELETE FROM checked_files');c.commit()"
```

（仅对当前项目库，删完下次 scan 即全量重查。）

---

## 风险预案

| 场景 | 处理 |
|------|------|
| `git amend` / `rebase` 改 hash | 脚本按「commit_time + subject」二次兜底去重，已查过的不重查 |
| 首次使用全量检查量大 | 首次必全量，之后增量；可加 `--author` 只查自己的 |
| 非 git 目录运行 | 脚本报错 `fatal: not a git repository`，不生成库 |
| 检查到一半中断 | 已标记的才生效，未标记的下次重查，幂等 |
| DB 文件被 git 追踪 | 该 skill 目录 .gitignore 已含 `*.db`；被检查项目需自行确保 `.code-check.db` 不入库 |

---

## 严禁

- 🔴 **禁止重复检查已查过的提交** — 先 scan 看差集，标记写回后再 scan 确认
- 🔴 **禁止循环内重复执行 git/查库** — 改动批量提取一次，内存里处理
- 🔴 **禁止不标记直接结束** — 检查完必须 mark 写回，否则下次全量重查
- 禁止对拿不准的隐患瞎报——标注「待确认」，让用户自己判断
- 禁止检查范围外顺手改代码——只报隐患，不改代码（除非用户明确要求）
- 禁止把其他同事的提交当自己的查——需要时用 `--author` 过滤

---

> 增量检查不是偷懒——查过的记下来，只碰新代码，改动越多查得越稳。
