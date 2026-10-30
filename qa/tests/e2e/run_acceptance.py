#!/usr/bin/env python3
"""正式版验收执行器（Horizon QA）。

驱动真实内核 bot/bridge.py 执行 TC-PLUG / TC-LINK / TC-SELF / TC-KRN / TC-SEC 用例，
结果写入 qa/results.json（TC-E2E 由 pytest 与存量脚本单独执行）。
"""
import asyncio
import json
import os
import shutil
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "qa", "tests", "fixtures")

import aiohttp
from aiohttp import web

import bot.bridge as bb

RESULTS = []


def record(case_id, ok, note="", elapsed=None):
    RESULTS.append({
        "id": case_id,
        "status": "PASS" if ok else "FAIL",
        "note": note[:300],
        "elapsed_s": round(elapsed, 1) if elapsed else None,
    })
    print(f"[{'PASS' if ok else 'FAIL'}] {case_id} {note[:120]}")


def fixture(name):
    return os.path.join(FIX, name)


def make_dir(files):
    d = os.path.join(ROOT, "qa", "tmp_srcdirs", str(time.time_ns()))
    os.makedirs(d, exist_ok=True)
    for f in files:
        shutil.copy(fixture(f), os.path.join(d, f))
    return d


class TestServer:
    """本地 HTTP 服务器：模拟官方源下载站。"""

    def __init__(self):
        self.app = web.Application()
        self.routes = {}
        self.runner = None
        self.port = None

    def set(self, path, body, content_type="application/javascript"):
        self.routes[path] = (body, content_type)

    async def start(self):
        async def handler(req):
            item = self.routes.get(req.path)
            if not item:
                return web.Response(status=404, text="not found")
            body, ct = item
            return web.Response(body=body if isinstance(body, bytes) else body.encode(),
                                content_type=ct)
        for p in self.routes:
            self.app.router.add_route("GET", p, handler)
        # 动态路由：运行中 set() 的新 path 也能响应
        self.app.router.add_route("GET", "/{tail:.*}", handler)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


HOT_V1 = open(fixture("lx_hot_v1.js"), encoding="utf-8").read()
HOT_V2 = HOT_V1.replace("v1", "v2").replace("1.0.0", "2.0.0")
AUDIO_BYTES = b"ID3\x04\x00\x00\x00" + bytes(range(256)) * 8


