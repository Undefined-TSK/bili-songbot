/** 切歌投票：动态门槛、uid 去重、超时结算。 */
import { config } from "./config.js";
import { sleep } from "./util.js";
import type { BiliSender } from "./sender.js";

export class VoteManager {
  active = false;
  private yes = new Set<number>();
  private no = new Set<number>();
  private voters = new Set<number>();
  private viewerCount = 0;
  private stillPlaying: (() => boolean) | null = null;
  private generation = 0;

  constructor(private sender: BiliSender | null) {}

  updateViewerCount(count: number): void {
    this.viewerCount = count;
  }

  setSender(sender: BiliSender): void {
    this.sender = sender;
  }

  getThreshold(): number {
    const v = this.viewerCount;
    if (v <= 2) return v <= 0 ? 1 : v;
    if (v <= 4) return 2;
    if (v <= 6) return 3;
    return Math.max(3, Math.ceil(v / 2));
  }

  async startVote(onPass: () => Promise<void>, isStillPlaying?: () => boolean): Promise<void> {
    if (this.active) return;
    this.active = true;
    this.yes.clear();
    this.no.clear();
    this.voters.clear();
    this.stillPlaying = isStillPlaying || null;
    const gen = ++this.generation;
    const threshold = this.getThreshold();
    await this.sender?.send(`[切歌投票] Y/N 同意切歌 (当前门槛: ${threshold}人参与, 需Y>N)`);
    void this.loop(gen, onPass);
  }

  private async loop(gen: number, onPass: () => Promise<void>): Promise<void> {
    const duration = config.num("vote_duration", 30);
    const broadcast = config.num("vote_broadcast_interval", 5);
    const start = Date.now();
    let lastBroadcast = 0;
    while ((Date.now() - start) / 1000 < duration) {
      if (gen !== this.generation) return;
      const elapsed = (Date.now() - start) / 1000;
      if (elapsed - lastBroadcast >= broadcast) {
        lastBroadcast = elapsed;
        await this.sender?.send(
          `[切歌投票] Y/N 剩余${Math.ceil(duration - elapsed)}s | 当前 Y:${this.yes.size} N:${this.no.size}`,
        );
      }
      await sleep(1000);
    }
    if (gen !== this.generation) return;
    this.active = false;
    const total = this.yes.size + this.no.size;
    const threshold = this.getThreshold();
    await this.sender?.send(`[投票结束] Y:${this.yes.size} N:${this.no.size} 总参与:${total} 门槛:${threshold}`);
    if (this.yes.size > this.no.size && total >= threshold) {
      if (this.stillPlaying && !this.stillPlaying()) {
        await this.sender?.send("[投票通过] 但原歌曲已播放完毕，继续播放下一首");
      } else {
        await this.sender?.send("[投票通过] 正在切歌...");
        await onPass();
      }
    } else {
      await this.sender?.send("[投票未通过] 继续播放当前歌曲");
    }
  }

  handleVote(uid: number, vote: string): void {
    if (!this.active || this.voters.has(uid)) return;
    this.voters.add(uid);
    const v = (vote || "").trim().toUpperCase();
    if (["Y", "YES", "1"].includes(v)) this.yes.add(uid);
    else if (["N", "NO", "0"].includes(v)) this.no.add(uid);
  }

  cancel(): void {
    this.generation += 1;
    this.active = false;
    this.yes.clear();
    this.no.clear();
    this.voters.clear();
  }
}
