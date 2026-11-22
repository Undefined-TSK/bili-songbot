/** B 站直播弹幕 WebSocket 客户端（ws 实现直播弹幕协议）。 */
import zlib from "node:zlib";
import WebSocket from "ws";
import { log } from "./logger.js";
import { sleep } from "./util.js";
import { CookieManager, getRealRoomId, UA } from "./protocol.js";

interface DanmuInfo {
  token: string;
  url: string;
}

function encodePacket(operation: number, body: Buffer | string = ""): Buffer {
  const payload = Buffer.isBuffer(body) ? body : Buffer.from(body);
  const header = Buffer.alloc(16);
  header.writeUInt32BE(payload.length + 16, 0);
  header.writeUInt16BE(16, 4);
  header.writeUInt16BE(1, 6);
  header.writeUInt32BE(operation, 8);
  header.writeUInt32BE(1, 12);
  return Buffer.concat([header, payload]);
}

type PacketHandler = (op: number, version: number, body: Buffer) => void;

function parsePackets(buf: Buffer, onPacket: PacketHandler, depth = 0): void {
  if (depth > 4) return;
  let offset = 0;
  while (offset + 16 <= buf.length) {
    const total = buf.readUInt32BE(offset);
    const headerLen = buf.readUInt16BE(offset + 4);
    const version = buf.readUInt16BE(offset + 6);
    const op = buf.readUInt32BE(offset + 8);
    if (total < headerLen || offset + total > buf.length) break;
    const body = buf.subarray(offset + headerLen, offset + total);
    try {
      if (version === 2) parsePackets(zlib.inflateSync(body), onPacket, depth + 1);
      else if (version === 3) parsePackets(zlib.brotliDecompressSync(body), onPacket, depth + 1);
      else onPacket(op, version, body);
    } catch (e) {
      log.debug(`弹幕包解析失败: ${(e as Error).message}`);
    }
    offset += total;
  }
}

export interface DanmakuHandlers {
  onMessage: (text: string, uid: number, username: string) => void;
  onViewerCount?: (count: number) => void;
}

export class BiliDanmaku {
  private ws: WebSocket | null = null;
  private running = false;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  connected = false;
  private authResolve: ((ok: boolean) => void) | null = null;

  constructor(
    private roomInput: string,
    private cookie: CookieManager,
    private handlers: DanmakuHandlers,
  ) {}

  async start(): Promise<void> {
    this.running = true;
    let delay = 2000;
    while (this.running) {
      try {
        const ok = await this.connectOnce();
        if (!this.running) break;
        delay = ok ? 2000 : Math.min(delay * 2, 30000);
      } catch (e) {
        if (!this.running) break;
        log.warn(`弹幕连接异常: ${(e as Error).message}，${Math.ceil(delay / 1000)}s 后重连`);
        delay = Math.min(delay * 2, 30000);
      }
      if (this.running) await sleep(delay);
    }
  }

  stop(): void {
    this.running = false;
    this.connected = false;
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.ws = null;
    this.authResolve?.(false);
    this.authResolve = null;
  }

  private async fetchInfo(realId: number): Promise<DanmuInfo | null> {
    const res = await fetch(
      `https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id=${realId}&type=0`,
      {
        headers: { "User-Agent": UA, Cookie: this.cookie.header(), Referer: "https://live.bilibili.com/" },
        signal: AbortSignal.timeout(10000),
      },
    );
    const json = (await res.json()) as Record<string, unknown>;
    const data = (json.data as Record<string, unknown>) || {};
    const hosts = (data.host_list as Array<Record<string, unknown>>) || [];
    const host = hosts[0];
    if (!host) return null;
    return {
      token: String(data.token ?? ""),
      url: `wss://${String(host.host)}:${Number(host.wss_port ?? 443)}/sub`,
    };
  }