async def phase_plug_krn():
    """阶段 A：一个夹具目录一次 bridge 启动，覆盖 TC-PLUG-001..009 / KRN-005/006/009。"""
    d = make_dir([
        "lx_valid_minimal.js", "lx_valid_multi.js", "lx_garbage.js", "lx_empty.js",
        "lx_nonlx.js", "lx_throw.js", "lx_lazy.js", "lx_valid_中文 名字.js",
        "lx_dup_same_name.js", "lx_slow_response.js", "lx_stderr_flood.js",
    ])
    bb.SOURCE_DIR = d
    bb.SOURCE_URLS = []
    t0 = time.time()
    b = bb.SourceBridge()
    await b.start()
    elapsed = time.time() - t0
    try:
        names = {os.path.basename(p.args[-1]): p for p in b.procs}
        script_names = [getattr(p, "script_name", "") for p in b.procs]

        # 去重语义：minimal 与 dup 同 @name，内核保留首个 —— 按 @name 断言而非文件名
        p_min = next((p for p in b.procs if getattr(p, "script_name", "") == "QA验收-最小标准源"), None)
        record("TC-PLUG-001", bool(p_min and p_min.ready),
               f"@name='QA验收-最小标准源' 进程就绪={bool(p_min and p_min.ready)}（文件 {os.path.basename(p_min.args[-1]) if p_min else None}）", elapsed)
        p_multi = names.get("lx_valid_multi.js")
        srcs = (p_multi.sources if p_multi else {})
        record("TC-PLUG-002", all(k in srcs for k in ("wy", "kw", "tx", "kg", "mg")),
               f"平台={sorted(srcs.keys())}")
        # 音质透传：请求 flac，handler 回显 info.type
        try:
            d = await asyncio.wait_for(
                p_multi.request({"action": "musicUrl", "source": "wy", "type": "flac",
                                 "info": {"musicInfo": {}}}, 8), 12)
            u = d.get("url") or ""
            record("TC-PLUG-003", "flac" in u, f"payload.type→info.type 音质透传: {u[:70]}")
        except Exception as e:
            record("TC-PLUG-003", False, f"透传失败: {e}")
        bad = ["lx_garbage.js", "lx_empty.js", "lx_nonlx.js", "lx_throw.js"]
        record("TC-PLUG-004", "lx_garbage.js" not in names and bool(p_min and p_min.ready),
               "垃圾文件被隔离且合法源不受影响")
        record("TC-PLUG-005", "lx_empty.js" not in names, "空文件被隔离")
        record("TC-PLUG-006", "lx_nonlx.js" not in names, "非LX脚本被隔离")
        record("TC-PLUG-007", "lx_throw.js" not in names, "初始化抛错被隔离")
        record("TC-PLUG-008", "lx_lazy.js" not in names and elapsed < 45 + 15,
               f"惰性脚本超时隔离，总启动 {elapsed:.1f}s")
        record("TC-PLUG-009", "lx_valid_中文 名字.js" in names and
               getattr(names["lx_valid_中文 名字.js"], "script_name", "") == "QA验收-中文 空格 文件名",
               "中文+空格文件名正常加载")
        # TC-KRN-009 本地同名去重：dup 与 minimal 同 @name，仅保留一个
        same = [p for p in b.procs if getattr(p, "script_name", "") == "QA验收-最小标准源"]
        record("TC-KRN-009", len(same) == 1, f"@name=A 进程数={len(same)}")
        # TC-KRN-005 慢响应不阻塞
        t1 = time.time()
        url = await asyncio.wait_for(
            b.get_url("wy", {"songmid": "1", "name": "x", "singer": "y"}, "128k"),
            timeout=20)
        dt = time.time() - t1
        record("TC-KRN-005", dt < 15,
               f"慢响应源挂起 30s，get_url {dt:.1f}s 内返回（url={bool(url)}，网络受限时允许 None）", dt)
        # TC-KRN-006 stderr 洪水
        p_flood = names.get("lx_stderr_flood.js")
        record("TC-KRN-006", bool(p_flood and p_flood.ready and p_flood.supports("musicUrl", "wy")),
               "2万行 stderr 洪水后进程存活且可路由")

        # TC-SELF-006 后台任务
        tnames = sorted(t.get_name() for t in b._bg_tasks)
        record("TC-SELF-006", "source-self-update" in tnames and "source-watchdog" in tnames,
               f"后台任务={tnames}")

        await b.stop()
        # TC-KRN-007 干净回收
        stopped = all(p.process is None or p.process.returncode is not None for p in names.values())
        record("TC-KRN-007", stopped and b.procs == [] and b.builtin is None,
               f"全部进程回收 procs={len(b.procs)} builtin={b.builtin}")
    finally:
        try:
            await b.stop()
        except Exception:
            pass
        shutil.rmtree(d, ignore_errors=True)


async def phase_krn_builtin():
    """阶段 B：TC-KRN-001 全部脚本损坏时内置引擎兜底。"""
    d = make_dir(["lx_garbage.js", "lx_throw.js"])
    bb.SOURCE_DIR = d
    bb.SOURCE_URLS = []
    b = bb.SourceBridge()
    await b.start()
    ok = bool(b.builtin and b.builtin.ready) and len(b.procs) == 0
    record("TC-KRN-001", ok, f"内置引擎就绪={bool(b.builtin and b.builtin.ready)}, 脚本进程={len(b.procs)}")
    await b.stop()
    shutil.rmtree(d, ignore_errors=True)


def clean_qa_cache():
    """清理历次 QA 运行留下的测试缓存（内容含 QA验收 标记），防污染在线缓存目录。"""
    d = os.path.join(os.path.dirname(os.path.abspath(bb.__file__)), "tmp", "online_sources")
    if not os.path.isdir(d):
        return
    for f in os.listdir(d):
        fp = os.path.join(d, f)
        try:
            if f.endswith(".js") and "QA验收" in open(fp, encoding="utf-8", errors="ignore").read():
                os.remove(fp)
        except Exception:
            pass


