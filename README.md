# B站直播间点歌机器人

一个基于 Python asyncio + Node.js 音源桥接器的 B站（Bilibili）直播间点歌机器人。通过 WebSocket 监听直播间弹幕，响应观众点歌和切歌投票指令，自动搜索音乐并播放。

## 功能特性

- **弹幕点歌** `/play 歌名` 或 `/play 歌名 - 歌手` — 5 大平台（网易云、QQ、酷我、酷狗、咪咕）并行搜索，3秒窗口收集结果择优返回（完美匹配立即返回）
- **切歌投票** `/switch` — 30 秒投票，根据在线人数动态计算门槛，投票目标歌曲已结束时自动跳过切歌
- **Node.js 音源桥接器** — 通过 `source_bridge.js` 正确执行落雪音乐格式 JS 音源脚本，支持搜索和 URL 获取
- **搜索结果评分** — 歌名完全匹配>包含匹配>模糊匹配，惩罚翻唱/混音/伴奏版本，优先返回原曲
- **模糊搜索匹配** — 文本标准化（去除括号内容/标点/空格）+ 双向子串匹配 + 字符重叠率≥60% 判定，兼容歌名细微差异
- **并发竞速搜索** — `asyncio.wait(FIRST_COMPLETED)` 并行搜索所有平台，8秒超时
- **并行音质获取 + 预验证 + 回退** — 并行获取所有音质 URL，GET Range 请求下载前 2KB 验证实际数据；所有验证失败时回退直接下载（确保代理 URL 歌曲 playable）；headers 按 URL 域名选择（参考Museek cdnHeadersForUrl），代理 URL 不带错误 Referer
- **音质降级** — flac24bit → flac → 320k → 192k → 128k 自动降级
- **代理 URL 回退** — 直接 CDN URL 优先，无直接 URL 时回退到代理 URL（由 GET Range 请求验证实际数据内容，参考Museek不拒绝代理URL）
- **Node.js 桥接器下载回退** — aiohttp 下载失败（0字节/超时）时，自动回退到 Node.js 原生 http/https 模块下载（参考 lx-music-desktop request.ts），解决代理 URL 对 Python 返回 0 字节的问题
- **先下载再播放** — 用 aiohttp 带平台特定 headers 下载到临时文件（失败时回退桥接器下载），再用 ffplay 播放，避免直链缺少 headers 导致失败
- **弹幕频率控制** — 5 秒最小间隔 + 每分钟 20 条上限 + 10030 自动重试（动态增加间隔）+ 按权重截断（中文=2 ASCII=1，40 单位=20 汉字）
- **弹幕歌名精简** — 只显示歌名（不含歌手），超过 10 字符自动截断为 `前5字...`（如 `Run Away With Me` → `Run A...`）
- **弹幕状态同步** — "正在播放"弹幕在音频下载完成、ffplay 启动前发送，确保弹幕状态与终端一致
- **API 请求限速** — 全局 API 请求频率限制器，所有 B站 API 调用最小间隔 1 秒，防止封 IP
- **WBI 签名** — 自动生成 w_rid/wts 签名参数，通过 B站风控验证
- **Cookie 管理** — 启动校验、过期预警、失效检测、热更新（改 config.json 即生效，无需重启）
- **弹幕连接** — 始终使用 `wss://host:443/sub`，遍历 host_list 优先选端口 443 主机，避免端口 2245 连接失败
- **搜索空格重试** — 带空格的搜索关键词失败后，自动用无空格关键词重试（如 "see you again" → "seeyouagain"），过滤垃圾结果（API原样返回搜索词）
- **代理 URL 快速失败** — 所有音质 URL 均为代理 URL（`.php?id=`）时直接跳过下载，不浪费时间
- **房间监控** — 每 60 秒检查直播状态，下播自动退出
- **日志系统** — 控制台 + 文件双输出，5MB 轮转 + 启动时自动清理过期日志（7天/50MB 双重限制）
- **播放结束通知** — 歌曲播放完毕/失败/切歌均发送弹幕通知

---

## 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.10+ | asyncio 异步框架 |
| Node.js | 16+ | 执行 JS 音源脚本（音源桥接器） |
| FFmpeg | 任意版本 | 提供 `ffplay` 命令行音频播放器 |
| pip 包 | 见 requirements.txt | aiohttp、websockets |

