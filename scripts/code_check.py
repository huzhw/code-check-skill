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
        扫描待检查改动（只列未检查的新项），输出精简清单（含过滤统计 + 检查分组）。
        --since：只查该时间之后的提交，如 today / 2026-08-06 / 3 days ago。
        --from：从该提交起（含）往后查，如 acd4fef8。
        --baseline：首次使用建基线，把当前全部历史 commit 标记为已检查，只查之后的新改动。
        --quiet：只输出「N commit / M 文件待查」一行，供快速判断。
    python code_check.py recheck [--author 姓名] [--since 时间] [--from 提交id]
        重查：列范围内所有项（含已查，全过滤的不列），每条带「已查 N 次」；check_count 随 mark 递增。
    python code_check.py baseline [--author 姓名]
        刷库建基线：全部历史 commit 标记已检查（check_count=0 种子），不实际检查。
    python code_check.py mark [--commit <hash>]... [--file <路径>:<内容hash>]...
        标记已检查，写回 SQLite（重复标记次数 +1）。
    python code_check.py status
        查看库记录数与检查次数。
    python code_check.py report-path [--commit <hash>]... [--work] [--recheck]
        建报告目录 `.code-check-reports/` 并打印报告文件路径（日期_时间[_recheck][_共N个_最早~最新][+work]），AI 写内容。

库位置：被检查项目 git 根目录下 `.code-check.db`（该文件加入 .gitignore，不入库）。
repo 标识：git remote origin 地址优先，无 remote 用绝对路径。
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys

DB_FILENAME = ".code-check.db"
REPORT_DIR_NAME = ".code-check-reports"

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
    """仓库标识：remote 地址优先，无 remote 用绝对路径。"""
    out = git(root, "remote", "get-url", "origin", check=False)
    url = out.strip()
    return url if url else os.path.abspath(root)


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


def filter_new_commits(conn, repo, commits):
    """去掉已检查的 commit。amend/rebase 改 hash 用 time+subject 二次兜底。"""
    rows = conn.execute(
        "SELECT commit_hash, commit_time, subject FROM checked_commits WHERE repo=?",
        (repo,)).fetchall()
    checked_hash = {r[0] for r in rows}
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
    """某 commit 已检查次数（0=未检查）。"""
    row = conn.execute(
        "SELECT check_count FROM checked_commits WHERE repo=? AND commit_hash=?",
        (repo, commit_hash)).fetchone()
    return row[0] if row else 0


def file_check_count(conn, repo, path, content_hash):
    """某文件改动已检查次数（0=未检查）。"""
    row = conn.execute(
        "SELECT check_count FROM checked_files WHERE repo=? AND file_path=? AND content_hash=?",
        (repo, path, content_hash)).fetchone()
    return row[0] if row else 0


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


def cmd_scan(args):
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
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
    new_commits = filter_new_commits(conn, rid, commits)
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
        rows = conn.execute(
            "SELECT 1 FROM checked_files WHERE repo=? AND file_path=? AND content_hash=?",
            (rid, path, h)).fetchall()
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
    print("code-check 扫描：{}".format(os.path.basename(os.path.normpath(root))))
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
    for c in commit_detail:
        print("  mark --commit {hash}".format(hash=c["hash"]))
    for c in skipped_commits:
        print("  mark --commit {hash}   # 全过滤，无需检查".format(hash=c["hash"]))
    for f in new_files:
        print("  mark --file {path}:{h}".format(path=f["path"], h=f["content_hash"]))