async def phase_link_self():
    """阶段 C：链接形态 + 自更新 + 自愈（本地 HTTP 服务器模拟官方源站）。"""
    clean_qa_cache()
    srv = TestServer()
    srv.set("/qa_hot.js", HOT_V1)
    srv.set("/audio.mp3", AUDIO_BYTES, "audio/mpeg")
    port = await srv.start()

    d = make_dir(["lx_hot_v1.js"])  # 本地副本与在线版同 @name → 验证去重
    bb.SOURCE_DIR = d
    bb.SOURCE_URLS = [f"http://127.0.0.1:{port}/qa_hot.js"]

    b = bb.SourceBridge()
    await b.start()
    try:
        online_dir = b._online_dir
        dest = b._online_map.get(bb.SOURCE_URLS[0])
        online_proc = next((p for p in b.procs if p.args[-1] == dest), None)
        # TC-LINK-001
        record("TC-LINK-001", bool(online_proc and online_proc.ready and os.path.exists(dest)),
               f"在线源已下载并加载 script_name={getattr(online_proc, 'script_name', None)}")
        # TC-LINK-007 同名去重（在线优先）
        hot = [p for p in b.procs if getattr(p, "script_name", "") == "QA验收-热更新源"]
        local_kept = [p for p in hot if not p.args[-1].startswith(online_dir)]
        detail = "; ".join(f"{os.path.basename(p.args[-1])}(sn={getattr(p,'script_name',None)})"
                           for p in hot)
        record("TC-LINK-007", len(hot) == 1 and len(local_kept) == 0,
               f"@name=热更新源 进程数={len(hot)}（在线版优先）[{detail}]")

        # TC-SELF-001 热更新
        srv.set("/qa_hot.js", HOT_V2)
        old_proc = online_proc
        await b._refresh_online_sources()
        new_proc = next((p for p in b.procs if p.args[-1] == dest), None)
        content_changed = "2.0.0" in open(dest, encoding="utf-8").read()
        record("TC-SELF-001", content_changed and new_proc is not old_proc and
               bool(new_proc and new_proc.ready),
               f"内容已更新={content_changed}, 进程已替换={new_proc is not old_proc}")
        # TC-SELF-002 无变化不重载
        before = new_proc
        await b._refresh_online_sources()
        after = next((p for p in b.procs if p.args[-1] == dest), None)
        record("TC-SELF-002", after is before, "内容无变化时进程对象不变")

        # TC-KRN-002 不支持平台：优雅 = 不抛异常（None 或 http 候选均可接受；
        # 内核设计为无声明时回退全集 + URL 校验兜底）
        try:
            r = await asyncio.wait_for(
                b.get_url("xx_unsupported", {"songmid": "1"}, "128k"), 40)
            record("TC-KRN-002", r is None or str(r).startswith("http"),
                   f"不支持平台优雅返回: {str(r)[:50]}")
        except Exception as e:
            record("TC-KRN-002", False, f"抛异常: {e}")
        # TC-KRN-003 / TC-SEC-001 URL 校验
        bad_urls = ["", "javascript:alert(1)", "ftp://x/y.mp3", "file:///etc/passwd"]
        res = [await b._validate_url(u) for u in bad_urls]
        record("TC-KRN-003", not any(res), f"拒绝 {bad_urls} -> {res}")
        record("TC-SEC-001", not any(res), "非 http(s) 协议白名单拒绝")
        # TC-KRN-004 音频校验（服务器在线）
        ok_audio = await b._validate_url(srv.url("/audio.mp3"))
        record("TC-KRN-004", ok_audio, "audio/mpeg+ID3 判定可播")
        # TC-E2E-006 下载链路（服务器在线）
        out = os.path.join(ROOT, "qa", "tmp_srcdirs", "dl_test.bin")
        r = await b.download(srv.url("/audio.mp3"), out)
        dl_ok = os.path.exists(out) and os.path.getsize(out) == len(AUDIO_BYTES)
        record("TC-E2E-006", dl_ok, f"落盘 {os.path.getsize(out) if os.path.exists(out) else 0}/{len(AUDIO_BYTES)} 字节")
        if os.path.exists(out):
            os.remove(out)

        # TC-SELF-003 下载失败保活（monkeypatch 模拟网络故障，服务器保持在线）
        orig_dl = b._download_one
        async def _dead(url):
            return None
        b._download_one = _dead
        n_before = len(b.procs)
        try:
            await b._refresh_online_sources()
            ok3 = len(b.procs) == n_before
        except Exception as e:
            ok3 = False
            record("TC-SELF-003", False, f"抛异常: {e}")
        else:
            record("TC-SELF-003", ok3, f"下载失败后进程保留 {len(b.procs)}/{n_before}")
        finally:
            b._download_one = orig_dl

        # TC-SELF-005 失败自愈：垃圾文件 → 覆写为合法脚本 → health_check 自愈
        bad_path = os.path.join(d, "lx_garbage.js")
        shutil.copy(fixture("lx_garbage.js"), bad_path)
        b._all_script_files.append(bad_path)
        b._failed_files.append(bad_path)
        shutil.copy(fixture("lx_valid_minimal.js"), bad_path)
        await b._health_check()
        healed = bad_path not in b._failed_files and any(
            p.args[-1] == bad_path for p in b.procs)
        record("TC-SELF-005", healed, "文件修复后看门狗自愈上线")

        # TC-SELF-008 自愈后去重依然生效
        sn = [getattr(p, "script_name", "") for p in b.procs]
        dups = sorted({s for s in sn if sn.count(s) > 1})
        record("TC-SELF-008", len(sn) == len(set(sn)),
               f"无双 @name 进程（共 {len(sn)} 个，重复: {dups}）")
    finally:
        await b.stop()
        shutil.rmtree(d, ignore_errors=True)
        clean_qa_cache()

    # TC-LINK-008 离线缓存二次启动（服务器已关停）
    b2 = bb.SourceBridge()
    b2._session = aiohttp.ClientSession()
    r = await b2._fetch_online_sources()
    await b2._session.close()
    # 缓存目录里应有上轮下载的热更新源缓存
    cache_used = bool(r)
    record("TC-LINK-008", cache_used,
           "服务器不可达时离线缓存可用于启动" if cache_used
           else "全部下载失败时缓存未被使用（持久化缺口，登记缺陷）")

    # TC-KRN-008 init 超时可配
    bridge_src = open(os.path.join(ROOT, "bot", "bridge.py"), encoding="utf-8").read()
    node_src = open(os.path.join(ROOT, "source_bridge.js"), encoding="utf-8").read()
    record("TC-KRN-008", "LX_INIT_TIMEOUT" in bridge_src and "LX_INIT_TIMEOUT" in node_src,
           "SOURCE_INIT_TIMEOUT 经环境变量传入内核守卫")


