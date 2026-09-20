# code-check — 增量代码隐患检查

基于 git 提交记录做**增量**代码隐患检查。SQLite 记录已检查过的提交和文件，只检查新增改动，已检查的自动跳过。多项目各自独立进度。

## 相关技能

- [git-commit](https://github.com/huzhw/git-commit-skill) — Git 提交规范
- [coding-rules](https://github.com/huzhw/coding-rules) — AI 编码协作规范
- [daily-record-gitlab-md](https://github.com/huzhw/daily-record-gitlab-md-skill) — 日报记录
- [daily-merge-gitlab-excel](https://github.com/huzhw/daily-merge-gitlab-excel-skill) — 日报合并
- [reread-rules](https://github.com/huzhw/reread-rules-skill) — 重新加载 CLAUDE.md 规则
- [deepseek-harness-settings-curator](https://github.com/huzhw/deepseek-harness-settings-curator) — DSH 模型配置梳理
- [deepseek-harness-plugin-doctor](https://github.com/huzhw/deepseek-harness-plugin-doctor) — DSH 插件与升级体检医生
- [agent-config-sync-check](https://github.com/huzhw/agent-config-sync-check)：四端同步守卫：链接/硬链接/README 同步检查与修复
- [daily-report-panel](https://github.com/huzhw/daily-report-panel)：日报管家（关联仓库，非 skill，自动合并/导出/发件）

---

## 解决了什么问题

写完代码想自查隐患，但**查过一次的改动下次又会全量重查**，浪费时间、浪费 token。这个技能用 SQLite 记住「查过什么」，增量差集只碰新代码。

## 核心能力

- **增量检查** — git log 与 SQLite 差集，只查新提交；工作区改动按内容 hash 去重
- **确定性过滤** — 构建产物（`target/`、`node_modules/`、`*.min.js`、图片/二进制、依赖锁等）、纯产物 commit 直接标记跳过；规则可配（skill 级 `scripts/ignore-rules.txt` 通用 + 仓库根 `.code-check-ignore` 项目专属）
- **分组检查** — 改动按类型分组（Java / MyBatis XML/SQL / 配置 XML / JSP / 前端 TS/JS / SQL 脚本 / 配置文件 / 其他），逐组过防大变更集漏查
- **规则外置（两层）** — 通用层 `rules/*.md` 只放语言通用规则（任何项目可用），AI 按组只加载涉及的规则，`rules/general.md`（破坏性操作/敏感信息/路径安全）恒加载；项目层 `rules/profile-*.md` 放团队规范，脚本按仓库特征目录自动检测（如 wpcms 系），检测到才加载；任何项目还可在仓库根自建 `.code-check-rules.md` 放自己的规范
- **位置锚定** — 每条隐患带 diff 原文片段做锚点，写报告前逐条核对，行号对不上降级「待确认」，防行号漂移
- **检查次数** — `check_count` 记录每个改动被检查几次，重复标记次数 +1
- **范围首查** — 只查今天提交（`scan --since today`）、昨天提交（`scan --since yesterday`）、从某提交起（`scan --from <commitId>`）
- **重查** — `recheck` 二次/多次分析，怕一次检查不准时用
- **刷库建基线** — `baseline` 老仓库一次性归档全部历史，不实际检查
- **数据表操作清单** — 报告列出本次 diff 实际涉及的库·表·操作，**UPDATE/DELETE/MERGE 重点核对：变更字段 + 定位条件（WHERE/ON）**，INSERT 仅核对表名
- **多项目隔离** — `repo` 字段区分仓库，各项目检查进度互不影响
- **已提交 + 未提交全覆盖** — 新 commit 和未提交的工作区改动都查
- **amend/rebase 免疫** — hash 变了按「时间+提交说明」兜底去重，不重查
- **报告落盘** — 完整报告写入被检查项目 `.code-check-reports/` 目录，文件名带日期、时间、提交次数与起止 commit
- **零依赖** — Python 标准库 sqlite3，不用装包

## 命令族（同仓库 8 个 skill）

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

**检查次数**：每个 commit/文件记录被检查次数，`mark` 首次=1、重复 +1。重要需求用 `recheck` 多查几轮，简单需求一次就够。

## 检查什么（按组加载规则，只查本次改动）

只对**本次 diff 实际改动的代码**检查，不查没碰过的历史代码。scan 按文件类型自动分组，AI 逐组检查、每组只加载对应规则文件（`rules/general.md` 恒加载）：

| 规则文件 | 层 | 覆盖维度 | 适用 |
|---|---|---|---|
| `rules/java.md` | 通用 | 性能（N+1/循环查库）、健壮性（NPE/资源/异常）、并发、事务 | Java 组，任何项目 |
| `rules/sql.md` | 通用 | 注入（`${}`/`#{}`）、**数据表操作核对表**、UPDATE/DELETE 无 WHERE、批量语句规模 | MyBatis XML/SQL、SQL 脚本组，任何项目 |
| `rules/frontend.md` | 通用 | XSS、React key/内存泄漏、空值渲染 | JSP、前端 TS/JS 组，任何项目 |
| `rules/general.md` | 通用 | 破坏性操作、敏感信息、路径安全、配置文件、其他文件兜底 | **所有组恒加载** + 配置 XML/配置文件/其他 |
| `rules/profile-*.md` | 项目 | 团队规范浓缩层：`profile-wpcms.md`（达梦方言/批量分包/权限白名单）、`profile-lanxum-java.md`（四库 SQL 兼容/模块依赖白名单/定时任务骨架）、`profile-lanxum-react.md`（ra-lib 传参/掩码回显/客户定制包） | 按仓库特征自动检测加载，其他项目不碰 |
| 仓库根 `.code-check-rules.md` | 项目 | 项目自定义规范（自建，如达梦方言用于非 wpcms 达梦项目） | 存在即加载 |

每种隐患必须给出：`文件:行号` + **锚点片段（diff 原文，防行号漂移）** + 隐患类型 + 为什么是问题 + 改法建议；拿不准标「待确认」，不胡编。完整执行细则见 `SKILL.md` 与 `rules/*.md`。

## 触发词

代码检查、检查代码、隐患检查、code-check、增量检查；家族子技能各自有 today / yesterday / from / recheck / baseline 触发词。

## 工作方式

```bash
# 1. 扫描（首查全范围；只查今天加 --since today；只查昨天加 --since yesterday；从某提交起加 --from <commitId>）
python ~/.claude/skills/code-check/scripts/code_check.py scan

# 2. AI 逐组检查隐患（按组加载 rules/*.md，general.md 恒加载）

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
#   → AI 逐组读 diff、按组加载 rules/*.md 报隐患（文件:行号 + 锚点片段 + 问题 + 改法）
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
| 文件名 | `日期_时间[_recheck][_共N个_最早~最新][+work].md`，如 `20260803_103000_共24个_e455b7b~db5a92f.md`、`20260804_103500_recheck_共15个_caad846~c202dc9.md` |
| 生成 | `report-path --commit <hash>... [--work] [--recheck]` 建目录、算文件名并打印路径，AI 把报告写入 |
| 撞名 | 同一秒多次检查自动追加 `_2`，不覆盖 |
| 无新改动 | 不写报告文件，直接结束 |

报告内容（结论置顶、按严重度分组）：

```
## code-check 报告 — {repo}

结论：查 {N} 个 commit + {M} 个工作区文件。⚠️ 需处理 {Y} 个（X1~X{Y}），🔶 待确认 {Z} 个（D1~D{Z}），其余 ✅。
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
- **只列本次 diff 实际 SQL 涉及的表**；库名 = SQL 里 schema 前缀或 datasource 归属，无前缀标「本库」
- 用途：核对**删了啥/按啥删、按啥更新/更新了啥**，表名有没有写错库、拼错、张冠李戴；SELECT 仅核对表名，不深究

### ⚠️ 需处理（{Y}）
- **X{n}** `{文件}:{行号}` `` `锚点片段` `` —— 一句话问题
  - 为什么是问题：...
  - 怎么改：...

### 🔶 待确认（{Z}）
- **D{n}** `{文件}:{行号}` `` `锚点片段` `` —— 问题 + 不确定点

### ✅ 通过
- [{short_hash}] {说明} —— 一句话
- {文件} —— 一句话

### 建议下一步
- ...
```

**条目编号规则**：需处理条目 `X1`、`X2`…（需→X），待确认条目 `D1`、`D2`…（待→D）；每份报告内从 1 连续递增，需处理按严重度高→低排序编号；某类为 0 个时结论行省略对应括号；重查（recheck）报告重新编号、不跨报告延续；「建议下一步」引用条目直接用编号（如「先修 X1」）。

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
├── SKILL.md               # 主技能：完整检查流程 + 规则加载 + 报告格式（给 AI 看）
├── scripts/code_check.py  # 唯一脚本，8 个 skill 共用（Python 标准库，零依赖）
├── scripts/ignore-rules.txt # 过滤规则配置（构建产物目录等，跨项目通用）
├── rules/java.md          # 通用层：Java 组检查规则（任何项目）
├── rules/sql.md           # 通用层：MyBatis XML/SQL 组规则（数据表核对，任何项目）
├── rules/frontend.md      # 通用层：JSP / 前端组检查规则（任何项目）
├── rules/general.md       # 通用层：破坏性操作/敏感信息/路径安全（恒加载）
├── rules/profile-wpcms.md # 项目层：wpcms 团队规范（脚本自动检测加载）
├── rules/profile-lanxum-java.md  # 项目层：蓝信 AMISP 后端规范（四库兼容等）
├── rules/profile-lanxum-react.md # 项目层：蓝信 AMISP 前端规范（ra-lib 等）
├── code-check-today/      # 子技能：只查今天
├── code-check-yesterday/  # 子技能：只查昨天
├── code-check-from/       # 子技能：从某提交起查
├── code-recheck-today/    # 子技能：重查今天
├── code-recheck-yesterday/# 子技能：重查昨天
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
