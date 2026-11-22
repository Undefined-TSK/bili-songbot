/** 运行时事件缓冲：弹幕流、日志流、弹幕速率与近段活动统计。 */
import type { DanmakuEntry, LogEntry } from "./types.js";
import { log } from "./logger.js";

const DANMAKU_CAP = 200;
const LOG_CAP = 300;

export class EventHub {
  private danmaku: DanmakuEntry[] = [];
  private logs: LogEntry[] = [];
  private danmakuTimes: number[] = [];
  private danmakuId = 0;
  private logId = 0;
  readonly startedAt = Date.now();

  addDanmaku(user: string, text: string, kind = ""): DanmakuEntry {
    const entry: DanmakuEntry = {
      id: ++this.danmakuId,
      ts: Date.now() / 1000,
      user: user || "匿名",
      text: text || "",
      kind: kind || "",
    };
    this.danmaku.push(entry);
    if (this.danmaku.length > DANMAKU_CAP) this.danmaku.shift();
    this.danmakuTimes.push(entry.ts);
    this.rate(60);
    return entry;
  }

  danmakuRecent(limit = 30): DanmakuEntry[] {
    return limit <= 0 ? [] : this.danmaku.slice(-limit);
  }

  danmakuAfter(afterId: number, limit = 100): DanmakuEntry[] {
    return this.danmaku.filter((d) => d.id > afterId).slice(0, limit);
  }

  addLog(level: string, name: string, msg: string): LogEntry {
    const entry: LogEntry = { id: ++this.logId, ts: Date.now() / 1000, level, name, msg };
    this.logs.push(entry);
    if (this.logs.length > LOG_CAP) this.logs.shift();
    return entry;
  }

  logsAfter(afterId = 0, limit = 200): LogEntry[] {
    return afterId <= 0 ? this.logs.slice(-limit) : this.logs.filter((r) => r.id > afterId).slice(0, limit);
  }

  rate(seconds = 60): number {
    const cutoff = Date.now() / 1000 - seconds;
    while (this.danmakuTimes.length && this.danmakuTimes[0] < cutoff) this.danmakuTimes.shift();
    return this.danmakuTimes.length;
  }

  activity(bucketSeconds = 10, buckets = 18): number[] {
    const now = Date.now() / 1000;
    const start = now - bucketSeconds * buckets;
    const counts = new Array<number>(buckets).fill(0);
    for (const ts of this.danmakuTimes) {
      if (ts < start) continue;
      const idx = Math.floor((ts - start) / bucketSeconds);
      if (idx >= 0 && idx < buckets) counts[idx] += 1;
    }
    return counts;
  }

  snapshotCounters(): { danmaku_rate: number; danmaku_total: number; log_total: number } {
    return { danmaku_rate: this.rate(60), danmaku_total: this.danmakuId, log_total: this.logId };
  }
}

export const hub = new EventHub();

export function installLogHandler(): void {
  log.onEntry((level, name, msg) => hub.addLog(level, name, msg));
}