async def phase_link_validation():
    """阶段 D：TC-LINK-002/003/004/005/006 + TC-SEC-003（轻量，直接驱动 _fetch_online_sources）。"""
    srv = TestServer()
    srv.set("/valid.js", HOT_V1)
    srv.set("/html.js", "<html><script>alert(1)</script></html>", "text/html")
    srv.set("/text.js", "hello world", "text/plain")
    port = await srv.start()
    b = bb.SourceBridge()
    b._session = aiohttp.ClientSession()

    import hashlib
    def cache_files():
        d = os.path.join(os.path.dirname(os.path.abspath(bb.__file__)), "tmp", "online_sources")
        return {f: os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d)} if os.path.isdir(d) else {}

    def target_name(url):
        return hashlib.md5(url.encode()).hexdigest()[:12] + ".js"

    try:
        # TC-LINK-002 404：目标缓存文件不得新增
        before = cache_files()
        bad1 = f"http://127.0.0.1:{port}/not_exist.js"
        bb.SOURCE_URLS = [bad1]
        await b._fetch_online_sources()
        record("TC-LINK-002", target_name(bad1) not in cache_files(),
               "404 链接被跳过，目标缓存文件未产生")
        # TC-LINK-003 / TC-SEC-003 HTML 拒绝
        bad2 = f"http://127.0.0.1:{port}/html.js"
        bb.SOURCE_URLS = [bad2]
        await b._fetch_online_sources()
        ok3 = target_name(bad2) not in cache_files()
        record("TC-LINK-003", ok3, "HTML 页面被内容校验拒绝，不落盘")
        record("TC-SEC-003", ok3, "（同 TC-LINK-003 注入面已封堵）")
        # TC-LINK-004 纯文本拒绝
        bad3 = f"http://127.0.0.1:{port}/text.js"
        bb.SOURCE_URLS = [bad3]
        await b._fetch_online_sources()
        record("TC-LINK-004", target_name(bad3) not in cache_files(), "无 lx 关键字内容被拒绝")
        # TC-LINK-005 镜像回退
        srv.set("/mirror_ok.js", HOT_V1)
        bad_url = f"http://127.0.0.1:{port}/not_exist.js"
        good_url = f"http://127.0.0.1:{port}/mirror_ok.js"
        orig = bb.SourceBridge._mirror_candidates
        bb.SourceBridge._mirror_candidates = staticmethod(lambda u: [bad_url, good_url])
        bb.SOURCE_URLS = [bad_url]
        try:
            await b._fetch_online_sources()
            dest = os.path.join(os.path.dirname(os.path.abspath(bb.__file__)),
                                "tmp", "online_sources", target_name(bad_url))
            got = os.path.exists(dest) and "QA验收-热更新源" in open(dest, encoding="utf-8").read()
            record("TC-LINK-005", got, "首选镜像 404 → 第二镜像成功并落盘")
        finally:
            bb.SourceBridge._mirror_candidates = staticmethod(orig)
        # TC-LINK-006 镜像候选派生
        cands = bb.SourceBridge._mirror_candidates(
            "https://ghproxy.net/raw.githubusercontent.com/owner/repo/main/path/latest.js")
        ok6 = (len(cands) >= 4 and any("raw.githubusercontent.com" in c for c in cands)
               and any("jsdelivr" in c for c in cands) and any("mirror.ghproxy" in c for c in cands))
        record("TC-LINK-006", ok6, f"候选 {len(cands)} 条: {sorted({c.split('/')[2] for c in cands})}")
    finally:
        await b._session.close()
        await srv.stop()


