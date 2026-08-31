# JUNCTION 说明 — code-check

> 本目录已与全局 skill 目录建立 junction，**实时双向同步，改哪边都一样**。

## 指向关系

| 项 | 路径 |
|----|------|
| 全局路径（junction） | `C:\Users\Administrator\.claude\skills\code-check` |
| 实际目录（F 仓库） | `F:\idea-workspase-skills\code-check` |
| 创建日期 | 2026-08-06 |

## 家族子技能 junction（2026-08-06 新增）

code-check 为家族仓库，主技能目录下还有 7 个子技能目录，各自建立了独立 junction：

| 全局路径 | 指向（仓库子目录） |
|---|---|
| `C:\Users\Administrator\.claude\skills\code-check-today` | `F:\idea-workspase-skills\code-check\code-check-today` |
| `C:\Users\Administrator\.claude\skills\code-check-yesterday` | `F:\idea-workspase-skills\code-check\code-check-yesterday` |
| `C:\Users\Administrator\.claude\skills\code-check-from` | `F:\idea-workspase-skills\code-check\code-check-from` |
| `C:\Users\Administrator\.claude\skills\code-recheck-today` | `F:\idea-workspase-skills\code-check\code-recheck-today` |
| `C:\Users\Administrator\.claude\skills\code-recheck-yesterday` | `F:\idea-workspase-skills\code-check\code-recheck-yesterday` |
| `C:\Users\Administrator\.claude\skills\code-recheck-from` | `F:\idea-workspase-skills\code-check\code-recheck-from` |
| `C:\Users\Administrator\.claude\skills\code-check-history` | `F:\idea-workspase-skills\code-check\code-check-history` |
| 全局路径（junction，DSH） | `C:\Users\Administrator\.dsh\skills\code-check` |
| 全局路径（junction，Codex） | `C:\Users\Administrator\.codex\skills\code-check` |
| 全局路径（junction，Zcode） | `C:\Users\Administrator\.zcode\skills\code-check` |

子技能共享家族根 `scripts/code_check.py`（SKILL.md 用绝对路径引用），只维护 F 仓库即可。

## 说明

- 全局目录是指向 F 仓库（或其子目录）的 junction，两侧是**同一个目录**，不是副本。
- 修改 F 仓库，全局立刻生效；在全局路径下改文件，F 仓库同步变化。
- 日常维护只改 F 仓库（git 提交推送后全局自动一致），**不需要手动复制同步**。

## 检查是否正常

```bash
cmd /c dir "C:\Users\Administrator\.claude\skills" | findstr code-check
cmd /c dir "C:\Users\Administrator\.dsh\skills" | findstr code-check
cmd /c dir "C:\Users\Administrator\.codex\skills" | findstr code-check
cmd /c dir "C:\Users\Administrator\.zcode\skills" | findstr code-check
```

正常应显示主技能 + 7 个子技能共 8 个 `<JUNCTION>`，如 `<JUNCTION>  ...  code-check-today`。

## 回滚方法（恢复成独立副本）

```bat
rd "C:\Users\Administrator\.claude\skills\code-check"
rd "C:\Users\Administrator\.claude\skills\code-check-today"
rd "C:\Users\Administrator\.claude\skills\code-check-yesterday"
rd "C:\Users\Administrator\.claude\skills\code-check-from"
rd "C:\Users\Administrator\.claude\skills\code-recheck-today"
rd "C:\Users\Administrator\.claude\skills\code-recheck-yesterday"
rd "C:\Users\Administrator\.claude\skills\code-recheck-from"
rd "C:\Users\Administrator\.claude\skills\code-check-history"
rd "C:\Users\Administrator\.dsh\skills\code-check"
rd "C:\Users\Administrator\.codex\skills\code-check"
rd "C:\Users\Administrator\.zcode\skills\code-check"
```

> 注意：`rd` 不要加 `/s`，否则可能递归进 F 源目录。删除 junction 只删链接，不删 F 源目录。

## 本 skill 特殊差异

- 家族仓库含 1 个主技能 + 7 个子技能，共享一份脚本。
- 全局 skills 目录其他技能为独立链接（symlink/junction），本家族同样以 F 仓库为准。
