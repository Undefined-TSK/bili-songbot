/** 播放队列（json 持久化；链接不落盘，重启后重新取链）。 */
import fs from "node:fs";
import type { QueueItem, SongInfo } from "./types.js";
import { makeSong, songToDict } from "./types.js";

function songFromRaw(raw: unknown): SongInfo | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!String(r.name ?? "").trim()) return null;
  const alternates = Array.isArray(r.alternates)
    ? r.alternates.map((a) => songFromRaw(a)).filter((a): a is SongInfo => !!a)
    : [];
  return makeSong({
    name: String(r.name ?? ""),
    singer: String(r.singer ?? ""),
    album: String(r.album ?? ""),
    songId: String(r.song_id ?? ""),
    platform: String(r.platform ?? ""),
    interval: String(r.interval ?? ""),
    score: Number(r.score ?? 0) || 0,
    alternates,
    raw: (r.raw as Record<string, unknown>) || undefined,
  });
}

export class PlayQueue {
  private items: QueueItem[] = [];

  constructor(private persistPath?: string) {
    this.load();
  }

  private load(): void {
    if (!this.persistPath) return;
    try {
      if (!fs.existsSync(this.persistPath)) return;
      const raw = JSON.parse(fs.readFileSync(this.persistPath, "utf8"));
      if (!Array.isArray(raw)) return;
      for (const entry of raw) {
        if (!entry || typeof entry !== "object") continue;
        const song = songFromRaw((entry as Record<string, unknown>).song);
        if (!song) continue;
        const e = entry as Record<string, unknown>;
        this.items.push({
          song,
          requester: String(e.requester ?? ""),
          requesterUid: Number(e.requester_uid ?? 0) || 0,
        });
      }
    } catch {
      this.items = [];
    }
  }

  private save(): void {
    if (!this.persistPath) return;
    try {
      const data = this.items.map((it) => ({
        song: songToDict(it.song),
        requester: it.requester,
        requester_uid: it.requesterUid,
      }));
      const tmp = `${this.persistPath}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(data), "utf8");
      fs.renameSync(tmp, this.persistPath);
    } catch {
      /* 静默降级 */
    }
  }

  add(item: QueueItem): number {
    this.items.push(item);
    this.save();
    return this.items.length;
  }

  peek(): QueueItem | undefined {
    return this.items[0];
  }

  removeCurrent(): void {
    if (this.items.length) {
      this.items.shift();
      this.save();
    }
  }

  removeAt(index: number): QueueItem | undefined {
    if (index < 0 || index >= this.items.length) return undefined;
    const [item] = this.items.splice(index, 1);
    this.save();
    return item;
  }

  moveTo(index: number, target: number): QueueItem | undefined {
    if (index < 0 || index >= this.items.length || target < 0 || target >= this.items.length) {
      return undefined;
    }
    if (index === target) return this.items[index];
    const [item] = this.items.splice(index, 1);
    this.items.splice(target, 0, item);
    this.save();
    return item;
  }

  clear(keepFirst = false): void {
    const kept = keepFirst && this.items.length ? [this.items[0]] : [];
    this.items = kept;
    this.save();
  }

  snapshot(): QueueItem[] {
    return [...this.items];
  }

  get size(): number {
    return this.items.length;
  }

  get isEmpty(): boolean {
    return this.items.length === 0;
  }
}
