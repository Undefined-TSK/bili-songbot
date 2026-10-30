# 第三方软件与代码声明（THIRD_PARTY NOTICES）

本项目（GPL-3.0）以源码形式公开发布，构成对第三方代码的分发，义务已按下列声明履行：

## 0. 本项目许可证

自有代码以 **GPL-3.0** 发布（见 LICENSE）。其中：

- lx-music-desktop 的代码（Apache-2.0）依其许可证并入本项目——Apache-2.0 与 GPL-3.0 单向兼容（Apache 软件基金会 FSF 兼容性声明），该部分保持其原有许可与归属声明
- lx-music-desktop README 补充条款（非商业等）仅约束其代码部分；使用者请一并遵守
- bilibili-api-python 等为 pip 独立安装依赖，不以源码形式包含在本仓库

## 1. lx-music-desktop（落雪音乐桌面版）——本项目唯一搬用的落雪代码

- 来源：https://github.com/lyswhut/lx-music-desktop
- 版本：v2.12.6（tag 固定）
- 许可证：Apache License 2.0（副本见 `LICENSES/lx-music-desktop-Apache-2.0.txt`）；**另受其 README「项目协议」九条补充条款约束**
- 搬用范围（经 esbuild 打包为 `musicSdk_bundle.cjs`，**可复现构建见 `tools/build_music_sdk/`**）：
  - `src/renderer/utils/musicSdk/**`（七平台搜索/直链 SDK，8841 行，未修改业务逻辑）
  - `src/renderer/utils/request.js`、`env.js`、`message.ts`（HTTP 层）
  - `src/common/utils/common.ts`、`tools.ts`、`nodejs.ts`、`index.ts`（工具函数）
  - `src/common/utils/lyricUtils`（KRC 解密）以 stub 替代（未搬用）
- 本项目的适配改动（非对原文件的修改）：新增 alias 解析文件与 stub
  （`renderer_alias/*`、`common_alias/*`、根 `index.js`/`request.js`），
  其中 `decodeName`/`dateFormat2` 为 Node 环境等价实现，`proxy` 置禁用态
- Apache 2.0 义务（仅分发时触发）：随分发附 Apache 2.0 许可证副本（本文件末尾引用）；
  注明源码来源与版本（即本节）；Object 形式分发须附源码或指明获取途径（即本节 URL）
- 补充条款要点（引用自 lx README「项目协议」）：
  - 非商业性质：仅用于技术探索研究，不作商业合作
  - 数据来源：各平台数据来自公开服务器拉取，项目方不对其合法性负责
  - 版权数据：使用者注意版权，本项目不拥有第三方版权数据

## 2. 随 musicSdk_bundle.cjs 内联的 npm 依赖（全部 MIT）

| 包 | 许可证 | 用途 |
|---|---|---|
| needle | MIT | HTTP 客户端 |
| tunnel | MIT | 代理隧道 |
| crypto-js | MIT | 平台签名算法 |
| electron-log | MIT | 日志 |

## 3. Python 依赖

| 包 | 许可证 | 用法 | 分发时注意 |
|---|---|---|---|
| bilibili-api-python | GPL-3.0-or-later | pip 独立安装，未修改未内联 | 通过 pip 独立安装属"聚合"而非衍生；分发本项目时应保持其为独立安装的依赖，不宜与其源码合并成单一作品 |
| python-mpv | GPLv2+ / LGPLv2.1+ | pip 独立安装（ctypes 动态调用 libmpv） | 动态调用，聚合关系 |
| rapidfuzz | MIT | pip 独立安装 | 分发时保留版权声明 |
| aiohttp | Apache-2.0 | pip 独立安装 | 同上 |

## 4. 自研部分（无第三方代码来源）

`source_bridge.js` 桥接器主体、`bot/*.py` 全部业务代码、`main.py`、
`tests/**`、`source_bridge.js` 的 stdin/stdout JSON 协议——均为本项目自研
（参考 lx 官方公开文档《自定义源脚本编写说明》的接口规范编写，规范本身不受版权保护）。

---

Apache License 2.0 全文见 https://www.apache.org/licenses/LICENSE-2.0
（与 lx-music-desktop 仓库 LICENSE 文件一致）
