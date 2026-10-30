#!/usr/bin/env python3
"""全面真实音源实测（用户 GitHub 仓库全部音源 29 个 + 内置引擎兜底）。

目标：用"现在的方式"验证 —— 任何类型歌曲、任何平台、多种搜索方式，
能否搜到（搜索命中）+ 能否播放（可播 URL + 音频验证）。

测试维度：
  A. 音源加载：29 个脚本 + 内置引擎，逐个报告加载状态（隔离异常脚本）。
  B. 平台可播性探针：5 大平台各搜一首已知歌 → 取 URL，报告每个平台是否可播、
     由哪个音源提供（海量曲库兜底验证）。
  C. 歌曲多样性矩阵：14 首不同类型歌曲（中文/英文/日文/粤语/影视/新歌/纯歌手/
     带版本名/合唱...），用生产 search_song（多平台模糊择优）搜索，再 get_music_url
     取 URL，抽样做音频字节验证。
  D. 汇总：搜索命中率、可播率、音频通过率、平台覆盖、隔离异常清单。
"""
import asyncio
import json
import os
import shutil
import sys
import time

PROJECT = "/workspace/bili-songbot-S0"
WORK = "/tmp/comprehensive_run"
SF = "/tmp/user_sources/文件形式"
SL = "/tmp/user_sources/链接形式"

# (类型, 关键词, 歌手)
SONG_MATRIX = [
    ("经典中文+歌手", "告白气球", "周杰伦"),
    ("只歌名", "晴天", ""),
    ("英文歌", "Shape of You", ""),
    ("英文歌+歌手", "See You Again", "Wiz Khalifa"),
    ("日文歌", "前前前世", ""),
    ("新歌热歌", "乌梅子酱", ""),
    ("影视金曲", "孤勇者", ""),
    ("粤语老歌", "千千阙歌", ""),
    ("短名快歌", "稻香", ""),
    ("带版本名", "起风了", ""),
    ("多人合唱", "屋顶", "周杰伦"),
    ("纯歌手搜索", "林俊杰", ""),
    ("网络热歌", "孤城", ""),
    ("经典老歌", "海阔天空", "Beyond"),
]

PLATFORMS = ["kw", "wy", "tx", "kg", "mg"]


