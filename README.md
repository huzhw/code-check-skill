# code-check — 增量代码隐患检查

基于 git 提交记录做**增量**代码隐患检查。SQLite 记录已检查过的提交和文件，只检查新增改动，已检查的自动跳过。多项目各自独立进度。

## 相关技能

- [git-commit](https://github.com/huzhw/git-commit-skill) — Git 提交规范
- [coding-rules](https://github.com/huzhw/coding-rules) — AI 编码协作规范
- [daily-record](https://github.com/huzhw/daily-record-skill) — 日报记录
- [daily-merge](https://github.com/huzhw/daily-merge-skill) — 日报合并
- [reread-claude-md](https://github.com/huzhw/reread-claude-md-skill) — 重新加载 CLAUDE.md 规则
- [service-manager](https://github.com/huzhw/service-manager)：桌面服务管理工具
- [token-3000](https://github.com/huzhw/token-3000-skill) — API 一键切换

---

## 解决了什么问题

写完代码想自查隐患，但**查过一次的改动下次又会全量重查**，浪费时间、浪费 token。这个技能用 SQLite 记住「查过什么」，增量差集只碰新代码。

## 核心能力

- **增量检查** — git log 与 SQLite 差集，只查新提交；工作区改动按内容 hash 去重
- **检查次数** — `check_count` 记录每个改动被检查几次，重复标记次数 +1
- **范围首查** — 只查今天提交（`scan --since today`）、从某提交起（`scan --from <commitId>`）
- **重查** — `recheck` 二次/多次分析，怕一次检查不准时用
- **刷库建基线** — `baseline` 老仓库一次性归档全部历史，不实际检查
- **数据表操作清单** — 报告列出本次 diff 实际涉及的库·表·操作，**写操作（增删改）重点核对：变更字段 + 定位条件（WHERE/ON）**，查询仅核对表名
- **多项目隔离** — `repo` 字段区分仓库，各项目检查进度互不影响
- **已提交 + 未提交全覆盖** — 新 commit 和未提交的工作区改动都查
- **amend/rebase 免疫** — hash 变了按「时间+提交说明」兜底去重，不重查
- **报告落盘** — 完整报告写入被检查项目 `.code-check-reports/` 目录，文件名带日期、时间、commit id
- **零依赖** — Python 标准库 sqlite3，不用装包

## 命令族（同仓库 6 个 skill）

| 命令 | 脚本调用 | 作用 |
|---|---|---|
| `/code-check` | `scan` | 首查：全范围增量 |
| `/code-check-today` | `scan --since today` | 首查：今天提交 |
| `/code-check-from <commitId>` | `scan --from <commitId>` | 首查：从某提交起（含）往后 |
| `/code-recheck-today` | `recheck --since today` | 重查今天（列全部带次数） |
| `/code-recheck-from <commitId>` | `recheck --from <commitId>` | 重查从某提交起 |
| `/code-check-history` | `baseline` | 刷库建基线（不检查） |

**检查次数**：每个 commit/文件记录被检查次数，`mark` 首次=1、重复 +1。重要需求用 `recheck` 多查几轮，简单需求一次就够。

## 检查什么（11 个维度，只查本次改动）

只对**本次 diff 实际改动的代码**逐维度检查，不查没碰过的历史代码。按改动类型套用下面维度：

| 维度 | 检查点 | 典型隐患 |
|------|--------|---------|
| **性能** | 循环内查库/HTTP、N+1 查询、大表无索引、全表扫描 | 逐条查库、无分页、子查询失控 |
| **注入安全** | SQL 拼接 `${}`、未用 `#{}`、字符串拼 SQL、路径拼 | SQL 注入、路径遍历 |
| **健壮性** | 空指针、类型转换、资源未关闭、异常吞掉、边界值 | NPE、连接泄漏、空集合 |
| **并发** | 共享可变状态、线程安全、锁范围 | 静态变量并发、并发修改 |
| **方言兼容** | 达梦/国产库 ROWNUM、分页、关键字、双引号、与 Oracle/MySQL 差异 | 分页写法不兼容、保留字当列名 |
| **前端** | XSS、危险标签、v-html、无 key、内存泄漏 | 注入 HTML、循环无 key |
| **事务** | 批量操作无事务、提交过早、脏读 | 半途失败数据不一致 |
| **数据表操作** | diff 里每处 SQL 涉及的库（schema）+ 表名 + 操作类型；**写操作（INSERT/UPDATE/DELETE/MERGE）必须写明变更字段 + 定位条件（WHERE/ON）**；SELECT 仅核对表名 | 表名写错库、表名拼错、张冠李戴、DELETE/UPDATE 无 WHERE 全表误删误改 |
| **破坏性操作** | 删文件/目录、SQL 清表/无条件删改、删对象（库/表/用户/索引/列）、权限变更、覆盖写无备份 | `rm -rf`、`DROP TABLE`/`TRUNCATE`、`DELETE`/`UPDATE` 无 WHERE、`DROP USER`/`DROP INDEX`/`DROP DATABASE`/`ALTER TABLE DROP COLUMN`、`REVOKE`、覆盖不备份 |
| **敏感信息** | 硬编码密钥/口令、日志打印密码/token、.env/证书入库 | API key 写死、log 打 token |
| **路径安全** | 解压/写入未隔离、上传文件名拼接、解压路径穿越、符号链接 | 解压落临时目录根、`../` 逃逸、软链指向外部 |

每种隐患必须给出：`文件:行号` + 隐患类型 + 为什么是问题 + 改法建议；拿不准标「待确认」，不胡编。逐维度的完整执行细则见 `SKILL.md`。

## 触发词

代码检查、检查代码、隐患检查、code-check、增量检查；家族子技能各自有 today / from / recheck / baseline 触发词。

## 工作方式

```bash
# 1. 扫描（首查全范围；只查今天加 --since today；从某提交起加 --from <commitId>）
python ~/.claude/skills/code-check/scripts/code_check.py scan

# 2. AI 逐项检查隐患（见上面检查清单 11 个维度）

# 3. 检查完标记写回（重复标记次数+1），下次不再重查
python ~/.claude/skills/code-check/scripts/code_check.py mark --commit <hash> --file "src/a.java:<hash>"

# 4. 想二次检查：recheck --since today / recheck --from <commitId>
# 5. 查看库记录与检查次数：status
# 6. 老仓库首次刷库：baseline
```

**一次完整使用**：

```bash
cd /path/to/project                    # 进入被检查的 git 仓库
python ~/.claude/skills/code-check/scripts/code_check.py scan         # 1. 扫描出待查 commit + 工作区改动
#   → AI 逐项读 diff、按检查清单报隐患（文件:行号 + 问题 + 改法）
python ~/.claude/skills/code-check/scripts/code_check.py report-path --commit abc123 --work   # 2. 生成报告文件路径（AI 写完整报告到 .code-check-reports/ 下）
python ~/.claude/skills/code-check/scripts/code_check.py mark --commit abc123 --file "src/a.java:<hash>"   # 3. 标记已检查
python ~/.claude/skills/code-check/scripts/code_check.py scan --quiet  # 4. 复查应显示"没有新改动"
python ~/.claude/skills/code-check/scripts/code_check.py recheck --since today   # 5. 重要需求再查一遍，次数 +1
python ~/.claude/skills/code-check/scripts/code_check.py status        # 6. 看检查次数/库记录
```

## 报告文件

检查完的完整报告**写入文件**，对话只留一行摘要，不刷屏。

| 项 | 说明 |
|----|------|
| 目录 | 被检查项目 git 根下 `.code-check-reports/`（与 `.code-check.db` 同级，不入库） |
| 文件名 | `日期_时间[_recheck][_commitids][+work].md`，如 `20260806_143000_acd4fef8+work.md`、`20260806_143500_recheck_acd4fef8.md` |
| 生成 | `report-path --commit <hash>... [--work] [--recheck]` 建目录、算文件名并打印路径，AI 把报告写入 |
| 撞名 | 同一秒多次检查自动追加 `_2`，不覆盖 |
| 无新改动 | 不写报告文件，直接结束 |

报告内容（结论置顶、按严重度分组）：

```
## code-check 报告 — {repo}

结论：查 {N} 个 commit + {M} 个工作区文件。⚠️ 需处理 {Y} 个，🔶 待确认 {Z} 个，其余 ✅。
{一句话总评，如「无紧急问题，可正常使用」或「有高风险需立即处理」}

### 🗄️ 数据表写操作（增删改，重点核对）
| 库/SCHEMA | 表名 | 操作 | 变更字段（更新/插入啥） | 定位条件（根据啥） | 来源 |
|---|---|---|---|---|---|
| AI_AUDIT | push_catalog_file_config | DELETE | —（删行） | WHERE ID | AiAuditCatalogFilePushConfigMybatisMapper.xml:122 |
| AI_AUDIT | push_catalog_file_config | UPDATE | PATH_EXPR、PROJECT_CATEGORY（COALESCE 保原值）等 | WHERE ID | AiAuditCatalogFilePushConfigMybatisMapper.xml:108 |
| 本库 | T_XY_CMS_CATALOG_FC | MERGE | 匹配:UPDATE c_hasfile='1'；不匹配:INSERT c_id,c_pid,…(14 字段) | ON c_id | UploadBatchMybatisMapper.xml:8 |
| 本库 | T_XY_DATA_FILE_VERSION | INSERT | ID,FILE_ID,NODE_ID,NAME,…(12 字段) | — | UploadBatchMybatisMapper.xml:151 |

### 🔍 数据表查询（SELECT，仅核对表名，不深究）
| 库/SCHEMA | 表名 | 来源 |
|---|---|---|
| 本库 | SYS_FILE_UPLOAD_FAIL_RECORD（联查 T_XY_CMS_PROJECT / T_XY_CMS_CATALOG_FC，分页） | UploadFailRecordMybatisMapper.xml:36 |

规则：
- **写操作按语句主类型标**：INSERT/UPDATE/DELETE/MERGE；MERGE 属"含增改"，匹配/不匹配两分支分别写
- **变更字段**：UPDATE 列 SET 的字段、INSERT 列插入字段、DELETE 填「—（删行）」；字段 ≤6 个全列，>6 个列前 3 个 +「等 N 个字段」
- **定位条件**：UPDATE/DELETE 填 WHERE 的键，MERGE 填 ON 的键，INSERT 填「—」；动态表名（${tableName}）标注分表
- **只列本次 diff 实际 SQL 涉及的表**；库名 = SQL 里 schema 前缀或 datasource 归属，无前缀标「本库」
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

## 数据存储

| 项 | 说明 |
|----|------|
| 库文件 | 被检查项目 git 根目录 `.code-check.db`（不入库） |
| 报告目录 | 被检查项目 git 根 `.code-check-reports/`（不入库），存检查报告 |
| `checked_commits` | repo + commit_hash 去重，`check_count` 记录检查次数 |
| `checked_files` | repo + file_path + content_hash 去重，`check_count` 记录检查次数 |

## 回滚 / 重置进度

想强制重新检查：删除 `.code-check.db` 里的已查记录，下次 `scan` 即全量重查：

```bash
python -c "import sqlite3;c=sqlite3.connect('.code-check.db');c.execute('DELETE FROM checked_commits');c.execute('DELETE FROM checked_files');c.commit()"
```

（仅对当前项目库；`git amend`/`rebase` 改 hash 有「时间+提交说明」兜底，不会误重查。）

## 仓库结构 / 维护

```text
code-check/
├── SKILL.md               # 主技能：完整检查流程 + 检查清单（给 AI 看）
├── scripts/code_check.py  # 唯一脚本，6 个 skill 共用（Python 标准库，零依赖）
├── code-check-today/      # 子技能：只查今天
├── code-check-from/       # 子技能：从某提交起查
├── code-recheck-today/    # 子技能：重查今天
├── code-recheck-from/     # 子技能：从某提交起重查
├── code-check-history/    # 子技能：刷库建基线
└── JUNCTION说明.md          # 全局 skill 目录 junction 双向同步说明
```

**维护**：仓库与全局 `~/.claude/skills/code-check*` 是 junction（双向同步），日常只改本仓库、提交推送即可，全局自动生效，不用手动复制。详见 `JUNCTION说明.md`。

## 安装

```bash
git clone https://github.com/huzhw/code-check-skill.git ~/.claude/skills/code-check
```

重启 Claude Code 生效。

## 许可

MIT
