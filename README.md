<div align="center">

# 🎵 bili-songbot

![version](https://img.shields.io/badge/version-v1.0.1-blue?style=for-the-badge)
![license](https://img.shields.io/badge/license-GPL--3.0-green?style=for-the-badge)
![platform](https://img.shields.io/badge/platform-Windows%20x64-informational?style=for-the-badge)

**B 站直播间弹幕点歌台：观众发一条弹幕就能点歌，程序搜索 → 排队 → 播放，全程无人值守。**

</div>

---

## 快速开始（免安装）

1. 下载 `SongBot-1.0.1-win-x64.zip`，解压到任意目录（U 盘也行，无需安装）。
2. 双击 `SongBot.exe`：窗口约 0.5 秒内出现，后端在后台自动启动，界面立即可用。
3. 首次运行会在 exe 同级生成 `SongBotData/`（配置、队列、统计、日志都在这里；删目录即彻底清理）。
4. 打开「设置」页填写直播间号与 B 站 Cookie，保存后自动热更新，**无需重启**。

> 运行环境已内置（Python 3.12 / Node 22 / ffplay），机器上**不需要**装 Python、Node 或 FFmpeg。

数据目录可用环境变量 `SONGBOT_DATA_DIR` 指定其它位置；目录不可写时自动回退 `%APPDATA%\SongBot`。

## 界面（3 页）

| 页面 | 内容 |
|---|---|
| 点歌台 | 搜索（支持拼音 / 错别字 / 繁体 / 歌词片段）、队列、正在播放与控制条 |
| 弹幕互动 | 实时弹幕流、按「点歌」过滤、以主播身份发弹幕、连接信息 |
| 设置 | 音源加载状态、账号 Cookie、直播间、播放/API 参数、运行日志 |

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
| `bili_room_id` | — | 直播间号（非数字则进入降级模式：不连弹幕，界面照常可用） |
| `bili_sessdata` / `bili_jct` / `bili_buvid3` / `bili_buvid4` | — | B 站 Cookie，用于收发弹幕，支持热更新 |
| `source_dir` | `sources` | 本地 lx 音源脚本目录 |
| `source_urls` | `[]` | 远程音源链接（自动下载 + 定时自更新） |
| `min_quality` | `flac24bit` | 音质下限（`128k`/`192k`/`320k`/`flac`/`flac24bit`；`flac24bit` 表示不设下限） |
| `url_fetch_timeout` | `15` | 取链限时（秒） |
| `search_cache_ttl` / `url_cache_ttl` | `60` / `240` | 搜索 / 播放链接缓存（秒），`0` 关闭 |
| `player_backend` | `ffplay` | `ffplay`（默认）或 `mpv`（需系统 libmpv，缺失时设置页不显示该选项） |
| `queue_file` / `stats_file` | `queue.json` / `song_stats.json` | 队列与点歌统计持久化，置空关闭 |
| `replies` | 内置文案 | 自定义弹幕模板（按键覆盖） |

完整字段见 `config.example.json`。

> Cookie 获取：浏览器登录 B 站 → F12 → Application → Cookies，取 `SESSDATA`、`bili_jct`、`buvid3`、`buvid4`。

## 项目结构

```text
bili-songbot/
├── main.py                 # Python 后端入口（aiohttp，仅监听 127.0.0.1）
├── bot/                    # 后端核心：core / search / bridge / danmaku / protocol / api / player …
├── design/                 # 前端三页（index.html + app.js + styles.css，无构建步骤）
├── desktop/                # Electron 外壳（窗口、后端进程托管、API 代理）
├── sources/                # 7 个预置 lx 音源脚本 + manifest.json
├── source_bridge.js        # Node 侧 lx 音源运行时（每源一进程）
├── musicSdk_bundle.cjs     # lx 内置搜索引擎打包产物（Apache-2.0）
├── tools/                  # build_windows_runtime.ps1（内置运行时准备）、build_music_sdk/ 等
├── tests/                  # 单元测试
└── qa/                     # 验收脚本（可选）
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
python -m venv venv         # 后端依赖（aiohttp / jieba / rapidfuzz / pypinyin / bilibili-api-python）
venv\Scripts\pip install -r requirements.txt
npm start                   # dev 运行（数据目录 %APPDATA%\songbot-desktop）

npm run dist                # 准备内置运行时 → 打包 release/SongBot-<版本>-win-x64.zip
npm run dist:dir            # 只出解压目录 release/win-unpacked（调试用）
python -m pytest tests/ -q  # 单元测试
```

- 内置运行时由 `tools/build_windows_runtime.ps1` 下载组装到 `build-runtime/`（Python embed + 依赖、`node.exe`、静态 `ffplay.exe`），构建时复制为 `resources/runtime/`。
- 打包产物为「解压即用」文件夹压缩包：解压后直接运行 exe，**不做自解压**，因此双击即开、无等待。

## 已知限制

- 必须联网：搜索、取链、弹幕都依赖 B 站与音乐平台接口。
- 音源脚本来自第三方社区，接口随时可能失效；可在 `sources/` 增删，或在 `source_urls` 挂在线源。
- 发送弹幕需要有效 Cookie；未配置时「发送」按钮置灰，只能接收与点歌。
- 暂无 Windows 之外的桌面包。

## License

- 本项目代码：**GPL-3.0**（见 [LICENSE](LICENSE)）
- `musicSdk_bundle.cjs` 来自 [lx-music-desktop](https://github.com/lyswhut/lx-music-desktop)（Apache-2.0 + 补充条款），可复现构建见 `tools/build_music_sdk/`，许可明细见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## 免责声明

本项目不存储、不传播任何音频文件，仅作个人学习目的的技术验证。所有音乐数据来自第三方公开接口，版权归原作者与音乐平台。请使用后 24 小时内清除产生的版权数据，支持正版；使用本项目产生的任何问题由使用者自行承担。