### 安装 FFmpeg（Windows）

1. 前往 https://www.gyan.dev/ffmpeg/builds/ 下载 Windows 预编译版
2. 解压后将其 `bin` 目录（含 `ffplay.exe`）加入系统 `PATH`
3. 验证：打开 cmd 执行 `ffplay -version`，能输出版本号即成功

如果 `ffplay` 不在 PATH 中，可在 `config.json` 中通过 `ffplay_path` 指定完整路径：
```json
"ffplay_path": "C:/ffmpeg/bin/ffplay.exe"
```

### 安装 Node.js

1. 前往 https://nodejs.org/ 下载 LTS 版本安装
2. 验证：打开 cmd 执行 `node -v`，能输出版本号即成功
3. 如果 node 不在 PATH 中，可在 `config.json` 中通过 `node_path` 指定完整路径

---

## 安装步骤

```bash
# 1. 安装 Python 依赖（使用虚拟环境）
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# 2. 确认 ffplay 可用
ffplay -version

# 3. 确认 Node.js 可用
node -v

# 4. 检查语法
venv\Scripts\python -c "import py_compile; py_compile.compile('bot.py', doraise=True)"
```

---

## 配置说明

编辑 `config.json`，填写以下字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `bili_room_id` | int | — | 直播间房间号（纯数字） |
| `bili_sessdata` | string | — | B站 Cookie 中的 SESSDATA 值 |
| `bili_jct` | string | — | B站 Cookie 中的 bili_jct 值（CSRF token） |
| `bili_buvid3` | string | — | B站 Cookie 中的 buvid3 值（风控必需） |
| `bili_buvid4` | string | — | B站 Cookie 中的 buvid4 值（风控辅助） |
| `danmaku_interval` | int | 5 | 弹幕发送最小间隔（秒），触发10030后动态增加 |
| `max_msg_per_minute` | int | 20 | 每分钟弹幕发送上限（条） |
| `danmaku_max_len` | int | 40 | 弹幕最大长度单位（中文=2，ASCII=1，40=20汉字，超出自动截断） |
| `api_rate_interval` | float | 1.0 | B站 API 请求最小间隔（秒） |
| `search_delay` | int | 2 | 搜索请求间隔（秒） |
| `room_check_interval` | int | 60 | 房间状态检查间隔（秒），检查结果记为 DEBUG 级别 |
| `vote_duration` | int | 30 | 切歌投票持续时间（秒） |
| `vote_broadcast_interval` | int | 8 | 投票进度广播间隔（秒） |
| `ffplay_path` | string | "ffplay" | ffplay 可执行文件路径 |
| `ffplay_args` | list | — | ffplay 额外参数 |
| `source_dir` | string | — | 音源脚本目录（落雪音乐格式 .js 文件） |
| `node_path` | string | "node" | Node.js 可执行文件路径 |
| `log_level` | string | "INFO" | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `log_file` | string | "logs/bot.log" | 日志文件路径 |
| `log_max_bytes` | int | 5242880 | 单个日志文件最大字节数（5MB），超出自动轮转 |
| `log_backup_count` | int | 3 | 保留的旧日志文件数量（总上限 = 4 × log_max_bytes） |
| `log_max_age_days` | int | 7 | 日志文件最大保留天数，启动时自动清理超期文件 |
| `log_max_total_mb` | int | 50 | logs 目录总大小上限(MB)，超限启动时从最旧开始删除 |

### 如何获取 B站 Cookie

1. 用浏览器登录 https://www.bilibili.com
2. 按 `F12` 打开开发者工具，切换到「Application」（Chrome）或「存储」（Firefox）
3. 在左侧找到 `Cookies` → `https://www.bilibili.com`
4. 找到以下四个值并复制到 `config.json`：
   - `SESSDATA` → 填入 `bili_sessdata`
   - `bili_jct` → 填入 `bili_jct`
   - `buvid3` → 填入 `bili_buvid3`
   - `buvid4` → 填入 `bili_buvid4`

