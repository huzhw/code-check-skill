---
name: code-recheck-from
description: 从指定提交起重查改动（二次/多次检查）。用法 /code-recheck-from <commitId>，列出该提交（含）之后所有改动带检查次数，AI 重新分析再标记次数+1；该范围没查过则等同首查。触发词：重查某提交起、recheck-from、code-recheck-from。
author: 胡志伟
motto: "重要需求多查几轮，查过的次数记下来。"
---

# code-recheck-from — 从某提交起重查

## 作用
对**从指定提交起（含）的范围**二次/多次检查：列出范围内所有 commit（**含已检查的**）带「已查 N 次」，AI 重新读 diff 再分析一遍，再次 `mark` 次数 +1。某段提交改动重要、想再查一遍时用。

**自适应**：该范围还没检查过 → 等同 `/code-check-from` 做首查，不空转。

## 用法
`/code-recheck-from <commitId>`，例如 `/code-recheck-from acd4fef8`。

## 流程
0. **先读主技能**：`Read F:/idea-workspase-skills/code-check/SKILL.md`，拿 11 维检查清单、报告格式、严禁清单后再开始
1. **取 commitId**：从用户消息里取提交 id 参数
2. **重查扫描**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> recheck --from <commitId>`
   （含该提交 + 之后的新提交 + 工作区，已查 N 次 / 0=新）
3. **逐项重新分析**：对列出的每一项（含已查过的）重新读改动、按家族主技能 `code-check` 的检查清单再查一遍
4. **报告**：结论置顶、按「⚠️需处理 / 🔶待确认 / ✅通过」分组
5. **再次标记**：`mark --commit <hash> ... --file <路径>:<内容hash> ...`（次数 +1）
6. **复查**：`recheck --from <commitId>` 确认次数已递增

## 说明
- `--from` 语义含该提交本身；commitId 非法会报错提示
- 想查"今天范围"的二次检查用 `/code-recheck-today`
