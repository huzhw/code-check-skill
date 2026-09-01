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
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> scan [--author 姓名] [--json] [--quiet] [--since 时间] [--from 提交id]   # 扫描待检查改动（只列未检查的新项）
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> recheck [--since 时间] [--from 提交id]   # 重查：列范围内所有项（含已查）带次数
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> baseline                                   # 刷库建基线：标记全部历史，不检查
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> mark --commit <hash> ...                 # 标记 commit 已检查（重复标记次数+1）
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> mark --file <路径>:<内容hash> ...         # 标记文件已检查
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> status                                   # 查看库记录数与检查次数
python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> report-path [--commit <hash>]... [--work] [--recheck]   # 建报告目录并打印报告文件路径（AI 写内容）
```

**检查次数（check_count）**：每个 commit/工作区文件记录被检查次数。`mark` 首次=1，重复标记 +1。
想二次/多次检查同一批改动时用 `recheck`（见下方命令族），重要需求多查几轮、简单需求一次就够。

**命令族（同仓库 8 个 skill）**：

| 命令 | 脚本调用 | 作用 |
|---|---|---|
| `/code-check` | `scan` | 首查：全范围增量 |
| `/code-check-today` | `scan --since today` | 首查：今天提交 |
| `/code-check-yesterday` | `scan --since yesterday` | 首查：昨天提交 |
| `/code-check-from <commitId>` | `scan --from <commitId>` | 首查：从某提交起（含）往后 |
| `/code-recheck-today` | `recheck --since today` | 重查今天（列全部带次数） |
| `/code-recheck-yesterday` | `recheck --since yesterday` | 重查昨天（列全部带次数） |
| `/code-recheck-from <commitId>` | `recheck --from <commitId>` | 重查从某提交起 |
| `/code-check-history` | `baseline` | 刷库建基线（不检查） |

---

## 流程

### 第 1 步：确认在 git 仓库内

`git rev-parse --show-toplevel` 确认当前目录是仓库根或子目录。

### 第 2 步：扫描增量改动

```bash
python "{技能目录}/scripts/code_check.py" scan
```

**可选**：只查自己提交加 `--author="胡志伟"`（对照 `git log --format="%an"` 里的实际作者名）。

脚本输出精简为：待查数量一行 → commit 列表（hash/时间/说明/改动文件）→ 工作区改动 → 末尾 mark 命令块（AI 用）。
`--quiet` 只输出「N commit / M 文件待查」一行，供快速判断。

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
| **数据表操作** | diff 里每处 SQL（Mapper XML / @Select / JDBC / 存储过程调用）涉及的库（schema）+ 表名 + 操作类型；**UPDATE/DELETE/MERGE 必须写明变更字段 + 定位条件（WHERE/ON）**，INSERT 仅核对表名 | 表名写错库、表名拼错、改了 A 表却以为是 B 表、跨 schema 引用没带前缀、DELETE/UPDATE 无 WHERE 全表误删误改 |
| **破坏性操作** | 删文件/目录、SQL 清表/无条件删改、删对象（库/表/用户/索引/列）、权限变更、覆盖写无备份 | `rm -rf`/`os.remove`/`File.delete`、`DROP TABLE`/`TRUNCATE`/`DELETE FROM`/`UPDATE` 无 WHERE、`DROP USER`/`DROP INDEX`/`DROP DATABASE`/`ALTER TABLE DROP COLUMN`、`REVOKE`、覆盖不备份 |
| **敏感信息** | 硬编码密钥/口令、日志打印密码/token、.env/证书入库 | API key 写死、log 打 token、`git add .` 带上 .env |
| **路径安全** | **解压/写入未在临时目录内加随机子目录隔离**、上传文件名拼接、解压路径穿越、符号链接 | **解压直接落临时目录根、`../` 逃逸出子目录**、`new File(dir+name)`、软链指向外部目录 |

**每种隐患必须给出**：`文件:行号` + 隐患类型 + 为什么是问题 + 改法建议。**拿不准的标注「待确认」，不许胡编。**

### 第 4 步：输出检查报告 → 写入报告文件

完整报告**写入文件**（不刷屏对话），对话只留一行摘要。**结论置顶**，隐患按严重度分组、每条一句话说清「位置+问题+改法」，让用户一眼看出有没有事、要不要动手：

1. 取报告路径：`python "{技能目录}/scripts/code_check.py" --repo-dir <仓库> report-path --commit <hash>... [--work] [--recheck]`
   - `--commit`：本次检查的所有 commit 短 hash，可多个；本次含工作区改动加 `--work`；重查加 `--recheck`
   - 脚本在 `<仓库git根>/.code-check-reports/`（与 .code-check.db 同级）建目录，按「日期_时间[_recheck][_共N个_最早~最新][+work]」自动命名（如 `20260803_103000_共24个_e455b7b~db5a92f.md`），起止取本次 commit 按提交时间最早/最晚的短 hash；撞名自动追加 `_2`，打印出目标路径
2. 用 Write 工具把下面格式的完整报告写到该路径（用第 1 步打印的绝对路径）
3. 对话只输出一行：`📄 报告：{路径}` + 结论行（如「⚠️ 需处理 2 个，无紧急」）

**无新改动**（scan 报「没有新改动」）：不写报告文件，直接结束。

```
## code-check 报告 — {repo}

