---
name: code-check-history
description: 刷库建基线：把当前全部历史 commit 标记为已检查，不实际检查。老仓库（几千 commit）首次用一次，之后 code-check 只查新改动。触发词：刷历史、建基线、baseline、code-check-history。
author: 胡志伟
motto: "历史一次性归档，只碰新代码。"
---

# code-check-history — 刷库建基线

## 作用
把仓库**全部历史 commit 标记为已检查**（`check_count=0` 种子，未实际检查），**只刷库不检查**。老仓库（几百/几千 commit）首次用 code-check 前跑一次，之后增量就只查新改动，不会把历史全列为待查。

## 流程
1. **刷库**：`python "F:/idea-workspase-skills/code-check/scripts/code_check.py" --repo-dir <仓库> baseline`
2. 输出「📌 已建立基线：标记 N 个历史 commit 为已检查」即完成，**不做实际检查**
3. 可 `status` 确认种子数（check_count=0 的条数）

## 说明
- **幂等**：已标记的不重复标记，可反复跑
- 建基线后，`/code-check` 只列基线之后的新改动
- 基线项 check_count=0（种子、未实际检查），真正检查过的项 mark 后 ≥1
- 只对当前仓库生效（每个仓库各自的 .code-check.db）
