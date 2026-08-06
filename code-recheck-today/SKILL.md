---
name: code-recheck-today
description: 重查今天检查过的改动（二次/多次检查）。列出今天已检查的提交/文件带检查次数，AI 重新分析再标记次数+1；今天没查过则等同 code-check-today 首查。触发词：再查一遍、重查今天、recheck、code-recheck-today。
author: 胡志伟
motto: "重要需求多查几轮，查过的次数记下来。"
---

# code-recheck-today — 重查今天

## 作用
对**今天范围内的改动二次/多次检查**：列出今天所有提交/文件（**含已检查的**），每条带「已查 N 次」，AI 重新读 diff 再分析一遍，再次 `mark` 次数 +1。怕一次检查不准时用。

**自适应**：今天还没检查过任何东西 → 等同 `/code-check-today` 做首查，不空转。

## 流程
0. **先读主技能**：`Read F:/idea-workspase-skills/code-check/SKILL.md`，拿 10 维检查清单、报告格式、严禁清单后再开始
1. **重查扫描**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> recheck --since today`
   （列出范围内所有 commit/文件，已查 N 次 / 0=新）
2. **逐项重新分析**：对列出的**每一项**（含已查过的）重新读改动、按家族主技能 `code-check` 的检查清单再查一遍，重点找上次漏掉的问题
3. **报告**：结论置顶、按「⚠️需处理 / 🔶待确认 / ✅通过」分组
4. **再次标记**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> mark --commit <hash> ... --file <路径>:<内容hash> ...`
   （重复标记 → 次数 +1）
5. **复查**：`recheck --since today` 确认次数已递增

## 说明
- 检查次数可在 `status` 查看（已查≥2 次的项）
- 想查"从某提交起"的二次检查用 `/code-recheck-from`
