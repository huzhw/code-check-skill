# 项目档案：lanxum-java（蓝信 AMISP 后端）

> 适用：lanxum-amisp-java 仓库（脚本按顶层 `lanxum-file-management` 目录自动检测）。
> 语义：叠加在通用规则（`rules/java.md` / `sql.md` / `frontend.md` / `general.md`）之上，冲突以本文件为准。
> 完整规范见仓库 `CLAUDE.md` 与 `memory/`（索引规则、任务骨架、依赖清单），本文件只放检查判定用的浓缩规则。

## 跨库 SQL 兼容 🔴（本项目最高危区）

四库同跑：达梦 DM8 / MySQL 8.0 / Oracle / SQL Server（dynamic-datasource `@DS` 切换，Druid 连接池）。
SQL 改动必须过以下核对，命中任一即报：

| 检查点 | 隐患 |
|--------|------|
| 单库函数 | `TO_DATE`/`NVL`/`LISTAGG` 等 MySQL 不存在，`IFNULL`/`GROUP_CONCAT` 等 Oracle/达梦不存在——跨库函数必须写兼容分支或用通用写法 |
| 数值排序 | `ORDER BY` 对编号列转数值（`CAST`/`TO_NUMBER`）必须兼容非数字、带空格编号，各库转换失败行为不同 |
| NULL 排序 | 各库 NULL 在前在后不一致，需 `NULLS LAST` 等显式声明并核实各库支持 |
| 分页/方言 | 改动 SQL 落在哪个库要确认（看 `@DS`/yml 数据源），不能按单一库方言写死 |

## 模块依赖白名单 🔴（import 越界必报）

- `lanxum-amisp` ↔ `lanxum-system` **互不引用**；`lanxum-file` ↔ `lanxum-file-conver-pdf` **互不引用**
- 跨模块通信走 Feign（接口定义在 `*-api` 模块），出现编译期依赖即报
- 共用工具放 `lanxum-common`；完整依赖允许表见仓库 CLAUDE.md

## Java 编码规约

| 检查点 | 隐患 |
|--------|------|
| 异常日志 | catch 块 `log.error(...)` 后必须调 `ExceptionLogger.logException(e)`（自动打全栈），只 error 不调即报 |
| 定时任务 | 新 `@Scheduled` 任务缺开关（`@ConditionalOnExpression`）、缺 `LockManager` 分布式锁、锁失败仍写 DB 任务日志、缺 `ScheduledTaskLogUtils` 三段式日志——骨架见 `memory/定时任务编写规则-通用骨架.md` |
| 权限编码 | 接口方法 `@LanxumRequiresPermission` 编码死码/漏配（对照 SYS_MODULE 菜单树；深查走仓库 `skills/permission-code-check.md`） |
| Java 8 | var/文本块/Record 等 9+ 语法即报 |

## Mapper 与配置

| 检查点 | 隐患 |
|--------|------|
| Mapper XML 位置 | 在 `src/main/java` 下 `mapper` 包（POM 已配资源包含），不在 resources——分组已按 `/mapper/` 归 SQL 组 |
| ORM 特征 | MyBatis-Plus 3.5.x（`BaseMapper`/Wrapper）、ShardingSphere 分库分表——逻辑 SQL 与物理表可能不一致，核对分片键 |
| yml 配置 | 新参数只写了部分 `application-*.yml` 即报（必须补齐该模块 resources 下全部 profile）；基座 `application.yml` 禁止新增内容；注释必须在参数上方、行尾注释即报 |
| 索引 DDL | diff 出现 CREATE/DROP INDEX 或建表：102 Oracle 主库 + 102 MySQL 备库两边都要执行，并同步 `memory/索引基线-AMISP.md`——漏一边即提醒 |
