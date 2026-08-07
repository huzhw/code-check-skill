#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code-check 增量代码检查脚本
============================

职责：负责增量逻辑的机械部分（git 提取 + SQLite 去重），AI 负责检查本身。

三张表两张，一张按 commit 去重（已提交），一张按文件内容 hash 去重（未提交工作区改动）。

命令：
    python code_check.py scan [--author 姓名] [--json] [--since 时间] [--from 提交id] [--baseline] [--quiet]
        扫描待检查改动（只列未检查的新项），输出精简清单。
        --since：只查该时间之后的提交，如 today / 2026-08-06 / 3 days ago。
        --from：从该提交起（含）往后查，如 acd4fef8。
        --baseline：首次使用建基线，把当前全部历史 commit 标记为已检查，只查之后的新改动。
        --quiet：只输出「N commit / M 文件待查」一行，供快速判断。
    python code_check.py recheck [--author 姓名] [--since 时间] [--from 提交id]
        重查：列范围内所有项（含已查），每条带「已查 N 次」；check_count 随 mark 递增。
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
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys

DB_FILENAME = ".code-check.db"
REPORT_DIR_NAME = ".code-check-reports"
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

def cmd_scan(args):
    root = repo_root(args.repo_dir)
    rid = repo_id(root)
    db_path = os.path.join(root, DB_FILENAME)
    conn = init_db(db_path)

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
    for c in new_commits:
        try:
            files = commit_changed_files(root, c["hash"])
        except RuntimeError:
            files = []
        commit_detail.append({
            "hash": c["hash"], "time": c["time"], "author": c["author"],
            "subject": c["subject"], "files": files,
        })

    # 2. 未提交工作区改动
    try:
        wfiles = working_changed_files(root)
    except RuntimeError:
        wfiles = []
    work_detail = []
    for status, path in wfiles:
        if status in ("D",):
            continue  # 已删除文件无内容可查
        if os.path.basename(path) == DB_FILENAME:
            continue  # 跳过库文件自身
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
        "new_commits": commit_detail,
        "working_files": work_detail,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    n_new_commit = len(commit_detail)
    new_files = [f for f in work_detail if not f["checked_before"]]
    n_new_file = len(new_files)

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
    if n_new_commit == 0 and n_new_file == 0:
        print("✅ 没有新改动，全部已检查过。")
        return
    print()

    if commit_detail:
        print("【已提交的新 commit】")
        for c in commit_detail:
            short = c["hash"][:7]
            # c["time"] 形如 2026-08-06T18:47:34+08:00，取 MM-DD HH:MM
            t = c["time"][5:16].replace("T", " ")
            print("  [{short}] {time} {author}  {subject}".format(
                short=short, time=t, author=c["author"], subject=c["subject"]))
            for status, path in c["files"]:
                print("      {status}  {path}".format(status=status, path=path))
        print()

    if new_files:
        print("【未提交工作区改动】")
        for f in new_files:
            print("  [{status}] {path}".format(status=f["status"], path=f["path"]))
        print()

    print("—— 检查完写回标记（AI 用）——")
    for c in commit_detail:
        print("  mark --commit {hash}".format(hash=c["hash"]))
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
        commit_rows.append({
            "hash": c["hash"], "time": c["time"], "author": c["author"],
            "subject": c["subject"], "files": files,
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
        h = file_content_hash(root, path)
        if h is None:
            continue
        work_rows.append({"status": status, "path": path,
                          "content_hash": h, "count": file_check_count(conn, rid, path, h)})

    print("=" * 50)
    print("code-check 重查：{}".format(os.path.basename(os.path.normpath(root))))
    print("=" * 50)
    print("范围：{} 个 commit，{} 个工作区文件".format(len(commit_rows), len(work_rows)))
    if not commit_rows and not work_rows:
        print("该范围内无改动。")
        return
    print()

    if commit_rows:
        print("【commit（已查 N 次，0=新）】")
        for c in commit_rows:
            t = c["time"][5:16].replace("T", " ")
            print("  [{short}] {time} {author}  {subject}  （已查 {cnt} 次）".format(
                short=c["hash"][:7], time=t, author=c["author"],
                subject=c["subject"], cnt=c["count"]))
            for status, path in c["files"]:
                print("      {status}  {path}".format(status=status, path=path))
        print()

    if work_rows:
        print("【工作区文件（已查 N 次，0=新）】")
        for f in work_rows:
            print("  [{status}] {path}  （已查 {cnt} 次）".format(
                status=f["status"], path=f["path"], cnt=f["count"]))
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
