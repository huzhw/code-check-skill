---
name: code-check-from
description: 从指定提交起检查新改动（含该提交，首查）。用法 /code-check-from <commitId>，查该提交本身+之后的新提交+未提交工作区改动。触发词：检查某提交起、from、code-check-from。
author: 胡志伟
motto: "增量检查不是偷懒——查过的记下来，只碰新代码。"
---

# code-check-from — 从某提交起检查

## 作用
检查**指定提交（含该提交）之后的新 commit + 未提交工作区改动**（首查）。用于"从这个提交往后都查一遍"的范围检查。

## 用法
`/code-check-from <commitId>`，例如 `/code-check-from acd4fef8`（commitId 是 git 提交短码/全码）。

## 流程
0. **先读主技能**：`Read F:/idea-workspase-skills/code-check/SKILL.md`，拿 10 维检查清单、报告格式、严禁清单后再开始
1. **取 commitId**：从用户消息里取提交 id 参数
2. **扫描**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> scan --from <commitId>`
   （`--from` 含该提交本身 + 之后的新提交 + 工作区改动；id 非法会报错）
3. **逐项检查**：按家族主技能 `code-check` 的检查清单逐维度检查，每条隐患给出 文件:行号 + 问题 + 改法，拿不准标「待确认」
4. **报告**：结论置顶、按「⚠️需处理 / 🔶待确认 / ✅通过」分组
5. **标记写回**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> mark --commit <hash> ... --file <路径>:<内容hash> ...`
6. **复查**：`scan --from <commitId> --quiet` 应显示无待查

## 说明
- 该范围内已检查过的项不重复列（首查语义）；二次检查用 `/code-recheck-from`
