"""lx 音源桥接器客户端：**多进程隔离架构**。

每个音源脚本一个 Node 子进程（node source_bridge.js <script.js>），外加一个
内置引擎进程（node source_bridge.js --builtin，lx musicSdk 搜索/URL）。
单个脚本的同步阻塞/崩溃只影响其自身进程，不影响其他音源——对齐 lx 桌面
的沙箱隔离策略，支撑"全部音源都可用"。

对 bot 其他模块的接口不变：start()/search()/get_url()/download()/stop()。
"""
import asyncio
import hashlib
import json
import os
import re
from typing import Dict, List, Optional

import aiohttp

from .config import (BRIDGE_SCRIPT, NODE_PATH, SOURCE_DIR, SOURCE_URLS, log,
                     SOURCE_INIT_TIMEOUT, SOURCE_REFRESH_INTERVAL,
                     SOURCE_WATCHDOG_INTERVAL)

SEARCH_TIMEOUT = 10     # 单脚本搜索超时（秒）
READY_TIMEOUT = max(25, SOURCE_INIT_TIMEOUT + 15)  # 单脚本加载超时（秒）≥脚本侧 init 超时
URL_TIMEOUT = 15        # 单脚本取 URL 超时（秒，真实脚本需网络往返）
COLLECT_WINDOW = 8      # 取 URL 收集候选窗口（秒）：快源先返回，慢/死源不入围


class _ScriptProc:
    """单个 Node 子进程（承载一个音源脚本或内置引擎）。"""

    def __init__(self, name: str, args: List[str]):
        self.name = name
        self.script_name = name           # 脚本 @name（去重用），就绪后由 ready 载荷修正
        self.args = args
        self.process: Optional[asyncio.subprocess.Process] = None
        self.sources: Dict[str, dict] = {}  # 声明的能力 {source: {actions: []}}
        self.ready = False
        self._req_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            NODE_PATH, BRIDGE_SCRIPT, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "LX_INIT_TIMEOUT": str(SOURCE_INIT_TIMEOUT)},
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await asyncio.wait_for(self._wait_ready(), timeout=READY_TIMEOUT)
        except Exception as e:
            await self.stop()
            raise RuntimeError(f"{self.name} 加载失败: {e}")

    async def _wait_ready(self):
        while not self.ready:
            await asyncio.sleep(0.1)

    async def _read_loop(self):
        while self.process and self.process.returncode is None:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                data = json.loads(line.decode("utf-8").strip())
                if data.get("type") == "ready":
                    self.sources = data.get("sources", {}) or {}
                    self.script_name = data.get("scriptName") or self.name
                    self.ready = True
                    continue
                req_id = data.get("id")
                if req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if data.get("success"):
                            fut.set_result(data)
                        else:
                            fut.set_exception(Exception(data.get("error", "未知错误")))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug(f"[{self.name}] 读取异常: {e}")

    async def _drain_stderr(self):
        while self.process and self.process.returncode is None:
            line = await self.process.stderr.readline()
            if not line:
                break
            log.debug(f"[{self.name}] {line.decode('utf-8', errors='replace').rstrip()}")

    def supports(self, action: str, source: str = "") -> bool:
        if not self.ready:
            return False
        if source:
            # QA 修复（BUG-003）：此前 `source in s` 是对 {'actions':[...]} 的键检查，
            # 恒为 False，平台声明路由失效（全部走全集兜底）。改为按声明正确判定。
            s = self.sources.get(source) or {}
            return action in (s.get("actions") or [])
        return True

    async def request(self, payload: dict, timeout: float):
        """发请求并等待响应；进程死亡/超时抛异常。"""
        if not self.process or self.process.returncode is not None:
            raise RuntimeError(f"{self.name} 进程已退出")
        self._req_id += 1
        req_id = self._req_id
        payload = dict(payload, id=req_id)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        try:
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def stop(self):
        for t in (self._reader_task, self._stderr_task):
            if t:
                t.cancel()
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.ready = False


# 完整 musicInfo 缓存中可能用于匹配 song_id 的键（与 source_bridge.js 保持一致）
_CACHE_KEYS = ("id", "songmid", "hash", "copyrightId", "songId", "strMediaMid")