async def main():
    # ---- 装配 ----
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK + "/sources", exist_ok=True)
    for d in (SF, SL):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".js"):
                    shutil.copy(os.path.join(d, f), os.path.join(WORK + "/sources", f))
    shutil.copytree(PROJECT + "/bot", WORK + "/bot", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PROJECT + "/source_bridge.js", WORK + "/source_bridge.js")
    shutil.copy(PROJECT + "/musicSdk_bundle.cjs", WORK + "/musicSdk_bundle.cjs")
    json.dump({"bili_room_id": "123456", "bili_sessdata": "x", "bili_jct": "x",
               "bili_buvid3": "x", "bili_buvid4": "x",
               "source_dir": WORK + "/sources", "node_path": "node"},
              open(WORK + "/config.json", "w"))
    sys.path.insert(0, WORK)
    import bot
    from bot.bridge import _ScriptProc, NODE_PATH, BRIDGE_SCRIPT

    total_files = len([f for f in os.listdir(WORK + "/sources") if f.endswith(".js")])

    # ---- A. 加载 ----
    print(f"=== A. 音源加载（{total_files} 个脚本 + 内置引擎，多进程隔离） ===")
    t0 = time.time()
    bridge = bot.SourceBridge()
    await bridge.start()
    loaded = [p.name for p in bridge.procs]
    print(f"就绪耗时 {time.time()-t0:.1f}s | 成功加载进程 {len(bridge.procs)}/{total_files}"
          f" | 内置引擎 {'✅' if bridge.builtin else '❌'}")
    print(f"已加载音源: {loaded}")
    bot.set_bridge(bridge)

    import aiohttp
    sess = aiohttp.ClientSession()

    # ---- B. 平台可播性探针 ----
    print(f"\n=== B. 平台可播性探针（每个平台搜已知歌→取URL） ===")
    print(f"{'平台':<5} {'搜索命中':<8} {'URL可播':<8} {'提供音源'}")
    plat_rows = {}
    for src in PLATFORMS:
        try:
            r = await bridge.search(src, "告白气球", 5)
            hit = bool(r)
            url = None
            provider = "-"
            if hit:
                it = r[0]
                sid = str(it.get("id", it.get("songmid", it.get("song_id", it.get("hash", "")))))
                info = {"name": it.get("name"), "singer": it.get("singer"),
                        "song_id": sid, "album": it.get("albumName", ""),
                        "interval": it.get("interval", ""), "hash": it.get("hash", ""),
                        "strMediaMid": it.get("strMediaMid", ""),
                        "copyrightId": it.get("copyrightId", ""),
                        "songmid": str(it.get("songmid", ""))}
                try:
                    url = await bridge.get_url(src, info, "128k")
                except Exception:
                    url = None
                if url:
                    # 快速判定提供方：内置引擎能否给出同一 URL（否则为社区脚本竞速胜出）
                    provider = "社区脚本"
                    if bridge.builtin:
                        try:
                            d = await bridge.builtin.request(
                                {"action": "musicUrl", "source": src, "info": info, "type": "128k"}, 8)
                            if d.get("url") and d["url"] == url:
                                provider = "内置引擎"
                        except Exception:
                            pass
            plat_rows[src] = (hit, bool(url), provider)
            print(f"{src:<5} {'✅' if hit else '❌':<8} {'✅' if url else '❌':<8} {provider}")
        except Exception as e:
            plat_rows[src] = (False, False, str(e)[:20])
            print(f"{src:<5} 异常: {e}")

    # ---- C. 歌曲多样性矩阵 ----
    print(f"\n=== C. 歌曲多样性矩阵（生产 search_song 多平台模糊择优 + get_music_url） ===")
    print(f"{'类型':<12} {'搜索词':<16} {'命中':<5} {'平台':<4} {'可播':<5} {'音频'}")
    rows = []
    for kind, kw, singer in SONG_MATRIX:
        try:
            t0 = time.time()
            song = await bot.search_song(sess, kw, singer)
            dt = time.time() - t0
            if not song:
                print(f"{kind:<12} {kw:<16} {'❌':<5}")
                rows.append((kind, kw, False, "-", False, "-"))
                continue
            url = await bot.get_music_url(sess, song)
            ok_url = bool(url and url.startswith("http"))
            audio = "-"
            if ok_url:
                try:
                    async with aiohttp.ClientSession() as s2:
                        h = {"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"}
                        async with s2.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=15),
                                        allow_redirects=True) as resp:
                            data = await resp.content.read(1024)
                            if data and len(data) > 200 and data[:1] not in (b"{", b"<", b"["):
                                audio = f"✅{len(data)}B"
                            else:
                                audio = f"⚠️{resp.status}"
                except Exception as e:
                    audio = f"⚠️{type(e).__name__}"
            print(f"{kind:<12} {kw:<16} {'✅':<5} {song.platform:<4} {'✅' if ok_url else '❌':<5} {audio}")
            rows.append((kind, kw, True, song.platform, ok_url, audio))
        except Exception as e:
            print(f"{kind:<12} {kw:<16} 异常: {e}")
            rows.append((kind, kw, False, "-", False, "-"))

    await sess.close()
    loaded_n = len(bridge.procs)  # stop 前捕获，stop 会清空 procs
    await bridge.stop()

    # ---- D. 汇总 ----
    search_hit = sum(1 for r in rows if r[2])
    playable = sum(1 for r in rows if r[4])
    audio_ok = sum(1 for r in rows if str(r[5]).startswith("✅"))
    plat_play = sum(1 for v in plat_rows.values() if v[1])
    print(f"\n=== D. 汇总 ===")
    print(f"音源加载: {loaded_n}/{total_files} 脚本进程 + 内置引擎")
    print(f"平台可播: {plat_play}/{len(PLATFORMS)} (kw/wy/tx/kg/mg)")
    print(f"歌曲搜索命中: {search_hit}/{len(SONG_MATRIX)}")
    print(f"歌曲可播URL: {playable}/{len(SONG_MATRIX)}")
    print(f"音频字节验证: {audio_ok}/{len(SONG_MATRIX)}")

    json.dump({"loaded": loaded_n, "total": total_files,
               "builtin": bool(bridge.builtin), "plat_rows": plat_rows,
               "song_rows": rows}, open("/tmp/comprehensive_result.json", "w"),
              ensure_ascii=False, indent=1)
    print("\n结果已保存: /tmp/comprehensive_result.json")


if __name__ == "__main__":
    asyncio.run(main())
