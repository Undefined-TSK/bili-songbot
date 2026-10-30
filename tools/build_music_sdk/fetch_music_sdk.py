#!/usr/bin/env python3
"""拉取 lx-music-desktop v2.12.6 的 musicSdk 源文件（jsdelivr CDN）。

清单：FILE_LIST.json（musicSdk 88 个文件 + 7 个 utils 依赖）。
输出目录：./src_fetched/（= 打包工作区）
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "lyswhut/lx-music-desktop"
TAG = "v2.12.6"
CDN = f"https://cdn.jsdelivr.net/gh/{REPO}@{TAG}/"
SKIP = ("/temp/", "api-test", "comment.js", "singer.js")  # 测试/临时/不需要的模块


def main():
    with open(os.path.join(HERE, "FILE_LIST.json"), encoding="utf-8") as f:
        lists = json.load(f)

    work = os.path.join(HERE, "src_fetched")
    os.makedirs(work, exist_ok=True)

    todo = [p for p in lists["musicSdk"] if not any(s in p for s in SKIP)] + lists["extra"]
    ok, fail = 0, []
    for rel in todo:
        dest = os.path.join(work, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        url = CDN + "src/renderer/" + rel
        r = subprocess.run(["curl", "-s", "--max-time", "25", "-o", dest, url])
        if r.returncode == 0 and os.path.getsize(dest) > 0:
            ok += 1
        else:
            fail.append(rel)
    print(f"拉取 {ok}/{len(todo)}")
    if fail:
        print("失败:", fail)
        sys.exit(1)


if __name__ == "__main__":
    main()