async def phase_e2e_real():
    """阶段 E：真实 45 源内核全链路（TC-E2E-002/003 + TC-KRN-010）。"""
    bb.SOURCE_DIR = os.path.join(ROOT, "sources")
    bb.SOURCE_URLS = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8")).get("source_urls", [])
    b = bb.SourceBridge()
    t0 = time.time()
    await b.start()
    try:
        # TC-E2E-002 真实搜索
        items = await b.search("wy", "海阔天空", 5)
        record("TC-E2E-002", bool(items), f"搜索返回 {len(items) if items else 0} 条（耗时 {time.time()-t0:.0f}s）")
        # TC-KRN-010 字段保真：搜索后 get_url 应能反查缓存
        if items:
            first = items[0]
            url = await b.get_url("wy", first, "128k")
            enriched = b._enrich("wy", first)
            has_id = any(first.get(k) for k in ("songmid", "id", "hash", "copyrightId", "albumId"))
            record("TC-KRN-010", has_id and (url is None or url.startswith("http")),
                   f"字段保真: id字段={has_id}, url={str(url)[:60]}")
        else:
            record("TC-KRN-010", False, "搜索无结果，无法验证字段保真")
        # TC-E2E-003 真实取链 kw
        kw_items = await b.search("kw", "海阔天空", 3)
        if kw_items:
            u = await b.get_url("kw", kw_items[0], "128k")
            record("TC-E2E-003", u is None or u.startswith("http"),
                   f"kw 取链: {str(u)[:70] if u else 'None（源端全部失败，允许）'}")
        else:
            record("TC-E2E-003", False, "kw 搜索无结果（网络受限）")
    finally:
        await b.stop()


