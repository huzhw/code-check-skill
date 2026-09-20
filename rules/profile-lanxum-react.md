# 项目档案：lanxum-react（蓝信 AMISP 前端）

> 适用：lanxum-amisp-react 仓库（脚本按顶层 `download/latest.json` 自动检测）。
> 语义：叠加在通用规则（`rules/frontend.md` / `general.md` 等）之上，冲突以本文件为准。
> 完整规范见仓库 `docs/`（AJAX/PERMISSION/ROUTE/PAGE/MODAL_PAGE/QIANKUN 等），本文件只放检查判定用的浓缩规则。

## 技术栈特征

JSX（非 TS）+ ra-lib 体系（@ra-lib/ajax、config/ajaxHoc 装饰器、hooks `props.ajax.useGet`）+ qiankun 微前端。

## 项目高频坑 🔴

| 检查点 | 隐患 |
|--------|------|
| ajax 传参与后端绑定 | ra-lib 约定：`get`/`del` = query string，`post`/`put`/`patch` = body。前端 body 传参对接后端 `@RequestParam`（收 query）必挂——改接口传参方式时两端必须核对（真实踩坑：编辑弹窗回显 5 文件链路） |
| 掩码回显写库 🔴 | 列表有掩码/脱敏展示，编辑弹窗回显必须**后端查库取原值**，禁止拿列表行数据（掩码值）直接回填保存——保存掩码值写库即报 |
| 客户定制包隔离 | `src/pages/**/customerLjzxt`（陆家嘴）、`customerXzxt`（西藏）、`customerJsxt`（江苏信托）、`customerPpp`（政企）为客户定制目录——改通用页面的 diff 涉及这些目录时，确认是否该走对应定制包而不是改通用代码；反之改定制包别顺手动通用页面 |
| 权限码 | `hasPermission('CODE')` / `<Permission code="CODE">` 硬编码，须语义化、唯一；新功能权限码前后端（SYS_MODULE 菜单树）是否配齐——漏配即提醒 |

## 发版与工程

| 检查点 | 说明 |
|--------|------|
| 下载器发版件 | `download/` 下 exe/zip 已被过滤，`latest.json` 版本号改动属正常发版记录，无需深查；版本号格式 v3.x.x 递增 |
| 发版脚本 | `scripts/` 下 shell 改动按 general 规则查（破坏性命令/路径） |
| 102 部署 | 前后端均走 Jenkins 手动构建，本地不部署——检查时不涉及，仅背景 |
