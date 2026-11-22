/** 日志：控制台 + 文件追加，并同步进内存事件缓冲（日志页数据源）。 */
import fs from "node:fs";
import path from "node:path";

type Level = "DEBUG" | "INFO" | "WARN" | "ERROR";
const ORDER: Record<Level, number> = { DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40 };

const LEVEL = (process.env.SONGBOT_LOG_LEVEL || "INFO").toUpperCase() as Level;

class Logger {
  private stream: fs.WriteStream | null = null;
  private listeners: Array<(level: string, name: string, msg: string) => void> = [];

  setFile(file: string): void {
    if (!file) return;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      this.stream = fs.createWriteStream(file, { flags: "a" });
    } catch {
      this.stream = null;
    }
  }

  onEntry(fn: (level: string, name: string, msg: string) => void): void {
    this.listeners.push(fn);
  }

  private write(level: Level, msg: string): void {
    if (ORDER[level] < (ORDER[LEVEL] ?? 20)) return;
    const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
    const line = `${ts} [${level}] [点歌机] ${msg}`;
    if (level === "ERROR") console.error(line);
    else if (level === "WARN") console.warn(line);
    else console.log(line);
    try {
      this.stream?.write(`${line}\n`);
    } catch {
      /* 日志失败不影响主链路 */
    }
    for (const fn of this.listeners) {
      try {
        fn(level, "点歌机", msg);
      } catch {
        /* ignore */
      }
    }
  }

  debug(msg: string): void {
    this.write("DEBUG", msg);
  }
  info(msg: string): void {
    this.write("INFO", msg);
  }
  warn(msg: string): void {
    this.write("WARN", msg);
  }
  error(msg: string): void {
    this.write("ERROR", msg);
  }
}

export const log = new Logger();
