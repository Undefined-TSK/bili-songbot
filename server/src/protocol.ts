/** B 站基础协议：Cookie 管理、房间号解析、buvid3/4、请求头与限速。 */
import { config } from "./config.js";
import { log } from "./logger.js";
import { RateLimiter } from "./util.js";

export const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

export const apiLimiter = new RateLimiter(config.num("api_rate_interval", 1));

let buvid3 = "";
let buvid4 = "";

export function updateBuvid3(value: string): void {
  if (value) buvid3 = value;
}

export function updateBuvid4(value: string): void {
  if (value) buvid4 = value;
}

export async function fetchBuvidPair(): Promise<{ b3: string; b4: string }> {
  try {
    const res = await fetch("https://api.bilibili.com/x/frontend/finger/spi", {
      headers: { "User-Agent": UA, Referer: "https://www.bilibili.com/" },
      signal: AbortSignal.timeout(8000),
    });
    const json = (await res.json()) as Record<string, unknown>;
    const data = (json.data as Record<string, unknown>) || {};
    const b3 = String(data.b_3 ?? "");
    const b4 = String(data.b_4 ?? "");
    if (b3) updateBuvid3(b3);
    if (b4) updateBuvid4(b4);
    return { b3, b4 };
  } catch (e) {
    log.warn(`获取 buvid 失败: ${(e as Error).message}`);
    return { b3: "", b4: "" };
  }
}

/** 解析 SESSDATA 过期时间（B 站常在 SESSDATA 值里塞时间戳）。 */
function sessdataExpiry(sessdata: string): number | null {
  try {
    const decoded = decodeURIComponent(sessdata);
    const parts = decoded.split(",");
    if (parts.length >= 2) {
      const ts = Number(parts[1]);
      if (Number.isFinite(ts) && ts > 0) return ts;
    }
  } catch {
    /* ignore */
  }
  return null;
}

export class CookieManager {
  sessdata = "";
  jct = "";
  valid: boolean | null = null;
  reason = "";

  constructor() {
    this.reloadFromConfig();
  }

  reloadFromConfig(): void {
    this.sessdata = String(config.get("bili_sessdata", "") ?? "");
    this.jct = String(config.get("bili_jct", "") ?? "");
    const cfgBuvid3 = String(config.get("bili_buvid3", "") ?? "");
    const cfgBuvid4 = String(config.get("bili_buvid4", "") ?? "");
    if (cfgBuvid3) updateBuvid3(cfgBuvid3);
    if (cfgBuvid4) updateBuvid4(cfgBuvid4);
  }

  get configured(): boolean {
    return Boolean(this.sessdata && this.jct);
  }

  header(includeCookies = true): string {
    const parts: string[] = [];
    if (includeCookies && this.sessdata) parts.push(`SESSDATA=${this.sessdata}`);
    if (includeCookies && this.jct) parts.push(`bili_jct=${this.jct}`);
    if (buvid3) parts.push(`buvid3=${buvid3}`);
    if (buvid4) parts.push(`buvid4=${buvid4}`);
    return parts.join("; ");
  }

  async validate(): Promise<boolean> {
    if (!this.configured) {
      this.valid = false;
      this.reason = "未配置 SESSDATA 或 bili_jct";
      return false;
    }
    const expiry = sessdataExpiry(this.sessdata);
    if (expiry && expiry * 1000 < Date.now()) {
      this.valid = false;
      this.reason = "SESSDATA 已过期";
      return false;
    }
    try {
      await apiLimiter.acquire();
      const res = await fetch("https://api.bilibili.com/x/web-interface/nav", {
        headers: { "User-Agent": UA, Cookie: this.header(), Referer: "https://live.bilibili.com/" },
        signal: AbortSignal.timeout(10000),
      });
      const json = (await res.json()) as Record<string, unknown>;
      const code = Number(json.code ?? -1);
      if (code === 0) {
        const data = (json.data as Record<string, unknown>) || {};
        if (data.isLogin) {
          this.valid = true;
          this.reason = `已登录: ${String(data.uname ?? "")}`;
        } else {
          this.valid = false;
          this.reason = "Cookie 未登录";
        }
      } else if (code === -101) {
        this.valid = false;
        this.reason = "Cookie 无效或已过期";
      } else {
        this.valid = false;
        this.reason = `接口返回 code=${code}`;
      }
    } catch (e) {
      this.valid = false;
      this.reason = `校验请求失败: ${(e as Error).message}`;
    }
    return Boolean(this.valid);
  }
}

export async function getRealRoomId(roomId: string | number): Promise<number | null> {
  try {
    const res = await fetch(`https://api.live.bilibili.com/room/v1/Room/room_init?id=${encodeURIComponent(String(roomId))}`, {
      headers: { "User-Agent": UA, Referer: "https://live.bilibili.com/" },
      signal: AbortSignal.timeout(8000),
    });
    const json = (await res.json()) as Record<string, unknown>;
    const data = (json.data as Record<string, unknown>) || {};
    const rid = Number(data.room_id ?? 0);
    return rid || null;
  } catch (e) {
    log.debug(`room_init 失败: ${(e as Error).message}`);
    return null;
  }
}

export interface RoomInfo {
  roomId: number;
  liveStatus: number;
  title: string;
  online: number;
}

export async function getRoomInfo(roomId: number): Promise<RoomInfo | null> {
  try {
    const res = await fetch(`https://api.live.bilibili.com/room/v1/Room/get_info?room_id=${roomId}`, {
      headers: { "User-Agent": UA, Referer: "https://live.bilibili.com/" },
      signal: AbortSignal.timeout(8000),
    });
    const json = (await res.json()) as Record<string, unknown>;
    const data = (json.data as Record<string, unknown>) || {};
    if (Number(json.code ?? -1) !== 0) return null;
    return {
      roomId: Number(data.room_id ?? roomId),
      liveStatus: Number(data.live_status ?? 0),
      title: String(data.title ?? ""),
      online: Number(data.online ?? 0),
    };
  } catch {
    return null;
  }
}

export async function sendDanmaku(
  cookie: CookieManager,
  roomId: number,
  message: string,
): Promise<{ ok: boolean; code: number; message: string }> {
  await apiLimiter.acquire();
  const body = new URLSearchParams({
    bubble: "0",
    msg: message,
    color: "16777215",
    mode: "1",
    fontsize: "25",
    rnd: String(Math.floor(Date.now() / 1000)),
    roomid: String(roomId),
    csrf: cookie.jct,
    csrf_token: cookie.jct,
  });
  try {
    const res = await fetch("https://api.live.bilibili.com/msg/send", {
      method: "POST",
      headers: {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        Referer: `https://live.bilibili.com/${roomId}`,
        Cookie: cookie.header(),
      },
      body: body.toString(),
      signal: AbortSignal.timeout(10000),
    });
    const json = (await res.json()) as Record<string, unknown>;
    const code = Number(json.code ?? -1);
    const msg = String(json.message ?? json.msg ?? "");
    return { ok: code === 0, code, message: msg };
  } catch (e) {
    return { ok: false, code: -1, message: (e as Error).message };
  }
}
