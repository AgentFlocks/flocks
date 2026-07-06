---
name: skyeye-sensor-use
description: 使用天眼 SkyEye Sensor 传感器侧精简 CLI 查询告警列表和告警统计。适用于用户提到"SkyEye Sensor""天眼流量传感器告警"场景。
---

# SkyEye Sensor 数据查询

> 如果是分析平台日志检索，不要用这个 skill，改用 `skyeye-use`。

## 适用范围

这个 skill 主要覆盖传感器侧接口：

- 告警统计
- 告警列表

不适合：

- Lucene 日志检索
- 专家模式管道检索
- 分析平台资产与系统模块
- 传感器枚举接口、License、权限和菜单配置

## 零、登录认证

> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

State 文件路径：`~/.flocks/browser/skyeye-sensor/auth-state.json`（固定，全局唯一）。
自动登录配置路径：`~/.flocks/browser/skyeye-sensor/auth-config.json`（只保存 base_url、state 路径和 secret 引用；密码写入 Flocks secret）。

## 跨平台执行约定（必须遵守）

`scripts/skyeye_sensor_auth.py` 和 `scripts/skyeye_sensor_cli.py` 都是 **skill-relative** 路径。执行任何脚本前，必须先把当前工作目录切到 `skill_load` 输出中的 **Base directory**，不要假设当前 shell 已经在 skill 目录，也不要拼未加引号的绝对路径。

推荐方式：

- 如果工具支持 `workdir` 参数，直接把 `workdir` 设为 `skill_load` 输出的 Base directory，再运行 `uv run python scripts/...`。
- 如果只能写 shell 命令，先进入 Base directory，再运行脚本。
- 跨平台示例优先使用 `--base-url`、`--auth-state` 等 CLI 参数，少用 shell 环境变量前缀。
- Windows PowerShell 设置环境变量必须用 `$env:NAME = 'value'`；不要用 POSIX 的 `NAME=value command`。
- macOS/Linux shell 可以用 `NAME=value command` 或 `export NAME=value`。
- 路径含空格时必须整体加引号。Windows 下优先用 `Set-Location -LiteralPath '<skill-dir>'`，macOS/Linux 下优先用 `cd "$SKILL_DIR"`。
- 不要在 `glob` / 文件工具的 `path` 参数里写 `$env:USERPROFILE`、`$HOME` 这类 shell 变量；这些参数通常不会做 shell 展开。需要用户目录时，用 `~/.flocks/...` 或先在 shell 里展开后再传入。

Windows PowerShell 模板：

```powershell
Set-Location -LiteralPath '<skill-load 输出的 Base directory>'
uv run python scripts/skyeye_sensor_auth.py --base-url 'https://<skyeye-sensor-domain>' validate
uv run python scripts/skyeye_sensor_cli.py --base-url 'https://<skyeye-sensor-domain>' alarm list --days 7
```

macOS/Linux 模板：

```bash
cd "<skill-load 输出的 Base directory>"
uv run python scripts/skyeye_sensor_auth.py --base-url 'https://<skyeye-sensor-domain>' validate
uv run python scripts/skyeye_sensor_cli.py --base-url 'https://<skyeye-sensor-domain>' alarm list --days 7
```

### 首次登录 / Session 过期重新登录

打开 SkyEye Sensor 页面或执行 CLI 前，必须按下面顺序处理登录态，不要一开始就要求用户提供账密：

1. 先检查 `auth-state.json` 是否可用。
2. 如果 state 可用，直接复用登录态继续打开页面或执行 CLI 获取数据。
3. 如果 state 不存在或失效，但本地已有可用于自动登录的配置，先在 skill Base directory 中执行自动刷新：`uv run python scripts/skyeye_sensor_auth.py ensure`。
4. 如果没有可用 state，也没有保存账密配置，再引导用户选择：
   - 提供 SkyEye Sensor 地址、用户名、密码，自动登录并保存账密配置，后续 state 失效时可直接自动刷新；
   - 不提供账密，走浏览器手动登录流程。
5. 只有自动登录失败、验证码 OCR / MFA / 登录页 DOM 变化 / 登录成功检测失败，或用户拒绝提供账密时，才回退手动登录。

只读查看本地是否具备自动刷新条件（需在 skill Base directory 中执行）：

```bash
uv run python scripts/skyeye_sensor_auth.py status
```

检查 state 是否可用（需在 skill Base directory 中执行）：

```bash
uv run python scripts/skyeye_sensor_auth.py --base-url https://<skyeye-sensor-domain> validate
```

如果 `validate` 返回 `valid: true`，继续后续页面操作或 CLI 查询；如果返回 `auth_state_not_found`、`auth_state_expired_or_login_page` 或 `auth_state_load_failed`，再看 `status` 中 `can_auto_refresh` 是否为 `true`。

有已保存账密配置时自动刷新（需在 skill Base directory 中执行）：

```bash
uv run python scripts/skyeye_sensor_auth.py ensure
```

自动刷新成功后继续执行原页面或 CLI 操作；失败才进入下方用户选择流程。

