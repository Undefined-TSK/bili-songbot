#!/usr/bin/env bash
# 重建 musicSdk_bundle.cjs（lx-music-desktop v2.12.6 musicSdk 的 Node 打包）
# 用法: bash tools/build_music_sdk/build.sh   （产物覆盖仓库根 musicSdk_bundle.cjs）
set -euo pipefail
cd "$(dirname "$0")"

# 1. 依赖
npm install esbuild needle tunnel crypto-js electron-log --registry=https://mirrors.cloud.tencent.com/npm/

# 2. 拉取 lx 源文件（清单见 FILE_LIST.json，来源见 THIRD_PARTY_NOTICES.md）
python3 fetch_music_sdk.py

# 3. 组装打包工作区：拉取物 + 本目录的 alias/stub 适配层
rm -rf work && mkdir -p work
cp -r src_fetched/* work/
cp index.js request.js work/
cp -r renderer_alias common_alias work/
ln -sfn ../musicSdk work/renderer_alias/musicSdk

# 4. esbuild 打包（alias 解析 lx 源码的 @renderer/@common 路径别名）
esbuild work/musicSdk/index.js --bundle --platform=node --format=cjs \
  --alias:@renderer/store=./work/renderer_alias/store.js \
  --alias:@renderer/utils/musicSdk=./work/musicSdk \
  --alias:@renderer/utils=./work/renderer_alias \
  --alias:@common=./work/common_alias \
  --outfile=../../musicSdk_bundle.cjs

echo "构建完成 -> musicSdk_bundle.cjs"
echo "验证: node -e \"const s=require('./musicSdk_bundle.cjs').default; s.kw.musicSearch.search('稻香',1,1).then(r=>console.log(r.list[0].name, r.list[0].singer))\""
