# Undefined-TSK 六大仓库横向分析报告

> 分析时间：2026-10-25 ｜ 分析方式：全量克隆/API 文件树 + 核心源码与文档阅读
> 覆盖仓库：unitflow、aifilmteam、bili-songbot、wechat-miniprogram、nbs-visualization、MusicNow.github.io（第 6 个无法访问，见 §2.6）
> 所有关键结论均标注出处；无法验证的内容标注「推测」。

---

## 〇、一句话总结（总）

这是一个人的「AI 全栈练习册」：**unitflow** 是工程最完整的「万物皆插件」Agent 平台（PyPedia），**aifilmteam** 是用 Blender 白模 + 大模型做影视的 7-Agent 工作流（方法论重于代码），**bili-songbot** 是打磨到极致细节的 B 站点歌机器人（单文件实战派），**wechat-miniprogram** 是安全设计典范的闯关式学习小程序 + 自建判题服务，**nbs-visualization** 是正在 OOP 重构中途的 MC 风格音乐可视化渲染器，**MusicNow.github.io 不存在（404，已验证）**。

---

## 一、总览表

| 仓库 | 项目定位 | 主要技术栈 | 成熟度判断 | 一句话总结 |
|---|---|---|---|---|
| [unitflow](https://github.com/Undefined-TSK/unitflow) | 万物皆插件轻量 Agent 平台；实际产品为「PyPedia」Python 百科全书桌面助手（Agent+RAG+记忆+自进化） | Python 3.11+ / FastAPI / LlamaIndex / LanceDB / 百炼 qwen-plus / Mem0 / MCP / pydantic-ai / 原生 JS 前端 | **较完整** | 741 项测试 + CI 门禁 + 架构文档俱全的「图即引擎」插件平台，是 6 个仓库中工程质量天花板 |
| [aifilmteam](https://github.com/Undefined-TSK/aifilmteam) | AI 影视团队工作流：Blender 极简白模占位 + 大模型后期写实化渲染，目标「像真人剧组实拍」 | Python / FastAPI(包装 CLI) / Blender 5.2.2(bpy) / ComfyUI / Wan2.2 / fal / 即梦 / Ollama / 自研 MCP Blender 服务 / 单卡 12GB GPU | **中期** | 「白模锁结构、大模型只执行不决定」的方法论 + 60 多个工具脚本 + 数值化 QC 硬门，强在纪律与文档，尚未产出成片 |
| [bili-songbot](https://github.com/Undefined-TSK/bili-songbot) | B 站直播间点歌机器人：弹幕 /play 点歌、/switch 切歌投票 | Python 3.10+ asyncio(aiohttp/websockets) + Node.js 音源桥接器 + ffplay；无数据库 | **中期** | 3041 行单文件扛下 WBI 签名、风控、5 平台竞速搜索、音质降级链的实战利器，README 即踩坑百科全书 |
| [wechat-miniprogram](https://github.com/Undefined-TSK/wechat-miniprogram) | 「XX闯关」多学科游戏化学习微信小程序（C语言/四六级/高数/线代） | 微信原生 + TypeScript；混合后端：微信云开发 + 自建判题服务（Node 零依赖 / Docker 沙箱 / Redis / HMAC 鉴权 / seccomp） | **中期** | 判题链路安全设计典范（前端不持 token、openid 可信身份、跨端契约逐字节一致），但产品功能仅落地 4 个页面 |
| [nbs-visualization](https://github.com/Undefined-TSK/nbs-visualization) | NBS（Minecraft Note Block Studio）音乐可视化视频生成器，MC 风格钢琴卷帘舞台 | Python 3.10+ / pynbs / Pillow / OpenCV / numpy / Flask / Kimi(Moonshot) API / imageio-ffmpeg | **中期偏初期** | 渲染引擎已模块化为 30 个模块并接入 Kimi AI 做镜头脚本，但处于 OOP 重构中途，README 与代码结构已脱节 |
| MusicNow.github.io | **无法分析** | — | — | **仓库不存在（HTTP 404，API 与网页双重验证）；该用户名下仅 5 个公开仓库，无此名称** |

---

## 二、逐仓库分析

### 2.1 unitflow（内部产品名：PyPedia）

**1. 项目定位**
- 仓库描述为「万物皆插件轻量 Agent 平台 — Unit 插件化 + 图即引擎 + 团队/AI 编排」（来源：GitHub 仓库 About）。
- 实际产品是 **PyPedia——个人本地优先的 Python 百科全书桌面助手**：把用户的资料库（官方文档、GitHub 仓库、离线文档、论坛）与对话结合，用 RAG+LLM 回答问题，四大特性为「记住你（Mem0 长期记忆）/ 理解水平（L1–L5 等级适配）/ 持续进化（Reflexion+Voyager，不微调权重）/ 有凭有据（级联检索+置信度）」（来源：PROJECT_MANUAL.html §1）。
- 目标用户：个人开发者/学生（桌面单用户场景）；二次开发者（平台扩展）。

**2. 技术栈**（来源：pypedia/requirements.txt、PROJECT_MANUAL.html §3.1、.env.example）
- Web/CLI：FastAPI 0.128 + uvicorn；typer CLI。
- LLM：OpenAI SDK → 阿里云百炼兼容端点（qwen-plus，缺密钥自动降级 mock 离线通道）。
- RAG：LlamaIndex 0.14.25（SentenceSplitter/QueryFusionRetriever）+ LanceDB 0.40（向量库，零服务进程）+ BM25+jieba（中文分词）+ qwen3-rerank / 本地 bge-reranker 精排。
- 记忆：Mem0 2.2.1（qdrant-client 本地模式）。
- Agent 协议层：MCP 2.3.0（filesystem/fetch/sequential-thinking 三个官方 server）；agent 扩展用 pydantic-ai-slim + detect-secrets + apscheduler + sqlalchemy。
- 可靠性：tenacity 重试、python-json-logger 结构化日志、filelock。
- 前端：原生 JS（非 ES module，13 个模块共约 2340 行），marked/DOMPurify/mermaid/ECharts/KaTeX/hljs 全部 vendor 本地化（static/vendor/）。
- 无传统数据库；数据落本地数据目录（向量库 + JSON + SQLite 仅 APScheduler jobstore）。

**3. 目录结构**（来源：PROJECT_MANUAL.html §3.2 + 实际文件树）
```
app.py                  FastAPI 入口：28+ API + 静态托管 + 中间件 + 配额门
pypedia/
  engine.py             编排主线（9 步线性管道）
  rag.py / retrieval/   LlamaIndex 混合检索 + 三级级联（main/supplement_a/b）
  conversation.py       长上下文（token 预算 + 滑窗 + 滚动摘要）
  levels.py             L1–L5 等级适配
  review.py             双模型答案二审
  mcp_client.py         MCP 协议层
  memory_store.py       Mem0 长期记忆；memory/ 会话+画像
  evolution/            Reflexion 反思 + Voyager 技能库
  ingest/ / knowledge/  知识摄入（parsers/sources/crawl/preset）
  models/               registry.py 百炼能力注册表（11 项）+ dashscope.py
  units/                万物皆插件内核：manifest/registry/loader/graph/builtins/market/editor/profile
  agent/                Hermes 特性：memory/skills/delegate/cron/hooks/redact/compress/transport
  llm/mock.py           离线 mock 通道
  sandbox_proxy.py / seams/  Python 执行沙箱工具层
static/                 前端外壳 + js/（util→md→chat→library→profile→evolve→plugins→editor→settings→app）
units/examples/         hello-world、python-encyclopedia 两个示例 Unit
units/schema/           unit.schema.json（plugin/agent/team 统一契约）
tests/                  40 个文件、612 个 test 函数（提交信息称 741 项全绿，差异推测来自参数化用例计数）
scripts/                benchmark + 5 个 e2e（editor/market/real_consistency/fullchain）
.github/workflows/ci.yml  push/PR 跑 pytest -m "not network"
```

**4. 架构与数据流**（来源：PROJECT_MANUAL.html §4、pypedia/units/graph.py docstring、app.py）
- 一次提问的完整链路：`用户输入 → MAX_QUERY_CHARS=4000 截断 → 级联检索（向量+BM25 RRF 融合 → 精排三级链）→ 上下文规划（滑窗/滚动摘要）→ MCP 分步推理（仅复杂问题）→ 等级适配注入 → qwen-plus 生成 → 置信度评估（主源55+跨源30+新鲜度15）→ 双模型二审 → Mem0 记忆沉淀 → 自进化 → 前端 marked/DOMPurify/ECharts/mermaid 渲染`。
- **内核设计「图即引擎」**：`GraphRuntime`（units/graph.py）直接解释执行节点图，无独立 AgentLoop；节点类型 input/output/plugin/model-call/judge/process；judge 出边带 condition（受限 `__builtins__` 求值）实现分支与循环；模型调用是图中唯一的 LLM 节点。
- **「万物皆插件」**：23 个内置能力的唯一真值源是 Unit manifest 字典（units/builtins.py），`PluginRegistry` 是 `UnitRegistry` 的兼容子类；市场安装与编辑器导出走同一契约（units/schema/unit.schema.json，type 支持 plugin/agent/team）。
- 降级语义：任何一步失败都有兜底（检索退空、MCP 降级、LLM 不可达降级 mock、置信度失败给 55 分），主流程不中断。

**5. 核心业务流程与关键接口**（来源：app.py 路由清单）
- `POST /api/ask`、`POST /api/ask/stream`：问答主链路（带 MAX_CONCURRENT_ASK=2、ASK_RATE_PER_MIN=10 配额）。
- `GET/POST/DELETE /api/sources/*`：知识源管理（url/github/upload/preset 四种摄入）。
- `GET /api/units`、`/api/units/{id}`、`GET/POST/DELETE /api/market/*`：Unit 内核与市场（上传导入、版本门禁、可回滚卸载）。
- `POST /api/editor/validate|export|install`：可视化图编辑器导出/安装。
- `GET/POST /api/agent/cron`、`/api/agent/skills`、`/api/sandbox/*`：Agent 定时任务、技能库、代码沙箱。
- `GET /api/plugins`、`/api/evolution`、`/api/capabilities`、`/api/mcp`、`/api/cost`：插件启停、自进化、能力开关、MCP 状态、成本统计。

**6. 快速上手**（来源：PROJECT_MANUAL.html §2、.env.example）
```bash
pip3 install -r pypedia/requirements.txt   # 版本已锁定
cp .env.example .env                       # 填 BAILIAN_API_KEY（缺省自动降级 mock）
python3.11 app.py                          # 自动开浏览器，http://127.0.0.1:8000/
python3 -m pytest                          # 全量约 19s，零真实 LLM 调用（MockEmbedding+打桩）
```
- 环境：Python 3.11+（必需）；Node.js 可选（MCP filesystem/sequential-thinking 走 npx，缺失自动降级）；Ollama 可选；Linux/macOS/Windows。

**7. 二次开发导航**
- 加新插件/Agent/团队：抄 `units/examples/hello-world/plugin.json` + plugin.py，契约见 `units/schema/unit.schema.json`，装载逻辑在 `pypedia/units/loader.py`、注册表 `registry.py`。
- 改问答质量：`pypedia/engine.py`（编排）、`levels.py`（等级指令）、`review.py`（二审提示词）、`retrieval/cascade.py`（级联策略）。
- 加模型能力：`pypedia/models/registry.py`（11 项百炼能力登记，开通后零改代码生效）+ `.env` 里取消注释。
- 改前端：`static/js/` 按依赖序加载（util→md→chat→…→app）。
- 加 API：`app.py`（单文件集中注册）。

**8. 技术亮点、潜在债务与风险点**
- 亮点：①「万物皆插件/万物皆对象/图就是引擎」的设计哲学成体系且落到 schema 与运行时；②三层验收（741 单测打桩 → e2e → 真实 LLM 一致性双轨），历史上逼出并修复了 ref 漂移、linkdot 连线失效、画布塌缩等真 bug；③降级不崩 + fail-closed 安全语义；④恒等重构（规则引擎关闭即旁路，单 Agent 图封装与旧架构产出逐项比对）。
- 债务/风险：①`app.py` 单文件集中 28+ 路由，体积偏大（推测继续膨胀后会需要拆分）；②前端为非模块化经典 script 全局共享，协作性差；③依赖极重（torch 2.13 + sentence-transformers，安装体积数 GB 级，桌面分发是负担）；④仓库名 unitflow 与内部品牌 PyPedia 不一致，对外认知成本（推测为改名未彻底）；⑤无 LICENSE 文件（仓库树中未见，推测）。

**9. 建议阅读顺序**
PROJECT_MANUAL.html（§3 架构→§4 数据流）→ `app.py`（路由面）→ `pypedia/units/graph.py` + `units/schema/unit.schema.json`（内核）→ `pypedia/engine.py`（编排）→ `pypedia/rag.py`/`retrieval/cascade.py`（检索）→ `tests/`（验收口径）→ `.env.example`（配置全貌）。

---

### 2.2 aifilmteam（AI 影视团队 v2）

**1. 项目定位**
- 一句话定位：「用 AI 做出**像好莱坞真人专业剧组实拍**的影片——不是 PPT 感、不是静图推拉、不是 AI 味」（来源：AI影视团队v2/PROJECT_STATE.md §1）。
- 既定路线（老板 2026-09-27 确立）：**Blender 极简白模占位 + 大模型后期渲染成片**——白模阶段把「能定死的全定死」（几何/灯光/相机/动作/天气/时间轴），写实化阶段大模型「只执行，不做决定」（来源：PROJECT_STATE.md §2）。
- 当前唯一在制项目：《雨夜寻猫》（30–45 秒能力样片为目标）；阶段状态 R4 技术验证镜进行中（来源：PROJECT_STATE.md §5-8、config/settings.yaml）。
- 用户：个人创作者/小团队（Windows 单机 + 12GB 显卡 + 云端 API 混合）。

**2. 技术栈**（来源：AI影视团队v2/requirements.txt、config/settings.yaml、AGENTS.md、tools/llm_gateway.py docstring）
- 语言/服务：Python；FastAPI+uvicorn（tools/api_server.py，端口 8190，本质是把 CLI 工具包装成 HTTP API）。
- 3D/渲染：Blender 5.2.2 便携版（bpy 脚本驱动，非 pip 包）；EEVEE 白模渲染。
- 生成模型：本地 Wan2.2 TI2V 5B（832×480/49 帧，峰值约 10.6GB 显存）、Wan2.1-VACE-1.3B（本地白模控制，权重 16.4GB）；ComfyUI（SDXL/AnimateDiff，D 盘便携版，端口 8188）；云端 fal（LTX-2.3 render-to-real）、即梦 Seedance 2.5、Higgsfield for Blender。
- LLM 网关：`tools/llm_gateway.py`（纯标准库）——本地优先（Ollama qwen3:8b）+ 云端备选链，按 tier 路由（low_structured/mid_reasoning/high_deep/vision/embedding），额度错误自动滑链，切换留痕 JSONL，密钥只从 runtime/secrets/dashscope.env 进程内读取。
- 音频：ACE-Step BGM、CosyVoice 3 / Qwen3-TTS（edge-tts 因合规已移除）、faster-whisper ASR 质检。
- 质检：Qwen3-VL-4B（视觉质检）、OpenCV Farneback 光流、ffmpeg signalstats/freezedetect、ArcFace 角色一致性。
- 协议：自建 MCP——`mcp_blender/baimo_mcp_addon`（Blender 内 20Hz 主线程 bpy，端口 17800）+ `baimo_mcp_server.py`。
- 中间件/基础设施：无数据库；GPU 显存锁（tools/vram_lock.py，TTL 1800s+心跳 60s）；文件系统即数据层（spec JSON + handoff JSON + jsonl 账本）。
- Web：web/react-workspace（极简 React 壳，package.json + 单 App.jsx，早期形态）。

**3. 目录结构**（来源：git 树 API 全量 700 条目 + PROJECT_STATE.md §5 目录地图）
```
aifilmteam/
├── 项目完整掌握报告.md        # 根目录报告（git 树中存在，但 GitHub web/raw 均 404，无法读取——已标注）
├── config/  docs/  体检产出/   # 外层残留（旧「AI影视团队工作平台」的资料与探针）
└── AI影视团队v2/              # 当前主项目
    ├── PROJECT_STATE.md       # 唯一入口（维护契约：每轮对话结束必更新三处）
    ├── AGENTS.md              # 运行纪律单一事实源（D23 分时纪律 + GPU 引擎接入索引）
    ├── spec/                  # delivery_spec.json(L1/L2/L3 三档) / qc_rubric.json(五项硬指标) / shot_spec.schema.json
    ├── agents/                # 7 岗 SKILL.md：director/producer/screenwriter/previs/blockout/realizer/script_supervisor
    │   └── _legacy_roles/     # 旧 15 岗档案（只留档）
    ├── tools/                 # 60+ 脚本：api_server/llm_gateway/crew_bus/handoff_manager/jobs/
    │                          #   vram_lock/qc_media/comfyui_*/wan22 相关/acestep/tts/vlm_check/k13_version_tree…
    ├── mcp_blender/           # Blender MCP addon + server + selftest
    ├── pipeline/              # timeline_build.py / tech_cut.py / audio_mix.py + tech_delivery_spec.json
    ├── projects/雨夜寻猫/      # 00_brief(剧本/分镜/镜头清单) → 01_spec → 02_blockout → 03_render → 04_qc
    │                          #   → 05_audio(37 文件) → 06_edit(粗剪 v1-v4) → 07_deliver；locks/ ditmap/
    ├── blender/  assets/  _legacy/(带 sha256 台账 830 文件)  tests/  web/  logs/
    └── docs/                  # R0-R4 轮次文档 + 调研板块①/② + 需求定稿 + R3-架构定稿 + _legacy_handoff/_legacy_source
```

**4. 架构与数据流**（来源：PROJECT_STATE.md、agents/*/SKILL.md、tools/api_server.py、spec/qc_rubric.json）
- **7-Agent 协作流水线**：`screenwriter（剧本）→ director（意图）→ previs（镜头规格 shot_spec）→ blockout（Blender 白模粗片）→ realizer（写实化：按镜选引擎+挂参考+提交/轮询/下载）→ script_supervisor（质检 QC）→ producer（预算/资产/交接管理）`；岗位间用 **handoff JSON**（projects/<片>/00_brief/handoffs/，带 ID 与版本）交接，`tools/handoff_manager.py` 管理。
- **关键调用链（写实化）**：`shot_spec.schema.json（镜头规格合同）→ blockout 出白模视频 → realizer 选引擎（R1 fal LTX-2.3 / R2 即梦 / R3 Higgsfield / 兜底本地 Wan2.2）→ 五段式 prompt + 参考资产（appearance_anchor 首帧锚定 + 三视图 + 光照片段）→ 多 seed 抽卡（参考实现每镜 4 seed）→ jobs.py 作业账本（幂等键，断网可恢复）→ 产物 03_render/<引擎>/<镜号>_s<seed>.mp4 → qc_rubric 五项硬指标（运动≥2px/帧、ArcFace 相似度≥0.75、音画同步等）→ 不过即打回`。
- **GPU 分时纪律**（AGENTS.md D23）：所有本地 GPU 引擎 submit 前必须经 `tools/vram_lock.py` 获取显存独占锁（12GB 单卡唯一共享资源），大模型生成与渲染/质检分时段；云端引擎（fal/即梦）豁免。
- **LLM 路由**：`llm_gateway.Gateway(tier).chat()/embed()`，本地 Ollama 优先，额度错/网络错自动滑云端链，单模型重试 0 次，全部失败抛 GatewayExhausted。
- **API 层**：api_server.py（8190）把 CLI 工具包装为 `/api/generate`、`/api/graph`、`/api/lock`、`/api/budget/*`、`/api/versions`、`/api/handoffs` 等 20+ 端点，subprocess 调用 tools/ 下脚本并解析 JSON stdout。
- **预算熔断**：settings.yaml `budget.r5_total_cny=100`，80% 预警、100% 熔断（需签字解锁）。

**5. 核心业务流程与关键接口**
- 业务流程见上（7-Agent + QC 硬门 + 成本回写 cost_ledger.jsonl）。
- 关键接口：MCP Blender server（baimo_mcp_server.py，15KB；addon server.py 26KB，20Hz bpy 主线程）；api_server HTTP 20+ 端点；llm_gateway 五档路由；vram_lock CLI。

**6. 快速上手**（来源：requirements.txt、scripts/bootstrap.ps1、PROJECT_STATE.md）
```bash
pip install -r AI影视团队v2/requirements.txt        # bpy 不在列——由 Blender 便携版自带
python -c "from config.config_loader import validate; validate()"   # 配置校验
python tools/api_server.py                          # 8190，前后端联调桥
```
- **重要限制（已验证）**：该项目与一台特定 Windows 机器深度耦合——根目录 `D:\AI影视团队v2\`、ComfyUI 在 D 盘便携版、12GB RTX 5070 Laptop、Blender 5.2.2 便携版；PROJECT_STATE.md 记载旧项目有「195 处字面量根路径」问题并已整改。**移植到其他机器需要大量路径与硬件适配（推测：这是个人工作流仓库而非可分发产品）**。

**7. 二次开发导航**
- 改某个 Agent 的行为：`agents/<role>/SKILL.md`（含 YAML frontmatter 的岗位卡，唯一事实来源）。
- 改质检标准：`spec/qc_rubric.json`（改必须递增 version，历史评分记录所用版本否则不可回归）。
- 加引擎/工具：`tools/` 新增脚本 + 在 `tools/api_server.py` 的 TOOLS 字典注册 + AGENTS.md 的 GPU 引擎索引登记（持锁 owner）。
- 改 LLM 路由：runtime/llm_router.json（llm_gateway 只读不改写）。
- 改交付规格：`spec/delivery_spec.json`（L1/L2/L3 三档 + AI 双标识 GB 45438-2025）。

**8. 技术亮点、潜在债务与风险点**
- 亮点：①「白模锁结构、大模型只执行不决定」是把不确定生成变为可控工程的核心方法论；②QC 五项硬指标数值化（硬门 blocking / 软门 WARN+归因 / 创意层老板判），把「像不像实拍」从审美争论变成可测数值；③单一事实来源纪律（每主题一份定稿，PROJECT_STATE.md 维护契约）；④幂等键+作业账本+成本回写，断网可恢复不重复计费；⑤资产台账（830 文件 sha256）+ 旧资产零删除可逆归档。
- 债务/风险：①与个人 Windows 硬件/路径深度耦合，可移植性差；②处于 R4 验证阶段，**尚未产出合格成片**（公开信息内）；③云端 API 成本与合规（fal 商用授权、LTX 社区许可 $10M 阈值等已在调研中闭合，但仍是持续风险）；④仓库体积 37.5MB 且含大量 legacy 文档/资产，认知负荷高；⑤12GB 单卡是硬瓶颈（Wan2.2 5B 峰值 10.6GB，几乎占满）；⑥根目录「项目完整掌握报告.md」在 GitHub web 层不可访问（树中存在但 raw/blob 404，GitHub 侧不一致，已实测）。

**9. 建议阅读顺序**
`AI影视团队v2/PROJECT_STATE.md`（唯一入口，必读）→ `AGENTS.md`（纪律）→ `docs/R3-架构定稿.md`（架构定稿）→ `spec/` 三份 JSON（可执行合同）→ `agents/realizer/SKILL.md`（最复杂的岗位卡，含实测坑）→ `tools/llm_gateway.py` + `tools/api_server.py`（工程接线）→ `docs/调研-板块①a/b`（技术选型依据）。

---

### 2.3 bili-songbot

**1. 项目定位**
- B 站直播间点歌机器人：WebSocket 监听直播间弹幕，响应 `/play 歌名` 点歌与 `/switch` 切歌投票，自动搜索音乐并播放（来源：README.md）。
- 目标用户：B 站主播（Windows 直播场景为主）；面向直播间观众提供互动点歌。

**2. 技术栈**（来源：README.md、requirements.txt、config.example.json）
- Python 3.10+，asyncio；依赖仅 **aiohttp + websockets**（requirements.txt 全文两行）。
- Node.js 16+：`source_bridge.js` 音源桥接器，在子进程中执行「落雪音乐（lx-music-desktop）格式」的 JS 音源脚本。
- 播放：FFmpeg 的 `ffplay`（先下载到本地临时文件再播放，不直接播流）。
- 无数据库、无 Web 框架；配置为 `config.json`（支持热更新）。

**3. 目录结构**（来源：jsdelivr 文件树 1186 条 + README）
```
bot.py             # 主脚本，3041 行单文件（~128KB）
source_bridge.js   # Node.js 音源桥接器，592 行
config.example.json / config.json / run.bat / requirements.txt / README.md / 工作记录.md
音源/文件形式/      # 22 个 lx-music 格式音源 JS（洛雪/星海/非常刀/聚合API 等）
音源/链接形式/      # 导入说明 txt
lx-music-desktop-master/   # vendored 的 lx-music-desktop 源码（参考资料，占仓库体积大头）
```
- `bot.py` 内部模块（按源码结构）：日志配置/配置加载（热更新）、`ApiRateLimiter`（全局 API 1s 限速）、WBI 签名（`enc_wbi`/`get_wbi_keys`）、`CookieManager`（校验/过期解码/失效检测/热更新）、`SourceBridge`（Node 子进程 stdin/stdout JSON 协议）、~40 个 `source_*` 音源函数、`search_song`（5 平台并行+3s 窗口+模糊匹配+评分）、`get_music_url`、`BiliDanmaku`（WS 客户端）、`BiliSender`（弹幕发送+频率控制+10030 重试+截断）、`PlaybackManager`（先下载再播放+双下载回退）、`VoteManager`（动态门槛+竞态检测）、`PlayQueue`（FIFO）、`BiliMusicBot`（主控）。

**4. 架构与数据流**（来源：README.md 播放流程图 + bot.py 结构）
- 点歌链：`弹幕 /play → "正在搜索" → Node 桥接器并发搜索 5 平台（8s 超时，3s 窗口择优，完美匹配立即返回）→ 模糊匹配过滤+评分 → 入队 → 并行获取所有音质 URL（flac24bit→flac→320k→192k→128k）→ GET Range 下载前 2KB 预验证（排除 JSON/HTML/0 字节）→ aiohttp 下载（按 URL 域名选 headers）→ 失败回退 Node 桥接器原生 http/https 下载 → ffplay 播放 → 弹幕通知`。
- 切歌链：`/switch → 30s 投票（每 8s 广播，按在线人数动态门槛）→ 通过则检查目标歌曲仍在播放（song_id 比对防误切）→ 停 ffplay`。
- 弹幕连接：始终 `wss://host:443/sub`（端口 2245/2246 不可靠，遍历 host_list 优先 443）；getDanmuInfo 三级回退（WBI 签名 → getConf → 硬编码地址）。

**5. 核心业务流程与关键接口**
- B 站 API：room_init（短号→真实房间号）、getDanmuInfo（WBI+buvid3）、nav（Cookie 校验）、弹幕发送（-352/-400/-101/-111/10030 错误码分类处理）。
- 音源协议：桥接器注入 `lx` 全局对象（request/on/send/utils.crypto/buffer/zlib），搜索用 `Promise.any`+`Promise.race` 8s 超时。

**6. 快速上手**（来源：README.md）
```bash
python -m venv venv && venv\Scripts\pip install -r requirements.txt
cp config.example.json config.json   # 填 bili_room_id + SESSDATA/bili_jct/buvid3/buvid4
python bot.py                        # 或双击 run.bat
```
- 环境：Python 3.10+、Node.js 16+、FFmpeg（ffplay 入 PATH 或 config 指定 `ffplay_path`）；**Cookie 必须手动从浏览器复制（buvid3/buvid4 是风控必需）**。

**7. 二次开发导航**
- 一切功能都在 `bot.py`：加音源=加一个 `source_*` 异步函数并注册到搜索表；改搜索策略=`search_song`（评分/模糊匹配/窗口）；改播放行为=`PlaybackManager`；改投票=`VoteManager.get_threshold`；改弹幕指令=`BiliMusicBot._on_danmaku`。
- 音源脚本本身在 `音源/文件形式/*.js`（落雪格式，可独立更新）。
- 桥接器协议改动用 `source_bridge.js`（stdin/stdout JSON）。

**8. 技术亮点、潜在债务与风险点**
- 亮点：①对 B 站风控体系的理解非常深（WBI 签名、buvid 指纹、真实 UID、443 强制、10030 退避、API 全局限速）；②多层下载容错（音质降级链 + Range 预验证 + 代理 URL 识别 + 双通道回退），把「代理 URL 返回 0 字节」这类灰产 API 的坑挨个填了；③README 即排障手册（10+ FAQ 全部有实测根因）；④日志系统完善（轮转+启动清理+分级降噪）。
- 债务/风险：①3041 行单文件，无测试、无类型标注、无模块拆分，维护成本随规模上升；②依赖第三方音源脚本——随时可能失效，且部分音源游走版权灰区（**合规风险**，推测需主播自行评估）；③Cookie 凭证明文存 config.json（README 已警告勿上传，但仓库内 config.example.json 用占位符是正确做法）；④vendored lx-music-desktop-master 使仓库 18.9MB，且 GitHub 语言统计显示 JavaScript 为主（实际机器人是 Python—— linguist 被 vendored 代码带偏）；⑤Windows 优先，Linux/macOS 需自配 ffplay/node。

**9. 建议阅读顺序**
README.md（全文，含所有踩坑）→ `bot.py` 的 `BiliMusicBot.run`（主控骨架）→ `BiliDanmaku`/`BiliSender`（弹幕收发）→ `search_song` + `source_*`（搜索与音源）→ `PlaybackManager`（播放容错）→ `source_bridge.js`（桥接协议）。

---

### 2.4 wechat-miniprogram（XX闯关）

**1. 项目定位**
- 「XX闯关」多学科游戏化学习微信小程序：面向大学生的闯关式学习平台，首批科目 C语言/英语四级/英语六级/高等数学/线性代数（来源：docs/XX闯关-整体方案-v1.1.md）。
- 产品形态：章节地图闯关（普通/Boss/挑战/自适应重练四种关卡）+ 学习闭环（今日任务/错题本/模拟考/学习报告）+ 对战社交（1V1 PK/组队副本/学习搭子/排行榜）+ 游戏化（段位/连胜/Combo/电子宠物/抽卡——后两者在方案中排 Phase 4）。
- **实际落地度**：代码中只有 4 个页面（index/level/wrong/profile，来源：miniprogram/app.json），即「答题引擎 + 学习闭环」核心路径；PK/组队/宠物等仍在方案文档中（**已验证的差距**）。

**2. 技术栈**（来源：package.json、project.config.json、judge-service/README.md）
- 小程序端：微信原生 + TypeScript（libVersion 3.5.0，useCompilerPlugins: typescript）；零运行时依赖（devDependencies 为空，题目 mock 数据内置）。
- 判题服务 judge-service：**Node.js + TypeScript，零第三方依赖**（自写 RESP2 Redis 客户端、HMAC、HTTP server）；Docker 沙箱（自建 gcc/python 镜像 + seccomp profile + tmpfs noexec）；Redis（限流计数器 + 结果缓存，fail-open 降级）。
- 云函数：cloudfunctions/judgeSubmit（纯 JS CommonJS，零依赖）——注入内部 token + HMAC 签名转发。
- 中间件：Redis（可选，缺失自动降级内存实现）；Docker（可选，JUDGE_SANDBOX=local 本地模式零隔离）。

**3. 目录结构**（来源：文件树）
```
miniprogram/
  pages/            index（首页/闯关）、level（关卡答题）、wrong（错题本）、profile（我的）
  components/question/renderers/   5 种题型渲染器：single-choice/multi-choice/fill-blank/program-output/code-editor
  engine/           题型引擎：types.ts（数据契约）+ registry.ts + machine.ts + judges/（exact/set/normalize/numeric/output/sandbox/ai 7 种判分器）+ session.ts + sanitize.ts
  services/         judge-contract.ts（跨端契约副本）/ judge-client.ts / judge.ts（L1 本地 / L2 远程调度）/ question.ts / progress.ts
  mock/             题库（boss/code/kp1-kp3…）+ bank-validator.ts
judge-service/
  src/server.js     HTTP 入口（路由/鉴权/nonce/并发闸门/优雅退出）；server.ts 为同内容 TS 副本（自检 import 用）
  src/auth.ts + auth/{hmac,nonce}.ts    HMAC-SHA256 签名（常量时间比较+双向时间窗）+ nonce 重放拦截
  src/redis/        client.ts（零依赖 RESP2）/ rate-counter.ts（原子 Lua）/ result-cache.ts（幂等跨实例）
  src/sandbox/      policy.ts（安全策略→docker 参数纯函数）/ runner.ts / docker-executor.ts / local-executor.ts / ratelimit.ts
  src/judges/       classify.ts（退出码/信号/内存/耗时→AC/WA/CE/RE/TLE/MLE/OLE/SE 八态）
  src/questions/    source.ts（内存/HTTP/云开发三通道题库）/ providers.ts / testcases.ts（防泄题 preview）
  images/           Dockerfile.gcc / Dockerfile.python（删网络模块与 pip）+ build.sh 自动校验
  etc/seccomp-judge.json + scripts/（seccomp-calibrate 校准工具 + check-adapters 体检）
cloudfunctions/judgeSubmit/   云函数中转（index.js 纯 JS）
docs/               整体方案 v1.0/v1.1、判题服务接口与验收 v0.2/v0.3、引擎设计、学习闭环、题库扩充、保姆级说明
tests/              engine/wiring/pages/judge/judge-http 五个 .mjs 自检（node 直跑，无需 docker）
```

**4. 架构与数据流**（来源：docs/判题服务-接口与验收-v0.3.md §1 链路图 + 各源码 docstring）
- 判题链：`小程序取题（ClientQuestion 无答案）→ 用户写代码 → submitToJudge → 云函数 judgeSubmit（取 openid=唯一可信身份，注入内部 token + HMAC 签名）→ 判题服务（precheck 代码大小 → 编译 → 逐测试点运行 → classify 八态结论）→ 一次性沙箱容器`。
- 签名协议：`v1 \n METHOD \n PATH \n TIMESTAMP \n NONCE \n SHA256_HEX(BODY)`，云函数与服务端各一份实现，tests/judge-http.test.mjs 用同一组测试向量断言一致。
- **三层判分策略**（miniprogram/engine/types.ts）：L1 本地确定性判分（exact/set/normalize/numeric/output，练习关用，判分依据随题下发）；L2 编译判分（sandbox，服务端 Docker）；L3 AI 语义判分（ai，已注册占位、链路未接——Phase 5）。
- **跨端契约单一来源**：`judge-service/src/contract.ts` 与 `miniprogram/services/judge-contract.ts` 逐字节一致，npm run sync:contract 同步 + 测试比对 SHA-256。
- 题型引擎注册制：题目是纯数据，新增题型 = 注册渲染器 + 注册判分器，不改页面（engine/types.ts docstring）。

**5. 核心业务流程与关键接口**
- `POST /judge`（判题服务）：请求头 X-Judge-Token/Timestamp/Nonce/Signature 全部必需；body 含 submissionId（幂等缓存键）/questionId/type/code/language/limits。
- 提交频率限制：30 次/分、300 次/日（服务端与客户端同口径）；结果缓存按 submissionId 幂等（同一次提交不重跑沙箱）。
- 防泄题：隐藏测试点不回传输入/期望/实际输出（questions/testcases.ts preview 生成）。

**6. 快速上手**（来源：docs/本机跑起来-保姆级说明.md、package.json scripts、judge-service/README.md）
```bash
npm test                      # 1741 项自动化自检（引擎192+接线538+页面81+判题652+HTTP278）
JUDGE_SANDBOX=local node judge-service/src/server.js   # 本地模式：不装 docker 跑通全链路
node judge-service/scripts/local-smoke.mjs             # 本机端到端冒烟 5/5（C/C++ 4 项 + Python 1 项）
```
- 小程序端：微信开发者工具导入（project.config.json，appid 为 touristappid——**未绑定真实主体，已验证**）；完整判题需 Redis + Docker（均有降级路径）。

**7. 二次开发导航**
- 加题型：`miniprogram/components/question/renderers/<新题型>/`（四件套 json/ts/wxml/wxss）+ `engine/judges/` 加判分器 + `engine/registry.ts` 注册 + mock/bank.ts 加题。
- 改判题安全策略：`judge-service/src/sandbox/policy.ts`（纯函数，可单测）+ `images/Dockerfile.*` + `etc/seccomp-judge.json`（需 seccomp-calibrate 实跑校准）。
- 改鉴权：`judge-service/src/auth.ts` 与 `cloudfunctions/judgeSubmit/index.js` **必须同时改**（两边签名串拼法一致，测试向量锁定）。
- 改限流/缓存：`judge-service/src/redis/rate-counter.ts`、`result-cache.ts`。

**8. 技术亮点、潜在债务与风险点**
- 亮点：①安全意识是六个仓库中最专业的：前端不持 token（包可反编译）、身份只认云函数 openid、nonce 防重放、常量时间比较、tmpfs noexec 关掉「往 /tmp 投二进制再执行」、沙箱镜像自建并自动校验（UID/无外联工具/无 pip）；②跨端契约逐字节一致 + 自动比对，消掉「服务端返回 wrong-answer 客户端没这个 case」类静默 bug；③1741 项自动化自检 + 真实 HTTP 冒烟 7/7；④零依赖 Node 服务（连 Redis 客户端都手写），部署面极小；⑤文档工程化（接口与验收手册逐版本记录差异）。
- 债务/风险：①产品功能与方案差距大（4 页面 vs 方案中 PK/组队/宠物/会员/支付）；②L3 AI 判分未接；③**合规红线未闭合**：教育类目资质（办学许可证）、题库版权、iOS 虚拟商品支付（方案文档自述需 Phase 0 确认）；④appid 为 touristappid（未上架状态）；⑤判题服务控制面镜像化但 compose 仅本地/内测编排，生产部署文档缺失（推测）。

**9. 建议阅读顺序**
`docs/XX闯关-整体方案-v1.1.md`（产品全貌）→ `miniprogram/engine/types.ts`（数据契约，最短但信息密度最高）→ `docs/判题服务-接口与验收-v0.3.md`（链路+安全）→ `judge-service/src/server.js` + `sandbox/policy.ts`（安全落地）→ `cloudfunctions/judgeSubmit/index.js`（为什么需要中转层）→ `tests/`（验收口径）。

---

### 2.5 nbs-visualization

**1. 项目定位**
- NBS（Minecraft Note Block Studio，`.nbs` 文件）音乐可视化视频生成器：pynbs 解析 + MC 风格贴图渲染成「钢琴卷帘」可视化视频（横轴时间、纵轴音高、播放头扫过、命中音符迸发粒子）（来源：README.md）。
- 两种使用方式：命令行单文件快速生成 + Flask Web 界面（拖拽上传、进度轮询、在线预览下载）。
- 目标用户：MC 音乐创作者/视频二创者。

**2. 技术栈**（来源：requirements.txt、pyproject.toml、.env.example、src/nbs_visualizer/*）
- Python 3.10+；依赖：pynbs、Pillow、opencv-python、numpy、Flask、python-dotenv、imageio-ffmpeg、pydantic（pyproject.toml 声明）。
- **Kimi（Moonshot）AI API**：src/nbs_visualizer/kimi_ai.py + kimi_prompts.py + kimi_translate.py——轨道角色命名、歌曲结构分析（前奏/主歌/副歌）、镜头脚本生成、字幕提示；全部带本地缓存（kimi_ai.py docstring + run_cli.py 的 use_kimi=True）。
- 视频合成：OpenCV + mp4v 编码；1280×720 @ 30 FPS（README）。
- 注意：**requirements.txt 与 pyproject.toml 依赖清单不一致**（前者 7 项不含 pydantic，后者 8 项含——已验证的小漂移）。

**3. 目录结构**（来源：git 树 API 146 条目 + pyproject.toml）
```
app.py                     Flask Web（/upload /status/<id> /download/<id>，任务 TTL 24h）
run_cli.py                 命令行入口（use_kimi=True 固定开启 AI 增强）
run_full_song.py / run_preview_30s.py / run_spectrum_preview.py / local_render.py / render_preview.py   # 多场景入口
src/nbs_visualizer/        核心包（setuptools src 布局）：
  _core.py(render_video) / renderer.py(FrameRenderer) / renderer_multiprocess.py / renderer_audio.py
  renderer_spectrums.py / renderer_subtitles.py / renderer_painter.py
  camera.py / particles.py / particle_effects.py / spectrum.py / anim_system.py
  stage.py / stage_layout.py / stage_constants.py / layout_calculator.py / layout_engine.py
  track_data.py / track_operations.py / track_state_machine.py / card_renderer.py / audio_analysis.py
  kimi_ai.py / kimi_prompts.py / kimi_translate.py / config.py / config_schema.py / resources.py / ui.py / utils.py / logger.py
assets/                    textures/（16 乐器方块+8 粒子+天空，部分为运行时生成的 (new) 纹理）、fonts/（含 msyh.ttc 19MB 等 4 个字体）、mc/、generate_textures.py、create_sample_nbs.py
templates/index.html       Web UI（MC 配色+拖拽上传）
oop-engineering-spec-v2.md 54KB OOP 工程规范文档（重构指导）
test_camera_diag.py / test_direction5.py   两个临时诊断脚本
```

**4. 架构与数据流**（来源：README + 源码结构 + run_cli.py）
- 主链：`pynbs 解析 .nbs（v1-v5，旋律/和弦/多层/多乐器）→ track_data/track_operations 轨道建模 →（可选）Kimi AI：命名/分组/结构分析/镜头脚本（带缓存）→ layout_engine/layout_calculator 布局 → FrameRenderer 逐帧渲染（Pillow 贴图 + OpenCV 合成 + 粒子系统 + 频谱）→ mp4v 输出`。
- 渲染器按关注点分离：audio（音频对齐）/ spectrums（频谱）/ subtitles（字幕）/ painter（绘制）/ multiprocess（多进程）。
- Web 侧：TaskManager 管理任务（内存字典 + 后台线程 + 24h TTL），前端 500ms 轮询 /status。
- NBS 格式硬约束：每个 tick 每个 layer 只能一个音符（README 注意事项）。

**5. 核心业务流程与关键接口**
- `render_video(input, output, progress_callback, use_kimi)`：核心渲染入口（run_cli.py 调用）。
- Flask：`POST /upload`（50MB 上限）、`GET /status/<task_id>`、`GET /download/<task_id>`、`GET /`。
- Kimi 服务：`KimiAIService`（命名/分组/脚本三类缓存文件）。

**6. 快速上手**（来源：README.md）
```bash
pip install -r requirements.txt
python assets/create_sample_nbs.py     # 生成 6.8 秒样本（README 说在 assets/ 下）
python run_cli.py my_song.nbs outputs/my_video.mp4
python app.py                          # Web：http://127.0.0.1:5000
```
- Kimi 功能需 .env 配 API key（config.get_kimi_api_key，缺失时推测自动跳过 AI 增强——**未在代码中最终确认降级行为**）。

**7. 二次开发导航**
- 改视觉/舞台：`stage.py` / `stage_layout.py` / `stage_constants.py` / `layout_engine.py`。
- 改粒子/特效：`particles.py` / `particle_effects.py` / `anim_system.py`。
- 改 AI 行为：`kimi_prompts.py`（提示词）+ `kimi_ai.py`（服务与缓存）。
- 改渲染性能：`renderer_multiprocess.py`；加新输出形态：仿照 `renderer_spectrums.py` 新增 renderer_* 模块。
- 贴图再生成：`assets/generate_textures.py`。

**8. 技术亮点、潜在债务与风险点**
- 亮点：①渲染引擎模块化程度高（30 个模块按关注点切分，且配 54KB OOP 工程规范文档，重构有章法）；②Kimi AI 增强（结构分析+镜头脚本+字幕）在同类工具中少见，且带缓存控成本；③多入口脚本覆盖 CLI/全曲/预览/频谱/本地调试多场景。
- 债务/风险：①**README 与代码结构严重脱节**：README 说 `nbs_visualizer.py` 单文件在根目录，实际是 `src/nbs_visualizer/` 包；README 说样本命令是 `python assets/create_sample_nbs.py` 而结构块又把 create_sample_nbs.py 列在根——文档处于重构前状态（已验证）；②根目录 `app.py` 的 `sys.path.insert(0, 根目录)` + `from nbs_visualizer import ...` 与 src 布局冲突——未 `pip install -e .` 时 Web 入口大概率 ImportError（**推测，未实跑**）；③入口脚本泛滥（6+ 个 run_*.py + 2 个 test_*.py 散在根目录）；④19MB 字体（msyh.ttc）入库，仓库 29MB；⑤mp4v 编码质量与浏览器兼容性一般（README 自述）；⑥除两个诊断脚本外无自动化测试。

**9. 建议阅读顺序**
README.md → `oop-engineering-spec-v2.md`（理解重构方向）→ `src/nbs_visualizer/_core.py` → `renderer.py`（FrameRenderer）→ `track_data.py`/`track_state_machine.py`（数据建模）→ `kimi_ai.py`（AI 增强）→ `app.py` + `templates/index.html`（Web 层）。

---

### 2.6 MusicNow.github.io

- **状态：无法访问，仓库不存在（已验证事实）**。
- 验证方式：① GitHub 网页 `https://github.com/Undefined-TSK/MusicNow.github.io` 返回 404 Page not found；② GitHub REST API `repos/Undefined-TSK/MusicNow.github.io` 返回 404；③ 该用户名下公开仓库列表仅 5 个（aifilmteam、bili-songbot、nbs-visualization、unitflow、wechat-miniprogram），无 MusicNow.github.io。
- 可能原因（推测）：从未创建、已删除、名称拼写不同、或为私有仓库。
- 从命名规律推测（**纯推测，无任何仓库内证据**）：`MusicNow.github.io` 形如 GitHub Pages 站点仓库，可能与 bili-songbot/nbs-visualization 同属「音乐」主题线（点歌机器人 + 音乐可视化），或是一个计划中的音乐聚合/展示页面。**在仓库可访问前，不做任何进一步结论。**
- 建议：确认仓库是否私有、是否改名（如 MusicNow、music-now 等），或提供正确链接后补充分析。

---

## 三、横向对比

### 3.1 技术栈、项目类型、工程成熟度差异

| 维度 | 观察 |
|---|---|
| **语言分布** | Python 为主力（unitflow/aifilmteam/nbs），Python+Node 混合（bili-songbot），TypeScript 全栈（wechat-miniprogram）；无 Go/Rust/Java |
| **项目类型** | ①平台/框架型：unitflow（插件内核+图引擎）；②垂直工作流型：aifilmteam（影视制作 Agent 团队）；③工具/机器人型：bili-songbot（直播弹幕点歌）；④产品型：wechat-miniprogram（小程序+微服务）；⑤媒体处理型：nbs-visualization（视频渲染） |
| **后端形态** | FastAPI×2（unitflow、aifilmteam）、Flask×1（nbs）、零框架 asyncio 脚本×1（bili）、零依赖 Node HTTP×1（wechat 判题）+ 微信云函数 |
| **数据层** | 全部「轻存储」：向量库/JSON/文件系统，无 MySQL/PostgreSQL/Mongo；仅 unitflow 用到 SQLite（APScheduler jobstore 间接） |
| **AI 用法** | 百炼/DashScope（unitflow 主力、aifilmteam 云端备选）、Ollama 本地（aifilmteam）、Kimi/Moonshot（nbs）、多模型混合（aifilmteam：Wan/ComfyUI/fal/即梦/Qwen3-VL/ACE-Step/CosyVoice）、判题 AI 占位（wechat） |
| **测试与 CI** | unitflow：612 test 函数 + CI 门禁（唯一有 CI 的）；wechat：1741 项 node 自检（无 CI 配置）；aifilmteam：少量 verify/smoke 脚本；bili/nbs：基本无测试 |
| **文档质量** | unitflow（PROJECT_MANUAL.html 19 章）≈ wechat（7 份版本化文档）> bili（README 即手册）> aifilmteam（海量过程文档但散）> nbs（README 已过期） |
| **成熟度** | 较完整：unitflow；中期：aifilmteam、bili-songbot、wechat-miniprogram、nbs-visualization（偏初期）；不存在：MusicNow.github.io |

### 3.2 仓库间的复用关系与共同基础设施

1. **unitflow ↔ aifilmteam（同源思想，最多交集）**
   - 都用 FastAPI + typer 风格 CLI + 百炼/DashScope（aifilmteam 的 llm_gateway 读 dashscope.env）。
   - 都玩「Agent 化」：unitflow 有 units 的 agent/team 类型与 11 个 agent 工具；aifilmteam 有 7 岗 SKILL.md + handoff JSON + crew_bus.py——**aifilmteam 的 Agent 协作可以用 unitflow 的 Unit/team 契约重新表达**（推测：两者是同一作者「万物皆插件」理念的前后两代实践）。
   - 都接 MCP：unitflow 消费官方 MCP server；aifilmteam 自建 Blender MCP server。
2. **aifilmteam ↔ nbs-visualization（渲染管线同构）**
   - 都是「Python + OpenCV/Pillow + FFmpeg 帧级渲染」；aifilmteam 的 Blender 渲染与 nbs 的帧渲染都涉及帧序列→mp4（pngseq_to_mp4.py vs renderer_multiprocess.py）。
   - 都依赖 GPU/大模型做生成（Wan/ComfyUI vs Kimi）。
3. **bili-songbot ↔ nbs-visualization（音乐主题线）**
   - bili 解决「直播放什么歌」，nbs 解决「歌曲怎么变成 MC 风视频」；共享「音乐源/格式」领域知识。MusicNow.github.io（不存在）从命名看最可能属于这条线（推测）。
4. **wechat-miniprogram（最独立）**
   - 唯一 to C 产品、唯一 TypeScript、唯一微信生态；但其**判题服务的安全模式（HMAC+nonce+沙箱+契约比对）可被任何项目复用**，unitflow 的 sandbox_proxy/seams/python_exec.py 与它是同一问题域（不可信代码执行）的不同解法。

### 3.3 组合成更大系统的可能方式与集成难点

**可能的组合形态（推测，基于各仓库现有能力）：**
- **「AI 内容工厂」**：unitflow 作为 Agent 编排底座 → 调度 aifilmteam 的影视工作流（白模→写实化→QC）→ nbs-visualization 提供片头/音乐可视化 → bili-songbot 直播间播放成片 → wechat-miniprogram 作为学习产品承载科普内容。
- **「音乐线」**：bili-songbot（点歌）+ nbs-visualization（可视化）+ MusicNow（页面，待确认）组成直播音乐工作站。

**集成难点：**
1. **运行时环境冲突**：aifilmteam 要 Windows+12GB GPU+Blender 便携版；unitflow 要 Python 3.11+数 GB 依赖；wechat 判题要 Docker+Redis；bili 要 Windows+ffplay+node。**没有一个统一部署形态**，组合只能以「进程间 API 调用」方式松耦合（unitflow 的 /api/agent/step 或 aifilmteam 的 api_server 可作入口）。
2. **认证与多租户缺失**：unitflow 有 Bearer Token 但默认关；aifilmteam api_server 监听 127.0.0.1 无鉴权；判题服务有完整 HMAC 体系但只服务小程序。组合时需要统一 gateway。
3. **数据模型不互通**：aifilmteam 的 shot_spec/handoff JSON、unitflow 的 Unit manifest、wechat 的 judge contract 是三套独立契约；组合需要适配层（有趣的是 unitflow 的 node_graph 契约理论上能表达另外两家的流程——推测这是作者自己仓库里的「下一步」）。
4. **GPU 资源争抢**：aifilmteam 已用 vram_lock 解决内部冲突；若 nbs（CPU 渲染为主）与 unitflow（本地 bge-reranker 用 GPU/CPU）加入，需要统一的资源仲裁（推测：vram_lock 可泛化）。
5. **合规边界**：bili 的音源脚本、aifilmteam 的云端生成商用授权、wechat 的教育资质——组合系统的合规面是各仓库之和。

### 3.4 最值得优先深入学习的仓库及原因

**首选：unitflow**。理由：①唯一有 CI 门禁 + 大体量测试 + 完整架构手册的仓库，工程实践可完整学习（测试策略、降级设计、安全语义、恒等重构）；②「万物皆插件+图即引擎」是一套可迁移的架构思想，其 Unit 契约（plugin/agent/team 三态统一）几乎是另外几个仓库 Agent 化的「前辈版本」；③代码体量适中（2.25MB 仓库）且文档自洽，阅读路径清晰。

**次选（按目的）**：
- 学**安全设计**→ wechat-miniprogram（judge-service 的鉴权/沙箱/契约一致性是教科书级；云函数中转层的「为什么」讲解极佳）。
- 学**领域方法论**→ aifilmteam（把不确定生成工程化的 QC 硬门 + 单一事实来源纪律 + 幂等账本，适用于任何 AI 生成类项目）。
- 学**第三方 API 灰集成**→ bili-songbot（WBI 签名、风控指纹、多层回退，是「与不稳定外部系统共处」的实战样本）。

---

## 四、仍需确认的问题

1. **MusicNow.github.io**：仓库是否存在（私有？改名？删除？）。当前 404 无法分析——需要仓库所有者确认或提供可访问链接。
2. **aifilmteam 根目录「项目完整掌握报告.md」**：git 树中存在（sha 2ff6ffb，10319 字节）但 GitHub web/raw/contents API 均 404（GitHub 侧数据不一致，已实测）。该报告内容未能读取，aifilmteam 分析基于 PROJECT_STATE.md/AGENTS.md/spec/tools 等其他材料补全。
3. **unitflow 测试数**：提交信息称「741 项 pytest 全绿」，实际清点 612 个 test 函数（40 文件）——差异推测来自参数化用例展开计数，未逐一定位。
4. **unitflow 仓库名与产品名关系**：仓库描述为 unitflow（Agent 平台），代码/文档全部自称 PyPedia——两者是「平台名 vs 首个应用名」还是改名残留，仓库内无明确说明（推测为后者）。
5. **nbs-visualization 的 Web 入口可用性**：根 app.py 的 import 方式与 src 布局疑似冲突（未 pip install 时可能 ImportError），未实跑验证；Kimi API key 缺失时的降级行为也未在代码中最终确认。
6. **nbs 依赖清单双轨**：requirements.txt（7 项）与 pyproject.toml（8 项，多 pydantic）不一致，哪份是权威未说明。
7. **bili-songbot 音源合规性**：22 个第三方音源脚本的版权/服务条款状态，仓库内无声明；实际直播使用需用户自行评估（仓库 README 亦未涉及）。
8. **wechat-miniprogram 生产部署**：judge-service 有 docker-compose 但标注「本地/内测编排」；生产环境（TLS、域名、Redis 集群、云函数正式配置）无文档；appid 为 touristappid，上架主体未确认。
9. **aifilmteam 可移植性**：项目深度绑定一台 Windows 机器（D:\ 路径、便携版 Blender/ComfyUI、12GB 显卡）；是否有其他机器跑通的记录，仓库内无证据。
10. **各仓库间是否有意组成同一产品线**：除命名与技术同源迹象外，无任何仓库内交叉引用可证（推测成分高，已在正文标注）。

---

## 附：分析方法与可信度说明

- 已完整获取：unitflow、wechat-miniprogram（全量源码）；aifilmteam（全量文件树 + 15 个核心文件全文）；bili-songbot（核心 2 文件 + 全量树 + README）；nbs-visualization（全量树 + 8 个核心文件 + README）。
- 因 GitHub 匿名 API 限流与网络波动，个别大文件（aifilmteam 根报告、nbs 的 oop 规范全文）未能读取，已在相应位置标注。
- 所有「已验证事实」均可回溯到上述文件；「推测」均明确标记。
