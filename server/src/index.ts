/** 入口：日志 → API → 机器人主循环。 */
import { ApiServer } from "./api.js";
import { BiliMusicBot } from "./bot.js";
import { config } from "./config.js";
import { installLogHandler } from "./events.js";
import { log } from "./logger.js";
import { lx } from "./lxserver.js";

async function main(): Promise<void> {
  log.setFile(config.dataPath("log_file", "logs/bot.log"));
  installLogHandler();
  log.info("点歌机器人启动中...");

  const bot = new BiliMusicBot();
  const api = new ApiServer(bot);
  await api.start();

  const lxReady = await lx.probe(true);
  log.info(`音源服务 lxserver: ${config.lxUrl()} ${lxReady ? "已就绪" : "不可达（搜索与播放将不可用）"}`);

  void bot.run();

  let shuttingDown = false;
  const shutdown = (signal: string): void => {
    if (shuttingDown) return;
    shuttingDown = true;
    log.info(`收到 ${signal}，正在退出...`);
    bot.stop();
    api.stop();
    setTimeout(() => process.exit(0), 400);
  };
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("unhandledRejection", (reason) => log.error(`未处理的 Promise 异常: ${String(reason)}`));
  process.on("uncaughtException", (e) => {
    log.error(`未捕获异常: ${e.stack || e.message}`);
  });
}

main().catch((e: unknown) => {
  log.error(`启动失败: ${(e as Error).stack || (e as Error).message}`);
  process.exit(1);
});
