# JUNCTION 说明 — code-check

> 本目录已与全局 skill 目录建立 junction，**实时双向同步，改哪边都一样**。

## 指向关系

| 项 | 路径 |
|----|------|
| 全局路径（junction） | `C:\Users\Administrator\.claude\skills\code-check` |
| 实际目录（F 仓库） | `F:\idea-workspase-skills\code-check` |
| 创建日期 | 2026-08-06 |

## 说明

- 全局目录是指向 F 仓库的 junction，两侧是**同一个目录**，不是副本。
- 修改 F 仓库，全局立刻生效；在全局路径下改文件，F 仓库同步变化。
- 日常维护只改 F 仓库（git 提交推送后全局自动一致），**不需要手动复制同步**。

## 检查是否正常

```bash
cmd /c dir "C:\Users\Administrator\.claude\skills" | findstr code-check
```

正常应显示 `<JUNCTION>  ...  code-check`。

## 回滚方法（恢复成独立副本）

```bat
rd "C:\Users\Administrator\.claude\skills\code-check"
```

> 注意：`rd` 不要加 `/s`，否则可能递归进 F 源目录。删除 junction 只删链接，不删 F 源目录。

## 本 skill 特殊差异

- 全新 skill，无历史全局版本，无备份。
- 全局 skills 目录其他技能为独立链接（symlink/junction），本技能同样以 F 仓库为准。