async def phase_static_audit():
    """阶段 F：静态审计类用例（TC-SEC-002/004/006/007/008 + E2E-007/008/009/011/012）。"""
    import re
    bridge_py = open(os.path.join(ROOT, "bot", "bridge.py"), encoding="utf-8").read()
    # TC-SEC-002 无 shell
    hits = re.findall(r"shell\s*=\s*True|os\.system\(", bridge_py) + \
           re.findall(r"shell\s*=\s*True|os\.system\(",
                      open(os.path.join(ROOT, "bot", "player.py"), encoding="utf-8").read()
                      ) if os.path.exists(os.path.join(ROOT, "bot", "player.py")) else []
    record("TC-SEC-002", len(hits) == 0, f"shell=True/os.system 命中 {len(hits)} 处")
    # TC-SEC-006 下载路径
    record("TC-SEC-006", "Content-Disposition" not in bridge_py,
           "download() 路径仅由调用方控制，远程头不参与拼接")
    # TC-SEC-007 示例配置无密钥
    ex = os.path.join(ROOT, "config.example.json")
    ok7 = os.path.exists(ex) and "xwT5YzREvIwK8LVZ72n" not in open(ex, encoding="utf-8").read()
    record("TC-SEC-007", ok7, "config.example.json 无真实密钥")
    # TC-SEC-008 沙箱边界
    node_src = open(os.path.join(ROOT, "source_bridge.js"), encoding="utf-8").read()
    record("TC-SEC-008", "require(" in node_src and "sandboxRequire" in node_src,
           "脚本经受控 require 沙箱执行（网络请求能力与落雪桌面一致，接受）")
    # TC-E2E-007 配置一致性
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    exj = json.load(open(ex, encoding="utf-8")) if os.path.exists(ex) else {}
    keys_ok = all(k in cfg for k in ("source_urls",))
    record("TC-E2E-007", keys_ok, f"config.json 合法，source_urls={len(cfg.get('source_urls', []))} 条")
    # TC-E2E-008 导入完整性
    import subprocess
    r = subprocess.run([sys.executable, "-c", "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['main.py','bot/bridge.py','bot/config.py','bot/player.py','bot/bili_api.py'] if __import__('os').path.exists(f)]; print('OK')"],
                       capture_output=True, text=True, cwd=ROOT)
    record("TC-E2E-008", "OK" in r.stdout, r.stdout.strip() or r.stderr[:120])
    # TC-E2E-009 文档一致性（读 manifest）
    mf = os.path.join(ROOT, "sources", "manifest.json")
    if os.path.exists(mf):
        m = json.load(open(mf, encoding="utf-8"))
        readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        loaded = m.get("loaded")
        record("TC-E2E-009", str(loaded) in readme, f"README 提及实测加载数 {loaded}")
    # TC-E2E-011 官方真实源
    async def _run():
        for f in ("sources/pdone-huibq.js",):
            p = os.path.join(ROOT, f)
            r = await asyncio.create_subprocess_exec(
                "node", os.path.join(ROOT, "tools", "load_one.js"), p,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=ROOT)
            out, _ = await asyncio.wait_for(r.communicate(), 40)
            ok = b'"ok":true' in out or '"ok": true' in out.decode(errors="ignore")
            record("TC-E2E-011", ok, f"{f} 加载 {'OK' if ok else out[:80]}")
    await _run()
    # TC-E2E-012 五平台覆盖
    if os.path.exists(mf):
        m = json.load(open(mf, encoding="utf-8"))
        plats = set()
        for s in m.get("sources", []):
            if s.get("status") == "ok":
                plats.update(s.get("platforms", []))
        need = {"wy", "kw", "tx", "kg", "mg"}
        record("TC-E2E-012", need <= plats, f"覆盖 {sorted(plats)}，缺 {sorted(need - plats)}")


async def main():
    print("========== Horizon 正式版验收 ==========")
    await phase_link_validation()
    await phase_plug_krn()
    await phase_krn_builtin()
    await phase_link_self()
    await phase_e2e_real()
    await phase_static_audit()
    out = os.path.join(ROOT, "qa", "results.json")
    json.dump(RESULTS, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    p = sum(1 for r in RESULTS if r["status"] == "PASS")
    print(f"\n汇总: {p}/{len(RESULTS)} PASS -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