> **buvid3 / buvid4**：B站风控指纹 Cookie，`getDanmuInfo` 接口强制要求 buvid3。没有它们，弹幕服务器连接会返回 `-352` 风控拦截。**必须从浏览器手动复制**。

### 音源脚本

音源脚本为落雪音乐桌面版（lx-music-desktop）格式的 `.js` 文件，放置在 `source_dir` 指定的目录中。程序通过 Node.js 桥接器（`source_bridge.js`）加载并执行这些脚本，注入 `lx` 全局对象来调用音源的搜索和 URL 获取功能。

---

## 启动方法

```bash
python bot.py
```

> **Windows 用户**：双击 `run.bat` 即可启动（自动使用项目虚拟环境 `venv\` 中的 Python）。也可手动运行：
> ```bash
> venv\Scripts\python bot.py
> ```

启动后会看到类似输出：
```
00:00:00 [INFO] ==================================================
00:00:00 [INFO] B站直播间点歌机器人 启动
00:00:00 [INFO] 房间号: 30598242
00:00:00 [INFO] SESSDATA 剩余有效期: XXX 天
00:00:00 [INFO] ==================================================
00:00:00 [INFO] 音源桥接器已就绪 (PID: 12345)
00:00:00 [INFO] 桥接器加载了 19 个音源脚本
00:00:00 [INFO] 使用 config.json 中的 bili_buvid3: XXXXXXXX...
00:00:00 [INFO] Cookie 有效，当前用户: 你的用户名
00:00:00 [INFO] 真实房间号: 30598242
00:00:00 [INFO] 连接弹幕服务器: wss://hw-bj-live-chat.bilibili.com:443/sub
00:00:00 [INFO] 弹幕认证成功
00:00:00 [INFO] 点歌机器人已上线！发送 /play 歌名 歌手 来点歌
```

按 `Ctrl+C` 可安全退出。

---

## 弹幕指令使用方法

### /play — 点歌

```
/play 歌名
/play 歌名 歌手
/play 歌名 - 歌手
/play 歌名 — 歌手
/play 歌名 – 歌手
/play 歌名 by 歌手
/play 歌名 歌手A 歌手B
/play 歌名 歌手A/歌手B
```

- **歌名**：必填，支持多词歌名（如 `see you again`）
- **歌手**：可选，需用分隔符指定
- **分隔符**：支持 ` - `、` — `、` – `、` by ` 拆分歌名和歌手；**无分隔符时整个查询作为歌名搜索**（不拆分歌手）
- 搜索失败时自动用无空格关键词重试（如 `see you again` → `seeyouagain`）
- 搜索结果过滤垃圾匹配（歌名=搜索词原样且歌手为空时跳过）
- 机器人会并行在 5 大平台搜索，3 秒窗口收集结果并评分择优（完美匹配立即返回）
- 搜索结果使用模糊匹配过滤（文本标准化 + 字符重叠率 ≥ 60%），兼容歌名细微差异
- 搜索到歌曲后直接加入播放队列末尾，弹幕回复排队位置
- 播放时并行获取所有音质 URL，全部为代理 URL 时直接跳过；否则 GET Range 预验证后按音质优先级依次尝试下载，下载失败自动重试下一音质；aiohttp 下载失败时自动回退 Node.js 桥接器下载

### /switch — 发起切歌投票

```
/switch
```

- 仅在正在播放歌曲时可发起
- 投票持续 30 秒，期间每 8 秒广播一次当前票数
- 观众发送 `Y` 或 `N` 参与投票（每人限投一票）

### 投票门槛（根据在线人数动态计算）

| 在线人数 | 需要的参与人数（Y+N） | 通过条件 |
|---------|-------------------|---------|
| 0-1 人 | 1 | Y > N |
| 2-4 人 | 2 | Y > N 且总参与 ≥ 2 |
| 5-6 人 | 3 | Y > N 且总参与 ≥ 3 |
| 7+ 人 | 半数（向上取整） | Y > N 且总参与 ≥ 半数 |

---

## 弹幕发送频率控制

为防止 B站封 IP 或限制账号，程序内置多层频率控制：

| 控制层 | 机制 | 说明 |
|--------|------|------|
| **弹幕间隔** | 3 秒最小间隔 | 两条弹幕之间至少间隔 3 秒（可配置 `danmaku_interval`） |
| **每分钟上限** | 20 条/分钟 | 超过后等待到下一分钟窗口（可配置 `max_msg_per_minute`） |
| **10030 重试** | 最多 3 次 | 频率过快时自动等待 5/10/15 秒后重试，不丢弃消息 |
| **动态间隔** | 连续 10030 加大间隔 | 每次连续触发 10030，间隔自动 +2 秒 |
| **字数截断** | 40 单位上限 | B站弹幕按中文=2/ASCII=1 计长，超出自动按权重截断（可配置 `danmaku_max_len`） |
| **API 限速** | 1 秒最小间隔 | 所有 B站 API 请求（弹幕发送、房间检查、Cookie 校验等）共享全局限速器 |

---

## 播放流程

```
用户发送 /play 歌名 歌手
  ↓
