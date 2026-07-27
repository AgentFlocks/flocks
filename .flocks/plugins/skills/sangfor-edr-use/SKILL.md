---
name: sangfor-edr-use
description: 用于处理深信服 EDR（终端检测与响应）相关任务，通过 Flocks browser/CDP 完成登录态复用、终端状态查询、概况统计、失陷设备排查和设备运行状态查看。只要用户提到深信服 EDR、EDR 或 sangfor EDR，必须先加载本 skill；本 skill 是 EDR 任务的唯一入口，未阅读前不要直接使用 browser-use。
---

# 深信服 EDR Use

本 skill 只负责任务入口和登录分流；CDP 操作、浏览器启动、验证码、selector、tab/iframe 处理和页面数据提取见 [references/cdp-workflow.md](references/cdp-workflow.md)。

## 登录分流

需要打开 EDR 页面、抓取页面请求或调用 EDR API 采集工具时，按以下顺序执行：

1. 调用 `sangfor_edr_auth(action=status_auth_state)`，确认 `validation.valid`、`can_auto_refresh` 和 `has_saved_token`。不要先向用户索要账密。
2. `validation.valid=true`：
   - 仅浏览器/CDP任务：直接继续；
   - 需要 API token：`has_saved_token=true` 才继续，否则调用 `refresh_auth_state` 补齐 token。
3. `validation.valid=false` 且 `can_auto_refresh=true`：调用 `ensure_auth_state` 自动登录。
4. 没有可用 state 或账密时：调用 `ensure_auth_state` 打开登录页；返回 `manual_login_required` 后，让用户在该工具打开的同一个 EDR tab 中完成登录、MFA 或 UKey。
5. 用户完成手动登录后，调用 `complete_manual_login`；只有返回 `manual_login_captured_auth_state` 且 `token_saved=true`，才认为浏览器登录和 API 登录准备均完成。
6. 返回 `browser_daemon_not_ready` 或 `auth_state_load_failed_browser_daemon_not_ready`：依次执行 `flocks browser --setup`、`flocks browser --doctor`，再重试原 action。

认证工具会在登录提交前监听 `launch_login.php` 的 fetch/XHR 响应，将 `data.token` 保存到 Secret Manager；token 不得回显、写入日志或写入 `auth-state.json`。后续 API 工具应从 Secret Manager 读取对应 token，并同时使用同一 EDR 的 cookies。

`bu.port` 是 Flocks browser daemon 的 IPC 端口文件，不是 Chrome 的 remote-debugging 端口；禁止手工创建或修改它。

## 任务边界

- 当前 `sangfor_edr_v1_0_0` 的页面业务仍通过浏览器/CDP完成；需要 API 采集时，必须先完成 token readiness 检查。
- 首页仪表盘可用于设备 CPU/内存/硬盘、终端概况和失陷统计。
- 查询失陷终端清单时，必须进入“威胁资产分析”并选择“已失陷终端”，不能使用默认“全部”列表。
- 设备状态抓取脚本位于 `references/fetch_edr_system_state.py`；执行前必须完成上述登录分流。

## 执行约束

- 运行 skill 内 Python 脚本时必须使用 Flocks 虚拟环境；不要使用系统 Python。
- 需要具体 CDP 命令、平台启动方式、验证码/selector 配置、tab/iframe 处理或页面关键词时，读取 `references/cdp-workflow.md`，不要在本文件重复展开。