def cmd_mark(args):
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    db_path = os.path.join(root, DB_FILENAME)
    conn = init_db(db_path)

    n_commit = 0
    for h in args.commit:
        # 回填 commit 元信息
        row = None
        try:
            out = git(root, "log", "-1", "--format=%aI\t%an\t%s", h)
            t, a, s = out.strip().split("\t", 2)
            row = (t, a, s)
        except RuntimeError:
            pass
        exists = conn.execute(
            "SELECT 1 FROM checked_commits WHERE repo=? AND commit_hash=?",
            (rid, h)).fetchone()
        if exists:
            # 重复标记 → 次数 +1（重查）
            conn.execute(
                "UPDATE checked_commits SET check_count = check_count + 1, checked_at = ? "
                "WHERE repo=? AND commit_hash=?",
                (now(), rid, h))
        else:
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
        exists = conn.execute(
            "SELECT 1 FROM checked_files WHERE repo=? AND file_path=? AND content_hash=?",
            (rid, path, h)).fetchone()
        if exists:
            conn.execute(
                "UPDATE checked_files SET check_count = check_count + 1, checked_at = ? "
                "WHERE repo=? AND file_path=? AND content_hash=?",
                (now(), rid, path, h))
        else:
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
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
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
            "count": commit_check_count(conn, rid, c["hash"]),
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
                          "content_hash": h, "count": file_check_count(conn, rid, path, h)})

    print("=" * 50)
    print("code-check 重查：{}".format(os.path.basename(os.path.normpath(root))))
    print("=" * 50)
    print("范围：{} 个 commit，{} 个工作区文件".format(len(commit_rows), len(work_rows)))
    profile = detect_project_profile(root)
    custom_rules = repo_rules_file(root)
    if profile:
        print("项目档案：{} → 额外加载 {}（团队规范，叠加在通用规则上）".format(
            profile, os.path.join(script_dir, "rules", "profile-{}.md".format(profile))))
    if custom_rules:
        print("项目自定义规范：{} → 加载".format(custom_rules))
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
    db_path = os.path.join(root, DB_FILENAME)
    if not os.path.exists(db_path):
        print("库不存在：{}（还没 scan 过）".format(db_path))
        return
    conn = init_db(db_path)
    n_commit = conn.execute(
        "SELECT COUNT(*) FROM checked_commits WHERE repo=?", (rid,)).fetchone()[0]
    n_commit_real = conn.execute(
        "SELECT COUNT(*) FROM checked_commits WHERE repo=? AND check_count>=1",
        (rid,)).fetchone()[0]
    n_commit_multi = conn.execute(
        "SELECT COUNT(*) FROM checked_commits WHERE repo=? AND check_count>=2",
        (rid,)).fetchone()[0]
    n_file = conn.execute(
        "SELECT COUNT(*) FROM checked_files WHERE repo=?", (rid,)).fetchone()[0]
    n_file_real = conn.execute(
        "SELECT COUNT(*) FROM checked_files WHERE repo=? AND check_count>=1",
        (rid,)).fetchone()[0]
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


def cmd_report_path(args):
    """建报告目录 + 算报告文件名，打印目标路径。AI 负责写报告内容。

    文件名：日期_时间[_recheck][_共N个_最早~最新][+work].md，如 20260803_103000_共24个_e455b7b~db5a92f.md。
    起止取本次 --commit 里按提交时间最早/最晚的短 hash（按时间排序，非传参顺序），体现「提交次数 + 范围」；
    单 commit 只列其 hash。撞名自动追加 _2/_3，避免同秒多次检查互相覆盖。无内容可报时（无 commit 也无工作区改动）报错不建。
    """
    root = repo_root(args.repo_dir)
    if not args.commit and not args.work:
        print("❌ 无可报告内容：需 --commit 或 --work（无新改动不写报告文件）", file=sys.stderr)
        return
    report_dir = os.path.join(root, REPORT_DIR_NAME)
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = ts
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

    p_recheck = sub.add_parser("recheck", help="重查：列范围内所有项（含已查）带次数")
    p_recheck.add_argument("--author", default=None, help="只查该作者的提交")
    p_recheck.add_argument("--since", default=None,
                           help="只查该时间之后的提交，如 today / 2026-08-06 / 3 days ago")
    p_recheck.add_argument("--from", dest="from_commit", default=None,
                           help="从该提交起（含）往后查，如 acd4fef8")

    p_baseline = sub.add_parser("baseline", help="刷库建基线：标记全部历史 commit，不检查")
    p_baseline.add_argument("--author", default=None, help="只标记该作者的提交")

    p_mark = sub.add_parser("mark", help="标记已检查（重复标记次数 +1）")
    p_mark.add_argument("--commit", action="append", default=[],
                        help="标记 commit 已检查，可多次")
    p_mark.add_argument("--file", action="append", default=[],
                        help="标记文件已检查，格式 路径:内容hash，可多次")

    sub.add_parser("status", help="查看库记录数与检查次数")

    p_report = sub.add_parser("report-path", help="建报告目录并打印报告文件路径（AI 写内容）")
    p_report.add_argument("--commit", action="append", default=[],
                          help="本次检查的 commit 短hash，可多次")
    p_report.add_argument("--work", action="store_true", help="本次含工作区改动")
    p_report.add_argument("--recheck", action="store_true", help="重查报告，文件名加 recheck_ 前缀")

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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
