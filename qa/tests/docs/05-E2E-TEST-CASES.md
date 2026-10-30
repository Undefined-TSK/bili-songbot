# TC-E2E 全软件端到端测试用例（权威来源）

> 验收目标：整机功能达到**正式版标准**——既有 164 条单元测试全过、真实内核全链路（搜索→取链→下载校验）、配置与文档一致性。

| 用例 | 标题 | 优先级 |
|---|---|---|
| TC-E2E-001 | 既有单元测试套件 164/164 全过 | P0 |
| TC-E2E-002 | 真实内核搜索（内置引擎，wy 平台） | P0 |
| TC-E2E-003 | 真实内核经 LX 源取链（kw 平台） | P0 |
| TC-E2E-004 | 真实内核冒烟脚本 smoke_bridge.py 正常收尾 | P0 |
| TC-E2E-005 | manifest 重新生成：45 源 ≥44 加载 | P0 |
| TC-E2E-006 | download() 全链路（本地源服务器 → 落盘文件） | P1 |
| TC-E2E-007 | config.example.json 合法且与 config.json 键结构一致 | P1 |
| TC-E2E-008 | main.py 与 bot 包可干净导入（语法/依赖完整） | P0 |
| TC-E2E-009 | 文档声称数字与实测一致（README/诊断报告 vs manifest） | P2 |
| TC-E2E-010 | tests/e2e_test.py、comprehensive、final_full、official_fallback 脚本可运行 | P1 |
| TC-E2E-011 | load_one 官方 sixyin/pdone-huibq 真实源加载 OK | P1 |
| TC-E2E-012 | 五平台覆盖：全部 45 源聚合后 wy/kw/tx/kg/mg 5/5 | P0 |

---

## TC-E2E-001 单元测试 (P0)
- **执行**：`python3 -m pytest tests/ -q`。
- **断言**：164 collected，0 failed。

## TC-E2E-002 真实搜索 (P0)
- **执行**：真实 45 源内核 `search('wy', '周杰伦', 5)`。
- **断言**：返回非空列表；元素含 name/singer 及平台标识。

## TC-E2E-003 真实取链 (P0)
- **执行**：kw 平台已知歌曲（海阔天空, 4728098）`get_url('kw', info, '128k')`。
- **断言**：返回 http(s) URL 或 None（网络受限环境接受 None 但不得抛错；沙箱有网时应返回 URL）。

## TC-E2E-004 冒烟脚本 (P0)
- **执行**：`python3 tools/smoke_bridge.py`。
- **断言**：退出码 0，汇总行输出就绪进程数。

## TC-E2E-005 manifest 基线 (P0)
- **执行**：`node tools/gen_manifest.js`。
- **断言**：total=45，loaded≥44，failed 仅 pdone-lx。

## TC-E2E-006 下载链路 (P1)
- **准备**：本地服务器返回音频字节。
- **执行**：`bridge.download(url, tmp_path)`。
- **断言**：落盘文件非空且字节数与响应一致。

## TC-E2E-007 配置一致性 (P1)
- **断言**：config.example.json 为合法 JSON；与 config.json 的键集合兼容（example ⊆ config 超集语义一致）。

## TC-E2E-008 导入完整性 (P0)
- **执行**：`python3 -c "import main; import bot.bridge; import bot.config"`（不启动服务）。
- **断言**：无 ImportError/SyntaxError。

## TC-E2E-009 文档一致性 (P2)
- **断言**：README 中音源加载数字与 manifest.json 实测一致。

## TC-E2E-010 存量测试脚本 (P1)
- **执行**：依次运行 tests/e2e_test.py、comprehensive_sources_test.py、final_full_test.py、official_fallback_case.py。
- **断言**：退出码 0 或脚本内自断言通过。

## TC-E2E-011 官方真实源 (P1)
- **执行**：load_one 加载 sources/pdone-huibq.js 与官方 sixyin 缓存版。
- **断言**：均 ok:true。

## TC-E2E-012 五平台覆盖 (P0)
- **执行**：汇总 manifest 各源 platforms。
- **断言**：wy/kw/tx/kg/mg 每平台 ≥1 个可用源。