弹幕回复"正在搜索: ..."
  ↓
Node.js 桥接器并发搜索 5 大平台（8秒超时）→ 3秒窗口收集结果，模糊匹配过滤+评分择优
  ↓
弹幕回复"已点: 歌名 (队列:N)"（只显示歌名，过长用...省略）
  ↓
队列消费循环取出歌曲
  ↓
并行获取所有音质 URL（flac24bit→flac→320k→192k→128k）
  ↓
GET Range 请求预验证 URL（下载前2KB检查实际数据内容，排除JSON/HTML/0字节）
  ↓
aiohttp 下载音频到 tmp/ 临时文件（30秒超时，按URL域名选择headers）
  ├─ 成功 → 继续
  └─ 失败 → Node.js 桥接器下载回退（原生http/https，支持重定向，30秒超时）
  ↓
下载完成 → 弹幕回复"正在播放: 歌名"（此时音频已就绪，状态一致）
  ↓
预验证失败的音质自动跳过，下载失败也自动尝试下一音质
  ↓
ffplay 播放本地文件
  ↓
弹幕回复"播放完毕: 歌名" 或 "播放失败: 歌名"
```

### 切歌流程

```
用户发送 /switch
  ↓
弹幕回复"[切歌投票] Y/N ..."
  ↓
30 秒投票，每 8 秒广播进度
  ↓
投票通过 → 检查原歌曲是否仍在播放：
  · 仍在播放 → 停止 ffplay → 弹幕"已切歌"
  · 已结束（自然播放完毕）→ 跳过切歌 → 弹幕"原歌曲已播放完毕，继续播放下一首"