```bash
flocks browser --doctor
```

如果 `flocks browser --doctor` 提示浏览器已运行，但 daemon 或 active browser connection 不可用，先执行 `flocks browser --setup` 触发 attach，不要先要求用户重复勾选 Allow remote debugging。

1. 执行 `flocks browser --setup` 触发交互式 attach，不要用短超时包装该命令。
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果 `--setup` 或 `--doctor` 明确提示 remote debugging 未启用，再提示用户打开 inspect 页面（例如 `chrome://inspect/#remote-debugging` 或 `edge://inspect/#remote-debugging`）并勾选 Allow remote debugging。
4. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。
5. 只有随后 `--doctor` 通过后，才继续后面的登录或页面操作。

当必须询问用户时，说明两种选择并尊重用户偏好：

- 提供账密并保存：调用 `scripts/skyeye_sensor_auth.py`，通过 browser daemon / CDP 驱动真实登录页自动登录。脚本会优先从登录页验证码图片元素动态获取图片、OCR 识别、填入页面、保存 state，并把用户名/密码写入 Flocks secret，把基础配置保存到 `~/.flocks/browser/skyeye-sensor/auth-config.json`。后续 `auth-state.json` 失效时，可在 skill Base directory 中直接执行 `uv run python scripts/skyeye_sensor_auth.py ensure`。
- 不提供账密：沿用原有浏览器登录流程。打开登录页后由用户手动完成登录（含短信验证码 / MFA 等），登录成功后保存 state。

手动登录时，打开登录页并等待用户完成登录：

```bash
flocks browser -c '
tid = new_tab("https://<skyeye-sensor-domain>/login", activate=True)
wait_for_load()
print(tid)
import json
print(json.dumps(page_info(), ensure_ascii=True))
'
```

自动执行上述命令，等待用户登录结束，收到通知后继续：

```bash
# 登录成功后立即保存 state
flocks browser state save ~/.flocks/browser/skyeye-sensor/auth-state.json
```

提供账密并保存时，执行自动登录：

```bash
uv run python scripts/skyeye_sensor_auth.py --base-url https://<skyeye-sensor-domain> ensure --username '<username>' --password '<password>'
```

下次 `auth-state.json` 失效且本地保存过账密配置时，可直接执行：

```bash
uv run python scripts/skyeye_sensor_auth.py ensure
```

也可以在查询命令前直接带账密或使用已保存账密刷新登录态：

```bash
uv run python scripts/skyeye_sensor_cli.py \
  --base-url https://<skyeye-sensor-domain> \
  --username '<username>' \
  --password '<password>' \
  alarm count

uv run python scripts/skyeye_sensor_cli.py --auto-login alarm list
```

如果验证码 OCR、MFA、登录页 DOM 变化或登录成功检测失败，回退到上面的手动登录流程。

### CLI 认证失败时的恢复流程

当 CLI 出现以下任一情况，优先判定为认证问题（**不要立刻要求用户重新登录**）：

- 返回 HTTP `401` / `403`
- 返回内容包含 `Unauthorized`、`login`、未登录、认证失败
- `auth-state.json` 存在，但 CLI 请求仍失败

**恢复步骤（最多尝试 1 次）**：

```bash
# 1) 重新加载 state（强制刷新浏览器会话）
flocks browser state load ~/.flocks/browser/skyeye-sensor/auth-state.json --url "https://<skyeye-sensor-domain>"

# 2) 读取当前页面状态
flocks browser -c '
import json
print(json.dumps(page_info(), ensure_ascii=True))
'
```

```bash
# 3) 根据结果决策
URL=$(flocks browser -c '
import json
info = page_info()
print(info.get("url", ""))
' | tail -n 1)
if [[ "$URL" == *"/login"* ]]; then
  echo "Session 仍无效，需重新登录"
  # → 走上方「首次登录 / 重新登录」流程
else
  flocks browser state save ~/.flocks/browser/skyeye-sensor/auth-state.json
  echo "Session 已恢复，可重试 CLI"
  # → 重试一次 CLI；若仍失败，再走重新登录，不要无限循环
fi
```

---

## 执行约定

CLI 在 skill 内：

`scripts/skyeye_sensor_cli.py`

执行命令时：

1. 必须先进入 `skill_load` 输出的 Base directory，或把命令工具的 `workdir` 设置为该目录。
2. 优先使用 `uv run python scripts/skyeye_sensor_cli.py ...`
3. 认证优先使用浏览器导出的 `auth-state.json`

可选认证环境变量：

- `SKYEYE_SENSOR_BASE_URL=https://<skyeye-sensor-domain>`
- `SKYEYE_SENSOR_AUTH_STATE=~/.flocks/browser/skyeye-sensor/auth-state.json`
- 如有单独 Cookie 文件，可用 `SKYEYE_SENSOR_COOKIE_FILE`
- 没有 state/cookie 时，再考虑 `SKYEYE_SENSOR_CSRF_TOKEN`

## 常用命令

如果已经通过环境变量或 CLI 参数设置好认证信息：