结论：查 {N} 个 commit + {M} 个工作区文件。⚠️ 需处理 {Y} 个，🔶 待确认 {Z} 个，其余 ✅。
{一句话总评，如「无紧急问题，可正常使用」或「有高风险需立即处理」}

### 🗄️ 数据表写操作（增删改，重点核对）
| 库/SCHEMA | 表名 | 操作 | 变更字段（更新/插入啥） | 定位条件（根据啥） | 来源 |
|---|---|---|---|---|---|
| 本库 | T_XY_CMS_CATALOG_FC | DELETE | —（删行） | WHERE c_id | UploadBatchMybatisMapper.xml:31 |
| AI_AUDIT | push_catalog_file_config | UPDATE | PATH_EXPR、PROJECT_CATEGORY（COALESCE 保原值）等 | WHERE ID | AiAuditCatalogFilePushConfigMybatisMapper.xml:108 |
| 本库 | T_XY_CMS_CATALOG_FC | MERGE | 匹配:UPDATE c_hasfile='1'；不匹配:INSERT | ON c_id | UploadBatchMybatisMapper.xml:8 |
| AI_AUDIT | push_catalog_file_ai_audit | INSERT | — | — | PushCatalogFileAiAuditMybatisMapper.xml:8 |

### 🔍 数据表查询（SELECT，仅核对表名，不深究）
| 库/SCHEMA | 表名 | 来源 |
|---|---|---|
| 本库 | SYS_FILE_UPLOAD_FAIL_RECORD（联查 T_XY_CMS_PROJECT / T_XY_CMS_CATALOG_FC，分页） | UploadFailRecordMybatisMapper.xml:36 |

规则：
- **写操作按语句主类型标**：INSERT/UPDATE/DELETE/MERGE；MERGE 属"含增改"，匹配/不匹配两分支分别写
- **详略分流**：INSERT 不展开（变更字段、定位条件都填「—」，核对到表名即可）；**UPDATE/DELETE/MERGE 是核对重点**
- **变更字段**：UPDATE/MERGE 列 SET 的字段（≤6 个全列，>6 个列前 3 个 +「等 N 个字段」）；DELETE 填「—（删行）」；INSERT 填「—」
- **定位条件（WHERE/ON）必填**：UPDATE/DELETE 填 WHERE 的键，MERGE 填 ON 的键，INSERT 填「—」；动态表名（${tableName}）标注分表
- **只列本次 diff 实际 SQL 涉及的表**，不在改动的表不列；库名 = SQL 里 schema 前缀或 datasource 归属，无前缀标「本库」
- 用途：核对**删了啥/按啥删、按啥更新/更新了啥**，表名有没有写错库、拼错、张冠李戴；SELECT 仅核对表名，不深究

### ⚠️ 需处理（{Y}）
- `{文件}:{行号}` —— 一句话问题
  - 为什么是问题：...
  - 怎么改：...

### 🔶 待确认（{Z}）
- `{文件}:{行号}` —— 问题 + 不确定点

### ✅ 通过
- [{short_hash}] {说明} —— 一句话
- {文件} —— 一句话

### 建议下一步
- ...
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
| 首次使用全量检查量大 | **老仓库（几百 commit 以上）用 `/code-check-history` 建基线**（脚本 `baseline`），历史 commit 一次性标记为已检查，只查之后的新改动 |
| git 对纯日期/today 解析有坑 | Windows 下 `--since` 只写 `today`/`2026-08-06` 会被 git 误解析成「明天」漏查；脚本已自动补 ` 00:00` 归一化，正常写即可 |
| 出现破坏性操作（删文件/SQL 清表/无条件删改/删对象/收权限，含 UPDATE 无 WHERE） | 必须列出数据影响范围和能否恢复；拿不准标「待确认」，不许直接放行 |
| 非 git 目录运行 | 脚本报错 `fatal: not a git repository`，不生成库 |
| 检查到一半中断 | 已标记的才生效，未标记的下次重查，幂等 |
| DB 文件被 git 追踪 | 该 skill 目录 .gitignore 已含 `*.db`；被检查项目需自行确保 `.code-check.db` 不入库 |
| 报告目录被 git 追踪 | `.code-check-reports/` 与 `.code-check.db` 同待遇：写报告前确认项目 `.gitignore` 含 `.code-check-reports/`，不入库 |

---

## 严禁

- 🔴 **禁止重复检查已查过的提交** — 先 scan 看差集，标记写回后再 scan 确认
- 🔴 **禁止循环内重复执行 git/查库** — 改动批量提取一次，内存里处理
- 🔴 **禁止不标记直接结束** — 检查完必须 mark 写回，否则下次全量重查
- 🔴 **禁止有改动却不写报告文件** — 有内容可查时，必须 report-path 取路径 + Write 落盘，对话只留摘要；无新改动可不写
- 禁止对拿不准的隐患瞎报——标注「待确认」，让用户自己判断
- 禁止检查范围外顺手改代码——只报隐患，不改代码（除非用户明确要求）
- 禁止把其他同事的提交当自己的查——需要时用 `--author` 过滤

---

> 增量检查不是偷懒——查过的记下来，只碰新代码，改动越多查得越稳。
