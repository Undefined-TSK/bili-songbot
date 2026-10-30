# TC-SEC 安全测试用例（权威来源，OWASP 对齐）

> 验收目标：第三方音源脚本在沙箱进程内运行；内容校验防注入；无 shell 注入面；密钥不入日志。

| 用例 | 标题 | 优先级 | OWASP |
|---|---|---|---|
| TC-SEC-001 | URL 协议白名单：非 http(s) 取链结果被拒绝 | P0 | A03 |
| TC-SEC-002 | 子进程仅 argv 列表启动，无 shell=True / os.system | P0 | A03 |
| TC-SEC-003 | 在线源内容校验拒绝 HTML/脚本注入页 | P0 | A05 |
| TC-SEC-004 | 日志不泄露 API key / cookie 等机密 | P0 | A09 |
| TC-SEC-005 | 源脚本崩溃/恶意输出不穿透到 Python 主进程 | P0 | A04 |
| TC-SEC-006 | download() 输出路径由调用方控制、远程响应头不参与路径拼接 | P1 | A01 |
| TC-SEC-007 | config.example.json 不含真实密钥 | P1 | A02 |
| TC-SEC-008 | 脚本进程沙箱边界：仅注入 lx API，不暴露宿主文件写能力 | P1 | A05 |

---

## TC-SEC-001 URL 协议白名单 (P0)
- **执行**：`_validate_url("javascript:alert(1)")`；审查 source_bridge.js 的 musicUrl 返回值正则 `/^https?:\/\//`。
- **断言**：非 http(s) 一律拒绝。

## TC-SEC-002 无 shell (P0)
- **执行**：全仓 grep `shell=True|os.system|subprocess.call(.*shell`。
- **断言**：零命中；子进程一律 `create_subprocess_exec(argv列表)`。

## TC-SEC-003 内容校验 (P0)
- **执行**：本地服务器返回 `<html>…</html>` 与含 `<script>` 的伪脚本。
- **断言**：均被 `_fetch_online_sources` 内容校验拒绝，不写入缓存。

## TC-SEC-004 日志机密 (P0)
- **准备**：完整冒烟运行产生日志。
- **断言**：日志文件与 stdout 中不出现源脚本内置的 API key（如六音 key、X-Card-Key 值）。

## TC-SEC-005 故障隔离 (P0)
- **执行**：throw/lazy/garbage 夹具脚本启动。
- **断言**：Python 主进程存活，仅该源被隔离（独立子进程边界）。

## TC-SEC-006 下载路径 (P1)
- **审查**：`download()` 的 output_path 仅来自调用方，远程 Content-Disposition 不参与路径拼接。
- **断言**：代码审查通过（grep 无 resp.filename/header 拼接路径）。

## TC-SEC-007 示例配置无密钥 (P1)
- **断言**：config.example.json 中无真实 cookie/token（与 config.json 对比）。

## TC-SEC-008 沙箱边界 (P1)
- **审查**：source_bridge.js 仅注入 lx / 受控 require；脚本无 Node fs 直写通道（写入仅经桥接器自身逻辑）。
- **断言**：审查通过；残留风险（脚本可发起任意网络请求）与落雪桌面一致，记录为接受的风险。
