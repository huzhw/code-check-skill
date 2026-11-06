#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code-check 增量代码检查脚本
============================

职责：负责增量逻辑的机械部分（git 提取 + SQLite 去重 + 文件过滤 + 分组），AI 负责检查本身。

两张表，一张按 commit 去重（已提交），一张按文件内容 hash 去重（未提交工作区改动）。

文件过滤（确定性排除）：构建产物 / 依赖锁 / 二进制不进待查清单。
规则来源三处追加合并：脚本内置默认 + 技能目录 scripts/ignore-rules.txt + 被检查仓库根 .code-check-ignore。
文件全被过滤的 commit 单独列出，AI 无需检查直接 mark。

文件分组：待查文件按类型归组（Java / MyBatis XML/SQL / 配置 XML / JSP / 前端 TS/JS / SQL 脚本 / 配置文件 / 其他），
每组绑定专属通用规则文件（rules/*.md），AI 逐组检查、按组加载规则。

规则分层：rules/*.md 只放语言通用规则（任何项目可用）；团队/项目规范放 rules/profile-{key}.md，
脚本按仓库顶层特征目录检测项目档案（如 wpcms 系仓库），检测到才在输出里提示 AI 额外加载；
仓库根 .code-check-rules.md 可放任意项目自己的规范，存在即提示加载。其他项目零配置，只走通用层。

命令：
    python code_check.py scan [--author 姓名] [--json] [--since 时间] [--from 提交id] [--baseline] [--quiet]
                              [--all] [--system 系统名]
        扫描待检查改动（只列未检查的新项），输出精简清单（含过滤统计 + 检查分组）。
        --since：只查该时间之后的提交，如 today / 2026-08-06 / 3 days ago。
        --from：从该提交起（含）往后查，如 acd4fef8。
        --baseline：首次使用建基线，把当前全部历史 commit 标记为已检查，只查之后的新改动。
        --quiet：只输出「N commit / M 文件待查」一行，供快速判断。
        --all：级联扫描当前系统登记的仓（scripts/projects.json 项目 → 组 → 仓），逐仓输出，末尾汇总。
               范围 = --system 点名的系统；不点名就用 --repo-dir 所在的系统（两套系统各扫各的）。
               锚点不在任何登记系统里时报错退出，不静默扩大范围到别的系统。
        --system：级联时点名要扫的系统（projects[].project 的名字），跨系统才用。
    python code_check.py recheck [--author 姓名] [--since 时间] [--from 提交id] [--all] [--system 系统名]
        重查：列范围内所有项（含已查，全过滤的不列），每条带「已查 N 次」；check_count 随 mark 递增。
        --all / --system：同 scan。
    python code_check.py baseline [--author 姓名]
        刷库建基线：全部历史 commit 标记已检查（check_count=0 种子），不实际检查。
    python code_check.py mark [--commit <hash>]... [--file <路径>:<内容hash>]...
        标记已检查，写回 SQLite（重复标记次数 +1）。
    python code_check.py status
        查看库记录数与检查次数。
    python code_check.py report-path [--commit <hash>]... [--work] [--recheck]
                            [--report-root 目录] [--project-name 中文名]
        建报告目录并打印报告文件路径（日期_时间[_recheck][_共N个_最早~最新][+work]），AI 写内容。
        --report-root：报告落到指定目录（级联模式用「工作区根/.code-check-reports」），默认被检查仓自己。
        --project-name：项目中文名，进了报告文件名。
    python code_check.py migrate-repo-id
        把库里旧的 repo 键（remote 地址 / 绝对路径）改写成新的「remote#目录名」键，进度不丢。

库位置：被检查项目 git 根目录下 `.code-check.db`（该文件加入 .gitignore，不入库）。
repo 标识：remote 地址（origin 优先、gitlab 次之）+ "#" + 仓库目录名；无 remote 用绝对路径 + "#目录名"。
            拼目录名是为区分「同一 remote 的两个 clone」（如 lanxum-amisp-java 与 -zhongHai）。
            旧键仍可被识别（查询时新旧键一起查），migrate-repo-id 可把旧键改写为新键。
项目中文名：scripts/projects.json 里每个仓的 cn 字段（级联扫描的清单与报告文件名共用）。
"""

import argparse
import contextlib
import datetime
import fnmatch
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

DB_FILENAME = ".code-check.db"
REPORT_DIR_NAME = ".code-check-reports"

# 级联配置：技能目录 scripts/projects.json，登记所有项目的工作区根、排除项、分组与仓中文名。
# 格式仿 legacy-audit.projects.json，但扩展为多项目 + 仓级中文名（cn）。
PROJECTS_CFG_NAME = "projects.json"

# ---------------- 文件过滤（确定性排除，借鉴 OCR「精确文件选择」） ----------------

# 内置默认过滤：构建产物 / 依赖锁 / 二进制，纯噪音不进 AI 视野。只放业界通用规则，
# 项目专属产物目录（如 js/v3）放被检查仓库根 .code-check-ignore。
# 规则写法：无通配符 = 路径段；含 * ? = glob（对完整路径和文件名各试一次）。
DEFAULT_IGNORE_PATTERNS = [
    # 构建产物
    "*.min.js", "*.min.css", "*.map",
    "target", "build", "dist", "out",
    "node_modules", "*.tsbuildinfo",
    "*.class", "*.jar", "*.war",
    # 依赖锁文件
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    # 图片/字体
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg", "*.bmp", "*.webp",
    "*.woff", "*.woff2", "*.ttf", "*.eot", "*.otf",
    # 文档/压缩包/二进制
    "*.pdf", "*.doc", "*.docx", "*.xls", "*.xlsx", "*.ppt", "*.pptx",
    "*.zip", "*.rar", "*.7z", "*.gz", "*.tar",
    "*.db", "*.sqlite", "*.bin", "*.exe", "*.dll", "*.so",
    # 媒体
    "*.mp3", "*.mp4", "*.avi", "*.mov", "*.wav",
]

# skill 级配置（通用追加，与本文件同目录）；项目级配置 = 被检查仓库根 .code-check-ignore
IGNORE_CFG_SKILL = "ignore-rules.txt"
IGNORE_CFG_REPO = ".code-check-ignore"


def load_ignore_patterns(root, script_dir):
    """内置默认 + skill 级 ignore-rules.txt + 仓库级 .code-check-ignore，追加合并。"""
    patterns = list(DEFAULT_IGNORE_PATTERNS)
    for cfg in (os.path.join(script_dir, IGNORE_CFG_SKILL),
                os.path.join(root, IGNORE_CFG_REPO)):
        if os.path.isfile(cfg):
            with open(cfg, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
    return patterns


def match_ignore(path, patterns):
    """路径是否命中过滤规则。返回 (True, 规则) / (False, None)。

    无通配符规则按「路径段」匹配：pat 作为完整目录段出现在路径任意位置即命中，
    兼容 commit 路径带仓库子目录前缀（如 wpcms_v5.0_maven/wpcms/.../js/v3/_shared/...）。
    """
    p = path.replace("\\", "/")
    base = os.path.basename(p)
    wrapped = "/" + p.strip("/") + "/"
    for pat in patterns:
        pat = pat.replace("\\", "/").strip().strip("/")
        if not pat:
            continue
        if "*" not in pat and "?" not in pat:
            if "/" + pat + "/" in wrapped:
                return True, pat
        else:
            if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(base, pat):
                return True, pat
    return False, None


# ---------------- 文件分组（确定性归组，借鉴 OCR「智能文件打包」） ----------------
# 组序固定；每组绑定专属通用规则文件，AI 按组加载（general.md 恒加载，见 SKILL.md）。

GROUP_ORDER = ["Java", "MyBatis XML/SQL", "配置 XML", "JSP",
               "前端 TS/JS", "SQL 脚本", "配置文件", "其他"]


def classify_file(path):
    """按文件类型归组。返回 (组名, 专属规则文件相对 skill 目录的路径)。"""
    p = path.replace("\\", "/").lower()
    if p.endswith(".java"):
        return "Java", "rules/java.md"
    if p.endswith(".xml"):
        # dao / mapper 是 MyBatis Mapper 目录通用约定（lanxum 系 Mapper XML 放 src/main/java 的 mapper 包）
        if "/dao/" in p or "/mapper/" in p:
            return "MyBatis XML/SQL", "rules/sql.md"
        return "配置 XML", "rules/general.md"
    if p.endswith(".jsp"):
        return "JSP", "rules/frontend.md"
    if p.endswith((".ts", ".tsx", ".vue", ".js", ".jsx", ".html")):
        return "前端 TS/JS", "rules/frontend.md"
    if p.endswith(".sql"):
        return "SQL 脚本", "rules/sql.md"
    if p.endswith((".properties", ".yml", ".yaml", ".json")):
        return "配置文件", "rules/general.md"
    return "其他", "rules/general.md"


# ---------------- 项目档案检测（分层规则：通用层 + 项目层） ----------------
# 规则分两层：rules/*.md 只放语言通用规则；团队/项目规范放 rules/profile-{key}.md，
# 脚本按仓库特征目录确定性检测，检测到才提示 AI 加载——其他项目零配置，只走通用层。
# 另支持仓库根 .code-check-rules.md（项目自定义规范，存在就提示加载）。

REPO_RULES_FILENAME = ".code-check-rules.md"

# 项目规范文件：仓根存在即提示 AI 加载（团队规范常写在这里）。
# 顺序即优先级，命中第一个就够；前面的优先。
PROJECT_RULE_FILES = ("AGENTS.md", "CLAUDE.md")

PROJECT_PROFILES = [
    # key → 特征列表。特征含 "/" 按相对路径判断存在；不含 "/" 按顶层目录名前缀判断（目录）
    ("wpcms", ("wpcms",)),
    ("lanxum-java", ("lanxum-file-management",)),
    ("lanxum-react", ("download/latest.json",)),
]


def detect_project_profile(root):
    """按特征检测项目档案。返回 profile key 或 None。"""
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    for key, markers in PROJECT_PROFILES:
        for m in markers:
            if "/" in m:
                # 相对路径特征：文件或目录存在即命中
                if os.path.exists(os.path.join(root, m.replace("/", os.sep))):
                    return key
            else:
                # 顶层目录前缀特征：不区分大小写
                ml = m.lower()
                for e in entries:
                    if e.lower().startswith(ml) and os.path.isdir(os.path.join(root, e)):
                        return key
    return None


def repo_rules_file(root):
    """仓库根项目自定义规范文件，存在返回路径，否则 None。"""
    p = os.path.join(root, REPO_RULES_FILENAME)
    return p if os.path.isfile(p) else None


def project_rule_files(root):
    """仓根的团队规范文件（AGENTS.md / CLAUDE.md），返回存在的路径列表。"""
    found = []
    for name in PROJECT_RULE_FILES:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            found.append(p)
    return found


def group_files(files):
    """[(状态, 路径)] → [(组名, 规则文件, [(状态, 路径)])]，按 GROUP_ORDER 固定组序，组内路径排序。"""
    buckets = {}
    for status, path in files:
        g, rule = classify_file(path)
        buckets.setdefault((g, rule), []).append((status, path))
    groups = []
    for g in GROUP_ORDER:
        for (gname, rule) in sorted(buckets):
            if gname == g:
                items = sorted(buckets[(gname, rule)], key=lambda x: x[1])
                groups.append((gname, rule, items))
    return groups
SCHEMA = """
CREATE TABLE IF NOT EXISTS checked_commits (
    repo        TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    commit_time TEXT,
    author      TEXT,
    subject     TEXT,
    checked_at  TEXT,
    check_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(repo, commit_hash)
);
CREATE TABLE IF NOT EXISTS checked_files (
    repo         TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    checked_at   TEXT,
    check_count  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(repo, file_path, content_hash)
);
"""


# ---------------- git 封装 ----------------

def git(root, *args, check=True):
    """执行 git 命令，返回 stdout 文本。"""
    p = subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if check and p.returncode != 0:
        raise RuntimeError(
            "git 命令失败: git {}\n{}".format(" ".join(args), p.stderr.strip()))
    return p.stdout


def repo_root(start="."):
    out = git(start, "rev-parse", "--show-toplevel")
    return out.strip()


def repo_id(root):
    """仓库标识：remote 地址 + "#" + 仓库目录名；无 remote 用绝对路径 + "#目录名"。

    拼目录名是为区分「同一 remote 的两个 clone」——如 lanxum-amisp-java 与
    lanxum-amisp-java-zhongHai 都指向 lanxum-amisp.git，键相同会导致一仓查过另一仓被跳过。
    """
    url = ""
    for remote in ("origin", "gitlab"):
        out = git(root, "remote", "get-url", remote, check=False).strip()
        if out:
            url = out
            break
    if not url:
        url = os.path.normpath(os.path.abspath(root))
    return "{}#{}".format(url, os.path.basename(os.path.normpath(root)))


def legacy_repo_ids(root):
    """旧版 repo 键（只有 remote 地址 / 绝对路径，无 #目录名）。用于兼容旧库记录。

    绝对路径统一用 os.path.normpath 归一（Windows 下会变反斜杠），
    否则跟库里存的老键（也是 normpath 过的）比不上，兼容就失效了。
    """
    out = git(root, "remote", "get-url", "origin", check=False).strip()
    ids = []
    if out:
        ids.append(out)
    abs_path = os.path.normpath(os.path.abspath(root))
    if abs_path not in ids:
        ids.append(abs_path)
    return ids


def discover_workspaces(script_dir):
    """读 scripts/projects.json，返回登记的工作区根目录列表（存在的）。"""
    cfg = load_projects_cfg(script_dir)
    roots = []
    for proj in cfg.get("projects", []):
        root = proj.get("root")
        if not root:
            continue
        if os.path.isdir(root):
            roots.append(os.path.abspath(root))
        else:
            print("⚠ 工作区根不存在，已跳过: {}".format(root), file=sys.stderr)
    return roots


def load_projects_cfg(script_dir):
    """读技能目录 scripts/projects.json。读不到返回空配置（不报错，级联才提示）。"""
    path = os.path.join(script_dir, PROJECTS_CFG_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print("⚠ 级联配置读取失败: {} ({})".format(path, e), file=sys.stderr)
        return {}


def discover_repos(script_dir, cfg=None, project_names=None):
    """级联发现登记仓。

    数据源 = 技能目录 scripts/projects.json：projects[].root + exclude + groups[].repos[]。
    groups 里给了分组名与每个仓的中文名（cn），报告按分组分层、文件名带中文名。

    cfg：已读出的配置（不传就现读文件，避免同一命令里读两遍）。
    project_names：只收哪几套系统（None = 全部）。级联命令默认只传锚点那一套，
                   底稿与档案是两套独立系统，不互相串。
    返回 [{root, name, cn, dir, group, project, project_root}]，按项目→分组→仓顺序。
    """
    if cfg is None:
        cfg = load_projects_cfg(script_dir)
    repos = []
    for proj in cfg.get("projects", []):
        pname = proj.get("project") or ""
        if project_names is not None and pname not in project_names:
            continue
        root = proj.get("root")
        if not root or not os.path.isdir(root):
            continue
        root = os.path.abspath(root)
        exclude = set(proj.get("exclude") or [])
        for group in proj.get("groups", []):
            gname = group.get("name") or ""
            for item in group.get("repos", []):
                # 兼容两种写法：字符串（无中文名）或对象 {dir, cn}
                if isinstance(item, str):
                    d, cn = item, ""
                else:
                    d = item.get("dir") or item.get("repo") or ""
                    cn = item.get("cn") or item.get("name") or ""
                if not d or d in exclude:
                    continue
                path = os.path.join(root, d)
                if not os.path.isdir(path):
                    print("⚠ 配置里的仓不存在，已跳过: {}".format(path), file=sys.stderr)
                    continue
                repos.append({
                    "root": path,
                    "name": cn or d,       # 中文名优先，缺省用目录名
                    "cn": cn,
                    "dir": d,
                    "group": gname,
                    "project": pname,
                    "project_root": root,
                })
    return repos


def system_names(cfg):
    """配置里登记的系统名（projects[].project），用于报错提示可选项。"""
    return [p.get("project") for p in cfg.get("projects", []) if p.get("project")]


def anchor_path(repo_dir):
    """级联锚点：优先取 git 仓根（在仓的子目录里敲也能定位），不是 git 仓就用传入目录本身。"""
    try:
        return repo_root(repo_dir)
    except RuntimeError:
        return os.path.abspath(repo_dir)


def is_within(path, parent):
    """path 是否落在 parent 目录内（含相等）。两边都按 realpath + normcase 归一。

    归一为的是防符号链接、大小写差异把归属判错；不同盘符时 commonpath 抛 ValueError，
    按「不在里面」处理，不影响其他候选根继续比。
    """
    try:
        p = os.path.normcase(os.path.realpath(path))
        q = os.path.normcase(os.path.realpath(parent))
    except OSError:
        return False
    if p == q:
        return True
    try:
        return os.path.commonpath([p, q]) == q
    except ValueError:
        return False


def resolve_scope_project(cfg, anchor):
    """按锚点目录判定这次级联属于哪套系统。

    返回配置里的 project 条目；锚点不落在任何登记根下返回 None。
    多个登记根嵌套时取路径最长的那个（更具体的那套）。
    """
    best, best_len = None, -1
    for proj in cfg.get("projects", []):
        root = proj.get("root")
        if not root or not os.path.isdir(root):
            continue
        if not is_within(anchor, root):
            continue
        n = len(os.path.normcase(os.path.realpath(root)))
        if n > best_len:
            best, best_len = proj, n
    return best


def resolve_cascade_scope(args, cfg):
    """定级联范围：返回 (系统名, 错误提示)。错误提示非空时调用方直接退出，不扫任何仓。

    --system 点名优先；不点名就用 --repo-dir 所在系统。两套系统互不越界，
    锚点没有归属时不静默退回「全扫」，免得在无关目录里一敲就把别的系统扫了。
    """
    named = getattr(args, "system", None)
    names = system_names(cfg)
    if named:
        if named not in names:
            return None, "❌ 没有这个系统: {}（可选：{}）".format(
                named, " / ".join(names) if names else "配置为空")
        return named, ""
    anchor = anchor_path(args.repo_dir)
    proj = resolve_scope_project(cfg, anchor)
    if proj is None:
        return None, ("❌ 当前目录不属于任何登记系统：{}\n"
                      "   用 --system <系统名> 指定要扫哪一套（可选：{}）").format(
                          anchor, " / ".join(names) if names else "配置为空")
    return proj.get("project") or "", ""


# ---------------- 数据库 ----------------

def ensure_column(conn, table, column, ddl):
    """旧库缺列时 ALTER 补齐，幂等。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info({})".format(table))]
    if column not in cols:
        conn.execute("ALTER TABLE {} ADD COLUMN {}".format(table, ddl))
        conn.commit()


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    ensure_column(conn, "checked_commits", "check_count", "check_count INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "checked_files", "check_count", "check_count INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    return conn


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------- 已提交改动 ----------------

def normalize_since(since):
    """归一化 --since：git 对纯日期/today 解析有坑，自动补具体时间。

    Windows 下 git 把 `today`、`2026-08-06` 这类无时间值解析成"明天"，
    导致 --since 直接漏掉当天所有提交。补 " 00:00" 后恢复正确语义。
    """
    if not since:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since) or since in ("today", "yesterday", "tomorrow"):
        return since + " 00:00"
    return since


def list_commits(root, author=None, since=None, from_commit=None):
    """全部非 merge 提交。返回 [{hash, time, author, subject}]，最新在前。
    --from 时范围 = 该提交本身 + 之后的新提交（含）。"""
    args = ["log", "--no-merges", "--format=%H\t%aI\t%an\t%s"]
    if from_commit:
        # git log A..HEAD 排除 A 本身，故范围取 A 之后，再单独补 A
        args.append(from_commit + "..HEAD")
    elif since:
        args.append("--since=" + since)
    if author:
        args.append("--author=" + author)
    out = git(root, *args)
    commits = []
    for line in out.strip().splitlines():
        if not line:
            continue
        h, t, a, s = line.split("\t", 3)
        commits.append({"hash": h, "time": t, "author": a, "subject": s})
    if from_commit:
        # 补上 from_commit 本身（若 author 过滤则校验作者）
        me = git(root, "log", "-1", "--format=%H\t%aI\t%an\t%s", from_commit).strip()
        if me:
            h, t, a, s = me.split("\t", 3)
            if author is None or author.lower() in a.lower():
                commits.append({"hash": h, "time": t, "author": a, "subject": s})
    return commits


def repo_key_set(root):
    """本仓在库里的全部有效键：新键 + 旧键（兼容未迁移的老库记录）。"""
    keys = [repo_id(root)]
    for old in legacy_repo_ids(root):
        if old not in keys:
            keys.append(old)
    return keys


def resolve_hash(root, h):
    """把短 hash 补成完整 40 位 SHA。库里存的必须是完整 SHA，否则跟 git log 的完整 hash 对不上，
    会被误判成「没查过」而重复出现。取不到就原样返回。"""
    h = (h or "").strip()
    if len(h) == 40:
        return h
    try:
        out = git(root, "rev-parse", "--verify", "--quiet", h + "^{commit}", check=False).strip()
    except RuntimeError:
        out = ""
    return out or h


def filter_new_commits(conn, repo, commits, root=None):
    """去掉已检查的 commit。amend/rebase 改 hash 用 time+subject 二次兜底。

    repo 可以是单个键，也可以是键列表（新键 + 旧键，一起查）。
    """
    keys = repo if isinstance(repo, (list, tuple)) else [repo]
    rows = []
    for k in keys:
        rows += conn.execute(
            "SELECT commit_hash, commit_time, subject FROM checked_commits WHERE repo=?",
            (k,)).fetchall()
    checked_hash = {r[0] for r in rows}
    # 兼容库里存的是短 hash 的老记录：补全成完整 SHA 再比对
    if root:
        for h in [x for x in checked_hash if len(x) != 40]:
            full = resolve_hash(root, h)
            if full:
                checked_hash.add(full)
    checked_ts = {(r[1], r[2]) for r in rows}
    new = []
    for c in commits:
        if c["hash"] in checked_hash:
            continue
        if (c["time"], c["subject"]) in checked_ts:
            continue  # amend/rebase 后 hash 变了，但时间+说明相同 → 已查过
        new.append(c)
    return new


def commit_check_count(conn, repo, commit_hash):
    """某 commit 已检查次数（0=未检查）。repo 可为键或键列表。"""
    keys = repo if isinstance(repo, (list, tuple)) else [repo]
    best = 0
    for k in keys:
        row = conn.execute(
            "SELECT check_count FROM checked_commits WHERE repo=? AND commit_hash=?",
            (k, commit_hash)).fetchone()
        if row:
            best = max(best, row[0])
    return best


def file_check_count(conn, repo, path, content_hash):
    """某文件改动已检查次数（0=未检查）。repo 可为键或键列表。"""
    keys = repo if isinstance(repo, (list, tuple)) else [repo]
    best = 0
    for k in keys:
        row = conn.execute(
            "SELECT check_count FROM checked_files WHERE repo=? AND file_path=? AND content_hash=?",
            (k, path, content_hash)).fetchone()
        if row:
            best = max(best, row[0])
    return best


def do_baseline(conn, rid, root, author=None):
    """刷库建基线：全部历史 commit 标记已检查（check_count=0 种子，未实际检查）。返回 commit 数。"""
    commits = list_commits(root, author=author)
    stamp = now()
    for c in commits:
        conn.execute(
            "INSERT OR IGNORE INTO checked_commits"
            "(repo, commit_hash, commit_time, author, subject, checked_at, check_count)"
            "VALUES (?,?,?,?,?,?,0)",
            (rid, c["hash"], c["time"], c["author"], c["subject"], stamp))
    conn.commit()
    return len(commits)


def is_first_commit(root, commit):
    """首个提交无 parent。"""
    out = git(root, "rev-list", "--parents", "-n", "1", commit)
    parts = out.strip().split()
    return len(parts) == 1


def commit_changed_files(root, commit):
    """某个 commit 改动的文件列表。返回 [(状态, 路径)]，A/M/D/R。"""
    if is_first_commit(root, commit):
        out = git(root, "show", "--name-status", "--format=", commit)
    else:
        out = git(root, "diff", "--name-status", commit + "^", commit)
    files = []
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        if path:
            files.append((status, path))
    return files


# ---------------- 未提交工作区改动 ----------------

def working_changed_files(root):
    """工作区+暂存区改动（含未跟踪文件）。返回 [(状态, 路径)]。"""
    out = git(root, "status", "--porcelain", "-z")
    # -z 格式: "XY path\0"（X=暂存状态, Y=工作区状态），重命名追加旧路径段
    files = []
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        seg = parts[i]
        if len(seg) < 4:
            i += 1
            continue
        status = seg[:2].strip()
        path = seg[3:]
        if path.startswith('"'):
            # git 引号转义的文件名（含特殊字符），做基本还原
            path = unquote_git_path(path)
        if status in ("R", "C"):
            # 重命名/复制：-z 下新路径已在 path，下一段是旧路径，跳过
            i += 1
        files.append((status, path))
        i += 1
    return files


def unquote_git_path(p):
    """还原 git 引号转义路径（简化处理，只处理常见转义）。"""
    try:
        import ast
        return ast.literal_eval(p)
    except Exception:
        return p.strip('"')


def file_content_hash(root, path):
    """文件内容 sha256。文件不存在返回 None。"""
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return None
    h = hashlib.sha256()
    with open(full, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------- 命令 ----------------

def split_files_by_ignore(files, patterns):
    """[(状态, 路径)] 拆成 (有效文件, 被过滤文件)。"""
    valid, filtered = [], []
    for status, path in files:
        hit, _ = match_ignore(path, patterns)
        if hit:
            filtered.append((status, path))
        else:
            valid.append((status, path))
    return valid, filtered


def cmd_scan_all(args, mode):
    """级联扫描/重查一套系统登记的仓。

    数据源 = scripts/projects.json（projects[].root + exclude + groups[].repos[]）。
    范围：`--system` 点名的系统，否则 `--repo-dir` 所在的系统 —— 底稿、档案是两套独立系统，
    互不越界；锚点没有归属时报错退出，不静默退回全扫。
    报告落位：<项目根>/.code-check-reports/<分组名>/<日期_时间_中文名_...>.md
    —— 落位只跟仓所属项目有关，与在哪敲无关。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = load_projects_cfg(script_dir)
    if not cfg.get("projects"):
        print("❌ 级联配置为空或读不到：{}".format(
            os.path.join(script_dir, PROJECTS_CFG_NAME)), file=sys.stderr)
        return
    project, err = resolve_cascade_scope(args, cfg)
    if err:
        print(err, file=sys.stderr)
        return
    repos = discover_repos(script_dir, cfg=cfg, project_names=[project])
    if not repos:
        print("❌ 系统「{}」下没有可扫的仓，检查 {}".format(
            project, os.path.join(script_dir, PROJECTS_CFG_NAME)), file=sys.stderr)
        return

    print("=" * 60)
    print("code-check {}：系统「{}」，级联 {} 个仓".format(mode, project, len(repos)))
    print("=" * 60)
    print()

    total_commit = 0
    total_file = 0
    touched = []
    for repo in repos:
        root = repo["root"]
        name = repo["name"]
        # 报告落点：<项目根>/.code-check-reports/<分组名>/，只跟仓所属项目有关
        report_root = os.path.join(repo["project_root"], REPORT_DIR_NAME, repo["group"]) \
            if repo["group"] else os.path.join(repo["project_root"], REPORT_DIR_NAME)

        sub = argparse.Namespace(**vars(args))
        sub.repo_dir = root
        sub.title = "{}（{}）".format(name, repo["dir"])
        sub.suppress_mark = True
        sub.all = False
        sub.system = None   # 范围已在上层定好，逐仓跑时别再提示 --system 无效

        if mode == "扫描":
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_scan(sub)
            text = buf.getvalue()
            n_commit = 0
            n_file = 0
            for line in text.splitlines():
                if line.startswith("待查："):
                    m = re.search(r"待查：(\d+) 个 commit，(\d+) 个工作区文件", line)
                    if m:
                        n_commit, n_file = int(m.group(1)), int(m.group(2))
            total_commit += n_commit
            total_file += n_file
            if n_commit or n_file:
                touched.append((name, repo, n_commit, n_file, report_root))
                print(text.rstrip())
                print()
            else:
                print("✅ {}：没有新改动".format(name))
                print()
        else:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_recheck(sub)
            text = buf.getvalue()
            n_commit = 0
            for line in text.splitlines():
                if line.startswith("范围："):
                    m = re.search(r"范围：(\d+) 个 commit，(\d+) 个工作区文件", line)
                    if m:
                        n_commit = int(m.group(1))
            total_commit += n_commit
            if n_commit:
                touched.append((name, repo, n_commit, 0, report_root))
                print(text.rstrip())
                print()
            else:
                print("✅ {}：该范围内无改动".format(name))
                print()

    print("=" * 60)
    print("汇总（系统「{}」）：{} 个仓，有改动的 {} 个，共 {} 个 commit / {} 个工作区文件".format(
        project, len(repos), len(touched), total_commit, total_file))
    if touched:
        print()
        print("有改动的仓（报告落位：<项目根>/{}/<分组名>/）".format(REPORT_DIR_NAME))
        for name, repo, nc, nf, rr in touched:
            print("  · {}（{} / {}）→ {}".format(
                name, repo["project"], repo["group"], rr))
    print("=" * 60)


def warn_system_without_all(args):
    """--system 只配合 --all 使用；单仓模式按 --repo-dir 走，点了也没用，提示一下。"""
    if getattr(args, "system", None) and not args.all:
        print("⚠ --system 只跟 --all 一起用（单仓模式按 --repo-dir 走），本次已忽略",
              file=sys.stderr)


def cmd_scan(args):
    warn_system_without_all(args)
    if args.all:
        return cmd_scan_all(args, "扫描")
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    keys = repo_key_set(root)
    db_path = os.path.join(root, DB_FILENAME)
    conn = init_db(db_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    patterns = load_ignore_patterns(root, script_dir)

    if args.from_commit:
        try:
            git(root, "rev-parse", "--verify", "--quiet", args.from_commit)
        except RuntimeError:
            print("❌ 提交 id 不存在或非法: {}".format(args.from_commit), file=sys.stderr)
            return

    # 1. 已提交的新 commit
    commits = list_commits(root, author=args.author, since=normalize_since(args.since),
                           from_commit=args.from_commit)
    if args.baseline:
        # 首次使用建基线：全部历史 commit 标记为已检查（种子，未实际检查），
        # 之后 scan 只报基线后新出现的 commit + 未提交工作区改动
        n = do_baseline(conn, rid, root, args.author)
        if not args.json:
            print("📌 已建立基线：标记 {} 个历史 commit 为已检查（check_count=0 种子，未实际检查）".format(n))
    new_commits = filter_new_commits(conn, keys, commits, root)
    commit_detail = []
    skipped_commits = []  # 文件全被过滤的 commit，无需检查直接标记
    for c in new_commits:
        try:
            files = commit_changed_files(root, c["hash"])
        except RuntimeError:
            files = []
        valid, filtered = split_files_by_ignore(files, patterns)
        entry = {
            "hash": c["hash"], "time": c["time"], "author": c["author"],
            "subject": c["subject"], "files": valid, "filtered_files": filtered,
        }
        if valid:
            entry["groups"] = [[g, rule, items] for g, rule, items in group_files(valid)]
            commit_detail.append(entry)
        else:
            skipped_commits.append(entry)

    # 2. 未提交工作区改动
    try:
        wfiles = working_changed_files(root)
    except RuntimeError:
        wfiles = []
    work_detail = []
    n_filtered_work = 0
    for status, path in wfiles:
        if status in ("D",):
            continue  # 已删除文件无内容可查
        if os.path.basename(path) == DB_FILENAME:
            continue  # 跳过库文件自身
        hit, _ = match_ignore(path, patterns)
        if hit:
            n_filtered_work += 1
            continue
        h = file_content_hash(root, path)
        if h is None:
            continue
        rows = []
        for k in keys:
            rows += conn.execute(
                "SELECT 1 FROM checked_files WHERE repo=? AND file_path=? AND content_hash=?",
                (k, path, h)).fetchall()
        work_detail.append({"status": status, "path": path,
                            "content_hash": h, "checked_before": bool(rows)})

    result = {
        "repo": rid,
        "db_path": db_path,
        "project_profile": detect_project_profile(root),
        "repo_rules_file": repo_rules_file(root),
        "new_commits": commit_detail,
        "skipped_commits": skipped_commits,
        "working_files": work_detail,
        "filtered_working_files": n_filtered_work,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    n_new_commit = len(commit_detail)
    new_files = [f for f in work_detail if not f["checked_before"]]
    n_new_file = len(new_files)
    n_filtered_total = n_filtered_work + sum(len(c["filtered_files"]) for c in commit_detail) \
        + sum(len(c["filtered_files"]) for c in skipped_commits)

    if args.quiet:
        # 只出一行总结，供快速判断是否有待查项
        if n_new_commit == 0 and n_new_file == 0:
            print("✅ 没有新改动")
        else:
            print("待查：{} 个 commit，{} 个工作区文件".format(n_new_commit, n_new_file))
        return

    print("=" * 50)
    print("code-check 扫描：{}".format(getattr(args, "title", None) or os.path.basename(os.path.normpath(root))))
    print("=" * 50)
    print("待查：{} 个 commit，{} 个工作区文件".format(n_new_commit, n_new_file))
    if n_filtered_total:
        print("已过滤 {} 个文件（构建产物/二进制等，规则：内置 + scripts/ignore-rules.txt + 仓库 .code-check-ignore）"
              .format(n_filtered_total))
    profile = detect_project_profile(root)
    custom_rules = repo_rules_file(root)
    if profile:
        print("项目档案：{} → 额外加载 {}（团队规范，叠加在通用规则上）".format(
            profile, os.path.join(script_dir, "rules", "profile-{}.md".format(profile))))
    else:
        print("项目档案：无 → 仅用通用规则（团队规范可写仓库根 {}）".format(REPO_RULES_FILENAME))
    if custom_rules:
        print("项目自定义规范：{} → 加载".format(custom_rules))
    for pf in project_rule_files(root):
        print("项目团队规范：{} → 加载（该仓规则以它为准）".format(pf))
    if n_new_commit == 0 and n_new_file == 0:
        if skipped_commits:
            print("⚠ 有效改动为 0（仅剩全过滤 commit，见下），未检查不标记。")
        else:
            print("✅ 没有新改动，全部已检查过。")
        has_content = bool(skipped_commits)
    else:
        has_content = True
    if not has_content:
        return
    print()

    if commit_detail:
        print("【已提交的新 commit（按组检查）】")
        for c in commit_detail:
            short = c["hash"][:7]
            # c["time"] 形如 2026-08-06T18:47:34+08:00，取 MM-DD HH:MM
            t = c["time"][5:16].replace("T", " ")
            print("  [{short}] {time} {author}  {subject}".format(
                short=short, time=t, author=c["author"], subject=c["subject"]))
            for g, rule, items in c["groups"]:
                print("      □ {g}（{n} 文件）→ {rule}".format(g=g, n=len(items), rule=rule))
                for status, path in items:
                    print("          {status}  {path}".format(status=status, path=path))
            if c["filtered_files"]:
                for status, path in c["filtered_files"]:
                    print("      ⏭过滤  {path}".format(path=path))
        print()

    if skipped_commits:
        print("【全过滤 commit（文件全是构建产物/二进制，无需检查，直接标记）】")
        for c in skipped_commits:
            short = c["hash"][:7]
            t = c["time"][5:16].replace("T", " ")
            print("  [{short}] {time} {author}  {subject}".format(
                short=short, time=t, author=c["author"], subject=c["subject"]))
            for status, path in c["filtered_files"]:
                print("      ⏭过滤  {path}".format(path=path))
        print()

    if new_files:
        print("【未提交工作区改动（按组检查）】")
        groups = group_files([(f["status"], f["path"]) for f in new_files])
        for g, rule, items in groups:
            print("  □ {g}（{n} 文件）→ {rule}".format(g=g, n=len(items), rule=rule))
            for status, path in items:
                print("      [{status}] {path}".format(status=status, path=path))
        print()

    print("—— 检查完写回标记（AI 用）——")
    if getattr(args, "suppress_mark", False):
        return
    for c in commit_detail:
        print("  mark --commit {hash}".format(hash=c["hash"]))
    for c in skipped_commits:
        print("  mark --commit {hash}   # 全过滤，无需检查".format(hash=c["hash"]))
    for f in new_files:
        print("  mark --file {path}:{h}".format(path=f["path"], h=f["content_hash"]))


def cmd_mark(args):
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    keys = repo_key_set(root)
    db_path = os.path.join(root, DB_FILENAME)
    conn = init_db(db_path)

    n_commit = 0
    for h in args.commit:
        h = resolve_hash(root, h)  # 短 hash 补全成完整 SHA，保证与 git log 对得上
        # 回填 commit 元信息
        row = None
        try:
            out = git(root, "log", "-1", "--format=%aI\t%an\t%s", h)
            t, a, s = out.strip().split("\t", 2)
            row = (t, a, s)
        except RuntimeError:
            pass
        # 新键 + 旧键都要看：老库记录在旧键下，不能因换键就重复插入一条新的
        exists = False
        for k in keys:
            if conn.execute(
                    "SELECT 1 FROM checked_commits WHERE repo=? AND commit_hash=?",
                    (k, h)).fetchone():
                exists = True
                # 重复标记 → 次数 +1（重查），所有键下的同一 commit 同步递增
                conn.execute(
                    "UPDATE checked_commits SET check_count = check_count + 1, checked_at = ? "
                    "WHERE repo=? AND commit_hash=?",
                    (now(), k, h))
        if not exists:
            conn.execute(
                "INSERT INTO checked_commits"
                "(repo, commit_hash, commit_time, author, subject, checked_at, check_count)"
                "VALUES (?,?,?,?,?,?,1)",
                (rid, h, row[0] if row else "", row[1] if row else "",
                 row[2] if row else "", now()))
        n_commit += 1

    n_file = 0
    for spec in args.file:
        if ":" not in spec:
            print("⚠ 跳过非法 --file 参数: {}".format(spec), file=sys.stderr)
            continue
        path, h = spec.rsplit(":", 1)
        exists = False
        for k in keys:
            if conn.execute(
                    "SELECT 1 FROM checked_files WHERE repo=? AND file_path=? AND content_hash=?",
                    (k, path, h)).fetchone():
                exists = True
                conn.execute(
                    "UPDATE checked_files SET check_count = check_count + 1, checked_at = ? "
                    "WHERE repo=? AND file_path=? AND content_hash=?",
                    (now(), k, path, h))
        if not exists:
            conn.execute(
                "INSERT INTO checked_files"
                "(repo, file_path, content_hash, checked_at, check_count)"
                "VALUES (?,?,?,?,1)",
                (rid, path, h, now()))
        n_file += 1

    conn.commit()
    conn.close()
    print("✅ 已标记: {} 个 commit，{} 个文件".format(n_commit, n_file))


def cmd_recheck(args):
    """重查：列范围内所有项（含已查），每条带「已查 N 次」。未指定范围则报错提示。"""
    warn_system_without_all(args)
    if args.all:
        return cmd_scan_all(args, "重查")
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    keys = repo_key_set(root)
    db_path = os.path.join(root, DB_FILENAME)
    conn = init_db(db_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    patterns = load_ignore_patterns(root, script_dir)

    if not args.since and not args.from_commit:
        print("❌ recheck 需指定范围：--since 时间 或 --from 提交id（如 recheck --since today）",
              file=sys.stderr)
        return
    if args.from_commit:
        try:
            git(root, "rev-parse", "--verify", "--quiet", args.from_commit)
        except RuntimeError:
            print("❌ 提交 id 不存在或非法: {}".format(args.from_commit), file=sys.stderr)
            return

    commits = list_commits(root, author=args.author, since=normalize_since(args.since),
                           from_commit=args.from_commit)
    commit_rows = []
    for c in commits:
        try:
            files = commit_changed_files(root, c["hash"])
        except RuntimeError:
            files = []
        valid, filtered = split_files_by_ignore(files, patterns)
        if not valid:
            continue  # 全过滤 commit 重查也不列
        commit_rows.append({
            "hash": c["hash"], "time": c["time"], "author": c["author"],
            "subject": c["subject"], "files": valid, "filtered_files": filtered,
            "groups": group_files(valid),
            "count": commit_check_count(conn, keys, c["hash"]),
        })

    try:
        wfiles = working_changed_files(root)
    except RuntimeError:
        wfiles = []
    work_rows = []
    for status, path in wfiles:
        if status in ("D",):
            continue
        if os.path.basename(path) == DB_FILENAME:
            continue
        hit, _ = match_ignore(path, patterns)
        if hit:
            continue
        h = file_content_hash(root, path)
        if h is None:
            continue
        work_rows.append({"status": status, "path": path,
                          "content_hash": h, "count": file_check_count(conn, keys, path, h)})

    print("=" * 50)
    print("code-check 重查：{}".format(getattr(args, "title", None) or os.path.basename(os.path.normpath(root))))
    print("=" * 50)
    print("范围：{} 个 commit，{} 个工作区文件".format(len(commit_rows), len(work_rows)))
    profile = detect_project_profile(root)
    custom_rules = repo_rules_file(root)
    if profile:
        print("项目档案：{} → 额外加载 {}（团队规范，叠加在通用规则上）".format(
            profile, os.path.join(script_dir, "rules", "profile-{}.md".format(profile))))
    if custom_rules:
        print("项目自定义规范：{} → 加载".format(custom_rules))
    for pf in project_rule_files(root):
        print("项目团队规范：{} → 加载（该仓规则以它为准）".format(pf))
    if not commit_rows and not work_rows:
        print("该范围内无改动（或全被过滤）。")
        return
    print()

    if commit_rows:
        print("【commit（已查 N 次，0=新，按组检查）】")
        for c in commit_rows:
            t = c["time"][5:16].replace("T", " ")
            print("  [{short}] {time} {author}  {subject}  （已查 {cnt} 次）".format(
                short=c["hash"][:7], time=t, author=c["author"],
                subject=c["subject"], cnt=c["count"]))
            for g, rule, items in c["groups"]:
                print("      □ {g}（{n} 文件）→ {rule}".format(g=g, n=len(items), rule=rule))
                for status, path in items:
                    print("          {status}  {path}".format(status=status, path=path))
        print()

    if work_rows:
        print("【工作区文件（已查 N 次，0=新，按组检查）】")
        groups = group_files([(f["status"], f["path"]) for f in work_rows])
        for g, rule, items in groups:
            print("  □ {g}（{n} 文件）→ {rule}".format(g=g, n=len(items), rule=rule))
            for status, path in items:
                f = next(x for x in work_rows if x["path"] == path)
                print("      [{status}] {path}  （已查 {cnt} 次）".format(
                    status=status, path=path, cnt=f["count"]))
        print()

    print("—— 检查完写回标记（AI 用），重复 mark 次数 +1 ——")
    if getattr(args, "suppress_mark", False):
        return
    for c in commit_rows:
        print("  mark --commit {hash}".format(hash=c["hash"]))
    for f in work_rows:
        print("  mark --file {path}:{h}".format(path=f["path"], h=f["content_hash"]))


def cmd_baseline(args):
    """刷库建基线：全部历史 commit 标记已检查（check_count=0 种子），不实际检查。"""
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    db_path = os.path.join(root, DB_FILENAME)
    conn = init_db(db_path)
    n = do_baseline(conn, rid, root, args.author)
    print("📌 已建立基线：标记 {} 个历史 commit 为已检查（check_count=0 种子，未实际检查）".format(n))
    conn.close()


def cmd_status(args):
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    keys = repo_key_set(root)
    db_path = os.path.join(root, DB_FILENAME)
    if not os.path.exists(db_path):
        print("库不存在：{}（还没 scan 过）".format(db_path))
        return
    conn = init_db(db_path)
    n_commit = 0
    n_commit_real = 0
    n_commit_multi = 0
    n_file = 0
    n_file_real = 0
    for k in keys:
        n_commit += conn.execute(
            "SELECT COUNT(*) FROM checked_commits WHERE repo=?", (k,)).fetchone()[0]
        n_commit_real += conn.execute(
            "SELECT COUNT(*) FROM checked_commits WHERE repo=? AND check_count>=1",
            (k,)).fetchone()[0]
        n_commit_multi += conn.execute(
            "SELECT COUNT(*) FROM checked_commits WHERE repo=? AND check_count>=2",
            (k,)).fetchone()[0]
        n_file += conn.execute(
            "SELECT COUNT(*) FROM checked_files WHERE repo=?", (k,)).fetchone()[0]
        n_file_real += conn.execute(
            "SELECT COUNT(*) FROM checked_files WHERE repo=? AND check_count>=1",
            (k,)).fetchone()[0]
    last = conn.execute(
        "SELECT MAX(checked_at) FROM checked_commits").fetchone()[0]
    print("repo      :", rid)
    print("db        :", db_path)
    print("已标记 commit: {} 条（种子0次 {} 条，实际检查≥1次 {} 条）".format(
        n_commit, n_commit - n_commit_real, n_commit_real))
    print("  其中重复检查≥2次: {} 条".format(n_commit_multi))
    print("已标记文件改动: {} 条（实际检查≥1次 {} 条）".format(n_file, n_file_real))
    print("最近检查时间 :", last or "无")
    conn.close()


def commit_time(root, commit):
    """取某 commit 的提交时间（ISO 8601），供排序起止。取不到返回空串。"""
    try:
        return git(root, "log", "-1", "--format=%aI", commit).strip()
    except RuntimeError:
        return ""


def sanitize_name(name):
    """去掉文件名非法字符，中文名直接用。返回可安全落盘的名字。"""
    if not name:
        return ""
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip().strip(".")
    return name


def cmd_report_path(args):
    """建报告目录 + 算报告文件名，打印目标路径。AI 负责写报告内容。

    文件名：日期_时间[_项目中文名][_recheck][_共N个_最早~最新][+work].md，
            如 20260803_103000_底稿主仓_recheck_共24个_e455b7b~db5a92f.md。
    起止取本次 --commit 里按提交时间最早/最晚的短 hash（按时间排序，非传参顺序），体现「提交次数 + 范围」；
    单 commit 只列其 hash。撞名自动追加 _2/_3，避免同秒多次检查互相覆盖。无内容可报时（无 commit 也无工作区改动）报错不建。
    --report-root：报告落到指定目录（级联模式用「工作区根/.code-check-reports」）；不传则写被检查仓自己。
    --project-name：项目中文名，进了报告文件名。
    """
    root = repo_root(args.repo_dir)
    if not args.commit and not args.work:
        print("❌ 无可报告内容：需 --commit 或 --work（无新改动不写报告文件）", file=sys.stderr)
        return
    if args.report_root:
        report_dir = os.path.abspath(args.report_root)
    else:
        report_dir = os.path.join(root, REPORT_DIR_NAME)
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = ts
    proj = sanitize_name(getattr(args, "project_name", None))
    if proj:
        name += "_" + proj
    if args.recheck:
        name += "_recheck"
    if args.commit:
        # 按提交时间排序：最早在前、最新在后 → 起止范围
        ordered = sorted(args.commit, key=lambda h: commit_time(root, h))
        total = len(ordered)
        first = ordered[0][:7]
        last = ordered[-1][:7]
        if total == 1:
            name += "_{}".format(first)
        else:
            name += "_共{}个_{}~{}".format(total, first, last)
    if args.work:
        name += "+work"
    path = os.path.join(report_dir, name + ".md")
    base, ext = os.path.splitext(path)
    counter = 2
    while os.path.exists(path):
        path = "{}_{}{}".format(base, counter, ext)
        counter += 1
    print(path)


def cmd_migrate_repo_id(args):
    """把库里旧的 repo 键改写成新的「remote#目录名」键，进度不丢。

    旧键只有两种：remote 地址（origin 优先）、绝对路径。新键在其后加 "#目录名"。
    只在「旧键存在且新键不存在」时改写；两者都在则保持不动（避免计数错乱）。
    """
    root = repo_root(args.repo_dir)
    db_path = os.path.join(root, DB_FILENAME)
    if not os.path.exists(db_path):
        print("库不存在：{}（无需迁移）".format(db_path))
        return
    new_id = repo_id(root)
    conn = init_db(db_path)
    changed = 0
    for old in legacy_repo_ids(root):
        if old == new_id:
            continue
        # 新键下已有该 commit → 旧键的记录是重复，删掉旧的
        dup = conn.execute(
            "SELECT COUNT(*) FROM checked_commits WHERE repo=? AND commit_hash IN "
            "(SELECT commit_hash FROM checked_commits WHERE repo=?)",
            (new_id, old)).fetchone()[0]
        if dup:
            conn.execute("DELETE FROM checked_commits WHERE repo=?", (old,))
        else:
            conn.execute("UPDATE checked_commits SET repo=? WHERE repo=?", (new_id, old))
        changed += conn.total_changes and 1 or 0
        dupf = conn.execute(
            "SELECT COUNT(*) FROM checked_files WHERE repo=? AND file_path IN "
            "(SELECT file_path FROM checked_files WHERE repo=?)",
            (new_id, old)).fetchone()[0]
        if dupf:
            conn.execute("DELETE FROM checked_files WHERE repo=?", (old,))
        else:
            conn.execute("UPDATE checked_files SET repo=? WHERE repo=?", (new_id, old))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM checked_commits WHERE repo=?", (new_id,)).fetchone()[0]
    conn.close()
    print("✅ 已迁移：{} → {}（该仓现 {} 条 commit 记录）".format(root, new_id, total))


# ---------------- 入口 ----------------

def main():
    # Windows 控制台默认 GBK，强制 utf-8 输出，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    parser = argparse.ArgumentParser(
        prog="code_check.py",
        description="code-check 增量检查脚本：git 提取 + SQLite 去重")
    parser.add_argument(
        "--repo-dir", default=".",
        help="git 仓库目录，默认当前目录")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="扫描待检查改动（只列未检查的新项）")
    p_scan.add_argument("--author", default=None, help="只查该作者的提交")
    p_scan.add_argument("--since", default=None,
                        help="只查该时间之后的提交，如 today / 2026-08-06 / 3 days ago")
    p_scan.add_argument("--from", dest="from_commit", default=None,
                        help="从该提交起（含）往后查，如 acd4fef8")
    p_scan.add_argument("--json", action="store_true", help="JSON 输出")
    p_scan.add_argument("--quiet", action="store_true", help="只输出待查数量一行")
    p_scan.add_argument("--baseline", action="store_true",
                        help="首次使用建基线：把当前全部历史 commit 标记为已检查，"
                             "之后只查基线后新出现的 commit + 未提交工作区改动")
    p_scan.add_argument("--all", action="store_true",
                        help="级联扫描当前系统登记的仓（scripts/projects.json 项目 → 组 → 仓）；"
                             "范围 = --system 点名，未点名用 --repo-dir 所在系统")
    p_scan.add_argument("--system", default=None,
                        help="级联时点名系统（projects[].project 的名字，如 档案V6）；跨系统才用")

    p_recheck = sub.add_parser("recheck", help="重查：列范围内所有项（含已查）带次数")
    p_recheck.add_argument("--author", default=None, help="只查该作者的提交")
    p_recheck.add_argument("--since", default=None,
                           help="只查该时间之后的提交，如 today / 2026-08-06 / 3 days ago")
    p_recheck.add_argument("--from", dest="from_commit", default=None,
                           help="从该提交起（含）往后查，如 acd4fef8")
    p_recheck.add_argument("--all", action="store_true",
                           help="级联重查当前系统登记的仓；范围规则同 scan --all")
    p_recheck.add_argument("--system", default=None,
                           help="级联时点名系统（projects[].project 的名字，如 档案V6）；跨系统才用")

    p_baseline = sub.add_parser("baseline", help="刷库建基线：标记全部历史 commit，不检查")
    p_baseline.add_argument("--author", default=None, help="只标记该作者的提交")

    p_mark = sub.add_parser("mark", help="标记已检查（重复标记次数 +1）")
    p_mark.add_argument("--commit", action="append", default=[],
                        help="标记 commit 已检查，可多次")
    p_mark.add_argument("--file", action="append", default=[],
                        help="标记文件已检查，格式 路径:内容hash，可多次")

    sub.add_parser("status", help="查看库记录数与检查次数")
    sub.add_parser("migrate-repo-id",
                   help="把库里旧的 repo 键（remote 地址 / 绝对路径）改写成新键「remote#目录名」，"
                        "进度不丢")

    p_report = sub.add_parser("report-path", help="建报告目录并打印报告文件路径（AI 写内容）")
    p_report.add_argument("--commit", action="append", default=[],
                          help="本次检查的 commit 短hash，可多次")
    p_report.add_argument("--work", action="store_true", help="本次含工作区改动")
    p_report.add_argument("--recheck", action="store_true", help="重查报告，文件名加 recheck_ 前缀")
    p_report.add_argument("--report-root", default=None,
                          help="报告落到指定目录（级联模式用「工作区根/.code-check-reports」），"
                               "默认写被检查仓自己")
    p_report.add_argument("--project-name", default=None,
                          help="项目中文名，进了报告文件名")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "recheck":
        cmd_recheck(args)
    elif args.command == "baseline":
        cmd_baseline(args)
    elif args.command == "mark":
        cmd_mark(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "report-path":
        cmd_report_path(args)
    elif args.command == "migrate-repo-id":
        cmd_migrate_repo_id(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