class SourceBridge:
    """音源桥接器（多进程池）。对外接口与单进程版完全一致。"""

    def __init__(self):
        self.procs: List[_ScriptProc] = []      # 脚本进程池
        self.builtin: Optional[_ScriptProc] = None  # 内置引擎进程
        self._session: Optional[aiohttp.ClientSession] = None
        # 后台自愈/自更新任务
        self._bg_tasks: List[asyncio.Task] = []
        # 全部候选脚本文件（本地 + 在线），供看门狗重试初始化失败者
        self._all_script_files: List[str] = []
        # 初始化失败的文件（后端不可达等），看门狗周期性重试以期自愈
        self._failed_files: List[str] = []
        # 在线 URL -> 缓存文件路径（自更新热重载用）
        self._online_map: Dict[str, str] = {}
        self._online_dir: Optional[str] = None
        # 字段保真缓存：搜索阶段拿到的完整 musicInfo 按 (source, idKey) 缓存，
        # 取 URL 时反查补全（对齐落雪：搜索→播放传递完整对象）。多进程下必须放在
        # Python 侧，否则"搜索"和"取 URL"命中不同脚本进程会拿不到 ID。
        self._music_info_cache: Dict[str, dict] = {}

    # ---------------------------------------------------------------- 启动

    async def start(self):
        if not os.path.exists(BRIDGE_SCRIPT):
            raise FileNotFoundError(f"桥接器脚本不存在: {BRIDGE_SCRIPT}")
        if not os.path.exists(SOURCE_DIR):
            raise FileNotFoundError(f"音源目录不存在: {SOURCE_DIR}")

        self._session = aiohttp.ClientSession()
        online_dir = await self._fetch_online_sources()
        script_files = []
        for d in [SOURCE_DIR] + ([online_dir] if online_dir else []):
            if os.path.isdir(d):
                script_files += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".js")]
        self._all_script_files = list(script_files)
        self._online_dir = online_dir
        # 在线 URL -> 缓存文件映射（自更新热重载时定位对应进程）
        for url in SOURCE_URLS:
            name = hashlib.md5(url.encode()).hexdigest()[:12] + ".js"
            if online_dir:
                self._online_map[url] = os.path.join(online_dir, name)

        # 内置引擎进程（始终启动：脚本全挂时仍能搜索——海量曲库兜底）
        self.builtin = _ScriptProc("内置引擎", ["--builtin"])

        # 每脚本一个进程，全部并行启动
        procs = [_ScriptProc(os.path.splitext(os.path.basename(f))[0], [f]) for f in script_files]
        all_procs = procs + [self.builtin]
        results = await asyncio.gather(*(p.start() for p in all_procs), return_exceptions=True)
        alive = []
        for p, r in zip(all_procs, results):
            if isinstance(r, Exception):
                log.warning(f"音源加载失败（已隔离）: {p.name} - {r}")
                if p is self.builtin:
                    self.builtin = None
            else:
                alive.append(p)
        self.procs = [p for p in procs if p in alive]
        # 记录初始化失败的文件，供看门狗周期重试（后端恢复即自愈）
        alive_files = {p.args[-1] for p in self.procs}
        self._failed_files = [f for f in script_files if f not in alive_files]

        # 同音源去重：本地 .js 与在线 URL 指向同一官方源时（如 pdone-*），
        # 在线版本优先（更新更及时），停掉重复加载的本地副本——避免同一源双进程竞速。
        self._dedupe_by_script_name(online_dir)

        n_online = len([p for p in self.procs if online_dir and p.args[-1].startswith(online_dir)])
        log.info(f"音源桥接器就绪: 脚本进程 {len(self.procs)}/{len(script_files)}"
                 + (f"（含在线导入 {n_online}）" if online_dir else "")
                 + (" + 内置引擎" if self.builtin else "")
                 + (f"；{len(self._failed_files)} 个初始化失败待自愈" if self._failed_files else ""))

        # 启动后台自愈/自更新（即「自检测」「保持更新」，永不失效）
        self._bg_tasks = [
            asyncio.create_task(self._self_update_loop(), name="source-self-update"),
            asyncio.create_task(self._watchdog_loop(), name="source-watchdog"),
        ]

    def _dedupe_by_script_name(self, online_dir: Optional[str]):
        """按脚本 @name 去重：同名的多个运行进程，在线版本优先，停掉其余本地副本。"""
        by_name: Dict[str, List[_ScriptProc]] = {}
        for p in self.procs:
            nm = getattr(p, "script_name", None) or p.name
            by_name.setdefault(nm, []).append(p)
        removed = []
        for nm, group in by_name.items():
            if len(group) <= 1:
                continue
            online = [p for p in group if online_dir and p.args[-1].startswith(online_dir)]
            keep = online if online else [group[0]]
            for p in group:
                if p not in keep:
                    removed.append(p)
        if removed:
            for p in removed:
                try:
                    asyncio.create_task(p.stop())
                except Exception:
                    pass
            self.procs = [p for p in self.procs if p not in removed]
            log.info(f"同音源去重：停用 {len(removed)} 个重复加载的本地副本（保留在线/首选版本）")

    @staticmethod
    def _mirror_candidates(url: str) -> List[str]:
        """为 GitHub raw 链接生成多镜像候选，提高下载持久性（任一镜像可达即可）。

        落雪官方/社区音源多托管在 GitHub，单一反代（如 ghproxy.net）偶发不可用。
        这里自动派生「直连 raw」「mirror.ghproxy.com」「jsdelivr」等候选，按序重试，
        任一成功即采用——对齐 lx 桌面「链接导入音源」的官方源持久化思路。
        """
        candidates = [url]
        if url.startswith("https://ghproxy.net/raw.githubusercontent.com/"):
            candidates.append(url.replace(
                "https://ghproxy.net/raw.githubusercontent.com/",
                "https://raw.githubusercontent.com/", 1))
        if url.startswith("https://ghproxy.net/"):
            candidates.append(url.replace(
                "https://ghproxy.net/", "https://mirror.ghproxy.com/", 1))
        m = re.match(
            r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(?:main|master)/(.+)",
            url)
        if m:
            owner, repo, path = m.group(1), m.group(2), m.group(3)
            candidates.append(f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@main/{path}")
            candidates.append(f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@main/{path}")
        # BUG-002 修复：ghproxy 形式链接同样派生 jsdelivr 候选
        # （此前仅 ghproxy→raw→mirror 三条链，jsdelivr 未覆盖 ghproxy 形式）
        for c in list(candidates):
            m2 = re.match(
                r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(?:main|master)/(.+)", c)
            if m2:
                owner, repo, path = m2.group(1), m2.group(2), m2.group(3)
                candidates.append(f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@main/{path}")
                candidates.append(f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@main/{path}")
        # 去重保序
        seen, out = set(), []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    async def _fetch_online_sources(self) -> Optional[str]:
        """下载 config source_urls 中的在线音源脚本（对齐 lx 的"链接导入音源"）。

        每个 URL 自动尝试多镜像候选，任一可达即采用；失败仅记警告、不影响启动，
        全部失败时回退到本地 sources/ 离线基线——保证「官方源持久化」与「离线可用」兼得。
        """
        if not SOURCE_URLS:
            return None
        cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "online_sources")
        os.makedirs(cache_dir, exist_ok=True)
        ok = 0
        for url in SOURCE_URLS:
            name = hashlib.md5(url.encode()).hexdigest()[:12] + ".js"
            dest = os.path.join(cache_dir, name)
            text = None
            last_err = ""
            for cand in self._mirror_candidates(url):
                try:
                    async with self._session.get(
                            cand, timeout=aiohttp.ClientTimeout(total=15),
                            headers={"User-Agent": "Mozilla/5.0"}) as resp:
                        if resp.status >= 400:
                            last_err = f"HTTP {resp.status}"
                            continue
                        body = await resp.text()
                        if (body.lstrip().startswith("<")
                                or ("lx" not in body and "EVENT_NAMES" not in body)):
                            last_err = "内容非脚本"
                            continue
                        text = body
                        break
                except Exception as e:
                    last_err = str(e) or "异常"
            if not text:
                log.warning(f"在线音源下载失败（已跳过，回退本地）: {url[:70]} - {last_err}")
                continue
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
            ok += 1
            log.info(f"在线音源已加载: {url[:70]} -> {name}")
        if ok == 0:
            # BUG-001 修复：全部在线下载失败时回退使用上次的离线缓存——
            # 链接形式音源的持久化缺口（服务器/网络极端不可用时仍可服役）。
            cached = [f for f in (os.listdir(cache_dir) if os.path.isdir(cache_dir) else [])
                      if f.endswith(".js")]
            if cached:
                log.info(f"在线音源全部下载失败，回退离线缓存服役: {len(cached)} 个")
                return cache_dir
        return cache_dir if ok else None

    # ---------------------------------------------------------------- 自更新 / 自检

    async def _self_update_loop(self):
        """后台自更新：定时重新拉取 source_urls，内容有变即热重载对应音源进程，
        保证「链接形式音源」始终为官方最新版，永不滞后。"""
        interval = max(600, int(SOURCE_REFRESH_INTERVAL * 3600))
        await asyncio.sleep(min(interval, 120))  # 启动后稍候再首轮更新，避免冷启动抖动
        while True:
            await asyncio.sleep(interval)
            try:
                await self._refresh_online_sources()
            except Exception as e:
                log.warning(f"在线音源自更新异常: {e}")

    async def _refresh_online_sources(self):
        """重新拉取全部 source_urls：下载成功且与缓存内容不同则热重载对应进程。"""
        if not SOURCE_URLS:
            return
        for url in SOURCE_URLS:
            dest = self._online_map.get(url)
            if not dest:
                continue
            text = await self._download_one(url)
            if not text:
                continue  # 下载失败：保留现有进程（上次成功版），下轮再试
            new_hash = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()
            old_hash = ""
            if os.path.exists(dest):
                with open(dest, "rb") as f:
                    old_hash = hashlib.md5(f.read()).hexdigest()
            if new_hash == old_hash:
                continue  # 内容无变化，跳过
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
            await self._reload_online_source(url, dest)
            log.info(f"在线音源已热更新: {url[:70]}")

    async def _download_one(self, url: str) -> Optional[str]:
        """用多镜像候选下载单个 URL，返回脚本文本或 None（任一镜像可达即采用）。"""
        for cand in self._mirror_candidates(url):
            try:
                async with self._session.get(
                        cand, timeout=aiohttp.ClientTimeout(total=15),
                        headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status >= 400:
                        continue
                    body = await resp.text()
                    if (body.lstrip().startswith("<")
                            or ("lx" not in body and "EVENT_NAMES" not in body)):
                        continue
                    return body
            except Exception:
                continue
        return None

    async def _reload_online_source(self, url: str, dest: str):
        """停掉该在线源旧进程，用新文件启动新进程，无缝替换（不影响其他音源）。"""
        old = next((p for p in self.procs if p.args[-1] == dest), None)
        new = _ScriptProc(os.path.splitext(os.path.basename(dest))[0], [dest])
        try:
            await new.start()
        except Exception as e:
            log.warning(f"在线音源热重载失败（保留旧版继续服役）: {url[:60]} - {e}")
            return
        if old:  # 新版本就绪，停掉旧版
            await old.stop()
            self.procs = [p for p in self.procs if p is not old]
        self.procs.append(new)
        self._dedupe_by_script_name(os.path.dirname(dest))

    async def _watchdog_loop(self):
        """后台看门狗/自检：周期性重启崩溃进程、重试初始化失败的音源（后端恢复即自愈），
        确保「永不失效」——除非音源后端彻底消亡（极端情况）。"""
        interval = max(60, int(SOURCE_WATCHDOG_INTERVAL))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._health_check()
            except Exception as e:
                log.warning(f"音源看门狗自检异常: {e}")

    async def _health_check(self):
        # 1) 重启运行时崩溃的进程（returncode 非 None）
        for p in list(self.procs):
            if p.process is None or p.process.returncode is not None:
                try:
                    await p.stop()
                except Exception:
                    pass
                try:
                    await p.start()
                    log.info(f"看门狗重启了崩溃进程: {p.name}")
                except Exception as e:
                    log.warning(f"进程重启失败: {p.name} - {e}")
        # 2) 重试初始化失败的音源（后端恢复即自愈，重新上线）
        still_failed = []
        for f in list(self._failed_files):
            proc = _ScriptProc(os.path.splitext(os.path.basename(f))[0], [f])
            try:
                await proc.start()
                self.procs.append(proc)
                log.info(f"看门狗自愈成功: {os.path.basename(f)} 已恢复上线")
            except Exception:
                still_failed.append(f)
        if self._failed_files and not still_failed:
            log.info("看门狗：全部此前初始化失败的音源已恢复")
        self._failed_files = still_failed
        self._dedupe_by_script_name(self._online_dir)

    # ---------------------------------------------------------------- 字段保真缓存

    def _cache_search(self, source: str, items):
        """搜索结果（完整 musicInfo 列表）按多键缓存，供取 URL 时反查补全。"""
        if not isinstance(items, list):
            return
        for it in items:
            if not isinstance(it, dict):
                continue
            for key in _CACHE_KEYS:
                v = it.get(key)
                if v not in (None, ""):
                    s = str(v)
                    if s.startswith("MUSIC_"):
                        s = s[6:]
                    self._music_info_cache[f"{source}:{s}"] = it
                    self._music_info_cache[f"{source}:MUSIC_{s}"] = it

    def _enrich(self, source: str, song_info: dict) -> dict:
        """取 URL 前用缓存补全完整 musicInfo：缓存命中则以完整对象为主、叠加瘦字段，
        确保脚本拿到的 hash/strMediaMid/copyrightId/types 等字段不丢失。"""
        if not isinstance(song_info, dict):
            return song_info
        candidates = []
        for key in ("song_id", "hash", "strMediaMid", "songmid", "copyright_id", "id", "songId"):
            v = song_info.get(key)
            if v not in (None, ""):
                s = str(v)
                candidates.append(s)
                if s.startswith("MUSIC_"):
                    candidates.append(s[6:])
        for c in candidates:
            hit = self._music_info_cache.get(f"{source}:{c}")
            if hit:
                merged = dict(hit)  # 完整对象优先
                # 用瘦字段覆盖/补全可变的展示信息，保留脚本所需 ID 字段
                for fld in ("name", "singer", "album", "interval", "img"):
                    if song_info.get(fld) not in (None, ""):
                        merged[fld] = song_info[fld]
                if song_info.get("albumName") and not merged.get("albumName"):
                    merged["albumName"] = song_info["albumName"]
                merged["source"] = source
                return merged
        return song_info

    # ---------------------------------------------------------------- 内部

    def _procs_for(self, action: str, source: str) -> List[_ScriptProc]:
        """按能力声明路由；无声明者时回退到全部存活脚本进程 + 内置引擎。"""
        declared = [p for p in self.procs if p.supports(action, source)]
        if declared:
            return declared
        fallback = [p for p in self.procs if p.ready]
        if self.builtin and self.builtin.supports(action, source):
            fallback.append(self.builtin)
        return fallback

    async def _race(self, procs: List[_ScriptProc], payload_tpl: dict, timeout: float,
                    collect_window: Optional[float] = None, collect_all: bool = False):
        """并发竞速。

        - collect_all=False：返回首个成功且非空的结果（其余取消）。
        - collect_all=True（仅 musicUrl）：在 collect_window 内收集全部 http URL 候选，
          返回列表，交由调用方校验后择优（避免"死链先返回抢赢"）。
        """
        if not procs:
            raise RuntimeError("没有可用音源进程")
        action = payload_tpl.get("action")
        tasks = {asyncio.create_task(p.request(dict(payload_tpl), timeout)): p for p in procs}
        deadline = asyncio.get_event_loop().time() + (collect_window or timeout)
        collected: List[str] = []
        try:
            while tasks:
                remain = max(0.05, deadline - asyncio.get_event_loop().time())
                done_now, tasks = await asyncio.wait(tasks, timeout=remain,
                                                     return_when=asyncio.FIRST_COMPLETED)
                for t in done_now:
                    if t.cancelled():
                        continue
                    try:
                        data = t.result()
                    except Exception:
                        continue
                    if action == "search":
                        v = data.get("data")
                        if v and (not isinstance(v, list) or len(v) > 0):
                            if collect_all:
                                collected.append(v)
                            else:
                                return v
                    else:
                        v = data.get("url")
                        if v and isinstance(v, str) and v.startswith("http"):
                            if collect_all:
                                if v not in collected:
                                    collected.append(v)
                            else:
                                return v
                if collect_window and asyncio.get_event_loop().time() >= deadline:
                    break
        finally:
            for t in tasks:
                t.cancel()
        if collect_all:
            return collected
        raise RuntimeError("所有音源进程请求失败")

    # ---------------------------------------------------------------- 接口

    async def _validate_url(self, url: str) -> bool:
        """快速校验 URL 是否真实可播：小范围 Range 请求，要求返回音频内容
        （content-type 为 audio/* 或二进制魔数，且体积极小/非空、非 text/html）。
        死链（如已失效的 music.3e0.cn 返回 200+0字节 text/html）会被判否。"""
        if not url or not url.startswith("http"):
            return False
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                       "Range": "bytes=0-65535"}
            async with self._session.get(url, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=8),
                                        allow_redirects=True) as resp:
                if resp.status not in (200, 206):
                    return False
                ct = (resp.headers.get("Content-Type", "") or "").lower()
                if ct.startswith("text/html") or ct.startswith("application/json"):
                    return False
                data = await resp.content.read(4096)
                if not data or len(data) < 64:
                    return False
                # 音频魔数快速识别
                head = data[:4]
                if head in (b"ID3", b"OggS", b"RIFF", b"ftyp") or head[:3] == b"fLa":
                    return True
                if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:  # MP3 帧同步
                    return True
                # 退路：二进制 content-type 且体量正常也视为可播
                if ct.startswith("audio/") or "octet-stream" in ct:
                    return True
                return False
        except Exception:
            return False

    async def search(self, source: str, keyword: str, limit: int = 5) -> Optional[list]:
        """搜索：声明支持 search 的脚本进程竞速；全败时内置引擎兜底。
        命中结果会缓存完整 musicInfo（字段保真），供 get_url 反查补全。"""
        try:
            procs = self._procs_for("search", source)
            data = await self._race(procs, {"action": "search", "source": source,
                                            "keyword": keyword, "limit": limit}, SEARCH_TIMEOUT)
            if data:
                self._cache_search(source, data)
            return data
        except Exception as e:
            log.debug(f"脚本搜索 {source} '{keyword}' 失败: {e}，内置引擎兜底")
        if self.builtin and self.builtin.ready:
            try:
                data = await self.builtin.request({"action": "search", "source": source,
                                                   "keyword": keyword, "limit": limit},
                                                  SEARCH_TIMEOUT + 4)
                v = data.get("data")
                if isinstance(v, list) and v:
                    self._cache_search(source, v)
                    return v
                if isinstance(v, dict):
                    self._cache_search(source, [v])
                    return [v]
            except Exception as e:
                log.debug(f"内置引擎搜索失败: {e}")
        return None

    async def get_url(self, source: str, song_info: dict, quality: str) -> Optional[str]:
        """musicUrl：收集所有脚本/内置引擎返回的候选 URL，校验真实可播后择优返回。
        死链（失效 API 返回 0 字节 text/html）被校验拒绝，自动落到下一个真实音源——
        避免"快返回的死链抢赢真实音源"。全败时返回 None。"""
        enriched = self._enrich(source, song_info)
        candidates: List[str] = []
        try:
            procs = self._procs_for("musicUrl", source)
            # 收集窗口内拿到全部候选（快源 2-5s 返回，慢/死源不入围）
            got = await self._race(procs, {"action": "musicUrl", "source": source,
                                          "info": enriched, "type": quality},
                                  URL_TIMEOUT, collect_window=COLLECT_WINDOW,
                                  collect_all=True)
            if isinstance(got, list):
                candidates += got
        except Exception as e:
            log.debug(f"脚本 URL 收集 {source} 失败: {e}")
        # 内置引擎兜底候选
        if self.builtin and self.builtin.ready:
            try:
                d = await self.builtin.request({"action": "musicUrl", "source": source,
                                                "info": enriched, "type": quality}, URL_TIMEOUT)
                u = d.get("url")
                if u and str(u).startswith("http") and u not in candidates:
                    candidates.append(u)
            except Exception as e:
                log.debug(f"内置引擎 URL {source} 失败: {e}")

        if not candidates:
            return None
        # 并发校验，返回首个真实可播 URL
        results = await asyncio.gather(*(self._validate_url(u) for u in candidates))
        for u, ok in zip(candidates, results):
            if ok:
                return u
        # 校验全部失败（网络隔离等）：退而求其次返回首个原始候选
        log.debug(f"{source} 全部候选校验失败，退回首个原始 URL")
        return candidates[0]

    async def download(self, url: str, output_path: str, headers: dict = None) -> dict:
        """Node 原生下载（aiohttp 失败的回退）：发给第一个存活进程。"""
        alive = ([self.builtin] if (self.builtin and self.builtin.ready) else []) + \
                [p for p in self.procs if p.ready]
        if not alive:
            raise RuntimeError("没有存活的桥接进程")
        data = await alive[0].request({"action": "download", "url": url,
                                       "outputPath": output_path, "headers": headers or {}}, 30)
        return data.get("data") or {}

    async def stop(self):
        for t in self._bg_tasks:
            if not t.done():
                t.cancel()
        self._bg_tasks = []
        for p in self.procs + ([self.builtin] if self.builtin else []):
            await p.stop()
        self.procs = []
        self.builtin = None
        if self._session:
            await self._session.close()
            self._session = None


# 进程级单例：由 main.py 启动时 set_bridge() 注入；search/player/core 通过 get_bridge() 获取
_bridge: Optional[SourceBridge] = None


def set_bridge(bridge: SourceBridge):
    global _bridge
    _bridge = bridge


def get_bridge() -> SourceBridge:
    if _bridge is None:
        raise RuntimeError("音源桥接器尚未启动（main.py 中应先调用 set_bridge）")
    return _bridge
