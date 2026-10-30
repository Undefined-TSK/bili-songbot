# TC-PLUG 音源即插即用测试用例（权威来源）

> 验收目标：用户把**任何符合落雪标准格式**的 JS 音源脚本放入 `sources/` 目录后，内核自动加载、零修改、零技术门槛。坏文件自动隔离，不影响其他源。

| 用例 | 标题 | 优先级 |
|---|---|---|
| TC-PLUG-001 | 最小合法 LX 标准源放入目录即自动加载（零修改） | P0 |
| TC-PLUG-002 | 全平台（wy/kw/tx/kg/mg）声明源全部注册 | P0 |
| TC-PLUG-003 | 质量列表（128k/320k/flac）完整透传 | P1 |
| TC-PLUG-004 | 语法错误垃圾文件被隔离，其他源不受影响 | P0 |
| TC-PLUG-005 | 空文件被隔离，不阻塞启动 | P1 |
| TC-PLUG-006 | 合法 JS 但非 LX 脚本（无 lx API）被隔离 | P1 |
| TC-PLUG-007 | 初始化抛错的脚本被隔离 | P1 |
| TC-PLUG-008 | 永不发 inited 的惰性脚本被 init 守卫超时隔离 | P0 |
| TC-PLUG-009 | 中文+空格文件名兼容 | P2 |
| TC-PLUG-010 | 真实存量 45 源逐个通过 load_one 验证（manifest 基线） | P0 |

**公共前置**：内核 `bot/bridge.py` 可导入；`source_bridge.js` 与 Node 环境可用。

---

## TC-PLUG-001 最小合法源即插即用 (P0)
- **准备**：临时目录内仅放入 `qa/tests/fixtures/lx_valid_minimal.js`；`SOURCE_DIR` 指向该目录。
- **执行**：`SourceBridge().start()`。
- **断言**：进程就绪；`script_name == 'QA验收-最小标准源'`（元数据零改动透传）；声明 `wy/musicUrl`。

## TC-PLUG-002 全平台声明 (P0)
- **准备**：目录内放入 `lx_valid_multi.js`。
- **执行**：bridge 启动。
- **断言**：`sources` 含 wy/kw/tx/kg/mg 全部 5 平台且 actions=['musicUrl']。

## TC-PLUG-003 质量列表透传 (P1)
- **准备/执行**：同 TC-PLUG-002 的 wy 平台声明。
- **断言**：qualitys == ['128k','320k','flac'] 保序完整。

## TC-PLUG-004 垃圾文件隔离 (P0)
- **准备**：目录 = 最小合法源 + `lx_garbage.js`（语法错误）。
- **执行**：bridge 启动。
- **断言**：合法源就绪；垃圾文件被拒（start 抛错被 gather 捕获）；启动不崩溃。

## TC-PLUG-005 空文件隔离 (P1)
- **准备**：目录 = 最小合法源 + `lx_empty.js`。
- **执行/断言**：同 TC-PLUG-004，空文件被隔离、启动完成。

## TC-PLUG-006 非 LX 脚本隔离 (P1)
- **准备**：目录 = 最小合法源 + `lx_nonlx.js`（setInterval 常驻但无 lx API）。
- **执行/断言**：非 LX 脚本因未注册 handler/未初始化被隔离；合法源正常。

## TC-PLUG-007 初始化抛错隔离 (P1)
- **准备**：目录 = 最小合法源 + `lx_throw.js`。
- **执行/断言**：抛错脚本被隔离，其余源不受影响。

## TC-PLUG-008 惰性脚本超时隔离 (P0)
- **准备**：目录 = 最小合法源 + `lx_lazy.js`（永不 inited）。
- **执行**：bridge 启动，计时。
- **断言**：惰性脚本在 READY_TIMEOUT 内被隔离；总启动时间 < READY_TIMEOUT + 15s；合法源正常。

## TC-PLUG-009 中文空格文件名 (P2)
- **准备**：目录含 `lx_valid_中文 名字.js`。
- **执行/断言**：正常加载，@name 元数据正确。

## TC-PLUG-010 存量源基线 (P0)
- **准备**：`sources/manifest.json`（由 tools/gen_manifest.js 生成）。
- **断言**：45 源中 ≥44 加载成功，唯一失败为 `pdone-lx`（systemd 环境限制）且有记录。