```bash
# 默认输出 JSON
uv run python scripts/skyeye_sensor_cli.py alarm count --days 7
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --page 1 --page-size 10
uv run python scripts/skyeye_sensor_cli.py alarm list --hours 6 --sip 1.1.1.1

# 配合 jq 提取字段
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --hazard-level "3,2" | jq '.items[].threat_name'

# 加 --table 输出格式化表格（人工阅读用）
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --table
```

带认证的完整单行格式（无需提前 export，适合 Windows/macOS/Linux 直接执行）：

```bash
uv run python scripts/skyeye_sensor_cli.py --base-url https://<skyeye-sensor-domain> --auth-state ~/.flocks/browser/skyeye-sensor/auth-state.json alarm list --days 7 --hazard-level "3,2"
```

## 查询策略

- 用户问"最近几天告警数量变化"时，优先 `alarm count`
- 用户问"列出具体告警"时，使用 `alarm list`
- 需要按更多页面字段精确过滤时，先查 `references/API_REFERENCE.md` 中"API Client 已支持"部分

## 常见过滤条件

时间：`--days` / `--hours`

IP 相关：`--sip` `--dip` `--alarm-sip` `--attack-sip`

告警分类：`--hazard-level` `--threat-type` `--attack-type` `--threat-name` `--attack-stage` `--attack-dimension`

处置状态：`--status` `--attack-result` `--host-state` `--is-read` `--user-label`

网络特征：`--proto` `--sport` `--dport` `--host` `--uri` `--xff` `--status-http`

威胁情报：`--ioc` `--attck-org` `--attck` `--alert-rule` `--is-web-attack`

网络标识：`--src-mac` `--dst-mac` `--vlan-id` `--vxlan-id` `--gre-key`

标签与来源：`--marks` `--ip-labels` `--alarm-source`

时间与文件：`--start-update-time` `--end-update-time` `--pcap-filename`

完整参数说明见 [references/API_REFERENCE.md](references/API_REFERENCE.md)。

## 常用查询示例

### 高危及以上告警

```bash
# 最近 7 天，严重 + 高危告警列表
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --hazard-level "3,2"

# 最近 24 小时，高危以上告警数量趋势
uv run python scripts/skyeye_sensor_cli.py alarm count --hours 24 --hazard-level "3,2"
```

### 攻击成功的告警

```bash
# 最近 7 天攻击成功告警
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --attack-result "攻击成功"

# 高危以上 + 攻击成功
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --hazard-level "3,2" --attack-result "攻击成功"

# 统计最近 7 天攻击成功告警数量
uv run python scripts/skyeye_sensor_cli.py alarm count --days 7 --attack-result "攻击成功"
```

### 未处置告警

```bash
# 最近 7 天未处置告警（status=0）
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --status "0"

# 高危以上 + 未处置
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --hazard-level "3,2" --status "0"
```

### 指定 IP 的告警

```bash
# 某 IP 作为流量源 IP 的告警
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --sip "1.1.1.1"

# 某 IP 作为流量目的 IP 的告警
uv run python scripts/skyeye_sensor_cli.py alarm list --days 7 --dip "192.168.1.100"
```

### 按威胁类型 + 主机状态过滤

```bash
# 威胁类型 2、3，主机状态包含失陷（-1）
uv run python scripts/skyeye_sensor_cli.py alarm list \
  --days 7 \
  --threat-type "2,3" \
  --host-state "0,1,2,-1"
```

### 告警数量变化趋势

```bash
# 最近 30 天每天告警数
uv run python scripts/skyeye_sensor_cli.py alarm count --days 30

# 最近 7 天，仅高危以上
uv run python scripts/skyeye_sensor_cli.py alarm count --days 7 --hazard-level "3,2"
```

> 当查询字段或条件不在常用查询字段/示例中，请阅读完整字段列表、CLI 参数说明、查询示例：[references/API_REFERENCE.md](references/API_REFERENCE.md)

## 边界说明

- 如果用户要求"原始日志检索 / Lucene / 字段状态 / expert_model"，这是分析平台能力，切换到 `skyeye-data-fetch`
- 这个 skill 是天眼流量传感器平台适用， CLI只保留 `alarm list` 和 `alarm count`

## 重要提醒

- **Session 管理**：详见[零、登录认证](#零登录认证)。任务开始前先用 `skyeye_sensor_auth.py validate` 确认 `auth-state.json` 可用；CLI 认证失败时先走恢复流程，不要立刻要求用户重新登录。
- **禁止连续失败循环**：同一命令最多重试 2 次；认证恢复流程只走一次，仍失败则提示用户手动重新登录。
   - **以下错误属于需要用户干预的基础设施问题，立即停止所有重试，直接告知用户处理**：
     - `ERR_CERT_AUTHORITY_INVALID`：站点证书不被本机信任，使用--ignore-https-errors 或 请求用户处理。

## 参考文档

- 完整字段列表、CLI 参数说明、查询示例：[references/API_REFERENCE.md](references/API_REFERENCE.md)
