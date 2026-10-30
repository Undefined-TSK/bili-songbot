"""最终全面实测：用户29音源(修复后) + 多方渠道新音源(模拟用户自行导入)
两阶段：
  A. 并行加载探针：每音源独立进程，记录 加载成败 + 真实原因（验证多进程隔离）
  B. 全量桥实测：多平台可播性 + 多歌曲类型搜播 + 音频字节验证（验证内核通用、不依赖特定音源）
"""
import asyncio, os, sys, json, shutil, logging, time, hashlib
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
# 默认用固化的全套音源 fixture（29 修复版 + 12 新音源），缺失时回退 /tmp 临时目录
_FIXTURE = os.path.join(HERE, "fixtures", "all_sources")
SOURCES = _FIXTURE if os.path.isdir(_FIXTURE) else "/tmp/final_sources"
WORK = "/tmp/final_run"

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("test")

# ---------------------------------------------------------------- 阶段A：准确加载状态
# 全量桥已在阶段B加载全部音源（含合理超时，权威）。此处仅对"未加载"的音源单独重跑，
# 拿准确失败原因——避免一次性并行 41 进程把 CPU/网络打满导致的误报。
async def probe_one_reason(fname):
    fpath = os.path.join(SOURCES, fname)
    proc = await asyncio.create_subprocess_exec(
        "node", os.path.join(PROJECT, "source_bridge.js"), fpath,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        return (fname, False, "初始化挂起/超时(上游接口无响应或需私有服务器)")
    txt = out.decode("utf-8", "replace")
    if "已加载" in txt:
        import re
        m = re.search(r"已加载: (.+?) v[\d.]+ \(sources: ([^)]*)\)", txt)
        return (fname, True, (m.group(1) if m else fname) + " | " + (m.group(2) if m else ""))
    for line in txt.splitlines():
        if any(k in line for k in ("加载失败", "执行失败", "初始化超时",
                                   "require is not defined", "初始化失败", "init failed", "语法错误")):
            return (fname, False, line.strip()[:140])
    return (fname, False, f"未初始化(rc={proc.returncode})")

async def phase_a(failed_files):
    log.warning(f"\n========== 阶段A：失败音源单独复检（{len(failed_files)} 个，独立进程拿真实原因）==========")
    if not failed_files:
        log.warning("  无失败音源 ✅")
        return
    res = await asyncio.gather(*(probe_one_reason(f) for f in failed_files))
    for f, _, info in sorted(res):
        tag = "【新】" if f.startswith("NEW_") else "【用户】"
        log.warning(f"  ❌ {tag}{f}\n       {info}")

# ---------------------------------------------------------------- 阶段B：全量搜播
PLATFORMS = ["kw", "wy", "tx", "kg", "mg"]
SONGS = [
    ("告白气球", "周杰伦"), ("晴天", ""), ("Shape of You", ""),
    ("前前前世", ""), ("海阔天空", "Beyond"), ("乌梅子酱", ""),
    ("孤勇者", ""), ("起风了", ""), ("Lemon", ""),
    ("稻香", "周杰伦"), ("光年之外", "邓紫棋"), ("演员", "薛之谦"),
    ("夜曲", "周杰伦"), ("林俊杰", ""),  # 纯歌手搜索
]
SONG_REF = {"告白气球": "http", "晴天": "http", "Shape of You": "http", "前前前世": "http",
            "海阔天空": "http", "乌梅子酱": "http", "孤勇者": "http", "起风了": "http",
            "Lemon": "http", "稻香": "http", "光年之外": "http", "演员": "http",
            "夜曲": "http", "林俊杰": "http"}

AUDIO_MAGIC = {b"ID3": "MP3", b"fLaC": "FLAC", b"OggS": "OGG", b"RIFF": "WAV"}
def detect(data):
    if len(data) < 4: return f"太短{len(data)}B"
    for m, n in AUDIO_MAGIC.items():
        if data[:4].startswith(m): return n
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0: return "MP3帧"
    if data[:4] == b"ftyp" or (len(data) > 8 and data[4:8] == b"ftyp"): return "M4A"
    return f"非音频({data[:8]!r})"

async def phase_b():
    # 装配运行目录
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(os.path.join(WORK, "sources"))
    for f in os.listdir(SOURCES):
        if f.endswith(".js"):
            shutil.copy(os.path.join(SOURCES, f), os.path.join(WORK, "sources", f))
    shutil.copytree(os.path.join(PROJECT, "bot"), os.path.join(WORK, "bot"),
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(PROJECT, "source_bridge.js"), os.path.join(WORK, "source_bridge.js"))
    shutil.copy(os.path.join(PROJECT, "musicSdk_bundle.cjs"), os.path.join(WORK, "musicSdk_bundle.cjs"))
    json.dump({"bili_room_id": "1", "bili_sessdata": "x", "bili_jct": "x",
               "bili_buvid3": "x", "bili_buvid4": "x",
               "source_dir": os.path.join(WORK, "sources"), "node_path": "node"},
              open(os.path.join(WORK, "config.json"), "w"), ensure_ascii=False)
    sys.path.insert(0, WORK)
    import bot
    from bot import SourceBridge, set_bridge
    import aiohttp

    log.warning("\n========== 阶段B：全量桥 搜播实测 ==========")
    bridge = SourceBridge(); await bridge.start(); set_bridge(bridge)
    sess = aiohttp.ClientSession()
    log.warning(f"桥就绪: 脚本进程 {len(bridge.procs)} + 内置引擎{'✅' if bridge.builtin else '❌'}")

    # B1 平台可播性
    log.warning("\n--- B1. 平台可播性 (kw/wy/tx/kg/mg) ---")
    plat_rows = {}
    for src in PLATFORMS:
        ok_url = None; prov = None
        try:
            r = await bridge.search(src, "告白气球", 5)
            if r:
                it = r[0]
                sid = str(it.get("id", it.get("songmid", it.get("song_id", it.get("hash", "")))))
                info = {"name": it.get("name"), "singer": it.get("singer"),
                        "song_id": sid, "album": it.get("albumName", ""), "interval": it.get("interval", "")}
                url = await bridge.get_url(src, info, "128k")
                if url:
                    ok_url = url; prov = it.get("source", src)
        except Exception as e:
            log.warning(f"  {src} ERR {e}")
        plat_rows[src] = (bool(ok_url), ok_url, prov)
        log.warning(f"  {src}: {'✅可播' if ok_url else '❌'} {('['+str(prov)+'] '+(ok_url[:60] if ok_url else ''))}")

    # B2 歌曲多样性
    log.warning("\n--- B2. 多歌曲类型搜播 (生产链路 search_song→get_music_url) ---")
    rows = []
    for kw, sg in SONGS:
        try:
            song = await bot.search_song(sess, kw, sg)
            url = await bot.get_music_url(sess, song) if song else None
            rows.append((kw, sg, bool(song), song.platform if song else "-",
                         bool(url), url[:60] if url else None))
        except Exception as e:
            rows.append((kw, sg, False, "-", False, f"ERR {e}"))
        log.warning(f"  {kw:<12} 搜到={'✅' if rows[-1][2] else '❌'} "
                    f"平台={rows[-1][3]} 可播={'✅' if rows[-1][4] else '❌'} {rows[-1][5] or ''}")

    # B3 音频字节验证（抽样）
    log.warning("\n--- B3. 音频字节验证 (抽样下载 64KB 识别真实音频) ---")
    audio_ok = 0; audio_tot = 0
    for kw, sg in [("告白气球", "周杰伦"), ("晴天", ""), ("Lemon", ""),
                   ("海阔天空", "Beyond"), ("孤勇者", ""), ("光年之外", "邓紫棋")]:
        song = await bot.search_song(sess, kw, sg)
        if not song: continue
        url = await bot.get_music_url(sess, song)
        if not url: continue
        audio_tot += 1
        try:
            async with aiohttp.ClientSession() as s2:
                async with s2.get(url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-65535"},
                                  timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as r:
                    data = await r.content.read(65536)
            typ = detect(data)
            is_audio = typ.startswith(("MP3", "FLAC", "OGG", "WAV", "M4A"))
            if is_audio: audio_ok += 1
            log.warning(f"  {kw:<12} {r.status} {len(data)}B → {typ} {'✅' if is_audio else '⚠️(非音频魔数)'}")
        except Exception as e:
            log.warning(f"  {kw:<12} 下载异常 {type(e).__name__}: {str(e)[:40]}")

    await sess.close()

    # 计算未加载音源（sources 目录文件 - 桥已加载进程名）【必须在 stop() 前，stop 会清空 procs】
    loaded_basenames = set()
    loaded_basenames = set()
    for p in bridge.procs:
        # p.name 是去掉扩展名的文件名
        loaded_basenames.add(p.name)
    all_files = [f for f in os.listdir(SOURCES) if f.endswith(".js")]
    failed_files = [f for f in all_files if os.path.splitext(f)[0] not in loaded_basenames]
    user_total = len([f for f in all_files if not f.startswith("NEW_")])
    user_loaded = len([f for f in loaded_basenames if not f.startswith("NEW_")])
    new_total = len([f for f in all_files if f.startswith("NEW_")])
    new_loaded = len([f for f in loaded_basenames if f.startswith("NEW_")])

    # 汇总
    search_hit = sum(1 for r in rows if r[2]); playable = sum(1 for r in rows if r[4])
    plat_play = sum(1 for v in plat_rows.values() if v[0])
    log.warning("\n========== 汇总 ==========")
    log.warning(f"音源加载: 用户原29修复后 {user_loaded}/{user_total} | 新音源(模拟自行导入) {new_loaded}/{new_total} | 合计 {len(bridge.procs)}/{len(all_files)}")
    log.warning(f"平台可播: {plat_play}/{len(PLATFORMS)}（kw/wy/tx/kg/mg）")
    log.warning(f"歌曲搜到: {search_hit}/{len(SONGS)} | 可播: {playable}/{len(SONGS)}")
    log.warning(f"音频字节确认: {audio_ok}/{audio_tot}")
    json.dump({"user_loaded": user_loaded, "user_total": user_total,
               "new_loaded": new_loaded, "new_total": new_total,
               "plat_play": plat_play, "plat_total": len(PLATFORMS),
               "search_hit": search_hit, "playable": playable, "song_total": len(SONGS),
               "audio_ok": audio_ok, "audio_tot": audio_tot},
              open("/tmp/final_result.json", "w"), ensure_ascii=False)
    log.warning("结果已写入 /tmp/final_result.json")
    await bridge.stop()
    return failed_files

async def main():
    # 先跑阶段B拿权威加载状态，再对失败项单独复检拿原因
    failed = await phase_b()
    await phase_a(failed)

if __name__ == "__main__":
    asyncio.run(main())
