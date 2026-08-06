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
- **多项目隔离** — `repo` 字段区分仓库，各项目检查进度互不影响
- **已提交 + 未提交全覆盖** — 新 commit 和未提交的工作区改动都查
- **amend/rebase 免疫** — hash 变了按「时间+提交说明」兜底去重，不重查
- **零依赖** — Python 标准库 sqlite3，不用装包

## 触发词

代码检查、检查代码、隐患检查、code-check、增量检查

## 工作方式

```bash
# 1. 扫描增量改动（差集）
python scripts/code_check.py scan

# 2. AI 逐项检查隐患（性能/注入/健壮性/并发/方言兼容/前端/事务）

# 3. 检查完标记写回，下次不再重查
python scripts/code_check.py mark --commit <hash> --file "src/a.java:<hash>"
```

## 数据存储

| 项 | 说明 |
|----|------|
| 库文件 | 被检查项目 git 根目录 `.code-check.db`（不入库） |
| `checked_commits` | repo + commit_hash 去重，记录已检查的提交 |
| `checked_files` | repo + file_path + content_hash 去重，记录已检查的工作区文件改动 |

## 安装

```bash
git clone https://github.com/huzhw/code-check-skill.git ~/.claude/skills/code-check
```

重启 Claude Code 生效。

## 许可

MIT