  private async connectOnce(): Promise<boolean> {
    const realId = await getRealRoomId(this.roomInput);
    if (!realId) throw new Error(`无法解析房间号: ${this.roomInput}`);
    const info = await this.fetchInfo(realId);
    if (!info) throw new Error("获取弹幕服务器失败");

    log.info(`连接弹幕服务器: ${info.url}`);
    const ws = new WebSocket(info.url, {
      headers: {
        "User-Agent": UA,
        Origin: "https://live.bilibili.com",
        Cookie: this.cookie.header(),
      },
      handshakeTimeout: 12000,
    });
    this.ws = ws;

    const authed = await new Promise<boolean>((resolve) => {
      let settled = false;
      const finish = (ok: boolean): void => {
        if (settled) return;
        settled = true;
        this.authResolve = null;
        resolve(ok);
      };
      this.authResolve = finish;
      const timer = setTimeout(() => finish(false), 12000);

      ws.on("open", () => {
        const auth = {
          uid: 0,
          roomid: realId,
          protover: 3,
          platform: "web",
          type: 2,
          key: info.token,
        };
        ws.send(encodePacket(7, JSON.stringify(auth)));
        log.debug("已发送弹幕鉴权包");
      });

      ws.on("message", (raw: WebSocket.RawData) => {
        const buf = Buffer.isBuffer(raw) ? raw : Buffer.from(raw as ArrayBuffer);
        parsePackets(buf, (op, _version, body) => {
          if (op === 8) {
            let code = 0;
            try {
              code = Number((JSON.parse(body.toString("utf8")) as Record<string, unknown>).code ?? 0);
            } catch {
              code = -1;
            }
            clearTimeout(timer);
            if (code === 0) {
              this.connected = true;
              log.info(`弹幕服务器已连接（房间 ${realId}）`);
              finish(true);
            } else {
              log.warn(`弹幕鉴权失败 code=${code}`);
              finish(false);
            }
          } else if (op === 3) {
            if (body.length >= 4) {
              const popularity = body.readUInt32BE(0);
              this.handlers.onViewerCount?.(popularity);
            }
          } else if (op === 5) {
            this.handleCommand(body.toString("utf8"));
          }
        });
      });

      ws.on("error", (e: Error) => {
        log.debug(`弹幕 WS 错误: ${e.message}`);
        finish(false);
      });
      ws.on("close", (code: number) => {
        this.connected = false;
        if (this.heartbeat) clearInterval(this.heartbeat);
        this.heartbeat = null;
        log.warn(`弹幕 WS 已关闭 code=${code}`);
        finish(false);
      });
    });

    if (!authed) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      return false;
    }

    this.heartbeat = setInterval(() => {
      try {
        ws.send(encodePacket(2));
      } catch {
        /* ignore */
      }
    }, 30000);

    return true;
  }

  private handleCommand(text: string): void {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(text) as Record<string, unknown>;
    } catch {
      return;
    }
    const cmd = String(data.cmd ?? "");
    if (cmd.startsWith("DANMU_MSG")) {
      const info = data.info as unknown[];
      if (!Array.isArray(info) || info.length < 3) return;
      const msg = String(info[1] ?? "").trim();
      const user = info[2] as unknown[];
      const uid = Array.isArray(user) ? Number(user[0] ?? 0) : 0;
      const uname = Array.isArray(user) ? String(user[1] ?? "匿名") : "匿名";
      if (!msg) return;
      this.handlers.onMessage(msg, uid, uname);
      return;
    }
    if (cmd === "SUPER_CHAT_MESSAGE") {
      const payload = (data.data as Record<string, unknown>) || {};
      const msg = String(payload.message ?? "").trim();
      const userInfo = (payload.user_info as Record<string, unknown>) || {};
      const uid = Number(payload.uid ?? 0);
      const uname = String(userInfo.uname ?? "匿名");
      if (msg) this.handlers.onMessage(msg, uid, uname);
    }
  }
}
