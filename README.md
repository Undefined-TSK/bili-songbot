<div align="center">

# 🎵 bili-songbot

![version](https://img.shields.io/badge/version-v1.0.0-blue?style=for-the-badge)
![license](https://img.shields.io/badge/license-GPL--3.0-green?style=for-the-badge)
![python](https://img.shields.io/badge/python-3.10%2B-informational?style=for-the-badge)

**B 站直播间弹幕点歌机器人：观众发一条弹幕就能点歌，机器人搜索 → 排队 → 播放，全程无人值守。**

</div>

---

> 直播主想要一个"公共点歌台"：观众点歌、大家排队听，还要保证播的永远是**原版**、点歌体验**零门槛**。市面上现成点歌器要么解析死板（必须 `歌名 歌手` 严格顺序），要么单音源一挂全挂。本项目兼容 **lx（落雪）音源生态**——社区 44 个音源即插即用，双通道搜索 + 跨平台共识，把"搜不准、播不对、源易挂"三个痛点一次解决。

## ✨ Features

- **点歌零门槛** — `/play 周杰伦 告白气球`、`/play 告白气球周杰伦`、全拼 `zhoujielun`、错别字 `周杰轮`、繁体 `告白氣球`、甚至歌词片段 `/lyric 天青色等烟雨` 都能命中；只记得旋律也能点
- **永远播原版** — 翻唱/Remix/Live/DJ 版强降权 + 跨平台共识加权（≥2 平台同返即锁定原唱）+ 置信度门禁拒绝无关结果 + 同歌跨源回退
- **音源即插即用** — 兼容 lx 标准与魔改 JS 音源脚本、支持 `source_urls` 链接音源；自更新（默认 6h）、看门狗自愈（默认 5min）、多镜像回退，用户放进去就能用，**不需要改任何脚本**
- **公共台秩序** — 同歌防重复入队、点歌人署名、自己点的歌直接切、别人的歌 Y/N 投票、点歌排行持久化
- **重启不丢队列** — 队列实时落盘（`queue_file`），机器人重启后未播完的歌自动恢复并继续排队（同类项目多为纯内存队列）
- **连点不重复打源** — 搜索与播放链接进程内 TTL 缓存（`search_cache_ttl` / `url_cache_ttl`），复读弹幕、重复播放直接复用；失效链接与下载失败的音质自动作废重取
- **开箱可定制** — 全部弹幕文案模板可覆盖，音质下限/批量上限即改即用；Cookie 热更新免重启

<details>
<summary>📖 全部弹幕指令</summary>

| 指令 | 说明 |
|---|---|
| `/play 歌名 歌手` | 点歌（顺序可颠倒、分隔可省略；分号 `；` 分隔可批量点歌） |
| `/lyric 歌词片段` | 歌词反查点歌（`/play` 失败时也自动兜底） |
| `/queue` `/队列` | 查看队列（含点歌人） |
| `/now` `/当前` | 正在播放（附署名） |
| `/switch` | 切歌：自己的直接切，别人的发起 Y/N 投票 |
| `/remove 序号` | 撤回**自己**点的歌 |
| `/top` | 点歌排行 TOP5（跨重启持久化） |
| `/help` | 帮助 |

</details>

## 🚀 Quick Start

前置依赖：**Python ≥ 3.10**、**Node.js ≥ 16**、**ffmpeg**（需要其中的 `ffplay`）。

```bash
# 1. 克隆并安装依赖
git clone https://github.com/Undefined-TSK/bili-songbot.git
cd bili-songbot
pip install -r requirements.txt # 全部依赖（含 rapidfuzz 模糊评分 / pypinyin 拼音容错 / python-mpv）
# python-mpv 另需系统 libmpv（Ubuntu: apt install libmpv2；仅 mpv 播放后端与相关测试需要，
# 默认 ffplay 后端不受影响）

# 2. 生成配置
cp config.example.json config.json
# 编辑 config.json，至少填写：
#   bili_room_id: 你的直播间号
#   bili_sessdata / bili_jct / bili_buvid3 / bili_buvid4: B 站 Cookie（用于收发弹幕）
# 音源不用管：sources/ 已预置 44 个音源，config.json 的 source_urls 还挂了 7 个官方在线源

# 3. 启动
python main.py
```

启动成功后日志会出现弹幕连接与音源加载信息（形如 `音源进程就绪`），在直播间发一条 `/play 告白气球 周杰伦`，机器人搜索后回弹幕「已加入队列」并开始播放，即验证成功。

> [!NOTE]
> Cookie 获取方式：浏览器登录 B 站 → F12 → Application → Cookies，取 `SESSDATA`、`bili_jct`、`buvid3`、`buvid4` 四项。改完 `config.json` 保存即可，**10 秒内热更新生效，无需重启**。

> [!WARNING]
> 本项目仅供个人学习研究，不存储、不传播任何音频文件。禁止商业使用，详见文末[免责声明](#-免责声明)。

## ⚙️ 配置（config.json）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `bili_room_id` | — | 目标直播间号（必填） |
| `bili_sessdata` 等 4 项 | — | B 站 Cookie（必填，支持热更新） |
| `source_dir` | `sources` | 本地 lx 音源脚本目录 |
| `source_urls` | 7 个官方源 | 远程音源链接列表（自动下载 + 自更新） |
| `min_quality` | `128k` | 音质下限（可设 `192k`/`320k`/`flac`） |
| `max_batch_songs` | `10` | 批量点歌数量上限 |
| `url_fetch_timeout` | `15` | 取链限时（秒），慢源不拖累整体 |
| `source_refresh_interval` | `21600` | 在线源自更新间隔（秒） |
| `source_watchdog_interval` | `300` | 看门狗自愈巡检间隔（秒） |
| `source_init_timeout` | `30` | 音源初始化超时（秒），慢源不再被误杀 |
| `enable_lyrics_search` | `true` | 歌词点歌/兜底开关 |
| `search_cache_ttl` | `60` | 搜索结果缓存时长（秒），`0` 关闭 |
| `url_cache_ttl` | `240` | 播放链接缓存时长（秒），`0` 关闭 |
| `cache_max_entries` | `256` | 每个缓存的条目上限（LRU 淘汰） |
| `queue_file` | `queue.json` | 队列持久化文件（重启恢复；置空关闭） |
| `replies` | 内置文案 | 自定义弹幕模板（键值覆盖） |
| `stats_file` | `song_stats.json` | 点歌统计文件（置空关闭） |

## 🧪 测试

```bash
pip install -r requirements.txt pytest   # 运行依赖 + 测试框架
python3 -m pytest tests/ -q              # 单元测试（依赖齐全时 207 条全通过）
python3 tests/e2e_flow.py                # 端到端（含真实网络搜索 → 取链 → 播放回退链路）
```

正式版验收（Horizon QA）：**47 条用例全 PASS**、0 开放缺陷，覆盖即插即用、链接/JS 双形态音源、自更新、自检测自愈、内核健壮性与安全。执行器见 `qa/tests/e2e/run_acceptance.py`，追踪数据见 `qa/results.json`。

<details>
<summary>🏗 技术栈与文件树（给交接者/贡献者）</summary>

| 层 | 技术 |
|---|---|
| 弹幕接入 | bilibili-api-python LiveDanmaku（连接/认证/心跳/重连/WBI 由库处理） |
| 音源内核 | Node.js 子进程池（每源一进程，lx 协议对齐 lx-music-desktop v2.12.6） |
| 播放引擎 | ffplay 子进程（默认）/ python-mpv（`player_backend=mpv` 可选） |
| 模糊匹配 | difflib 默认 / rapidfuzz 可选 + pypinyin 拼音层 |
| 持久化 | JSON 文件（队列、统计、在线源缓存） |

```text
bili-songbot/
├── main.py                  # 入口
├── bot/                     # 核心 Python 包
│   ├── core.py              # 主编排：搜索→入队→取链→播放
│   ├── bridge.py            # 音源内核（Node 子进程管理/去重/自更新/自愈）
│   ├── danmaku.py           # 弹幕发送（BiliSender：频控/10030 退避）
│   ├── danmaku_biliapi.py   # 弹幕监听（bilibili-api-python LiveDanmaku 适配）
│   ├── protocol.py          # B 站 HTTP 协议（限流 / buvid3 / Cookie 热重载）
│   ├── search.py            # 双通道搜索与共识排序
│   ├── cache.py             # 进程内 TTL 缓存（搜索/播放链接复用）
│   ├── lyrics.py            # 歌词点歌
│   ├── artist_alias.py      # 艺名别名互认
│   └── config.py            # 配置热加载
├── source_bridge.js         # Node 侧 lx 音源运行时（lx API 实现）
├── musicSdk_bundle.cjs      # lx 内置搜索引擎打包产物（Apache-2.0，见构建说明）
├── sources/                 # 44 个预置音源（46 个文件）
├── qa/                      # 正式版验收套件（47 用例执行器 + results.json）
├── tests/                   # 207 个单元测试（依赖齐全时全通过）+ e2e_flow.py 端到端脚本
└── tools/
    ├── build_music_sdk/     # musicSdk 可复现构建（见其 README）
    └── verify_deadcode.py   # 死代码零引用证明（AST 级）
```

</details>

## 🤖 交接指南（AI / 新维护者必读）

**当前状态：v1.0.0 正式版已发布**（tag `v1.0.0`，含完整源码 zip 资产）。单元测试 207 条全部通过，验收 47/47，0 开放缺陷。以下是从零接手所需的全部上下文。

### 必须知道的关键实现细节

1. **lx 音源协议**：脚本元数据用 `/*! ... */` 块注释（`@name/@version` 等，**不是** `//` 行注释，否则去重会把多文件误并为一个）；`lx.on(request, handler)` 的 handler 返回 `Promise<url字符串>`；初始化完成必须 `lx.send(inited, {status, sources})`；音质要求走 payload 的 `type` 字段（内核转成 handler 收到的 `info.type`）。
2. **lx.utils 必须按官方 preload.js 实现**：`crypto.md5/aesEncrypt/rsaEncrypt/randomBytes`、`buffer.from/bufToString`、`zlib` 全套。验证器曾因 utils 为空对象误杀六音/野花/野草等依赖官方 utils 的源——`source_bridge.js` 已按官方实现补齐，改动时勿回退。
3. **音源去重**：按 `@name` 去重、在线官方版优先；本地重复副本保留作离线兜底。在线源缓存名 = `md5(原始 URL)`（不是回退后的镜像 URL），缓存在 `bot/tmp/online_sources/`。
4. **镜像回退**：每个在线源派生 5 路候选（ghproxy.net → raw 直连 → mirror.ghproxy → fastly.jsdelivr → cdn.jsdelivr）。断网时若缓存非空则回退缓存服役（BUG-001 修复）。
5. **平台声明路由**：`bridge.py` 的 `supports()` 用 `sources.get(platform)` 判定，不能用 `in` 键检查（BUG-003，`{'actions': [...]}` 形态会恒 False 导致全走全集兜底）。
6. **已知环境限制**：`pdone-lx`（独家音源 v6）依赖 systemd 安装本地服务，容器/Windows 跑不起来，落雪桌面端同样受限——已隔离保留，不要删除；如需支持可做 systemd 沙箱方案。
7. **QA 测试注意**：测试夹具会写 `bot/tmp/online_sources/`，跑完必须清理含「QA验收」标记的缓存，否则污染后续运行；测试中 `supports()` 断言要允许"None 或 http 候选"两种优雅回退形态。
8. **内核终态参考**：真实内核 53 候选脚本 → 37 个唯一音源进程就绪 + 内置引擎；7 个在线官方源实时服役；14 个重复副本去重；2 个待自愈属正常态。

### 待办路线（按优先级）

| 事项 | 说明 |
|---|---|
| S1 弹幕层切换 | ~~已完成~~：弹幕监听改用 `bilibili-api-python` 的 `LiveDanmaku`（`bot/danmaku_biliapi.py`）；手写 WebSocket 协议栈与 WBI 签名已删除（零引用证明：`python3 tools/verify_deadcode.py`），`websockets` 仅测试用 |
| S2 播放层切换 | ~~已完成~~：python-mpv 已在 requirements 中；config.json 设 `"player_backend": "mpv"` 即用常驻 mpv 实例播放（需系统 libmpv：`apt install libmpv2`），默认仍为 ffplay |
| S3 评分器切换 | ~~已完成~~：`rapidfuzz` 已为项目依赖，`search._fuzzy_match` 直接使用它（默认即健壮实现，空转兼容壳 `search_rapidfuzz` 已删除） |
| 覆盖率量化 | 接入 `pytest-cov`，当前仅有用例数无行覆盖率 |
| pdone-lx 适配 | systemd 沙箱方案（见上文第 6 条） |

### 历史文档（git 历史找回）

下列文档已按「文档只留 README」原则收敛，需要时从 git 历史取回：

```bash
git show 62e1d90:S0交付说明.md        # S0 交付说明
git show 62e1d90:S1清理预案.md        # 弹幕层切换与死代码清理预案
git show 62e1d90:lx兼容性对齐报告.md   # 45 音源逐文件溯源与协议对齐
git show 62e1d90:音源健康诊断报告.md   # 音源健康分析
git show 62e1d90:合规审查报告.md       # 合规审查
git show 62e1d90:竞品分析报告.md       # 竞品分析
git show 62e1d90:qa/QA-ACCEPTANCE-REPORT.md   # 正式版验收报告（56/56 签字版）
```

## 🤝 Contributing

1. Fork 本仓库，创建 `feature/xxx` 分支
2. 提交前确保 `python3 -m pytest tests/ -q` 全过
3. 发起 PR 并描述改动动机与验证方式

## 📄 License

- 本项目代码：**GPL-3.0**（见 [LICENSE](LICENSE)）
- `musicSdk_bundle.cjs` 来自 [lx-music-desktop](https://github.com/lyswhut/lx-music-desktop)（Apache-2.0 + 补充条款），可复现构建见 `tools/build_music_sdk/`，许可明细见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## ⚠️ 免责声明

本项目不存储、不传播任何音频文件，仅作个人学习目的的技术验证。所有音乐数据来自第三方公开接口，版权归原作者与音乐平台。请使用后 24 小时内清除产生的版权数据，支持正版；使用本项目产生的任何问题由使用者自行承担。
