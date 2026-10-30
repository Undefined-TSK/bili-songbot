#!/usr/bin/env python3
"""bili-songbot 入口：启动音源桥 → 获取真实房间号 → 运行主控。"""
import asyncio

import aiohttp

from bot import log
from bot.bridge import SourceBridge, set_bridge
from bot.core import BiliMusicBot
from bot import protocol


async def main():
    # 文件日志：按 config.json 的 log_file/log_level 落盘（日志轮转 + 过期清理）
    from bot.config import CFG, cleanup_old_logs, setup_file_logging
    setup_file_logging(CFG)
    cleanup_old_logs(CFG)
    log.info("=" * 50)
    log.info("B站直播间点歌机器人 启动")
    from bot.config import ROOM_ID
    log.info(f"房间号: {ROOM_ID}")
    # 打印 Cookie 过期信息
    from bot.protocol import CookieManager
    cm = CookieManager()
    log.info(cm.check_expiry())
    log.info("=" * 50)
    # 启动 Node.js 音源桥接器（单例注入）
    bridge = SourceBridge()
    await bridge.start()
    set_bridge(bridge)
    # 获取真实房间号
    async with aiohttp.ClientSession() as session:
        protocol.REAL_ROOM_ID = await protocol.get_real_room_id(session)
    bot = BiliMusicBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        log.info("收到中断信号，正在退出...")
    except Exception as e:
        log.error(f"运行异常: {e}")
        raise
    finally:
        if bridge:
            await bridge.stop()
        if bot.session:
            await bot.session.close()
        log.info("程序已退出")


if __name__ == "__main__":
    asyncio.run(main())