投票未通过 → 继续播放
```

> 切歌不会触发"播放失败"消息，程序通过 `_intentional_stop` 标记区分主动停止和真正的播放异常，切歌时仅显示"已切歌"。
> **竞态修复**：投票结束时通过 `song_id` 比较确认投票目标歌曲是否仍在播放，防止"歌曲自然结束后切到下一首"的误切问题。

---

## Cookie 有效期管理

### 自动检测流程

1. **启动校验**：调用 B站 nav API 确认 Cookie 有效
2. **过期预警**：从 SESSDATA 解码过期时间戳，启动时打印剩余天数
3. **运行时检测**：弹幕发送返回 -101/-111/-400 时自动标记 Cookie 失效
4. **热更新**：修改 `config.json` 保存后，10 秒内自动检测并恢复

### code=10030 处理

B站弹幕发送返回 `code=10030` 表示频率过快，**不是 Cookie 失效**。程序会：
1. 等待 5 秒后重试（最多 3 次，等待递增 5/10/15 秒）
2. 连续触发时自动加大发送间隔
3. 不会标记 Cookie 无效

---

## 项目结构

```
B站点歌机/
├── bot.py             # 主脚本（Python，~2800行）
├── source_bridge.js   # Node.js 音源桥接器（执行 JS 音源脚本）
├── config.json        # 配置文件（含 B站凭证 + 频率控制 + 日志设置）
├── requirements.txt   # Python 依赖
├── run.bat            # Windows 一键启动脚本（使用 venv 虚拟环境，CRLF 编码）
├── README.md          # 本文件
├── 工作记录.md        # 开发变更记录
├── venv/              # Python 虚拟环境（自动创建，含 aiohttp/websockets）
├── logs/              # 日志目录（自动创建）
│   ├── bot.log        # 当前日志
│   └── bot.log.1      # 轮转的旧日志
└── tmp/               # 音频临时文件目录（自动创建，播放后自动清理）
```

### bot.py 核心模块

| 模块 | 说明 |
|------|------|
| 日志配置 | 控制台 + 文件双输出，支持轮转和级别配置 |
| 配置加载 | 读取 config.json，支持热更新 |
| ApiRateLimiter | 全局 B站 API 请求频率限制器（默认 1 秒间隔） |
| WBI 签名 | w_rid/wts 自动生成，通过 B站风控 |
| CookieManager | 校验、过期检测、热更新 |
| SourceBridge | Node.js 子进程管理，stdin/stdout JSON 通信 |
| 搜索 | 5 平台并行搜索（3秒窗口择优），模糊匹配+歌名歌手校验+评分，8秒超时 |
| BiliDanmaku | WebSocket 弹幕客户端（WBI签名、buvid、真实UID、强制443端口wss） |
| BiliSender | HTTP 弹幕发送（频率控制、10030重试、字数截断） |
| PlaybackManager | 先下载再播放，aiohttp+桥接器双下载回退，并行音质获取+预验证，主动停止/失败区分 |
| VoteManager | 切歌投票（动态门槛，song_id 竞态检测） |
| PlayQueue | FIFO 播放队列 |
| BiliMusicBot | 主控器，协调所有模块 |

### source_bridge.js

Node.js 音源桥接器，核心功能：
- 加载 `source_dir` 目录下所有 `.js` 音源脚本
- 注入 `lx` 全局对象（`request`/`on`/`send`/`utils.crypto`/`utils.buffer`/`utils.zlib`）
- 通过 stdin/stdout JSON 协议与 Python 通信
- 搜索：并发查询所有平台，`Promise.any` + `Promise.race` 8 秒超时，返回首个成功结果
- URL 获取：1 秒收集窗口 + 直接 CDN URL 优先 + 代理 URL 回退（由 Python 端 GET Range 验证）
- 下载：Node.js 原生 http/https 模块流式下载（支持重定向/超时/大小检查），作为 aiohttp 下载失败的回退方案
- stdin 关闭时等待 pending 请求完成

---

## 日志系统

机器人内置完整的日志系统，同时输出到**控制台**和**文件**。

| 输出目标 | 格式 | 说明 |
|---------|------|------|
| 控制台 | `HH:MM:SS [LEVEL] 消息` | 实时查看 |
| 文件 | `YYYY-MM-DD HH:MM:SS [LEVEL] [点歌机] 消息` | 持久化保存 |

- **路径**：`logs/bot.log`（可配置）
- **自动轮转**：单个文件达到 5MB 后自动切割，保留 3 个旧文件，总上限 20MB（4 × 5MB）
- **启动清理**：每次启动时自动清理超过 7 天的日志文件；如果 logs 目录总大小超过 50MB，从最旧开始删除直到达标（永不删除当前 `bot.log`）
- **编码**：UTF-8
- **日志优化**：房间状态正常检查记录为 DEBUG 级别（不再刷屏 INFO 日志）；10030 频率限制记录为 INFO（已自动重试，无需警告）

### 日志级别

| 级别 | 用途 |
|------|------|
| `DEBUG` | 详细调试信息（桥接器日志、API限速等待等） |
| `INFO` | 常规运行信息（默认级别） |
| `WARNING` | 非致命异常（10030频率过快、连接断开重连等） |
| `ERROR` | 错误（Cookie失效、播放失败等） |

> **排查 bug 时**：将 `log_level` 改为 `DEBUG`，重启机器人。

---

## 常见问题

### Q: 启动后没有弹幕"点歌机器人已上线"？

A: 检查 Cookie 是否有效。启动日志会显示 `Cookie 有效` 或 `Cookie 无效`。若无效，请更新 `config.json`。

### Q: 弹幕连接报错 "code: -352" 或 "code: -400"？

A: B站对 `getDanmuInfo` 接口有严格要求：
- **-400**：参数名错误或缺少必要参数。程序已通过 `room_init` API 自动转换短号为真实房间号。
- **-352**：WBI 签名验证失败。检查 `bili_buvid3` 是否已填写。

程序内置三级回退：getDanmuInfo (WBI签名) → getConf (旧接口) → 硬编码地址。

### Q: 弹幕连接报 "HTTP 404" 或 "InvalidMessage"？

A: B站弹幕服务器返回的端口 2245/2246 既不支持 wss 也不支持 ws，只有端口 443 + wss 可靠。程序已修复为始终使用 `wss://host:443/sub`，并遍历 host_list 优先选端口 443 的主机。如果仍出现连接问题，通常是 B站服务器临时故障，程序会自动 3 秒后重连。

