"""本地 HTTP API：把点歌机器人的真实状态与控制能力暴露给桌面 UI。

安全边界：
- 只监听 127.0.0.1，不对局域网开放；
- 可选 token（环境变量 SONGBOT_API_TOKEN，由 Electron 外壳注入），
  除 /api/health 外均需 `X-SongBot-Token` 头一致；
- 所有接口仅做状态读取或调用既有内核方法（搜索/入队/播放/切歌/设置），
  不另起一套点歌逻辑。
"""
import asyncio
import json
import os
import time
from typing import Optional

from aiohttp import web

from . import config as config_module
from . import danmaku as danmaku_module
from . import events
from .config import CONFIG_PATH, log
from .models import QueueItem, SongInfo
from .search import PLATFORM_NAMES, QUALITY_LADDER, suggest_song

DEFAULT_PORT = 8790
MAX_BODY = 64 * 1024


# ---------------------------------------------------------------- 序列化


def song_dict(song: Optional[SongInfo]) -> Optional[dict]:
    if song is None:
        return None
    return {
        "name": song.name,
        "singer": song.singer,
        "album": song.album,
        "song_id": song.song_id,
        "platform": song.platform,
        "platform_name": PLATFORM_NAMES.get(song.platform, song.platform),
        "interval": song.interval,
        "score": song.score,
        "alternates": [song_dict(a) for a in (song.alternates or [])],
    }


def song_from_dict(raw: dict) -> Optional[SongInfo]:
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        return None
    alternates = [song_from_dict(a) for a in raw.get("alternates") or [] if isinstance(a, dict)]
    return SongInfo(
        name=str(raw.get("name") or ""),
        singer=str(raw.get("singer") or ""),
        album=str(raw.get("album") or ""),
        song_id=str(raw.get("song_id") or ""),
        platform=str(raw.get("platform") or ""),
        interval=str(raw.get("interval") or ""),
        score=raw.get("score") or 0,
        alternates=[a for a in alternates if a],
    )


