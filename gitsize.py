#!/usr/bin/env python3
"""gitsize: 找出是什么让你的 git 仓库变胖。

用法：
    gitsize                # 在 git 仓库里运行
    gitsize --top 10       # 只看前 10 个大文件
    gitsize --ext          # 按扩展名分组统计
    gitsize --json         # 机器可读输出
    gitsize --suggest      # 给出清理建议（只建议，不做任何破坏性操作）

纯标准库实现。只读取仓库，不做任何写入或破坏性操作。
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

VERSION = "0.1.0"


def human_size(n: int) -> str:
    """字节数转人类可读。"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"  # pragma: no cover


def run_git(args, cwd=None, input_data=None):
    """运行 git 命令，返回 stdout 文本。失败时抛 RuntimeError。"""
    try:
        p = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError("找不到 git 命令，请先安装 git")
    except subprocess.TimeoutExpired:
        raise RuntimeError("git 命令超时，仓库可能过大")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout


def ensure_repo():
    """确认当前在 git 仓库里（含工作区），否则报错退出。"""
    try:
        top = run_git(["rev-parse", "--show-toplevel"]).strip()
    except RuntimeError:
        print("error: 当前目录不在 git 仓库中", file=sys.stderr)
        sys.exit(1)
    # 确认至少有一次提交
    try:
        run_git(["rev-parse", "--verify", "HEAD"])
    except RuntimeError:
        print("error: 仓库还没有任何提交", file=sys.stderr)
        sys.exit(1)
    return top


def collect_blobs():
    """收集历史上所有 blob：返回 [(sha, path, size), ...]。

    path 可能为空（dangling 对象）或含特殊字符；size 为字节数。
    """
    objects_out = run_git(["rev-list", "--objects", "--all"])
    shas = []
    path_of = {}
    for line in objects_out.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        sha = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        shas.append(sha)
        path_of[sha] = path
    if not shas:
        return []
    # batch-check 一次查全部类型和大小
    check = run_git(
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_data="\n".join(shas) + "\n",
    )
    blobs = []
    for line in check.splitlines():
        fields = line.split(" ")
        if len(fields) < 3:
            continue
        sha, otype, size = fields[0], fields[1], fields[2]
        if otype != "blob":
            continue
        try:
            size = int(size)
        except ValueError:
            continue
        blobs.append((sha, path_of.get(sha, ""), size))
    return blobs


def introducing_commit(sha):
    """找到首次引入该 blob 的提交（短 hash + 标题），找不到返回 (None, None)。"""
    try:
        out = run_git(
            ["log", "--all", "--reverse", "--format=%h%x00%s", "--find-object=" + sha]
        )
    except RuntimeError:
        return None, None
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return None, None
    h, _, subject = lines[0].partition("\x00")
    return h.strip(), subject.strip()


def head_paths():
    """HEAD 中所有文件路径集合（用于判断 blob 是否已在 HEAD 中被删除）。"""
    out = run_git(["ls-tree", "-r", "--name-only", "-z", "HEAD"])
    return set(p for p in out.split("\x00") if p)


def biggest_in_head():
    """HEAD 中最大的文件：返回 (path, size)，没有返回 (None, 0)。"""
    out = run_git(["ls-tree", "-r", "-l", "HEAD"])
    best, best_size = None, 0
    for line in out.splitlines():
        # 格式: <mode> <type> <sha> <size>\t<path>
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        fields = meta.split()
        if len(fields) < 4 or fields[1] != "blob":
            continue
        try:
            size = int(fields[3])
        except ValueError:
            continue  # submodule 显示为 -
        if size > best_size:
            best, best_size = path, size
    return best, best_size


def repo_size_info():
    """仓库体积信息：返回 dict(pack_kb, loose_kb, in_pack, loose_count)。"""
    out = run_git(["count-objects", "-v"])
    info = {"pack_kb": 0, "loose_kb": 0, "in_pack": 0, "loose_count": 0}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        try:
            num = int(v)
        except ValueError:
            continue
        if k == "size-pack":
            info["pack_kb"] = num
        elif k == "size":
            info["loose_kb"] = num
        elif k == "in-pack":
            info["in_pack"] = num
        elif k == "count":
            info["loose_count"] = num
    return info


def ext_of(path):
    """取扩展名，无扩展名返回 '（无扩展名）'。"""
    if not path:
        return "（未知路径）"
    base = os.path.basename(path)
    if "." in base and not base.startswith("."):
        return "." + base.rsplit(".", 1)[1].lower()
    return "（无扩展名）"


