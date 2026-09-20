---
name: code-check-yesterday
description: 只检查昨天提交的新改动（首查）。范围=昨天提交+未提交工作区，前一天工作增量检查；无新改动则提示。触发词：昨天检查、yesterday、code-check-yesterday。
author: 胡志伟
motto: "增量检查不是偷懒——查过的记下来，只碰新代码。"
---

# code-check-yesterday — 检查昨天的新改动

## 作用
只检查**昨天提交的新 commit + 未提交工作区改动**（首查）。前一天工作增量检查用，范围限定在昨天的提交，不碰更早历史。

## 流程
0. **先读主技能**：`Read F:/idea-workspase-skills/code-check/SKILL.md`，拿分组检查规则（rules/*.md 加载方式）、报告格式、严禁清单后再开始
1. **扫描**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> scan --since yesterday`
2. **逐组检查**：对列出的每个 commit / 工作区文件，按 scan 输出的分组逐组过，每组先 Read 对应 `{技能目录}/rules/xxx.md`（rules/general.md 恒加载），每条隐患给出 文件:行号 + 锚点片段 + 问题 + 改法，拿不准标「待确认」
3. **报告**：结论置顶、按「⚠️需处理 / 🔶待确认 / ✅通过」分组
4. **标记写回**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> mark --commit <hash> ... --file <路径>:<内容hash> ...`
5. **复查**：`scan --since yesterday --quiet` 应显示无待查

## 说明
- 只列未检查的新项；昨天已检查过的，二次检查用 `/code-recheck-yesterday`
- 无新改动 → 直接报告"昨天没有新改动"
- 标记后次数=1；要再查一遍走 recheck，次数+1
