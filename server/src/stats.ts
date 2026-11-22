/** 点歌统计：按「歌名 - 歌手」累计次数，json 持久化。 */
import fs from "node:fs";

interface StatEntry {
  count: number;
  last?: number;
  last_by?: string;
}

export class SongStats {
  private data: Record<string, StatEntry> = {};

  constructor(private path?: string) {
    if (!path) return;
    try {
      if (fs.existsSync(path)) {
        const raw = JSON.parse(fs.readFileSync(path, "utf8"));
        if (raw && typeof raw === "object") this.data = raw as Record<string, StatEntry>;
      }
    } catch {
      this.data = {};
    }
  }

  private key(name: string, singer: string): string {
    return singer ? `${name} - ${singer}` : name;
  }

  record(name: string, singer: string, requester = ""): void {
    try {
      const k = this.key(name, singer);
      const entry = this.data[k] || { count: 0 };
      entry.count = Number(entry.count || 0) + 1;
      entry.last = Date.now() / 1000;
      if (requester) entry.last_by = requester;
      this.data[k] = entry;
      if (this.path) {
        const tmp = `${this.path}.tmp`;
        fs.writeFileSync(tmp, JSON.stringify(this.data), "utf8");
        fs.renameSync(tmp, this.path);
      }
    } catch {
      /* 统计失败不影响点歌主链路 */
    }
  }

  top(n = 5): Array<[string, number]> {
    return Object.entries(this.data)
      .map(([k, v]) => [k, Number(v.count || 0)] as [string, number])
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, n);
  }

  total(): number {
    return Object.values(this.data).reduce((sum, v) => sum + Number(v.count || 0), 0);
  }

  today(): number {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const startTs = start.getTime() / 1000;
    return Object.values(this.data).filter((v) => Number(v.last || 0) >= startTs).length;
  }
}
