---
name: code-check-today
description: 只检查今天提交的新改动（首查）。范围=今天提交+未提交工作区，当天工作增量检查；无新改动则提示。触发词：检查今天、today、code-check-today。
author: 胡志伟
motto: "增量检查不是偷懒——查过的记下来，只碰新代码。"
---

# code-check-today — 检查今天的新改动

## 作用
只检查**今天提交的新 commit + 未提交工作区改动**（首查）。当天工作增量检查用，范围限定在今天的提交，不碰历史。

## 流程
0. **先读主技能**：`Read F:/idea-workspase-skills/code-check/SKILL.md`，拿 10 维检查清单、报告格式、严禁清单后再开始
1. **扫描**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> scan --since today`
2. **逐项检查**：对列出的每个 commit / 工作区文件，按家族主技能 `code-check` 的检查清单逐维度检查（性能/注入/健壮性/并发/方言兼容/前端/事务/破坏性操作/敏感信息/路径安全），每条隐患给出 文件:行号 + 问题 + 改法，拿不准标「待确认」
3. **报告**：结论置顶、按「⚠️需处理 / 🔶待确认 / ✅通过」分组
4. **标记写回**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> mark --commit <hash> ... --file <路径>:<内容hash> ...`
5. **复查**：`scan --since today --quiet` 应显示无待查

## 说明
- 只列未检查的新项；今天已检查过的，二次检查用 `/code-recheck-today`
- 无新改动 → 直接报告"今天没有新改动"
- 标记后次数=1；要再查一遍走 recheck，次数+1
