# TC-KRN 内核健壮性测试用例（权威来源）

> 验收目标：内核**标准性、兼容性、健壮性、通用性**——个别源挂掉绝不影响整体；全部源挂掉仍有内置引擎兜底；竞速超时优雅降级。

| 用例 | 标题 | 优先级 |
|---|---|---|
| TC-KRN-001 | 全部脚本损坏时内置引擎仍然启动 | P0 |
| TC-KRN-002 | 请求不支持的平台 → 优雅返回 None 不抛错 | P0 |
| TC-KRN-003 | _validate_url 拒绝空/非 http URL | P0 |
| TC-KRN-004 | _validate_url 接受真实音频流（魔数识别） | P0 |
| TC-KRN-005 | 慢响应源挂起 → 竞速超时优雅返回，不阻塞其他源 | P0 |
| TC-KRN-006 | stderr 洪水（2 万行）不影响进程存活与响应 | P1 |
| TC-KRN-007 | stop() 干净回收全部进程（无孤儿进程） | P0 |
| TC-KRN-008 | init 超时时长受 SOURCE_INIT_TIMEOUT 控制 | P1 |
| TC-KRN-009 | 两个本地脚本同 @name → 去重保留首选 | P1 |
| TC-KRN-010 | 取链全程（搜索缓存→get_url→字段保真）字段不丢失 | P0 |

---

## TC-KRN-001 内置引擎兜底 (P0)
- **准备**：`SOURCE_DIR` 指向仅含垃圾文件的临时目录。
- **执行**：bridge 启动。
- **断言**：`builtin` 进程就绪（脚本全挂也能搜索）。

## TC-KRN-002 不支持平台 (P0)
- **执行**：`get_url('xx_unsupported', info, '128k')`。
- **断言**：返回 None，无未捕获异常。

## TC-KRN-003 URL 校验拒绝 (P0)
- **断言**：`_validate_url('')`、`_validate_url('javascript:alert(1)')`、`_validate_url('ftp://x')` 全部 False。

## TC-KRN-004 URL 校验接受音频 (P0)
- **准备**：本地服务器返回 `Content-Type: audio/mpeg` + ID3 头字节。
- **断言**：`_validate_url` 返回 True。

## TC-KRN-005 慢响应不阻塞 (P0)
- **准备**：目录含 `lx_slow_response.js`（挂起 30s）+ 合法快速源。
- **执行**：对 wy 发起 get_url（整体限时）。
- **断言**：在限时内返回（不等到 30s）；快速源结果不受影响；无泄漏异常。

## TC-KRN-006 stderr 洪水 (P1)
- **准备/执行**：`lx_stderr_flood.js` 正常加载后发起 musicUrl 请求。
- **断言**：进程 ready 且请求可路由到该进程（handler 存在）。

## TC-KRN-007 干净回收 (P0)
- **执行**：`bridge.stop()`。
- **断言**：全部进程 returncode 非 None；`procs==[]`；`builtin is None`；session 关闭。

## TC-KRN-008 init 超时可配 (P1)
- **断言**：`_ScriptProc.start` 注入环境变量 `LX_INIT_TIMEOUT=SOURCE_INIT_TIMEOUT`；`source_bridge.js` 读取该变量作为守卫时长。

## TC-KRN-009 本地同名去重 (P1)
- **准备**：目录含 minimal（@name=A, v1.0.0）与 dup（@name=A, v0.9.0）。
- **执行/断言**：仅保留 1 个 @name=A 进程（首选），另一个停用。

## TC-KRN-010 字段保真 (P0)
- **执行**：真实源搜索 → `_cache_search` → `get_url` → `_enrich`。
- **断言**：songmid/hash 等关键字段在取链阶段不丢失（缓存反查成功）。
