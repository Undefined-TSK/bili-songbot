# TC-LINK 链接形式音源测试用例（权威来源）

> 验收目标：`config.json` 的 `source_urls` 中的落雪标准链接音源，开机自动下载合并加载；多镜像回退；与本地 JS 源按 `@name` 去重；**链接形式与 JS 脚本形式双形态即插即用**。

| 用例 | 标题 | 优先级 |
|---|---|---|
| TC-LINK-001 | 合法链接音源自动下载并加载为进程 | P0 |
| TC-LINK-002 | HTTP 404 链接被跳过，不阻塞启动 | P0 |
| TC-LINK-003 | HTML 页面（伪脚本）被内容校验拒绝 | P0 |
| TC-LINK-004 | 纯文本无 lx 关键字内容被拒绝 | P1 |
| TC-LINK-005 | 多镜像回退：首选镜像失败、第二镜像成功即采用 | P0 |
| TC-LINK-006 | GitHub 链接自动派生 ≥4 个镜像候选（直连/mirror/jsdelivr×2） | P1 |
| TC-LINK-007 | 在线版与本地 JS 同 @name 时在线版优先（去重） | P0 |
| TC-LINK-008 | 全部下载失败时离线缓存仍可用于二次启动 | P1 |

**公共前置**：本地 aiohttp 测试服务器（127.0.0.1 随机端口）可启停；`SOURCE_URLS` 可被注入。

---

## TC-LINK-001 合法链接自动加载 (P0)
- **准备**：本地服务器 `/qa_valid.js` 返回合法 LX 脚本；`SOURCE_URLS=[该URL]`。
- **执行**：bridge 启动。
- **断言**：`bot/tmp/online_sources/` 出现缓存 .js；对应进程就绪；@name 元数据正确。

## TC-LINK-002 404 跳过 (P0)
- **准备**：`SOURCE_URLS=[http://127.0.0.1:PORT/not_exist.js]`。
- **执行/断言**：启动完成无异常；无对应进程；返回警告日志。

## TC-LINK-003 HTML 拒绝 (P0)
- **准备**：服务器返回 `<html>...<script>...</script></html>`。
- **执行/断言**：内容校验拒绝（`startswith('<')`），不落盘、不加载。

## TC-LINK-004 纯文本拒绝 (P1)
- **准备**：服务器返回 `hello world`（无 lx/EVENT_NAMES）。
- **执行/断言**：被拒绝，不加载。

## TC-LINK-005 镜像回退 (P0)
- **准备**：monkeypatch `_mirror_candidates` 返回 `[主URL(404), 备URL(200脚本)]`。
- **执行**：`_fetch_online_sources`。
- **断言**：采用第二镜像内容，下载成功。

## TC-LINK-006 镜像候选派生 (P1)
- **执行**：`_mirror_candidates("https://ghproxy.net/raw.githubusercontent.com/o/r/main/p.js")`。
- **断言**：候选含 ghproxy 原链、raw 直连、mirror.ghproxy、fastly.jsdelivr、cdn.jsdelivr ≥4 条、保序去重。

## TC-LINK-007 同名去重在线优先 (P0)
- **准备**：本地目录放 `@name=QA去重源` 的 JS；`SOURCE_URLS` 指向同 @name 的在线脚本。
- **执行**：bridge 启动（含 `_dedupe_by_script_name`）。
- **断言**：仅保留在线版进程；本地副本被停用；进程池无重复 @name。

## TC-LINK-008 离线缓存二次启动 (P1)
- **准备**：首次启动成功后关停本地服务器；二次启动。
- **断言**：若缓存仍被使用则通过；若未使用——**记录为缺陷**（链接源离线持久性缺口），不阻塞发版判断。
