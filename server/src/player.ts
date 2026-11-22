/** ffplay 播放器：下载到临时文件 → 播放；暂停/续播/音量用「记录进度 + 重启」实现。 */
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { config } from "./config.js";
import { log } from "./logger.js";
import type { SongInfo } from "./types.js";
import { parseDuration } from "./util.js";

const PLATFORM_REFERER: Record<string, string> = {
  wy: "https://music.163.com/",
  tx: "https://y.qq.com/",
  kw: "https://www.kuwo.cn/",
  kg: "https://www.kugou.com/",
  mg: "https://music.migu.cn/",
};

const PLATFORM_UA: Record<string, string> = {
  wy: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
  tx: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
  kw: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
  kg: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
  mg: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
};

function isAudioBuffer(buf: Buffer, contentType: string): boolean {
  if (buf.length < 1024) return false;
  if (contentType.includes("text/html") || contentType.includes("application/json")) return false;
  const head = buf.subarray(0, 12);
  if (head.subarray(0, 3).toString("latin1") === "ID3") return true;
  if (head[0] === 0xff && (head[1] & 0xe0) === 0xe0) return true;
  const sig = head.toString("latin1");
  if (sig.startsWith("fLaC") || sig.startsWith("OggS") || sig.startsWith("RIFF")) return true;
  if (head.subarray(4, 8).toString("latin1") === "ftyp") return true;
  if (sig.startsWith("MAC ")) return true;
  // 有些 CDN 返回 mp4/m4a 但头部不标准：命中常见音频 content-type 也放行
  return /audio|mpeg|mp4|flac|ogg/i.test(contentType);
}

function extensionFor(contentType: string, url: string): string {
  if (/flac/i.test(contentType) || /\.flac($|\?)/i.test(url)) return ".flac";
  if (/m4a|mp4/i.test(contentType) || /\.(m4a|mp4)($|\?)/i.test(url)) return ".m4a";
  if (/ogg|opus/i.test(contentType) || /\.(ogg|opus)($|\?)/i.test(url)) return ".ogg";
  if (/wav/i.test(contentType)) return ".wav";
  return ".mp3";
}

