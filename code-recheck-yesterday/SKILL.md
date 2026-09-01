---
name: code-recheck-yesterday
description: 重查昨天检查过的改动（二次/多次检查）。列出昨天已检查的提交/文件带检查次数，AI 重新分析再标记次数+1；昨天没查过则等同 code-check-yesterday 首查。触发词：昨天重查、recheck-yesterday、code-recheck-yesterday。
author: 胡志伟
motto: "重要需求多查几轮，查过的次数记下来。"
---

# code-recheck-yesterday — 重查昨天

## 作用
对**昨天范围内的改动二次/多次检查**：列出昨天所有提交/文件（**含已检查的**），每条带「已查 N 次」，AI 重新读 diff 再分析一遍，再次 `mark` 次数 +1。怕一次检查不准时用。

**自适应**：昨天还没检查过任何东西 → 等同 `/code-check-yesterday` 做首查，不空转。

## 流程
0. **先读主技能**：`Read F:/idea-workspase-skills/code-check/SKILL.md`，拿 11 维检查清单、报告格式、严禁清单后再开始
1. **重查扫描**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> recheck --since yesterday`
   （列出范围内所有 commit/文件，已查 N 次 / 0=新）
2. **逐项重新分析**：对列出的**每一项**（含已查过的）重新读改动、按家族主技能 `code-check` 的检查清单再查一遍，重点找上次漏掉的问题
3. **报告**：结论置顶、按「⚠️需处理 / 🔶待确认 / ✅通过」分组
4. **再次标记**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> mark --commit <hash> ... --file <路径>:<内容hash> ...`
   （重复标记 → 次数 +1）
5. **复查**：`recheck --since yesterday` 确认次数已递增

## 说明
- 检查次数可在 `status` 查看（已查≥2 次的项）
- 想查"从某提交起"的二次检查用 `/code-recheck-from`
