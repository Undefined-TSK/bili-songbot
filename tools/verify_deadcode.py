#!/usr/bin/env python3
"""S1 切换后死代码零引用证明脚本 v2（AST 级静态扫描 + 级联删除模拟）。

用法: python3 tools/verify_deadcode.py

扫描视角 = "S1 已切换"（core.py/__init__.py 的 import 改向在扫描规则中模拟，
不改动真实文件）。输出三级结论：
  [不可删]   存在活性引用（误列入删除清单的，以此为准纠正）
  [可删]     切换后零活性引用
  [级联可删] 唯一引用在"待删的 danmaku.py BiliDanmaku"内，随其级联删除

回滚基准（bot_original_frozen.py / bot_singlefile_s0_snapshot.py）不参与扫描。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
BOT_DIR = os.path.join(PROJECT_ROOT, "bot")

# 回滚基准（历史存档，非活跃代码）
IGNORE_FILES = {"bot_original_frozen.py", "bot_singlefile_s0_snapshot.py"}

# S1 切换后 import 改向（扫描时模拟，不动真实文件）
SWITCH_PATCHES = [
    ("bot/core.py", "from .danmaku import BiliDanmaku, BiliSender",
     "from .danmaku_biliapi import BiliDanmaku\nfrom .danmaku import BiliSender"),
    ("bot/__init__.py", "from .danmaku import BiliDanmaku, BiliSender",
     "from .danmaku_biliapi import BiliDanmaku, BiliSender"),
]

# 待删主目标：danmaku.py 的手写版 BiliDanmaku 类
DELETE_CLASS = ("bot/danmaku.py", "BiliDanmaku")

# 级联候选：仅被 BiliDanmaku（或其 import 行）引用的 protocol 符号
CASCADE_TARGETS = [
    ("bot/protocol.py", "get_wbi_keys"),
    ("bot/protocol.py", "enc_wbi"),
    ("bot/protocol.py", "_get_mixin_key"),
    ("bot/protocol.py", "MIXIN_KEY_ENC_TAB"),
    ("bot/protocol.py", "BILI_DANMAKU_CONF_OLD"),
]

# 活性候选：曾被误列入删除清单，本脚本负责证明其活性
KEEP_TARGETS = [
    ("bot/protocol.py", "get_real_room_id"),
    ("bot/protocol.py", "fetch_buvid3"),
    ("bot/protocol.py", "update_buvid3"),
]


def iter_py_files():
    for base in (BOT_DIR, PROJECT_ROOT):
        for name in sorted(os.listdir(base)):
            if name.endswith(".py"):
                yield os.path.join(base, name)
    tests_dir = os.path.join(PROJECT_ROOT, "tests")
    tools_dir = os.path.join(PROJECT_ROOT, "tools")
    for d in (tests_dir, tools_dir):
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(".py"):
                    yield os.path.join(d, name)


def load_files() -> dict:
    files = {}
    for p in iter_py_files():
        rel = os.path.relpath(p, PROJECT_ROOT).replace(os.sep, "/")
        if rel in IGNORE_FILES:
            continue
        files[rel] = open(p, encoding="utf-8").read()
    return files


def apply_switch(files: dict) -> list:
    applied = []
    for rel, old, new in SWITCH_PATCHES:
        if rel in files and old in files[rel]:
            files[rel] = files[rel].replace(old, new)
            applied.append(rel)
    return applied


def class_line_range(source: str, class_name: str):
    """返回 (start, end) 行范围；找不到返回 None。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node.lineno, node.end_lineno
    return None


