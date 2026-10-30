# bili-songbot 轮子清算报告（最终版）

> 基于对 `bot.py`（3042 行）+ `source_bridge.js`（500 行）的逐行扒码，所有结论均带行号，可复核。

---

## 一、最大发现：842 行死代码（不是轮子，是化石）

上次清算我说"约 1400 行轮子"，漏了最大的一块。这次用引用分析跑出来的铁证：

```
死代码区间: 901-1742 = 842 行
死代码区内定义的常量: 0 个
被区外引用的常量: 无
source_* 函数调用点（非定义行）: 3 处 —— 全部在死代码区内部互相调用
```

**33 个 `source_*` 函数 + `ALL_SOURCES` 注册表（1708-1742），全文零调用。** 现在的搜索链路 `search_song`（1977 行）只走 `_bridge.search()`，即 Node.js 桥接 lx 音源 JS 脚本。这 842 行是项目早期"用 Python 翻译音源接口"时代的遗留，改了架构没删代码，一直躺在文件里。

### 这些死代码是什么：lx 音源脚本的手工 Python 翻译

扒开函数名就明白了——`source_github_huibq` / `source_github_flower` / `source_github_ikun` / `source_github_grass` / `source_github_lx` / `source_github_juhe`，这些"音源"全部对应 [pdone/lx-music-source](https://github.com/pdone/lx-music-source) 仓库里的 JS 音源脚本（huibq / flower / ikun / grass / lx / juhe / sixyin）。也就是说：

> **这个项目早期把 lx-music 生态的 JS 音源脚本，一个一个手工翻译成了 Python 函数，共 33 个。后来写了 `source_bridge.js` 直接跑 JS 脚本（正路），842 行翻译代码成了化石。**

这是本次清算最贵的一课：**lx 音源是标准 JS 脚本，直接跑就行，翻译成 Python 是双重维护**——脚本更新一次（lx 音源平均两周一更），33 个 Python 函数全部过期。

---

## 二、3042 行总账

| 区块 | 行号 | 行数 | 判定 |
|---|---|---|---|
| 音源死代码（33 个 source_* + ALL_SOURCES） | 901-1742 | **842** | **删，零引用** |
| 协议层（BiliDanmaku 230 + getDanmuInfo/WBI/buvid3 辅助 ~170 + CookieManager ~96 + 端点常量区 ~320） | 237-850 | **~800** | **换 bilibili-api** |
| 音源桥（SourceBridge 167 + search_song 100 + _score_search_result 36） | 1746-2095 | **~300** | **保留骨架**（已是 lx 标准） |
| 播放层（PlaybackManager 116 + 下载/临时文件管理） | 2462-2577 | **~150** | **换 python-vlc** |
| 业务层（BiliSender 108 + VoteManager 91 + PlayQueue 33 + BiliMusicBot 297 + 指令处理） | 2353-2709, 2711-3007 | **~530** | **保留**，业务特定，PyPI 无通用库 |
| 工具层（日志 30-80 / 配置 180-410 / 入口 3008-3042） | — | **~230** | 保留（日志已用标准库，未造轮子） |

**可消灭合计：842（死代码）+ ~800（协议层）+ ~150（播放层）+ ~80（模糊匹配/热重载）≈ 1870 行，占 61%。**

**剩下 ~1170 行全部有存在理由**：业务 530 + 音源桥 300 + 工具 230 + lx shim（JS 侧 500 行）。

---

## 三、分层替换清单

### 第 1 类：直接删（842 行，零风险）

| 对象 | 行号 | 动作 |
|---|---|---|
| 33 个 `source_*` 函数 | 901-1703 | 删 |
| `ALL_SOURCES` 注册表 | 1706-1742 | 删 |
| 仅供它们使用的端点常量 | 656-850 区间 | 删（硬编码的星海/溯音/聚合 API URL 和明文 key `oiapi-ef6133b7-...` 一起埋葬，还消除泄露风险） |

> 附带收益：明文 API key 进 git 是安全事故，删掉即止血。

### 第 2 类：换官方库（协议层 ~800 行）

| 手写实现 | 行号 | 替换为 | 已验证 |
|---|---|---|---|
| `BiliDanmaku`（230 行 websocket 客户端：getDanmuInfo 三级回退 / WBI 签名 / `_send_auth` / `_heartbeat` / `_handle_packet` / protover=1 封包） | 2099-2328 | `bilibili_api.live.LiveDanmaku` | ✅ 本地已装 17.4.2，`connect`/`on`/`add_event_listener`/brotli+raw json 双协议俱全 |
| `CookieManager` 的 Cookie 刷新 / SESSDATA 过期解析 / nav 校验 | 411-506 | `bilibili_api.Credential`（login_v2 自动刷新） | ✅ 同上 |
| `fetch_buvid3` / `get_real_room_id` / WBI 签名辅助 | 237-410 | bilibili-api 内置 | ✅ 同上 |
| `ApiRateLimiter`（23 行） | 180-202 | bilibili-api 内置重试 + `aiolimiter`（PyPI 1.3.0） | ✅ |

**这一层是"协议苦役"，不是护城河。** B 站接口平均每月小改、每季大改，手写协议层的唯一结局是烂在文件里——bilibili-api（Nemo2011 fork，17.4.2 仍在月更）把这活接了。唯一要保留的是 `BiliSender` 里的**实战对抗资产**：10030 连续计数退避、弹幕权重截断 `_truncate_danmaku`（108 行）——这是踩坑踩出来的，库里没有。

### 第 3 类：换通用库（播放 + 辅助 ~230 行）

| 手写实现 | 行号 | 替换为 | 已验证 |
|---|---|---|---|
| `PlaybackManager`（116 行手动管 ffplay 子进程：Popen/wait/terminate/kill/删临时文件） | 2462-2577 | **python-mpv**（首选，1.0.8）；python-vlc 备选 | ✅ mpv.py API 面已扒源验证：`playlist_append/next/prev/play_index/shuffle` + `seek` + `observe_property` + `ytdl` 全原生透传 |
| `_normalize_text` / `_fuzzy_match` / `_score_search_result`（80 行手写字符重叠率评分） | 1913-1974 | `rapidfuzz`（3.14.6，C++ 实现，token_set_ratio 直接覆盖"歌名-歌手"匹配场景） | ✅ |
| `CookieManager.check_hot_reload`（mtime 轮询热重载） | 411-506 内 | `watchdog`（6.0.0 已装） | ✅ |
| 音质降级链 `QUALITY_LADDER` + 并行波次竞速 | 2871-2954 | **保留**（这是借鉴 MuSeek 的正确设计，非轮子） | — |
| `validate_audio_url`（GET Range 探活） | 704-741 | **保留**（借鉴 MuSeek `looksLikeRealAudio`，对抗性逻辑） | — |

#### S2 播放层选型修订：python-mpv 上位，python-vlc 降备选

原报告写 python-vlc 为首选，经对外部调研逐条查证后修订。mpv 对本场景有三处实质收益：

1. **队列是播放器内核原生支持**（`playlist_append`/`playlist_next`/`playlist_play_index`），不是 Python 层手写队列——无缝切歌（gapless）是 mpv 独有，直播间切歌时观众听不到间隙
2. **m3u8 原生支持**——lx 音源返回的 QQ 音乐/酷狗部分音质链接就是 HLS，mpv 直接吃
3. **yt-dlp 内置**（`ytdl=True`）——以后要放 B 站视频/直播流不用换库

**部署前提（诚实记账）**：python-mpv 只是 ctypes 绑定，系统必须装 libmpv。你的直播机是 Windows，需要额外打包 libmpv.dll（mpv 官方 Windows build 提供）——一次性成本，换的是永久免维护播放内核。**如果这一步嫌麻烦，python-vlc 是可接受的降级**，代价是切歌有间隙、事件回调坑多。

### 第 4 类：保留（业务层 ~530 行 + 音源桥 ~300 行）

诚实的边界，以下**确实没有现成库**，别再花时间找：

- `VoteManager`（91 行）：切歌投票、动态门槛、uid 去重——直播间业务特定。已对"投票库"类目做 PyPI 实证（见第八节证伪记录）：存在的全是论坛/选举式库，直播间实时投票应用库为零
- `PlayQueue`（33 行）：`collections.deque` 封装，已用标准库
- `BiliSender` 对抗逻辑（108 行）：10030 退避、弹幕截断——实战资产
- `SourceBridge`（167 行）：Python↔Node JSON-RPC 桥，`asyncio` Future 映射——**这是架构件不是轮子**
- `search_song` 的 3 秒收集窗口 + 评分择优（100 行）：直播场景特定（观众等不了 10 秒）

---

## 四、融入 lx 生态：统一音源协议（"OpenAI 兼容接口"式）

你的判断是对的：**不要自造音源标准，要做 lx 标准的消费者**。lx-music-desktop 的音源接口就是音乐解析领域的事实标准——MuSeek、CeruMusic、Mio-Music、澜音、songloft、MPDL、Listen1 全部遵循。项目接入方式设计如下：

### 音源目录 = lx 标准 JS 脚本目录

```
bili-songbot/
├── sources/                  # 与 lx-music-desktop 音源目录同构
│   ├── lx/latest.js          # 官方源（pdone/lx-music-source）
│   ├── huibq/latest.js       # 用户自取，丢进来即用
│   ├── flower/latest.js
│   └── ...                   # 任何 lx 兼容脚本，无需改一行代码
├── source_bridge.js          # lx API 运行时（Node 侧）
└── bot.py                    # Python 主控
```

**用户导入自定义音源 = 下载 lx 音源 JS 脚本丢进 `sources/`**。与 MuSeek/CeruMusic 的操作完全一致。这就是"OpenAI 兼容接口"式的兼容度——不是我们兼容它们，是**我们和它们跑同一套脚本**。

### 调度协议与 lx api-source.js 一一对应

| lx 标准接口 | bot.py 侧 | 说明 |
|---|---|---|
| `musicSearch.search(name, singer, source, limit)` | `SourceBridge.search()` | 已有 ✅ |
| `musicInfo.getMusicUrl(source, musicInfo, type)` | `SourceBridge.get_url()` | 已有 ✅ |
| `musicInfo.getMusicPic` / `getLyric` | 待补 | 直播间可只做 URL，歌词按需 |
| `supportQuality` / `filterData` | `QUALITY_LADDER` | 已有 ✅ |

### lx 全局 API 运行时（source_bridge.js 的角色）

`source_bridge.js` 里手写的 lx shim（`lx.request` / `lx.crypto` AES+RSA / `lx.utils` base64+hex）**不是轮子，是必要适配层**——lx-music-desktop 的官方运行时（`api-source.js`）是 Electron 渲染进程专用，无法直接在 Node 跑，npm 上也没有独立的 lx 运行时包（已搜证）。但这个 shim 要做两件事才配得上"融入生态"：

1. **支持脚本热加载**：音源目录新增/更新 JS 文件时自动重新加载（现在要重启进程）
2. **透传 `lx.EVENT_NAMES` 全部事件**：`request` / `inited` / `updateAlert`——`updateAlert` 是音源脚本给用户发更新通知的通道，lx 生态通用约定，必须支持

---

## 五、混合架构（JS + Python）最终分工

```
┌─────────────────────────────────────────────┐
│ Python 主控（bot.py，目标 ~1170 行）          │
│  ├─ bilibili-api: LiveDanmaku + Credential   │
│  ├─ python-vlc: 播放                          │
│  ├─ rapidfuzz: 搜索评分                       │
│  └─ 业务: VoteManager + PlayQueue + BiliSender│
├─────────────────────────────────────────────┤
│ Node 桥（source_bridge.js，~500 行）          │
│  └─ lx API 运行时 + 音源脚本热加载             │
├─────────────────────────────────────────────┤
│ sources/（lx 标准 JS 音源，用户可无限扩展）     │
└─────────────────────────────────────────────┘
```

- **JS 侧只干一件事**：当 lx 音源的运行时宿主。这是 JS 的生态位，Python 硬啃要永久维护 33 个翻译函数（已经用 842 行死代码证明了此路不通）。
- **Python 侧只干三件事**：B 站协议（官方库）、播放（通用库）、业务（自己的）。

---

## 六、切片顺序（按"每一步都能跑"排）

| 切片 | 动作 | 预计减行 | 风险 |
|---|---|---|---|
| **S0** | 删 842 行死代码 + 明文 key | -842 | 零（零引用，跑一遍 `/play` 回归即可） |
| **S1** | `BiliDanmaku` → `LiveDanmaku`，`CookieManager` → `Credential` | -600 | 低（先双跑对比，事件字段对齐后切） |
| **S2** | `PlaybackManager` → python-mpv（备选 python-vlc） | -116 | 低-中（Windows 需打包 libmpv.dll；降级：python-vlc） |
| **S3** | `_score_search_result` → rapidfuzz | -80 | 低 |
| **S4** | 音源目录改造：`sources/` 同构 lx + 热加载 + updateAlert | +100 | 中（bridge 升级，需回归 5 平台搜索） |
| **S5** | 热重载换 watchdog | -30 | 低 |

**S0 做完，文件从 3042 → 2200 行，且删掉的是零调用代码。** S1+S2+S3 做完 ≈ 1400 行，功能不减反增（bilibili-api 自带 WBI 重试、Cookie 自动刷新，比手写版抗改版）。

---

## 七、这次清算改口的三个判断

1. **上次说"约 1400 行轮子"——漏报了 842 行死代码**，实际可消灭 1870 行（61%）。引用分析（grep 调用点）必须做，肉眼读类结构会漏。
2. **上次说"lx 音源可以直接替换他的音源"——不准确**。真相是：他的 33 个 Python 函数本来就是 lx 音源的手工翻译，`source_bridge.js` 已经是正确路线，S0 就是删化石、走正路。
3. **投票/队列/对抗逻辑"无库可用"——维持原判**，但理由修正：不是"还没人造轮子"，是这些是**直播间业务逻辑**，属于你的产品差异点，本来就不该外包给库。

---

## 八、对外部调研的逐条证伪（v2 新增）

收到一份第三方选型调研，对其关键结论逐条查证，**三处不实、一处需精确化、两项是伪问题**。全部证据可复现：

### 证伪 1：投票库清单大半是幻觉

| 调研里的包名 | PyPI 实证 | 实际用途 |
|---|---|---|
| `python-vote` | **不存在** | — |
| `vote-core` | **不存在**（疑为已消亡的 pyvotecore 误记，pyvotecore 也不存在） | — |
| `flask-vote` | **不存在** | — |
| `django-vote` 2.5.0 | ✅ 存在 | "A simple Django app to conduct vote"——Reddit 式顶帖，Django ORM + DB 模型绑定，asyncio 单文件 bot 用不上 |

**结论**：本报告"直播间实时投票无库可用"成立，原因精确化——不是没人造投票库，是造出来的全是 Web 框架绑定的论坛场景。`VoteManager`（动态门槛 + uid 去重 + 超时结算）是直播间业务，**保持自写**。

### 证伪 2：blivedm "无 PyPI 包"说法不精确，且 PyPI 上埋着坑

- PyPI 的 `blivedm` 0.1.1（2023-05 上传，author 挂 xfgryujk）**是抢名错误包**——GitHub README 原文："本包没有发布到PyPI，从PyPI安装会得到错误的版本"
- 真身安装：`uv add git+https://github.com/xfgryujk/blivedm.git --branch master`
- blivedm 本身极活跃（2026-08 还在合 SEND_GIFT_V2/Protobuf），且同时支持 web 端接口**和 B 站直播开放平台**——这是它相对 bilibili-api-python 的独特价值

**结论**：协议层首选仍是 `bilibili-api-python`（需要 Credential/WBI/房间信息/弹幕一站式），但 blivedm 记录为**合规路线备用件**——哪天网页接口被风控打穿，换 blivedm 的开放平台模式，业务层不用重写。注意：必须 git 安装，`pip install blivedm` 会装到错包。

### 证伪 3：starbot-bilibili-core 活跃度存疑

PyPI 发布史：1.0.0（2023-11）→ 1.3.0（**2024-04**）→ 停更至今（2026-10，一年半无版本）。对比 bilibili-api-python 17.4.2 持续月更。**结论：不选。** 点歌 bot 只需要 `DANMU_MSG` + 人气值两个事件，用不上它的事件自动拆包；将来做"送礼点歌/SC 点歌"再评估。

### 精确化 4：python-mpv 上位（采纳，补部署前提）

调研推 python-mpv 为首选——**结论正确，已采纳为 S2 首选**（API 面已扒源验证：`playlist_append/next/prev/play_index/shuffle` + `seek` + `observe_property` + `ytdl` 原生透传，1.0.8）。调研没说的前提：Windows 直播机需自带 libmpv.dll，python-vlc 保留为降级方案。另：调研举例的"B站/YouTube 网络源"对本项目是伪需求——点歌机播的是 lx 音源解析出的直链/m3u8，ytdl 只是赠品。

### 伪问题 5：日志库选型

调研列了 logging/loguru/structlog/picologging——bot.py 第 30-80 行**本就用标准库 logging + RotatingFileHandler**，未造轮子。单文件 bot 换 loguru 省 40 行样板、引入一个依赖，不划算。**没有问题就没有动作**，此项从清单划掉。

### 伪问题 6：pywebview/GUI 是 scope creep

pywebview 6.2.1 + mpv 控制面板技术上成立，但点歌 bot 是直播时的**后台工具**，你隔着 OBS 直播时并不会去点 GUI 按钮。列为 S6 可选项，**现在做的唯一作用是再给一次"直奔完全体"的烂尾机会**。

---

## 附：验证方式

```bash
# 复核死代码结论
cd bili-dl && grep -n "source_" bot.py | grep -v "async def source_"
# 预期：只有 ALL_SOURCES 注册表（1708-1742）和 3 处死区内互调

# 验证替换库
pip show bilibili-api-python python-vlc rapidfuzz watchdog
```

```bash
# 复核证伪结论（v2）
pip index versions python-mpv django-vote starbot-bilibili-core   # 存在
pip index versions python-vote vote-core flask-vote               # 不存在（空输出）
pip show blivedm                                                  # PyPI 0.1.1 是抢名错包，真身走 git+master
curl -s https://pypi.org/pypi/starbot-bilibili-core/json | python3 -c "import json,sys; d=json.load(sys.stdin); print([ (v,r[0]['upload_time'][:10]) for v,r in d['releases'].items() if r ])"   # 最后发布 2024-04
```

---

## 九、音源层引擎选型终审（v3 新增）

用户提供了"加载/执行洛雪 JS 音源"的配套库清单，逐条查证后终审。**结论：音源层维持 Node 桥（source_bridge.js）不变，所有替代路线均不划算。**

### 查证 1：lx-music-api-server 存在，但是"Python 重写"路线的现存标本

| 项 | 实证 |
|---|---|
| 是什么 | [MeoProject/lx-music-api-server](https://github.com/MeoProject/lx-music-api-server)，"适用于 LX Music 的解析接口服务器的 **Python 实现**"，FastAPI，2026-01 仍在提交（ikun0014 接手维护） |
| 实现方式 | 把 kw/kg/tx/wy 四个平台的解析逻辑**用 Python 重写**，不是跑 lx JS 脚本 |
| 覆盖度 | 链接/详情/歌词/刷新登录；**搜索功能"可能会在近期实现"（即现在没有）** |
| 硬依赖 | **Redis 6.0** + 独立 HTTP 服务器进程，不是可嵌入的库 |
| external_scripts | 仅存在于 Runarry 个人 fork，**主线没有** |

**判决：不切。** 它精确复刻了 bot.py 842 行死代码的错误模式——把生态的 JS 标准资产用 Python 重写一遍，结果是四平台、无搜索、外加一个 Redis。而 `source_bridge.js` 直接跑 lx 生态标准 JS 脚本：五平台全功能（kw/kg/tx/wy/mg）+ 用户可导入任何第三方脚本。**它的存在反而反证了 Node 桥路线正确。** 记录为生态情报，不进切片计划。

### 查证 2：quickjs 系跑不了 lx 音源（除非重写 shim）

| 包 | PyPI 实证 | 性质 |
|---|---|---|
| `quickjs` 1.19.4 | ✅ | PetterS/quickjs，"Wrapping the quickjs C library"——**纯 JS 引擎** |
| `python-quickjs-ng` 0.15.1.1 | ✅ | "Thin Python wrapper of quickjs-ng"——**纯 JS 引擎** |
| `quickjs-runtime` | ❌ 不存在 | — |

两个包都只是引擎，**没有 Node 的 http/crypto/zlib/fetch**。而 lx 音源脚本依赖的 `lx.request`/`lx.crypto`/`lx.utils` 全靠 shim 用 Node 原生模块实现（source_bridge.js 第 14-19 行的 `http/https/crypto/zlib` require）。换 quickjs = 把 500 行 JS shim 翻写成 Python（pycryptodome + aiohttp + 手写 AES/RSA）——**这正是 842 行死代码的同款错误（手工翻译生态运行时），收益仅为"少装一个 Node"，负值交易。**

### 查证 3：JSPyBridge 是伪选项

`javascript` 包（JSPyBridge）底层仍是 Node.js 子进程，IPC 换了个写法，**一个依赖都没少**。不解决任何问题。

### 查证 4：mutagen / pylrc 是伪需求

- `mutagen` 1.48.1 ✅ 存在——点歌 bot 不写音频标签，不需要
- `pylrc` 0.1.2 ✅ 存在但陈旧（2018 年后无实质更新）——直播间场景不显示歌词，不需要
- `lrc-parser` ❌ 不存在

**均为伪需求，不动。** 点歌机的核心链路是：弹幕 → 搜索 → URL → 播放。歌词和标签是装饰性功能，属于"直奔完全体"的诱惑。

### 终审决定（不变清单）

| 决定 | 状态 |
|---|---|
| B 站协议层 = `bilibili-api-python`（S1） | ✅ 确认（用户已拍板） |
| 音源运行时 = Node 桥 `source_bridge.js` | ✅ 终审不变 |
| 播放层 = `python-mpv`（S2） | ✅ 确认 |
| 评分 = `rapidfuzz`（S3） | ✅ 确认 |
| 切 lx-music-api-server / quickjs / JSPyBridge | ❌ 全部否决（证据如上） |

---

## 十、S0 已执行：3042 → 2039 行（-1002，零行为变化）

用户要求"改动后必须仍能跑通、不出 bug"。S0 是全程唯一可做**机械等价证明**的切片，已执行并三重验证：

### 执行结果

| 验证层 | 方法 | 结果 |
|---|---|---|
| 静态引用 | 逐名字分析 107 个顶层定义的引用点 | 删除 82 个定义（31 个 source_* + ALL_SOURCES + 51 个孤儿常量/函数），**零悬空引用** |
| AST 对比 | 新旧文件顶层定义逐一按行号切片比对 | 75 个存活定义**源码逐字节一致**，import 一致，模块级语句一致 |
| 编译 | `py_compile` | 通过 |
| 导入冒烟 | 隔离目录 + 假 config.json | 9 个关键符号 + 14 个常量全在位，类可实例化 |
| 端到端 | 假 lx 音源脚本 + Node v22 实测 | 桥启动 → 音源加载 → search → musicUrl → search_song → 空结果场景，5 项全过 |

### 等价性论证

删除的 82 个定义满足：**活代码区零引用**（引用分析证明）+ 新文件存活代码与原件**逐字节相同**（AST 对比证明）⇒ 运行时代码路径与原件完全一致 ⇒ **行为 100% 不变**。这不是"应该没问题"，是可复证的机械结论。

### 交付物

`bili-songbot-S0/`：删减版 bot.py（2039 行）+ 冻结原件 bot_original_frozen.py（回滚基准）+ 未改动的 source_bridge.js + 测试资产（tests/e2e_test.py，可重复运行）+ S0交付说明.md（含用户侧 5 分钟回归清单）。

### 后续切片的验证规格（沿用本次纪律）

- **S1（协议层）**：切之前先产出新旧事件字段对照表（DANMU_MSG → LiveDanmaku 事件的字段映射），切之后用录制的真实弹幕包回放对比
- **S2（播放层）**：本地文件 + URL + m3u8 三类播放回归 + 队列推进事件对比
- **S3（评分）**：用历史搜索词构建对照集，rapidfuzz 新旧评分输出对比，劣化用例人工审查
- 每一刀单独交付、单独回归、保留回滚基准——**绝不 stacked refactor**

---

## 十一、模块化重构已执行：2039 行单文件 → 9 个职责模块 + OO 收编（v4 新增）

用户指令："把单文件分成按照他们的职责对应命名，然后分成多个模块。之后，然后再用内核对象的思想来封装一些，就可以减少很多重复代码。然后就可以开始行动了，不用再问我。"已执行完毕。

### 新结构（入口 bot.py → main.py）

| 模块 | 行数 | 职责 |
|---|---|---|
| `main.py` | 47 | 入口：装配 BiliMusicBot 并运行 |
| `bot/config.py` | 190 | 配置加载、日志系统、配置常量（唯一允许定义配置初值处） |
| `bot/protocol.py` | 373 | B站 HTTP 协议层：限流、WBI 签名、buvid3、Cookie 热重载 |
| `bot/bridge.py` | 191 | lx 音源 Node 桥 + **单例注入**（set_bridge/get_bridge） |
| `bot/models.py` | 64 | SongInfo/QueueItem/PlayQueue |
| `bot/search.py` | 231 | 音源搜索、评分择优、音质降级 |
| `bot/player.py` | 266 | URL 预验证、下载（aiohttp + 桥兜底）、ffplay 管理 |
| `bot/vote.py` | 105 | 切歌投票（sender 依赖注入，避免循环依赖） |
| `bot/danmaku.py` | 375 | 弹幕 WebSocket 监听 + 弹幕发送（10030 退避） |
| `bot/core.py` | 321 | 主控编排：指令路由、队列消费、播放编排 |

### 拆包的关键陷阱（已处理）

可变全局（`BILI_HEADERS`/`REAL_ROOM_ID`/`USER_UID`/API 限流器/Cookie）跨模块后**必须 `protocol.X` 动态访问**——`from protocol import BILI_HEADERS` 拿到的是快照，Cookie 热更新后 danmaku.py 仍在用旧 headers，行为静默改变。全部调用点已改为动态访问，Cookie 热更新单测覆盖此路径。

### OO/DRY 收编（4 处）

1. 桥接器单例 `set_bridge/get_bridge`——替代散落的模块全局赋值，未启动时清晰 RuntimeError 替代 NameError
2. `player._new_tmp_path()`——收编两个 download 函数逐字重复的临时文件创建
3. `player._safe_unlink()`——收编 4 处相同的 try-unlink-except
4. `core._try_play_by_quality()`——收编两处结构相同的音质播放循环（验证过滤/警告文案参数化）

**被否决的"统一 HTTP 客户端层"**：11 个请求调用点的 headers/timeout/限流/WBI/异常处理各不相同，强行包一层会把每个调用点的差异揉进一个函数，增加耦合且改变行为——负值交易，不做。

### 验证矩阵（全部实测通过）

| 验证 | 结果 |
|---|---|
| 静态未解析名 | 干净 |
| AST 定义级终审 | 相对 S0 快照 0 缺失；6 新增（3 架构件 + 3 收编件）；12 个差异定义 token 级逐一核对符合预期（`_PROJECT_ROOT` 两层 dirname 与原单文件一层语义等价） |
| 编译 + 包导入 | 20 符号全导出 |
| 端到端（Node v22） | 7 项：包导出冒烟 → 桥启动/音源加载 → search → musicUrl → search_song → 空结果 → 未启动桥守卫 |
| 单元测试 | 29 例全过：vote 9（门槛表/去重/归一化/通过/未通过/未达门槛/歌曲已结束/重复发起/消息格式）、queue 5（FIFO/peek/remove_current）、matcher 12（标准化/模糊匹配/评分 tiers/歌手字段规范化）、cookie 3（检测+双模块写回/无变化不重建/缺 jct 不半截写入） |
| Cookie 热更新 | mtime 检测 → reload → protocol 侧 → config 写回 → headers 重建 |

测试资产位于 `bili-songbot-S0/tests/`，用户机器上 `python3 tests/e2e_test.py` 即可复跑（无需 B 站账号）。评分器行为已固化为单测，后续 S3 换 rapidfuzz 时直接对比。

### 顺带查实（写测试时发现，未改）

`CookieManager.reload_config()` 成功路径无条件返回 True——与冻结原件逐字节一致，非重构引入；`changed` 只门控 headers 重建。调用方忽略返回值故无实际影响，但副作用是任何 config.json 保存都会触发一次"Cookie 已更新"弹幕。记为已知怪癖，后续切片处理。

### 用户侧变化

唯一的操作变化：**启动命令从 `python bot.py` 改为 `python main.py`**。config.json、source_bridge.js、音源目录、全部行为不变。回滚：`cp bot_original_frozen.py bot.py`。

---

## 十二、轮子台账：消灭重复造轮子总账（v5 新增）

用户提醒勿忘主线。截至当前的全部清算结果：

### 已消灭（-1317 行 + 1 处架构债）

| 轮子 | 原形态 | 处理 | 收益 |
|---|---|---|---|
| lx 音源 Python 翻译版 | 33 个 source_* + ALL_SOURCES + 51 个孤儿常量（含明文 API key） | S0 整删 | -1002 行 |
| 手写 WS 协议栈（连接/认证/心跳/重连） | BiliDanmaku 约 230 行 struct/zlib 封包解包 | S1 适配器就绪（bilibili-api-python），待用户实测切换 | 切换后 -230 行 |
| 手写 WBI 签名 | get_wbi_keys/enc_wbi/_get_mixin_key + mixin 表 | 同上（库内置） | -40 行 |
| 手写 buvid3 获取 | fetch_buvid3/update_buvid3 | 同上（Credential 自动补） | -30 行 |
| 手写真实房间号获取 | get_real_room_id | 同上（LiveRoom.get_room_id） | -15 行 |
| 模块内重复 ×4 | tmp 创建 ×2 / safe-unlink ×4 / 音质播放循环 ×2 / 桥单例散落赋值 | OO 收编 | 净 -约 40 行 + 架构债清零 |

### 合理自研（不动）

| 自研 | 理由 |
|---|---|
| BiliSender（弹幕发送/频率控制/10030 退避） | bilibili-api-python 与 blivedm 均为只读客户端，无一覆盖发送 |
| _truncate_danmaku | B站弹幕长度规则（中文 2 单位）特有逻辑 |
| Node 音源桥 | lx 生态唯一正确路线（quickjs 跑不了 lx 脚本，见第九节终审） |
| PlayQueue/VoteManager | 业务模型，非基础设施 |
| 配置热重载（mtime） | watchdog 依赖换 10 行代码，负值交易 |

### 查无此库（外部调研证伪，避免幻觉替换）

- 投票库：python-vote / vote-core / flask-vote 均不存在；django-vote 是 Django app 不适用——VoteManager 保持自研
- lx 音源 Python 解析包：不存在——Node 桥是唯一路线
- lx-music-api-server：是"Python 重写音源"路线的同款错误，且四平台无搜索、硬依赖 Redis——不切

### 最终形态预测

S1 切换后：bot.py 体系 ~1900 行（-315）；S2（python-mpv）再 -约 150；S3（rapidfuzz）不动行数提准确率。项目总量从 3042 → **~1750 行**，且消除全部"手写协议栈"类高危维护债（B站改协议时社区库跟着修）。

## 十三、台账 v6 增量：S2/S3 就绪 + S1 清单零引用证明修正（2026-10-28）

### S1 删除清单修正（tools/verify_deadcode.py 零引用证明，AST 级）

v5 预估 -315 行有误，零引用证明纠正：

| v5 条目 | v5 预估 | 证明结果 |
|---|---|---|
| 手写 WS 协议栈 BiliDanmaku | -230 | ✅ 确认（danmaku.py 26-253 行，228 行） |
| 手写 WBI 签名 | -40 | ✅ 确认（get_wbi_keys/enc_wbi/_get_mixin_key/MIXIN_KEY_ENC_TAB，54 行，含级联） |
| BILI_DANMAKU_CONF_OLD | — | ✅ 级联可删（1 行） |
| 手写 buvid3 获取 fetch_buvid3/update_buvid3 | -30 | ❌ **改判不可删**：core.py:48/51/52 活性调用（Cookie 启动校验/热更新），库的 Credential 只覆盖弹幕 WS，不覆盖主流程 HTTP |
| 手写真实房间号 get_real_room_id | -15 | ❌ **改判不可删**：main.py:29 活性调用（启动流程拿 REAL_ROOM_ID） |

**实际删除集 = 283 行**（-228 -55 -1 + danmaku.py import 清理），预案见 `bili-songbot-S0/S1清理预案.md`。

### S2 就绪（bot/player_mpv.py，未接主路径）

- 消灭：每首歌 spawn/kill ffplay 子进程 + stderr PIPE 管理（管道写满死锁的经典坑）+ returncode 间接推断成败，约 60 行
- 换成：libmpv 常驻实例（idle 复用，gapless 就绪）+ END_FILE reason 直接判定；下载/桥兜底/on_start/on_finish/intentional 契约 1:1
- 验证：8 例真 libmpv 本地播放测试（ao=null 无声卡走真实解码）全过
- 切换后清理预估：player.py 的 ffplay 进程管理段（play/_wait_process 及 stop 内进程控制，约 -60 行；download_audio/桥下载仍被复用，不可删）

### S3 就绪（bot/search_rapidfuzz.py，未接主路径）

- 消灭：手写"字符重叠率"模糊匹配（~10 行）及其**质量缺陷**：对顺序/重复完全失明——A/B 实测 `球气白告` vs `告白气球` 判 100% 匹配、无更好候选时误选乱序垃圾
- 换成：fuzz.ratio >= 60（编辑距离，顺序敏感），分档结构参数化复用（`_score_search_result` 加 fuzzy_match 注入点，零复制）
- A/B 判定标准预先声明，6 例全过：12 例分档固化同分、3 处差异全为"手写误判→rf 修复"方向、择优 best 一致
- 启用方式：main.py 两行 `search_rapidfuzz.enable()`；手写版保留为回退实现（钩子默认），**不算死代码**

### 最终形态预测（v6 修正）

S1 切换清理 -283；S2 切换清理 -60；S3 不减行数（修复误判 + C++ 性能）。项目总量 3042 → S0 后 2207 → **切换全完成后 ~1865 行**（55%），消除手写协议栈/子进程管理/失明模糊匹配三类维护债，且每一刀都有零引用证明或 A/B 证据背书。
