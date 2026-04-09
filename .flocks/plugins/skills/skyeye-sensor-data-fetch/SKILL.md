---
name: skyeye-sensor-data-fetch
description: "Uses the lightweight SkyEye Sensor CLI to query alert lists and alert statistics on the sensor side. Use it when the user mentions \"SkyEye Sensor\" or Tianyan/SkyEye traffic-sensor alert scenarios."
description_cn: "使用天眼 SkyEye Sensor 传感器侧精简 CLI 查询告警列表和告警统计。适用于用户提到“SkyEye Sensor”或天眼流量传感器告警相关场景。"
---

# SkyEye Sensor 数据查询

> 如果是分析平台日志检索，不要用这个 skill，改用 `skyeye-data-fetch`。

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

State 文件路径：`~/.flocks/browser/skyeye-sensor/auth-state.json`（固定，全局唯一）。

### 首次登录 / Session 过期重新登录

```bash
# 用 --headed 打开浏览器，人工完成登录（含短信验证码/MFA等）
agent-browser close
agent-browser --headed open "https://<skyeye-sensor-domain>/login"
```

自动执行上述命令，等待用户登录结束，收到通知后继续：

```bash
# 登录成功后立即保存 state
agent-browser state save ~/.flocks/browser/skyeye-sensor/auth-state.json
```

### CLI 认证失败时的恢复流程

当 CLI 出现以下任一情况，优先判定为认证问题（**不要立刻要求用户重新登录**）：

- 返回 HTTP `401` / `403`
- 返回内容包含 `Unauthorized`、`login`、未登录、认证失败
- `auth-state.json` 存在，但 CLI 请求仍失败

**恢复步骤（最多尝试 1 次）**：

```bash
# 1) 重新加载 state（强制刷新浏览器会话）
agent-browser close
agent-browser state load ~/.flocks/browser/skyeye-sensor/auth-state.json

# 2) 访问受保护页面验证
agent-browser open "https://<skyeye-sensor-domain>"
agent-browser wait --load networkidle

# 3) 根据结果决策
URL=$(agent-browser get url)
if [[ "$URL" == *"/login"* ]]; then
  echo "Session 仍无效，需重新登录"
  # → 走上方「首次登录 / 重新登录」流程
else
  agent-browser state save ~/.flocks/browser/skyeye-sensor/auth-state.json
  echo "Session 已恢复，可重试 CLI"
  # → 重试一次 CLI；若仍失败，再走重新登录，不要无限循环
fi
```

---

## 执行约定

CLI 在 skill 内：

`scripts/skyeye_sensor_cli.py`

执行命令时：

1. 优先使用 `uv run python scripts/skyeye_sensor_cli.py ...`
2. 认证优先使用浏览器导出的 `auth-state.json`

认证环境变量：

- `SKYEYE_SENSOR_BASE_URL=https://<skyeye-sensor-domain>`
- `SKYEYE_SENSOR_AUTH_STATE=~/.flocks/browser/skyeye-sensor/auth-state.json`
- 如有单独 Cookie 文件，可用 `SKYEYE_SENSOR_COOKIE_FILE`
- 没有 state/cookie 时，再考虑 `SKYEYE_SENSOR_CSRF_TOKEN`

## 常用命令

如果已经通过 `export` 设置好环境变量：

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

带认证的完整单行格式（无需提前 export，适合直接执行）：

```bash
SKYEYE_SENSOR_BASE_URL=https://<skyeye-sensor-domain> \
SKYEYE_SENSOR_AUTH_STATE=~/.flocks/browser/skyeye-sensor/auth-state.json \
uv run python scripts/skyeye_sensor_cli.py \
  alarm list --days 7 --hazard-level "3,2" --json
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

- **Session 管理**：详见[零、登录认证](#零登录认证)。任务开始前先确认 `auth-state.json` 存在；CLI 认证失败时先走恢复流程，不要立刻要求用户重新登录。
- **禁止连续失败循环**：同一命令最多重试 2 次；认证恢复流程只走一次，仍失败则提示用户手动重新登录。
   - **以下错误属于需要用户干预的基础设施问题，立即停止所有重试，直接告知用户处理**：
     - `ERR_CERT_AUTHORITY_INVALID`：站点证书不被本机信任，使用--ignore-https-errors 或 请求用户处理。

## 参考文档

- 完整字段列表、CLI 参数说明、查询示例：[references/API_REFERENCE.md](references/API_REFERENCE.md)