def _func_range(source: str, func_name: str):
    """函数/常量定义的行范围（用于二级级联的 skip）。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node.lineno, node.end_lineno
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == func_name:
                    return node.lineno, node.end_lineno
    return None


def collect_refs(source: str, symbol: str, skip_ranges=None):
    """收集 source 中对 symbol 的引用点（排除定义本身与 skip_ranges 行范围）。"""
    refs = []
    tree = ast.parse(source)
    def_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            def_lines.add(node.lineno)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == symbol:
                    def_lines.add(node.lineno)
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.ImportFrom):
            if any(a.name == symbol for a in node.names):
                hit = ("import", node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            hit = ("attribute", node.lineno)
        elif isinstance(node, ast.Name) and node.id == symbol:
            hit = ("name", node.lineno)
        if hit and hit[1] not in def_lines:
            if skip_ranges and any(s <= hit[1] <= e for s, e in skip_ranges):
                continue
            refs.append(hit)
    return refs


def main():
    files = load_files()
    switched = apply_switch(files)
    print(f"[模拟切换] import 改向: {', '.join(switched)}")
    print(f"[扫描范围] {len(files)} 个 py 文件（已排除回滚基准 {', '.join(sorted(IGNORE_FILES))}）\n")

    # 1. 主目标：danmaku.py 的 BiliDanmaku 类定义
    rel, cls = DELETE_CLASS
    src = files.get(rel, "")
    rng = class_line_range(src, cls)
    if rng is None:
        print(f"[可删] {rel} :: {cls} —— 类定义不存在（已删？）")
    else:
        # 切换后，"from .danmaku import BiliDanmaku" 应已不存在（补丁改向）
        danmaku_import_refs = []
        for path, s in files.items():
            tree = ast.parse(s)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and any(a.name == cls for a in node.names):
                    mod = (node.module or "")
                    if mod.endswith("danmaku") and "biliapi" not in mod:
                        danmaku_import_refs.append((path, node.lineno))
        if danmaku_import_refs:
            print(f"[不可删] {rel} :: {cls} —— 仍有指向手写版的 import：")
            for path, ln in danmaku_import_refs:
                print(f"    {path}:{ln}")
        else:
            print(f"[可删] {rel} :: {cls} —— 切换后无 import 指向手写版；"
                  f"类定义位于第 {rng[0]}-{rng[1]} 行，可整类删除")

    # 2. 级联候选（fixpoint 迭代）：活性引用 = 不在[待删类范围 + 已判可删符号范围]内的引用。
    #    二级级联（如 _get_mixin_key 只被 enc_wbi 体内引用）随 enc_wbi 判死传递。
    print()
    skip_ranges = [rng] if rng else []
    deleted_ranges = []
    pending = list(CASCADE_TARGETS)
    results = {}
    while pending:
        still = []
        for rel, symbol in pending:
            src = files.get(rel, "")
            extra = []
            for drel, dsym in deleted_ranges:
                if drel == rel:
                    dr = class_line_range(src, dsym) or _func_range(src, dsym)
                    if dr:
                        extra.append(dr)
            active, cascade = [], []
            for path, s in files.items():
                for kind, ln in collect_refs(s, symbol, skip_ranges=None):
                    if any(s0 <= ln <= e0 for s0, e0 in skip_ranges + extra):
                        cascade.append((path, ln, kind))
                    elif path == DELETE_CLASS[0]:
                        cascade.append((path, ln, kind + " [import行,随删]"))
                    else:
                        active.append((path, ln, kind))
            if active:
                results[symbol] = ("keep", active)
                still.append((rel, symbol))
            else:
                results[symbol] = ("dead", cascade)
                deleted_ranges.append((rel, symbol))
        if len(still) == len(pending):
            break
        pending = still
    for rel, symbol in CASCADE_TARGETS:
        verdict, detail = results.get(symbol, ("keep", []))
        if verdict == "dead":
            print(f"[级联可删] {rel} :: {symbol} —— 无活性引用"
                  f"（{len(detail)} 处引用随 BiliDanmaku/上游死符号级联删除）")
        else:
            print(f"[不可删] {rel} :: {symbol} —— 有活性引用：")
            for path, ln, kind in detail:
                print(f"    {path}:{ln} ({kind})")

    # 3. 活性候选：证明其引用（纠正删除清单）
    print()
    for rel, symbol in KEEP_TARGETS:
        refs = []
        for path, s in files.items():
            for kind, ln in collect_refs(s, symbol):
                refs.append((path, ln, kind))
        if refs:
            print(f"[不可删] {rel} :: {symbol} —— 活性引用 {len(refs)} 处：")
            for path, ln, kind in refs:
                print(f"    {path}:{ln} ({kind})")
        else:
            print(f"[可疑] {rel} :: {symbol} —— 未发现引用（需人工复核是否真死）")

    print()
    print("判定规则：活性引用 = 指向该符号且不在'待删 danmaku.py BiliDanmaku 类'内的引用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
