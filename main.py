#!/usr/bin/env python3
"""bili-songbot 入口：启动音源桥 → 获取真实房间号 → 运行主控。"""
import asyncio

import aiohttp

from bot import log
from bot.api import ApiServer
from bot.bridge import SourceBridge, set_bridge
from bot.core import BiliMusicBot
from bot import protocol


async def main():
    # 文件日志：按 config.json 的 log_file/log_level 落盘（日志轮转 + 过期清理）
    from bot.config import CFG, ROOM_ID, cleanup_old_logs, setup_file_logging
    setup_file_logging(CFG)
    cleanup_old_logs(CFG)
    log.info("=" * 50)
    log.info("B站直播间点歌机器人 启动")
    log.info(f"房间号: {ROOM_ID}")
    # 打印 Cookie 过期信息
    from bot.protocol import CookieManager
    cm = CookieManager()
    log.info(cm.check_expiry())
    log.info("=" * 50)

    bot = BiliMusicBot()
    # 先启动本地 HTTP API（仅监听 127.0.0.1）：后续直播间/音源初始化失败时，
    # 桌面 UI 仍可打开并在设置页修改配置，而不是直接显示“后端启动失败”。
    api = ApiServer(bot)
    api_started = False
    try:
        await api.start()
        api_started = True
    except Exception as e:
        log.error(f"本地 API 启动失败（桌面控制不可用）: {e}")

    bridge = None
    degraded = ""
    # 启动 Node.js 音源桥接器（单例注入）
    try:
        bridge = SourceBridge()
        await bridge.start()
        set_bridge(bridge)
    except Exception as e:
        degraded = f"音源桥启动失败: {e}"

    if not degraded:
        room = str(ROOM_ID or "").strip()
        if not room.isdigit():
            degraded = f"直播间号未配置（当前为 {room or '空'}），请在设置页填写真实房间号后重启应用"
        else:
            # 获取真实房间号
            try:
                async with aiohttp.ClientSession() as session:
                    protocol.REAL_ROOM_ID = await protocol.get_real_room_id(session)
            except Exception as e:
                degraded = f"获取真实直播间号失败: {e}"

    try:
        if degraded:
            log.error(degraded)
            log.error("已进入降级模式：本地 API 保持在线，可在「音源与设置」修改配置后重启应用")
            await asyncio.Event().wait()   # 等待外壳退出（taskkill）
        else:
            await bot.run()
    except KeyboardInterrupt:
        log.info("收到中断信号，正在退出...")
    except Exception as e:
        log.error(f"运行异常: {e}")
        raise
    finally:
        if api_started:
            await api.stop()
        if bridge:
            await bridge.stop()
        if bot.session:
            await bot.session.close()
        log.info("程序已退出")


if __name__ == "__main__":
    asyncio.run(main())