export async function downloadAudio(song: SongInfo, url: string): Promise<string | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  try {
    const headers: Record<string, string> = { "User-Agent": PLATFORM_UA[song.platform] || PLATFORM_UA.wy };
    const referer = PLATFORM_REFERER[song.platform];
    if (referer) headers.Referer = referer;
    const res = await fetch(url, { headers, signal: controller.signal, redirect: "follow" });
    if (!res.ok) {
      log.warn(`下载失败 HTTP ${res.status}: ${song.name}`);
      return null;
    }
    const contentType = (res.headers.get("content-type") || "").toLowerCase();
    const buf = Buffer.from(await res.arrayBuffer());
    if (!isAudioBuffer(buf, contentType)) {
      log.warn(`下载内容不是有效音频（${contentType || "unknown"}, ${buf.length}B）: ${song.name}`);
      return null;
    }
    const file = path.join(
      os.tmpdir(),
      `songbot-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}${extensionFor(contentType, url)}`,
    );
    fs.writeFileSync(file, buf);
    log.info(`已下载音频: ${song.name} (${(buf.length / 1024 / 1024).toFixed(1)}MB)`);
    return file;
  } catch (e) {
    log.warn(`下载异常: ${song.name} - ${(e as Error).message}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

interface PlayResult {
  code: number | null;
  error?: Error;
}

export class FfplayPlayer {
  private proc: ChildProcess | null = null;
  private file: string | null = null;
  private targetPath = "";
  private paused = false;
  private needRespawn = false;
  private intentionalStop = false;
  private basePos = 0;
  private runStart: number | null = null;
  private duration = 0;
  private volume = 100;
  private wake: (() => void) | null = null;
  private playing = false;
  private currentSong: SongInfo | null = null;

  get isPlaying(): boolean {
    return this.playing;
  }

  get current(): SongInfo | null {
    return this.currentSong;
  }

  position(): number {
    const p = this.runStart !== null ? this.basePos + (Date.now() - this.runStart) / 1000 : this.basePos;
    const max = this.duration > 0 ? this.duration : 86400;
    return Math.max(0, Math.min(p, max));
  }

  private waitResume(): Promise<void> {
    return new Promise((resolve) => {
      this.wake = resolve;
    });
  }

  private cleanup(): void {
    if (this.file) {
      try {
        fs.unlinkSync(this.file);
      } catch {
        /* 已被删/占用则忽略 */
      }
      this.file = null;
    }
    this.playing = false;
    this.paused = false;
    this.runStart = null;
    this.proc = null;
  }

  async play(song: SongInfo, url: string, onStart?: () => void | Promise<void>): Promise<boolean> {
    this.stop();
    this.intentionalStop = false;
    this.paused = false;
    this.needRespawn = false;
    this.basePos = 0;
    this.duration = parseDuration(song.interval);
    this.currentSong = song;

    const file = await downloadAudio(song, url);
    if (!file) {
      this.currentSong = null;
      return false;
    }
    this.file = file;
    this.targetPath = file;
    this.playing = true;
    let started = false;

    try {
      while (true) {
        const result = await this.spawnOnce(() => {
          if (!started) {
            started = true;
            void onStart?.();
          }
        });
        if (this.intentionalStop) break;
        if (this.paused) {
          await this.waitResume();
          if (this.intentionalStop) break;
        }
        if (this.needRespawn) {
          this.needRespawn = false;
          continue;
        }
        if (result.error) {
          log.error(`ffplay 启动失败: ${result.error.message}（请安装 ffmpeg/ffplay 或配置 ffplay_path）`);
          return false;
        }
        break;
      }
      return started;
    } finally {
      if (started) {
        /* 正常结束 */
      }
      this.currentSong = null;
      this.cleanup();
    }
  }

  private spawnOnce(onSpawn?: () => void): Promise<PlayResult> {
    return new Promise((resolve) => {
      const ffplay = String(config.get("ffplay_path", "ffplay"));
      const extra = (config.get("ffplay_args", []) as string[]) || [];
      const args = [
        "-nodisp",
        "-autoexit",
        "-loglevel",
        "quiet",
        "-volume",
        String(this.volume),
        ...(this.basePos >= 1 ? ["-ss", this.basePos.toFixed(2)] : []),
        "-i",
        this.targetPath,
      ];
      for (const a of extra) if (!args.includes(a)) args.push(a);

      log.debug(`ffplay 启动: ${ffplay} ${args.join(" ")}`);
      let proc: ChildProcess;
      try {
        proc = spawn(ffplay, args, { stdio: ["ignore", "ignore", "pipe"], windowsHide: true });
      } catch (e) {
        resolve({ code: null, error: e as Error });
        return;
      }
      this.proc = proc;
      this.runStart = Date.now();
      onSpawn?.();
      let settled = false;
      const done = (result: PlayResult): void => {
        if (settled) return;
        settled = true;
        this.runStart = null;
        this.proc = null;
        resolve(result);
      };
      proc.stderr?.on("data", (chunk: Buffer) => {
        const text = chunk.toString().trim();
        if (text) log.debug(`ffplay: ${text.slice(0, 300)}`);
      });
      proc.on("error", (e) => done({ code: null, error: e }));
      proc.on("exit", (code) => done({ code }));
    });
  }

  pause(): boolean {
    if (!this.proc || this.paused) return false;
    this.basePos = this.position();
    this.runStart = null;
    this.paused = true;
    this.needRespawn = true;
    try {
      this.proc.kill();
    } catch {
      /* ignore */
    }
    return true;
  }

  resume(): boolean {
    if (!this.paused) return false;
    this.paused = false;
    const wake = this.wake;
    this.wake = null;
    wake?.();
    return true;
  }

  setVolume(volume: number): boolean {
    const v = Math.max(0, Math.min(100, Math.round(volume)));
    const changed = v !== this.volume;
    this.volume = v;
    if (this.proc && !this.paused && changed) {
      this.basePos = this.position();
      this.runStart = null;
      this.needRespawn = true;
      try {
        this.proc.kill();
      } catch {
        /* ignore */
      }
    }
    return true;
  }

  stop(): void {
    this.intentionalStop = true;
    const wake = this.wake;
    this.wake = null;
    wake?.();
    try {
      this.proc?.kill();
    } catch {
      /* ignore */
    }
    this.proc = null;
  }

  state(): Record<string, unknown> {
    return {
      backend: "ffplay",
      playing: this.playing,
      paused: this.paused,
      position: Math.round(this.position() * 10) / 10,
      duration: Math.round(this.duration),
      volume: this.volume,
      capabilities: { pause: true, resume: true, volume: true, seek: false },
    };
  }
}
