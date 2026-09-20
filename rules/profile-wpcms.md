# 项目档案：wpcms（团队规范层）

> 适用：wpcms 体系仓库（脚本按顶层 `wpcms*` 特征目录自动检测，检测到才加载本文件）。
> 语义：**叠加在通用规则（`rules/java.md` / `sql.md` / `frontend.md` / `general.md`）之上**，冲突时以本文件为准。
> 纯达梦但非 wpcms 的项目：把「达梦方言」节抄进仓库根 `.code-check-rules.md` 即可生效。

## Java 团队规范

| 规范 | 隐患 |
|------|------|
| Action 只调 Service | `*Action.java`（等同 Controller）直接注入/调用 Mapper/DAO → 违规，数据访问必须经 Service |
| SLF4J 1.x 日志 | warn/info/debug 超 2 参数用 `+` 拼接 → 应 `String.format()` |
| 达梦批量写入 | 全量 List 直接传 `batchInsert`/`batchUpdate` → 必须 `DmBatchUtil.batch(list, batchSize, chunk -> mapper.batchXxx(chunk))` 分包 |
| 批大小配置 | 批大小硬编码 → 应 `batch.properties` 的 `{业务}.batch.size` + `@Value("${xxx.batch.size:100}")` |
| 项目 id 条件 | 表里有项目 id 字段必带（总表分表一视同仁）：T_XY_DATA_FILE_FC（dg_file_project_id）、T_XY_CMS_CATALOG_FC（c_p_id）、文件分表 T_XY_CMS_FILE_FC_{XX}（dg_file_project_id）等，SELECT/UPDATE/DELETE 一律带项目 id 条件，按主键单查也不例外；分表有其他项目杂行（生产实测），表名隔离不豁免；豁免：全局维护型任务语句（无项目上下文，如全表兜底回填、队列扫描） |
| 文件查询用分表 | 文件查询必须用项目分表 `T_XY_CMS_FILE_FC_{XX}`（`PartTableUtil` 拼表名），禁止用总表 `T_XY_DATA_FILE_FC` 查（写入才总表+分表双写）——发现 SELECT 总表取文件数据即报 |
| 底稿文件双写 | 文件新增/修改/删除必须总表 `T_XY_DATA_FILE_FC` 与项目分表 `T_XY_CMS_FILE_{XX}` 双写/双删（事务包裹）；查询一律用分表（`PartTableUtil.getCmsFileTableNameByProjectId`） |
| 删目录先查分表 | 删目录表数据前必须先用项目分表确认该目录下无文件，禁止不做存在性检查直接删 |
| 新接口显式传 projectId | 前端调新接口必传 projectId，禁止服务端仅凭业务主键反查项目归属兜底 |
| JSP 改 React 不删老 | 只允许新增（新页面/新接口/新视图/新权限），动旧 JSP/旧 Controller 老方法/旧权限配置 → 违规 |

## 达梦方言兼容

| 检查点 | 说明 |
|--------|------|
| 分页 | 用达梦语法（OFFSET/FETCH 或 ROWNUM 包装），禁止 MySQL `LIMIT x,y` 直写 |
| 函数 | 禁 Oracle/MySQL 独有函数（IFNULL→COALESCE、GROUP_CONCAT→LISTAGG 核实、NVL 核实兼容性） |
| 保留字 | 关键字当列名/表名（`LEVEL`、`COMMENT`、`TYPE` 等）必须双引号或改名 |
| 日期 | TO_DATE/TO_CHAR 格式串、隐式转换 |
| 空串 | 达梦 '' 即 NULL（同 Oracle），别按 MySQL 语义判空串 |
| JDBC 参数上限 | PreparedStatement 参数上限 65535，硬编码不可配；批量必须分包（见上 DmBatchUtil） |

## 前端团队规范（JSP / React v3）

| 规范 | 隐患 |
|------|------|
| React 新页面三件套 | 缺 `App.tsx`/`main.tsx`/`index.html` 之一、缺 `vite.config.ts` 入口、缺 JSP 壳 |
| JSP 壳 | 未 include `tags.jsp`、未加载 `v3-common.css`、`<script src>` 未带 `?v=${jsVer}` |
| 权限白名单 | 新接口未在 module XML（module-sys.xml / module-process.xml）对应节点下追加 `<action>`；按钮权限未配 `<nodes>` 子节点 + JSP `token.haveCode` + React `window._perm` |
| 场景 A/B/C | 新独立页面建 `sysReact01xxxx` 节点；弹窗/iframe 复用已有业务权限编码；按钮权限建父编码+两位序号子节点 |
| 视图名 | Controller 视图路径与 JSP 文件路径不对应（`v3/{模块}/{功能}/index`） |
| 缩进 | JSP/TSX 未用 4 空格缩进、CSS 属性未分行；XML 用原文件缩进 |
| jsVer | 改 js 未刷 `version.jsp` 版本号（当天首个改动才刷，发版前再刷） |
| 前后端接口 | GET 用 `@RequestParam`、POST 用 `@RequestBody`；前端走 `$.rJsonPost`/`$.rPost`/`$.rGet` |
| 构建产物 | `js/v3/` 下是 vite 产物，改了源码没重新 `yarn build` → 页面不生效（提醒，不算隐患） |

## 配置 XML 团队规范

| 检查点 | 隐患 |
|--------|------|
| module XML 权限 | 新增接口漏配 `<action>` → 接口 403；`<nologin>`/`<authc>` 配错导致该登录的免登录了 |
| batch.properties | 新增批大小键与代码 `@Value` 不一致 |
| dbconfig.properties | jdbc.url 切环境未还原（本地 L24-26 / 内测 L21-23） |
