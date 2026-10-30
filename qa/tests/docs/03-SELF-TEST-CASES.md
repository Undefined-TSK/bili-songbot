# TC-SELF 自更新 / 自检测 / 自愈测试用例（权威来源）

> 验收目标：音源"自己保持更新、自己检测故障、尽量自己不挂"——热更新、崩溃重启、失败自愈、周期看门狗，全部自动化，用户零动手。

| 用例 | 标题 | 优先级 |
|---|---|---|
| TC-SELF-001 | 在线脚本内容变化 → 自动热重载替换进程 | P0 |
| TC-SELF-002 | 内容无变化 → 不做无谓重载 | P1 |
| TC-SELF-003 | 下载失败 → 保留现有进程继续服役 | P0 |
| TC-SELF-004 | 运行时进程崩溃 → 健康检查自动重启 | P0 |
| TC-SELF-005 | 初始化失败的源 → 文件修复后看门狗自愈上线 | P0 |
| TC-SELF-006 | start() 启动自更新+看门狗两个后台任务 | P1 |
| TC-SELF-007 | 周期配置可读（refresh 小时级 / watchdog 分钟级，带下限保护） | P2 |
| TC-SELF-008 | 自愈/重启后同 @name 去重依然生效（不产生双进程） | P1 |

---

## TC-SELF-001 热更新 (P0)
- **准备**：`SOURCE_URLS` 指向本地服务器；首轮内容 = `lx_hot_v1.js`（v1）；bridge 启动。
- **执行**：服务器切换为 v2 内容（同 @name，版本/URL 变化，md5 不同）；调用 `_refresh_online_sources()`。
- **断言**：缓存文件内容更新；旧进程被停、新进程就绪；日志出现热更新记录。

## TC-SELF-002 无变化不重载 (P1)
- **准备**：同上但内容不变。
- **执行/断言**：`_refresh_online_sources()` 后进程对象不变（is 比较通过）。

## TC-SELF-003 下载失败保活 (P0)
- **准备**：服务器关停后调用 `_refresh_online_sources()`。
- **执行/断言**：不抛异常；现有在线源进程原样保留。

## TC-SELF-004 崩溃重启 (P0)
- **准备**：bridge 就绪后取一个脚本进程。
- **执行**：`p.process.kill()`；等待 returncode 非 None；调用 `_health_check()`。
- **断言**：进程被重启（新 returncode is None、ready=True）；仍在进程池。

## TC-SELF-005 失败自愈 (P0)
- **准备**：目录含垃圾文件（启动后进入 `_failed_files`）。
- **执行**：把垃圾文件覆写为合法 LX 脚本；调用 `_health_check()`。
- **断言**：`_failed_files` 移除该文件；新进程上线。

## TC-SELF-006 后台任务 (P1)
- **执行/断言**：`start()` 后 `_bg_tasks` 含 `source-self-update` 与 `source-watchdog` 两个任务。

## TC-SELF-007 周期配置 (P2)
- **断言**：`_watchdog_loop` 周期 = max(60, SOURCE_WATCHDOG_INTERVAL)；`_self_update_loop` 周期 = max(600, SOURCE_REFRESH_INTERVAL*3600)。

## TC-SELF-008 自愈后去重 (P1)
- **准备**：TC-SELF-005 自愈后，目录中存在与在线版同 @name 的本地脚本。
- **执行/断言**：`_health_check` 尾部 `_dedupe_by_script_name` 生效，无双 @name 进程。