def _mask(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    return "•" * 12 + value[-4:] if len(value) > 12 else "•" * len(value)


def _read_cfg() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return dict(config_module.CFG)


class ApiServer:
    def __init__(self, bot):
        self.bot = bot
        self.token = os.environ.get("SONGBOT_API_TOKEN", "").strip()
        self.port: Optional[int] = None
        self.runner: Optional[web.AppRunner] = None
        self.app = web.Application(middlewares=[self._middleware], client_max_size=MAX_BODY)
        self._routes()

    # ------------------------------------------------------------ 启动/停止

    def _routes(self):
        add = self.app.router.add_post
        self.app.router.add_get("/api/health", self.h_health)
        self.app.router.add_get("/api/snapshot", self.h_snapshot)
        self.app.router.add_get("/api/queue", self.h_queue)
        add("/api/queue/remove", self.h_queue_remove)
        add("/api/queue/pin", self.h_queue_pin)
        add("/api/queue/clear", self.h_queue_clear)
        add("/api/queue/skip", self.h_queue_skip)
        add("/api/play", self.h_play)
        add("/api/search", self.h_search)
        add("/api/live", self.h_live)
        self.app.router.add_get("/api/logs", self.h_logs)
        self.app.router.add_get("/api/danmaku", self.h_danmaku)
        self.app.router.add_get("/api/sources", self.h_sources)
        self.app.router.add_get("/api/settings", self.h_settings_get)
        self.app.router.add_put("/api/settings", self.h_settings_put)
        add("/api/player/pause", self.h_pause)
        add("/api/player/resume", self.h_resume)
        add("/api/player/volume", self.h_volume)
        add("/api/bot/accepting", self.h_accepting)

    async def start(self) -> int:
        desired = int(os.environ.get("SONGBOT_API_PORT")
                      or _read_cfg().get("api_port") or DEFAULT_PORT)
        last_error: Optional[Exception] = None
        for port in range(desired, desired + 10):
            runner = web.AppRunner(self.app, access_log=None)
            try:
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", port)
                await site.start()
                self.runner, self.port = runner, port
                break
            except OSError as e:
                last_error = e
                try:
                    await runner.cleanup()
                except Exception:
                    pass
        if self.port is None:
            raise RuntimeError(f"本地 API 端口被占用（{desired}-{desired + 9}）: {last_error}")
        events.install_log_handler(log)
        log.info(f"本地 API 就绪: http://127.0.0.1:{self.port}"
                 + ("（token 已启用）" if self.token else ""))
        return self.port

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    # ------------------------------------------------------------ 中间件

    @web.middleware
    async def _middleware(self, request, handler):
        if request.method == "OPTIONS":
            return self._cors(web.Response(status=204))
        if self.token and request.path != "/api/health":
            if request.headers.get("X-SongBot-Token", "") != self.token:
                return self._cors(self._json({"ok": False, "error": "token 无效"}, 401))
        try:
            resp = await handler(request)
        except web.HTTPException:
            raise
        except Exception as e:
            log.error(f"API {request.path} 异常: {e}")
            resp = self._json({"ok": False, "error": str(e)}, 500)
        return self._cors(resp)

    def _json(self, payload: dict, status: int = 200) -> web.Response:
        return web.json_response(payload, status=status)

    @staticmethod
    def _cors(resp: web.Response) -> web.Response:
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SongBot-Token"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def _body(self, request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ------------------------------------------------------------ 状态

    async def h_health(self, request):
        return self._json({"ok": True, "status": "ok", "port": self.port,
                          "uptime": round(time.time() - events.hub.started_at, 1)})

    def _queue_payload(self) -> dict:
        bot = self.bot
        items = bot.queue.snapshot()
        current_item = bot._current_item
        skip_first = bool(current_item and items and items[0] is current_item)
        pending = []
        for pos, idx in enumerate(range(1 if skip_first else 0, len(items)), 1):
            item = items[idx]
            pending.append({
                "index": idx,
                "pos": pos,
                "song": song_dict(item.song),
                "requester": item.requester,
            })
        current_song = bot.playback.current_song or (current_item.song if current_item else None)
        return {
            "current": {
                "song": song_dict(current_song),
                "requester": current_item.requester if current_item else "",
            } if current_song else None,
            "pending": pending,
            "size": bot.queue.size,
        }

    def _playback_payload(self) -> dict:
        bot = self.bot
        try:
            state = bot.playback.state()
        except Exception:
            state = {"backend": getattr(bot.playback, "BACKEND", "ffplay"),
                     "playing": bool(getattr(bot.playback, "is_playing", False)),
                     "paused": False, "position": 0, "duration": 0, "volume": 100,
                     "capabilities": {"pause": True, "resume": True, "volume": True}}
        song = bot.playback.current_song or (bot._current_item.song if bot._current_item else None)
        state["song"] = song_dict(song)
        state["requester"] = bot._current_item.requester if bot._current_item else ""
        return state

    def _sources_payload(self) -> dict:
        try:
            from .bridge import get_bridge
            bridge = get_bridge()
        except Exception:
            return {"total": 0, "ready": 0, "platforms": [], "items": []}
        items = []
        platforms = set()
        for proc in list(bridge.procs):
            srcs = sorted((proc.sources or {}).keys())
            platforms.update(srcs)
            items.append({
                "name": proc.script_name or proc.name,
                "file": proc.name,
                "ready": bool(proc.ready),
                "sources": srcs,
            })
        if bridge.builtin is not None:
            items.append({"name": "内置引擎", "file": "内置", "ready": bool(bridge.builtin.ready),
                          "sources": sorted((bridge.builtin.sources or {}).keys())})
        return {
            "total": len(items),
            "ready": sum(1 for it in items if it["ready"]),
            "platforms": sorted(platforms),
            "items": items,
        }

    @staticmethod
    def _available_backends() -> list:
        """可用播放后端：ffplay 始终可用，mpv 需 python-mpv + 系统 libmpv。"""
        backends = ["ffplay"]
        try:
            import mpv  # noqa: F401
            backends.append("mpv")
        except Exception:
            pass
        return backends

    def _settings_payload(self) -> dict:
        cfg = _read_cfg()
        sessdata = str(cfg.get("bili_sessdata") or "")
        jct = str(cfg.get("bili_jct") or "")
        return {
            "room_id": str(cfg.get("bili_room_id") or ""),
            "has_sessdata": bool(sessdata),
            "sessdata_hint": _mask(sessdata),
            "jct_hint": _mask(jct),
            "buvid3_hint": _mask(str(cfg.get("bili_buvid3") or "")),
            "cookie_status": self.bot.cookie_mgr.check_expiry(),
            "cookie_valid": bool(self.bot.cookie_mgr.valid),
            "player_backend": str(cfg.get("player_backend") or "ffplay"),
            "min_quality": str(cfg.get("min_quality") or "flac24bit"),
            "enable_lyrics_search": bool(cfg.get("enable_lyrics_search", True)),
            "max_msg_per_minute": int(cfg.get("max_msg_per_minute", 20)),
            "danmaku_interval": float(cfg.get("danmaku_interval", 5)),
            "qualities": list(QUALITY_LADDER),
            "backends": self._available_backends(),
            "api_port": self.port or _read_cfg().get("api_port") or DEFAULT_PORT,
        }

    async def h_snapshot(self, request):
        bot = self.bot
        counters = events.hub.snapshot_counters()
        return self._json({
            "ok": True,
            "room": {
                "id": str(config_module.ROOM_ID),
                "connected": bool(getattr(bot.danmaku, "connected", False)),
                "accepting": bool(bot.accepting),
            },
            "viewers": bot.viewer_count,
            "playback": self._playback_payload(),
            "queue": self._queue_payload(),
            "stats": {
                "songs_total": bot.stats.total(),
                "songs_today": bot.stats.today(),
                "uptime": round(time.time() - bot.started_at, 1),
                "danmaku_rate": counters["danmaku_rate"],
                "top": [{"name": name, "count": count} for name, count in bot.stats.top(5)],
            },
            "sources": {k: v for k, v in self._sources_payload().items() if k != "items"},
            "danmaku": events.hub.danmaku_recent(30),
            "activity": events.hub.activity(),
            "last_event_id": counters["danmaku_total"],
        })

    # ------------------------------------------------------------ 队列操作

    async def h_queue(self, request):
        return self._json({"ok": True, **self._queue_payload()})

    async def h_queue_remove(self, request):
        body = await self._body(request)
        try:
            idx = int(body.get("index"))
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "index 无效"}, 400)
        item = self.bot.queue.remove_at(idx)
        if item is None:
            return self._json({"ok": False, "error": "队列位置不存在"}, 404)
        log.info(f"队列移除: {item.song.name} - {item.song.singer}")
        return self._json({"ok": True, "queue": self._queue_payload()})

    async def h_queue_pin(self, request):
        body = await self._body(request)
        try:
            idx = int(body.get("index"))
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "index 无效"}, 400)
        target = 1 if (self.bot._current_item and self.bot.queue.size > 1) else 0
        item = self.bot.queue.move_to(idx, target)
        if item is None:
            return self._json({"ok": False, "error": "队列位置不存在"}, 404)
        log.info(f"队列置顶: {item.song.name}")
        return self._json({"ok": True, "queue": self._queue_payload()})

    async def h_queue_clear(self, request):
        keep = bool(self.bot._current_item)
        self.bot.queue.clear(keep_first=keep)
        log.info("队列已清空" + ("（保留正在播放）" if keep else ""))
        return self._json({"ok": True, "queue": self._queue_payload()})

    async def h_queue_skip(self, request):
        if not self.bot.playback.is_playing:
            return self._json({"ok": False, "error": "当前没有在播放的歌曲"}, 409)
        self.bot.playback.stop()
        log.info("外部请求切歌")
        return self._json({"ok": True, "playback": self._playback_payload()})

    # ------------------------------------------------------------ 点歌

    async def _enqueue(self, song: SongInfo, requester: str = "主播") -> dict:
        bot = self.bot
        dup = bot._find_duplicate(song)
        if dup == "playing":
            return {"ok": False, "error": f"《{song.name}》正在播放中", "status": 409}
        if isinstance(dup, int):
            return {"ok": False, "error": f"《{song.name}》已在队列第 {dup} 位", "status": 409}
        item = QueueItem(song=song, requester=requester, requester_uid=0)
        pos = bot.queue.add(item)
        bot.stats.record(song.name, song.singer, requester)
        log.info(f"外部点歌入队: {song.name} - {song.singer} (队列:{pos})")
        if bot.sender is not None:
            try:
                asyncio.create_task(bot.sender.send(f"已点: {song.name} (队列:{pos})"))
            except Exception:
                pass
        return {"ok": True, "pos": pos, "song": song_dict(song), "status": 200}

    async def h_play(self, request):
        body = await self._body(request)
        song = song_from_dict(body.get("song")) if body.get("song") else None
        query = str(body.get("query") or "").strip()
        if song is None and query:
            if self.bot.session is None:
                return self._json({"ok": False, "error": "后端仍在启动，请稍后重试"}, 503)
            try:
                song = await asyncio.wait_for(self.bot._resolve_single(query), timeout=20)
            except Exception as e:
                log.error(f"外部搜索失败: {e}")
                song = None
        if song is None:
            return self._json({"ok": False, "error": "未找到匹配歌曲"}, 404)
        result = await self._enqueue(song, requester=str(body.get("requester") or "主播"))
        status = result.pop("status", 200)
        return self._json(result, status)

    async def h_search(self, request):
        body = await self._body(request)
        query = str(body.get("query") or "").strip()
        if not query:
            return self._json({"ok": False, "error": "请输入歌名或歌手"}, 400)
        if self.bot.session is None:
            return self._json({"ok": False, "error": "后端仍在启动，请稍后重试"}, 503)
        try:
            song = await asyncio.wait_for(self.bot._resolve_single(query), timeout=20)
        except Exception as e:
            log.error(f"搜索失败: {e}")
            song = None
        results = []
        if song:
            results = [song_dict(song)] + [song_dict(a) for a in (song.alternates or [])]
        suggestion = None
        if song is None:
            try:
                sug = await asyncio.wait_for(
                    suggest_song(self.bot.session, query), timeout=4)
                suggestion = song_dict(sug)
            except Exception:
                suggestion = None
        return self._json({"ok": True, "query": query, "results": results,
                          "suggestion": suggestion})

    # ------------------------------------------------------------ 弹幕/日志

    async def h_live(self, request):
        body = await self._body(request)
        text = str(body.get("text") or "").strip()
        if not text:
            return self._json({"ok": False, "error": "弹幕内容为空"}, 400)
        if self.bot.sender is None:
            return self._json({"ok": False, "error": "弹幕发送尚未就绪"}, 503)
        if not self.bot.cookie_mgr.valid:
            return self._json({"ok": False, "error": "Cookie 已失效，无法发送弹幕"}, 400)
        await self.bot.sender.send(text)
        return self._json({"ok": True})

    async def h_logs(self, request):
        try:
            after = int(request.query.get("after", "0"))
        except ValueError:
            after = 0
        try:
            limit = min(300, max(1, int(request.query.get("limit", "200"))))
        except ValueError:
            limit = 200
        items = events.hub.logs_after(after, limit)
        last = items[-1]["id"] if items else after
        return self._json({"ok": True, "items": items, "last_id": last})

    async def h_danmaku(self, request):
        try:
            after = int(request.query.get("after", "0"))
        except ValueError:
            after = 0
        return self._json({"ok": True, "items": events.hub.danmaku_after(after, 100)})

    async def h_sources(self, request):
        return self._json({"ok": True, **self._sources_payload()})

    # ------------------------------------------------------------ 设置

    async def h_settings_get(self, request):
        return self._json({"ok": True, "settings": self._settings_payload()})

    async def h_settings_put(self, request):
        body = await self._body(request)
        cfg = _read_cfg()
        restart_required = []
        changed = []

        def put(key, value):
            if cfg.get(key) != value:
                cfg[key] = value
                changed.append(key)

        for key in ("bili_sessdata", "bili_jct", "bili_buvid3", "bili_buvid4"):
            if key in body:
                value = str(body.get(key) or "").strip()
                if value and not value.startswith("•"):
                    put(key, value)
        if "bili_room_id" in body:
            room = str(body.get("bili_room_id") or "").strip()
            if room.isdigit() and room != str(cfg.get("bili_room_id") or ""):
                put("bili_room_id", room)
                restart_required.append("bili_room_id")
        if "player_backend" in body:
            backend = str(body.get("player_backend") or "").lower()
            if backend in ("ffplay", "mpv") and backend != cfg.get("player_backend"):
                put("player_backend", backend)
                restart_required.append("player_backend")
        if "min_quality" in body:
            quality = str(body.get("min_quality") or "")
            if quality in QUALITY_LADDER:
                put("min_quality", quality)
        if "enable_lyrics_search" in body:
            put("enable_lyrics_search", bool(body.get("enable_lyrics_search")))
        if "max_msg_per_minute" in body:
            try:
                value = max(1, min(100, int(body.get("max_msg_per_minute"))))
                put("max_msg_per_minute", value)
            except (TypeError, ValueError):
                pass
        if "danmaku_interval" in body:
            try:
                value = max(0.0, min(60.0, float(body.get("danmaku_interval"))))
                put("danmaku_interval", value)
            except (TypeError, ValueError):
                pass
        if "api_port" in body:
            try:
                value = int(body.get("api_port"))
                if 1024 <= value <= 65000 and value != cfg.get("api_port"):
                    put("api_port", value)
                    restart_required.append("api_port")
            except (TypeError, ValueError):
                pass

        if changed:
            try:
                tmp = CONFIG_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                os.replace(tmp, CONFIG_PATH)
            except Exception as e:
                log.error(f"写入 config.json 失败: {e}")
                return self._json({"ok": False, "error": f"保存配置失败: {e}"}, 500)

            # 热更新（进程内常量与 Cookie）
            if any(k in changed for k in ("bili_sessdata", "bili_jct",
                                          "bili_buvid3", "bili_buvid4")):
                self.bot.cookie_mgr.reload_config()
            if "min_quality" in changed:
                config_module.MIN_QUALITY = cfg["min_quality"]
                import bot.core as core_module
                core_module.MIN_QUALITY = cfg["min_quality"]
            if "enable_lyrics_search" in changed:
                config_module.ENABLE_LYRICS_SEARCH = cfg["enable_lyrics_search"]
                import bot.core as core_module
                core_module.ENABLE_LYRICS_SEARCH = cfg["enable_lyrics_search"]
            if "max_msg_per_minute" in changed:
                config_module.MAX_MSG_PER_MINUTE = cfg["max_msg_per_minute"]
                danmaku_module.MAX_MSG_PER_MINUTE = cfg["max_msg_per_minute"]
            if "danmaku_interval" in changed:
                config_module.DANMAKU_INTERVAL = cfg["danmaku_interval"]
                danmaku_module.DANMAKU_INTERVAL = cfg["danmaku_interval"]
            log.info(f"设置已保存: {', '.join(changed)}"
                     + (f"（需重启: {', '.join(restart_required)}）" if restart_required else ""))

        return self._json({"ok": True, "changed": changed,
                          "restart_required": restart_required,
                          "settings": self._settings_payload()})

    # ------------------------------------------------------------ 播放控制

    async def h_pause(self, request):
        ok = bool(self.bot.playback.pause())
        return self._json({"ok": ok, "playback": self._playback_payload()},
                          200 if ok else 409)

    async def h_resume(self, request):
        ok = bool(self.bot.playback.resume())
        return self._json({"ok": ok, "playback": self._playback_payload()},
                          200 if ok else 409)

    async def h_volume(self, request):
        body = await self._body(request)
        try:
            volume = int(body.get("volume"))
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "volume 无效"}, 400)
        value = int(self.bot.playback.set_volume(volume))
        return self._json({"ok": True, "volume": value,
                          "playback": self._playback_payload()})

    async def h_accepting(self, request):
        body = await self._body(request)
        if "accepting" in body:
            self.bot.accepting = bool(body.get("accepting"))
        else:
            self.bot.accepting = not self.bot.accepting
        state = self.bot.accepting
        log.info("已恢复接单" if state else "已暂停接单")
        if self.bot.sender is not None:
            try:
                asyncio.create_task(self.bot.sender.send(
                    "已恢复接单，欢迎点歌~" if state else "已暂停接单，稍后再来~"))
            except Exception:
                pass
        return self._json({"ok": True, "accepting": state})
