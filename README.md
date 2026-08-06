# code-check — 增量代码隐患检查

基于 git 提交记录做**增量**代码隐患检查。SQLite 记录已检查过的提交和文件，只检查新增改动，已检查的自动跳过。多项目各自独立进度。

## 相关技能

- [git-commit](https://github.com/huzhw/git-commit-skill) — Git 提交规范
- [coding-rules](https://github.com/huzhw/coding-rules) — AI 编码协作规范
- [daily-record](https://github.com/huzhw/daily-record-skill) — 日报记录
- [daily-merge](https://github.com/huzhw/daily-merge-skill) — 日报合并
- [reread-claude-md](https://github.com/huzhw/reread-claude-md-skill) — 重新加载 CLAUDE.md 规则
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
- **多项目隔离** — `repo` 字段区分仓库，各项目检查进度互不影响
- **已提交 + 未提交全覆盖** — 新 commit 和未提交的工作区改动都查
- **amend/rebase 免疫** — hash 变了按「时间+提交说明」兜底去重，不重查
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

## 触发词

代码检查、检查代码、隐患检查、code-check、增量检查；家族子技能各自有 today / from / recheck / baseline 触发词。

## 工作方式

```bash
# 1. 扫描（首查全范围；只查今天加 --since today；从某提交起加 --from <commitId>）
python scripts/code_check.py scan

# 2. AI 逐项检查隐患（性能/注入/健壮性/并发/方言兼容/前端/事务/破坏性操作/敏感信息/路径安全）

# 3. 检查完标记写回（重复标记次数+1），下次不再重查
python scripts/code_check.py mark --commit <hash> --file "src/a.java:<hash>"

# 4. 想二次检查：recheck --since today / recheck --from <commitId>
# 5. 老仓库首次刷库：baseline
```

## 数据存储

| 项 | 说明 |
|----|------|
| 库文件 | 被检查项目 git 根目录 `.code-check.db`（不入库） |
| `checked_commits` | repo + commit_hash 去重，`check_count` 记录检查次数 |
| `checked_files` | repo + file_path + content_hash 去重，`check_count` 记录检查次数 |

## 安装

```bash
git clone https://github.com/huzhw/code-check-skill.git ~/.claude/skills/code-check
```

重启 Claude Code 生效。

## 许可

MIT
