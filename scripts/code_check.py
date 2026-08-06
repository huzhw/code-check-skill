#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code-check 增量代码检查脚本
============================

职责：负责增量逻辑的机械部分（git 提取 + SQLite 去重），AI 负责检查本身。

三张表两张，一张按 commit 去重（已提交），一张按文件内容 hash 去重（未提交工作区改动）。

命令：
    python code_check.py scan [--author 姓名] [--json]
        扫描待检查改动：已提交新 commit + 未提交工作区改动，输出清单。
    python code_check.py mark [--commit <hash>]... [--file <路径>:<内容hash>]...
        标记已检查，写回 SQLite。
    python code_check.py status
        查看库记录数。

库位置：被检查项目 git 根目录下 `.code-check.db`（该文件加入 .gitignore，不入库）。
repo 标识：git remote origin 地址优先，无 remote 用绝对路径。
"""

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

DB_FILENAME = ".code-check.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS checked_commits (
    repo        TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    commit_time TEXT,
    author      TEXT,
    subject     TEXT,
    checked_at  TEXT,
    UNIQUE(repo, commit_hash)
);
CREATE TABLE IF NOT EXISTS checked_files (
    repo         TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    checked_at   TEXT,
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

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------- 已提交改动 ----------------

def list_commits(root, author=None):
    """全部非 merge 提交。返回 [{hash, time, author, subject}]。"""
    args = ["log", "--no-merges", "--format=%H\t%aI\t%an\t%s"]
    if author:
        args.append("--author=" + author)
    out = git(root, *args)
    commits = []
    for line in out.strip().splitlines():
        if not line:
            continue
        h, t, a, s = line.split("\t", 3)
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

    # 1. 已提交的新 commit
    commits = list_commits(root, author=args.author)
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

    print("=" * 60)
    print("code-check 扫描结果")
    print("repo      :", rid)
    print("db        :", db_path)
    print("=" * 60)
    print("\n## 已提交的新 commit（{} 个）".format(len(commit_detail)))
    for c in commit_detail:
        short = c["hash"][:7]
        print("\n[{short}] {time}  {author}  {subject}".format(
            short=short, time=c["time"][:16], author=c["author"], subject=c["subject"]))
        print("  → 标记: python code_check.py mark --commit {hash}".format(hash=c["hash"]))
        if c["files"]:
            for status, path in c["files"]:
                print("    {status}  {path}".format(status=status, path=path))

    print("\n## 未提交工作区改动（{} 个文件）".format(
        sum(1 for f in work_detail if not f["checked_before"])))
    for f in work_detail:
        tag = "已查过" if f["checked_before"] else "新增"
        print("  [{tag}] {status}  {path}".format(
            tag=tag, status=f["status"], path=f["path"]))
    new_files = [f for f in work_detail if not f["checked_before"]]
    if new_files:
        print("\n  → 标记示例:")
        for f in new_files:
            print("      python code_check.py mark --file {path}:{h}".format(
                path=f["path"], h=f["content_hash"]))

    n_new_commit = len(commit_detail)
    n_new_file = sum(1 for f in work_detail if not f["checked_before"])
    print("\n共待检查：{} 个 commit，{} 个工作区文件".format(n_new_commit, n_new_file))
    if n_new_commit == 0 and n_new_file == 0:
        print("✅ 没有新改动，全部已检查过。")


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
        conn.execute(
            "INSERT OR IGNORE INTO checked_commits"
            "(repo, commit_hash, commit_time, author, subject, checked_at)"
            "VALUES (?,?,?,?,?,?)",
            (rid, h, row[0] if row else "", row[1] if row else "",
             row[2] if row else "", now()))
        n_commit += 1

    n_file = 0
    for spec in args.file:
        if ":" not in spec:
            print("⚠ 跳过非法 --file 参数: {}".format(spec), file=sys.stderr)
            continue
        path, h = spec.rsplit(":", 1)
        conn.execute(
            "INSERT OR IGNORE INTO checked_files"
            "(repo, file_path, content_hash, checked_at)"
            "VALUES (?,?,?,?)",
            (rid, path, h, now()))
        n_file += 1

    conn.commit()
    conn.close()
    print("✅ 已标记: {} 个 commit，{} 个文件".format(n_commit, n_file))


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
    n_file = conn.execute(
        "SELECT COUNT(*) FROM checked_files WHERE repo=?", (rid,)).fetchone()[0]
    last = conn.execute(
        "SELECT MAX(checked_at) FROM checked_commits").fetchone()[0]
    print("repo      :", rid)
    print("db        :", db_path)
    print("已检查 commit: {} 条".format(n_commit))
    print("已检查文件改动: {} 条".format(n_file))
    print("最近检查时间 :", last or "无")
    conn.close()


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

    p_scan = sub.add_parser("scan", help="扫描待检查改动")
    p_scan.add_argument("--author", default=None, help="只查该作者的提交")
    p_scan.add_argument("--json", action="store_true", help="JSON 输出")

    p_mark = sub.add_parser("mark", help="标记已检查")
    p_mark.add_argument("--commit", action="append", default=[],
                        help="标记 commit 已检查，可多次")
    p_mark.add_argument("--file", action="append", default=[],
                        help="标记文件已检查，格式 路径:内容hash，可多次")

    sub.add_parser("status", help="查看进度")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "mark":
        cmd_mark(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