def analyze(top_n):
    """主分析流程，返回结构化结果 dict。"""
    blobs = collect_blobs()
    blobs.sort(key=lambda b: b[2], reverse=True)
    head = head_paths()
    head_best, head_best_size = biggest_in_head()
    size_info = repo_size_info()

    top = []
    for sha, path, size in blobs[:top_n]:
        h, subject = introducing_commit(sha)
        top.append(
            {
                "sha": sha,
                "path": path,
                "size": size,
                "in_head": bool(path) and path in head,
                "introduced_by": h,
                "introduced_subject": subject,
            }
        )

    reclaimable = sum(b["size"] for b in top if not b["in_head"])

    ext_groups = defaultdict(lambda: {"size": 0, "count": 0})
    for _, path, size in blobs:
        g = ext_groups[ext_of(path)]
        g["size"] += size
        g["count"] += 1
    ext_list = sorted(ext_groups.items(), key=lambda kv: kv[1]["size"], reverse=True)

    return {
        "blob_count": len(blobs),
        "total_history_bytes": sum(b[2] for b in blobs),
        "pack_kb": size_info["pack_kb"],
        "loose_kb": size_info["loose_kb"],
        "in_pack": size_info["in_pack"],
        "loose_count": size_info["loose_count"],
        "head_biggest": {"path": head_best, "size": head_best_size},
        "top": top,
        "reclaimable_bytes": reclaimable,
        "ext_groups": [
            {"ext": ext, "size": g["size"], "count": g["count"]} for ext, g in ext_list
        ],
    }


def print_table(result, top_n, show_ext, suggest):
    total_kb = result["pack_kb"] + result["loose_kb"]
    print("===== 仓库体积 =====")
    print(f"对象总数：{result['blob_count']} 个 blob（pack 内 {result['in_pack']}，零散 {result['loose_count']}）")
    print(f"磁盘占用：约 {human_size(total_kb * 1024)}（pack {human_size(result['pack_kb'] * 1024)} + 零散 {human_size(result['loose_kb'] * 1024)}）")
    print(f"历史 blob 累计：{human_size(result['total_history_bytes'])}")
    hb = result["head_biggest"]
    if hb["path"]:
        print(f"HEAD 中最大文件：{hb['path']}（{human_size(hb['size'])}）")
    print()
    print(f"===== 历史上最大的 {len(result['top'])} 个文件 =====")
    print(f"  {'大小':>10}  {'状态':<4}  路径 / 引入提交")
    print("  " + "-" * 60)
    for b in result["top"]:
        status = "现存" if b["in_head"] else "已删"
        intro = f"{b['introduced_by']} {b['introduced_subject']}" if b["introduced_by"] else "（未知）"
        path = b["path"] or f"（无路径，{b['sha'][:8]}）"
        print(f"  {human_size(b['size']):>10}  {status:<4}  {path}")
        print(f"  {'':>10}  {'':<4}  └─ 首次引入：{intro}")
    if show_ext:
        print()
        print("===== 按扩展名分组（历史累计） =====")
        for g in result["ext_groups"][:15]:
            print(f"  {g['ext']:<14}  {human_size(g['size']):>10}  ({g['count']} 个文件）")
    if suggest:
        print()
        print("===== 清理建议（仅建议，不做任何操作） =====")
        deleted = [b for b in result["top"] if not b["in_head"]]
        if deleted:
            print(f"以下大文件已从 HEAD 删除，但仍占历史体积约 {human_size(result['reclaimable_bytes'])}：")
            for b in deleted:
                print(f"  - {b['path'] or b['sha'][:8]}（{human_size(b['size'])}）")
            print("如确认不再需要，可考虑 git filter-repo / BFG Repo-Cleaner 重写历史。")
            print("注意：重写历史会改变所有提交 hash，需团队协调，操作前务必备份。")
        else:
            print("Top 文件都在 HEAD 中现存，无\"已删但占历史\"的情况。")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="gitsize",
        description="找出是什么让你的 git 仓库变胖（只读分析，不做任何修改）。",
    )
    ap.add_argument("--version", action="version", version=f"gitsize {VERSION}")
    ap.add_argument("--top", type=int, default=20, metavar="N", help="显示历史上最大的 N 个文件（默认 20）")
    ap.add_argument("--ext", action="store_true", help="按扩展名分组统计")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--suggest", action="store_true", help="给出清理建议（仅建议，不执行）")
    args = ap.parse_args(argv)

    if args.top is not None and args.top < 1:
        print("error: --top 至少为 1", file=sys.stderr)
        return 2

    ensure_repo()
    result = analyze(args.top)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_table(result, args.top, args.ext, args.suggest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
