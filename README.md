<div align="center">

# 🎵 bili-songbot

![version](https://img.shields.io/badge/version-v2.0.0-blue?style=for-the-badge)
![license](https://img.shields.io/badge/license-GPL--3.0-green?style=for-the-badge)
![platform](https://img.shields.io/badge/platform-Windows%20x64-informational?style=for-the-badge)

**B 站直播间弹幕点歌台：观众发一条弹幕就能点歌，程序搜索 → 排队 → 播放，全程无人值守。**

</div>

---

## 快速开始（免安装）

1. 下载 `SongBot-2.0.0-win-x64.zip`，解压到任意目录（U 盘也行，无需安装）。
2. 先启动 **lxserver** 音源服务（见下一节），默认地址 `http://127.0.0.1:9527`。
3. 双击 `SongBot.exe`：窗口出现后，后端（Node/TypeScript）自动在后台启动。
4. 首次运行会在 exe 同级生成 `SongBotData/`（配置、队列、统计、日志都在这里；删目录即彻底清理）。
5. 打开「设置」页填写直播间号与 B 站 Cookie，保存后自动热更新，**无需重启**。

> 桌面包内置 Electron 自带的 Node 22 与 ffplay，机器上**不需要**装 Python、Node 或 FFmpeg；但必须能访问 lxserver。

数据目录可用环境变量 `SONGBOT_DATA_DIR` 指定其它位置；目录不可写时自动回退 `%APPDATA%\SongBot`。

## 音源服务 lxserver（必须，独立运行）

本项目**不内嵌**任何音源。搜索、取链、歌词全部由 [lxserver](https://github.com/XCQ0607/lxserver) 提供，后端通过 HTTP 调用：

```bash
# Node >= 22.13
git clone https://github.com/XCQ0607/lxserver.git
cd lxserver && npm install && npm start   # 默认监听 127.0.0.1:9527
```

- 在 lxserver 管理端导入音源脚本并**启用**（仓库 `sources/` 下的 7 个脚本可直接使用；也可以用它自己的在线源市场）。
- 多数音源脚本需要在 lxserver 侧允许 VM：环境变量 `SYSTEM_ALLOW_UNSAFE_VM=true`。
- 如果 lxserver 跑在另一台机器，把 `config.json` 的 `lxserver_url` 改成对应地址即可。
- 本项目的「设置 → 音源加载状态」会显示 lxserver 是否就绪。

## 界面（3 页）

| 页面 | 内容 |
|---|---|
| 点歌台 | 搜索（支持拼音 / 错别字 / 繁体 / 歌词片段）、队列、正在播放与控制条 |
| 弹幕互动 | 实时弹幕流、按「点歌」过滤、以主播身份发弹幕、连接信息 |
| 设置 | 音源服务状态、账号 Cookie、直播间、播放/API 参数、运行日志 |

## 弹幕指令

| 指令 | 说明 |
|---|---|
| `/play 歌名 歌手` | 点歌（顺序可颠倒、分隔可省略；分号分隔可批量） |
| `/lyric 歌词片段` | 歌词反查点歌（`/play` 失败时也自动兜底） |
| `/queue` `/队列` | 查看队列（含点歌人） |
| `/now` `/当前` | 正在播放（附署名） |
| `/switch` | 切歌：自己的直接切，别人的发起 Y/N 投票 |
| `/remove 序号` | 撤回**自己**点的歌 |
| `/top` | 点歌排行 TOP5（跨重启持久化） |
| `/help` | 帮助 |

## 配置（`SongBotData/config.json`）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `bili_room_id` | — | 直播间号（留空则只启动本地 API，不连弹幕） |
| `bili_sessdata` / `bili_jct` / `bili_buvid3` / `bili_buvid4` | — | B 站 Cookie，用于收发弹幕，支持热更新 |
| `lxserver_url` | `http://127.0.0.1:9527` | 音源服务地址（**核心依赖**） |
| `min_quality` | `flac24bit` | 音质下限（`128k`/`192k`/`320k`/`flac`/`flac24bit`；`flac24bit` 表示不设下限） |
| `url_fetch_timeout` | `15` | 取链限时（秒） |
| `search_cache_ttl` / `url_cache_ttl` | `60` / `240` | 搜索 / 播放链接缓存（秒），`0` 关闭 |
| `player_backend` | `ffplay` | 播放后端（当前仅 ffplay） |
| `ffplay_path` / `ffplay_args` | `ffplay` / 内置参数 | ffplay 可执行文件与额外参数 |
| `enable_lyrics_search` | `true` | `/play` 失败时用歌词片段兜底 |
| `max_msg_per_minute` / `danmaku_interval` | `20` / `5` | 发弹幕频率上限 / 最小间隔（秒） |
| `queue_file` / `stats_file` | `queue.json` / `song_stats.json` | 队列与点歌统计持久化，置空关闭 |
| `replies` | 内置文案 | 自定义弹幕模板（按键覆盖） |

完整字段见 `config.example.json`。

> Cookie 获取：浏览器登录 B 站 → F12 → Application → Cookies，取 `SESSDATA`、`bili_jct`、`buvid3`、`buvid4`。

## 架构

```text
Electron 外壳（desktop/）
   │  spawn（ELECTRON_RUN_AS_NODE）
   ▼
Node/TypeScript 后端（server/，127.0.0.1:8790-8799）
   ├── 本地 HTTP API  ◄── design/ 前端（原生 HTML/JS/CSS，无构建步骤）
   ├── B 站弹幕 WebSocket（ws）/ 弹幕发送 / 投票
   ├── 队列、统计、搜索评分、拼音模糊匹配（pinyin-pro + fuzzball）
   └── ffplay 子进程播放
   │  HTTP
   ▼
lxserver（独立服务，默认 :9527）──► 各音乐平台
```

## 项目结构

```text
bili-songbot/
├── server/                 # TypeScript 后端（Node 原生 http + fetch + ws）
│   ├── src/                # bot / api / search / lxserver / player / danmaku …
│   └── dist/               # 构建产物（npm run server:build）
├── design/                 # 前端三页（index.html + app.js + styles.css，无构建步骤）
├── desktop/                # Electron 外壳（窗口、后端进程托管、API 代理）
├── sources/                # 7 个可导入 lxserver 的音源脚本（不随应用打包）
├── tools/                  # build_windows_runtime.ps1（仅下载 ffplay）
├── config.example.json
└── README.md
```

## 本地接口

后端只监听 `127.0.0.1`（默认 8790，被占用时顺延，可用 `SONGBOT_API_PORT` 指定）。除 `/api/health` 外均需请求头 `X-SongBot-Token`；Electron 主进程代理请求，token 不下发渲染层。

| 方法 | 路径 |
|---|---|
| GET | `/api/health`、`/api/snapshot`、`/api/queue`、`/api/logs`、`/api/danmaku`、`/api/sources`、`/api/settings` |
| POST | `/api/queue/remove`、`/api/queue/pin`、`/api/queue/clear`、`/api/queue/skip`、`/api/play`、`/api/search`、`/api/live`、`/api/player/pause`、`/api/player/resume`、`/api/player/volume`、`/api/bot/accepting` |
| PUT | `/api/settings` |

## 开发与打包

```bash
npm install                 # Electron + electron-builder
npm run server:install      # 后端依赖（pinyin-pro / fuzzball / ws / typescript）
npm start                   # dev 运行（会先构建 server/dist）

npm run dist                # 下载 ffplay → 打包 release/SongBot-<版本>-win-x64.zip
npm run dist:dir            # 只出解压目录 release/win-unpacked（调试用）
```

- 只单独跑后端：`npm run server:start`（需先 `npm run server:build`）。
- 内置运行时由 `tools/build_windows_runtime.ps1` 下载静态 `ffplay.exe` 到 `build-runtime/ffmpeg/`。
- 打包产物为「解压即用」文件夹压缩包：解压后直接运行 exe，**不做自解压**。

## 已知限制

- 必须联网，且必须能访问 lxserver；lxserver 未启动时搜索/播放不可用（弹幕与界面仍可用）。
- 音源脚本来自第三方社区，接口随时可能失效；请在 lxserver 侧增删或更新音源。
- 发送弹幕需要有效 Cookie；未配置时只能接收与点歌。
- 暂无 Windows 之外的桌面包。

## License

- 本项目代码：**GPL-3.0**（见 [LICENSE](LICENSE)）
- lxserver 为独立运行的第三方服务，不在本仓库分发；其许可与免责声明以该项目为准。
- `sources/` 下的音源脚本来自第三方社区，仅作个人学习研究使用。

## 免责声明

本项目不存储、不传播任何音频文件，仅作个人学习目的的技术验证。所有音乐数据来自第三方公开接口，版权归原作者与音乐平台。请使用后 24 小时内清除产生的版权数据，支持正版；使用本项目产生的任何问题由使用者自行承担。
