/** 弹幕发送：限频、截断、风控退避。 */
import { config } from "./config.js";
import { log } from "./logger.js";
import { sleep, truncateDanmaku } from "./util.js";
import { CookieManager, sendDanmaku } from "./protocol.js";

export class BiliSender {
  private roomId: number;
  private sentAt: number[] = [];
  private lastSent = 0;
  private blockedUntil = 0;
  private consecutiveBlocks = 0;

  constructor(
    private cookie: CookieManager,
    roomId: number,
  ) {
    this.roomId = roomId;
  }

  get room(): number {
    return this.roomId;
  }

  setRoom(roomId: number): void {
    this.roomId = roomId;
  }

  async send(message: string): Promise<boolean> {
    const text = truncateDanmaku(message || "", config.num("danmaku_max_len", 20) * 2);
    if (!text) return false;
    if (!this.cookie.configured) {
      log.debug(`弹幕未发送（Cookie 未配置）: ${text}`);
      return false;
    }
    const now = Date.now();
    if (now < this.blockedUntil) {
      log.debug(`弹幕退避中(${Math.ceil((this.blockedUntil - now) / 1000)}s): ${text}`);
      return false;
    }
    const intervalMs = config.num("danmaku_interval", 5) * 1000;
    const wait = this.lastSent + intervalMs - now;
    if (wait > 0) await sleep(wait);

    const windowStart = Date.now() - 60000;
    this.sentAt = this.sentAt.filter((t) => t >= windowStart);
    const maxPerMinute = config.num("max_msg_per_minute", 20);
    if (this.sentAt.length >= maxPerMinute) {
      log.warn(`弹幕频率超限（${maxPerMinute}/分钟），丢弃: ${text}`);
      return false;
    }

    const result = await sendDanmaku(this.cookie, this.roomId, text);
    this.lastSent = Date.now();
    if (result.ok) {
      this.sentAt.push(this.lastSent);
      this.consecutiveBlocks = 0;
      log.info(`已发送弹幕: ${text}`);
      return true;
    }
    if (result.code === -101) {
      log.error("Cookie 无效或已过期，弹幕发送已禁用");
      this.cookie.valid = false;
      this.cookie.reason = "Cookie 无效或已过期";
      return false;
    }
    if ([10030, 10031, 10032].includes(result.code) || result.code === 1003212) {
      this.consecutiveBlocks = Math.min(this.consecutiveBlocks + 1, 5);
      const backoff = Math.min(10 * 2 ** (this.consecutiveBlocks - 1), 160);
      this.blockedUntil = Date.now() + backoff * 1000;
      log.warn(`发送受风控 code=${result.code}，退避 ${backoff}s`);
      return false;
    }
    log.warn(`弹幕发送失败 code=${result.code}: ${result.message}`);
    return false;
  }
}