### Q: 搜索英文歌曲失败（如 "see you again"）？

A: 部分音源 API 不支持带空格的搜索关键词。程序已内置重试机制：搜索失败后自动用无空格关键词重试（如 "see you again" → "seeyouagain"）。如果仍然失败，可以手动去掉空格发送 `/play seeyouagain`。

### Q: 弹幕发送失败，提示超出长度限制？

A: B站弹幕按"中文=2单位、ASCII=1单位"计长，限制约 20 个汉字（40 单位）。程序已内置按权重自动截断（`danmaku_max_len` 配置，默认 40 单位），超长消息会按权重截断后发送。

### Q: 弹幕发送失败，code=10030？

A: 这是频率过快，不是 Cookie 失效。程序会自动等待 5/10/15 秒重试（最多 3 次）。如果频繁出现，可以增大 `danmaku_interval` 或减小 `max_msg_per_minute`。

### Q: 切歌后提示"播放异常"？

A: 已修复。程序通过 `_intentional_stop` 标记区分主动切歌和真正的播放失败。切歌时只会显示"已切歌"，不会显示"播放失败"或"播放异常"。

### Q: 搜索结果不正确？

A: 程序通过 Node.js 桥接器执行音源脚本，确保与落雪音乐桌面版完全一致的搜索行为。如果结果仍不对，检查音源脚本是否为最新版本。

### Q: 播放失败，下载 0 字节？

A: 可能原因：
- 音源 API 返回了代理 URL（非直接音频链接），Python aiohttp 下载返回 0 字节
- URL 已过期
- 网络连接问题

程序已内置多重保护：
- 代理 URL 黑名单过滤 + 3 秒收集窗口优先选择直接 CDN URL
- **音质降级重试**：下载失败（0 字节）时自动尝试下一音质（flac24bit → flac → 320k → 192k → 128k），直到找到可用的音频链接
- **Node.js 桥接器下载回退**：aiohttp 下载失败时，自动回退到 Node.js 原生 http/https 模块下载（支持重定向、15秒超时、文件大小检查），解决代理 URL 对 Python 返回 0 字节的问题
- **代理 URL 快速跳过**：已知无法下载的代理 URL（`.php?id=` 模式）直接跳过桥接器下载，避免无谓等待（5 个音质从 5+ 分钟降至 ~5 秒）
- 所有音质均失败时才报告"播放失败，已跳过"

### Q: 如何排查某个音源为什么失败？

A: 将 `log_level` 改为 `DEBUG`，重启机器人。日志中会记录桥接器的详细输出。

### Q: 如何防止被 B站封 IP？

A: 程序已内置多层保护：
- 弹幕发送：3 秒间隔 + 20 条/分钟上限 + 10030 自动退避
- API 请求：全局 1 秒最小间隔限速器
- 可通过 `api_rate_interval` 调整 API 限速强度

---

## 注意事项

### 1. 安全提醒

- `config.json` 包含 B站登录凭证，**请勿分享或上传到公开仓库**
- 建议将 `config.json` 加入 `.gitignore`

### 2. 音源可用性

- 音源脚本可能随时失效，程序会自动跳过不可用的音源
- 音源脚本需要放在 `source_dir` 指定的目录中

### 3. 运行平台

- 主要面向 Windows 平台开发
- Linux/macOS 需确保 ffplay 和 node 已安装并在 PATH 中

### 4. 直播间状态

- 机器人仅在直播间开播期间工作
- 每 60 秒检查一次直播状态，检测到下播后自动退出
