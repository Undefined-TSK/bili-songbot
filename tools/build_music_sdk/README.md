# musicSdk_bundle.cjs 构建说明

`musicSdk_bundle.cjs`（仓库根）是 lx-music-desktop v2.12.6 内置搜索引擎（musicSdk）
经 esbuild 打包的 Node 单文件产物。本目录提供**可复现构建**：

```bash
bash tools/build_music_sdk/build.sh
```

## 结构

| 文件 | 说明 |
|---|---|
| FILE_LIST.json | 拉取清单（musicSdk 88 文件 + 7 个 utils 依赖） |
| fetch_music_sdk.py | 从 jsdelivr CDN 按清单拉取 lx 源文件 |
| renderer_alias/ | `@renderer/utils` 路径别名的 Node 适配层：`utils.js`（decodeName HTML 实体解码等价实现）、`index.js`（barrel）、`store.js`（stub：apiSource/userApi 空 + proxy 禁用态）、`request.js`/`env.js`/`message.ts`（构建时从 lx 源码拉入）、`musicSdk/`（symlink） |
| common_alias/utils/ | `@common/utils` 别名层：`common.ts`/`tools.ts`/`nodejs.ts`/`index.ts`（构建时从 lx 源码拉入，本目录留存副本）、`lyricUtils/kg.js`（stub：KRC 歌词解密，搜索不取歌词） |
| index.js / request.js | 工作区根文件：承接 musicSdk 内部的 `../../index`、`../../request` 相对引用 |

## 许可

构建产物与拉取的源文件遵循 lx-music-desktop 的许可（Apache-2.0 + 其 README 补充条款），
详见仓库 `THIRD_PARTY_NOTICES.md` 与 `LICENSES/lx-music-desktop-Apache-2.0.txt`。
本目录的适配文件（stub/alias/build 脚本）遵循本项目许可证。
