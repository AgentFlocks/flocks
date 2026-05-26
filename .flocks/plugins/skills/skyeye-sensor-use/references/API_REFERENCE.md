# SkyEye Sensor 查询参考

CLI 路径：`./.flocks/skills/skyeye-sensor-data-fetch/scripts/skyeye_sensor_cli.py`

建议在 `./.flocks/skills/skyeye-sensor-data-fetch/scripts` 目录执行，并通过环境变量提供认证信息：

```bash
export SKYEYE_SENSOR_BASE_URL="https://<skyeye-sensor-domain>"
export SKYEYE_SENSOR_AUTH_STATE="$HOME/.flocks/browser/skyeye-sensor/auth-state.json"
```

备选认证方式（无 state 文件时）：

- `SKYEYE_SENSOR_COOKIE_FILE`
- `SKYEYE_SENSOR_CSRF_TOKEN`

## 支持命令

- `alarm list` — 告警明细列表
- `alarm count` — 告警统计

## 快速示例

```bash
# 告警明细
uv run python skyeye_sensor_cli.py alarm list --days 7 --page 1 --page-size 10

# 按条件过滤
uv run python skyeye_sensor_cli.py alarm list --hours 6 --sip "1.1.1.1"

# 告警统计
uv run python skyeye_sensor_cli.py alarm count --days 1 --sip "1.1.1.1"
```

## 适用范围

仅适用于：

- 设备：`流量传感器`
- 视角：`全部`

不适用于：邮件威胁检测系统、文件威胁鉴定器、攻击诱捕系统、服务器安全管理系统等。

当前实现覆盖 `/skyeye/alarm/*` 接口，`api_client.py` 固定带 `data_source=1`。

---

## CLI 支持的检索条件（全量）

`alarm list`、`alarm count` 支持的全部过滤参数：

| CLI 参数 | 接口字段 | 页面中文含义 |
| --- | --- | --- |
| `--days` | `start_time` / `end_time` | 最近 N 天 |
| `--hours` | `start_time` / `end_time` | 最近 N 小时 |
| `--hazard-level` | `hazard_level` | 威胁级别（3=严重 2=高危 1=中危 0=低危） |
| `--threat-type` | `threat_type` | 威胁类型 ID |
| `--host-state` | `host_state` | 主机状态 / 处置状态 |
| `--user-label` | `user_label` | 用户标签 / 已标记 |
| `--attack-result` | `attack_result` | 攻击结果 |
| `--status` | `status` | 处理状态 |
| `--sip` | `sip` | 源 IP（流量层） |
| `--dip` | `dip` | 目的 IP（流量层） |
| `--alarm-sip` | `alarm_sip` | 受害 IP |
| `--attack-sip` | `attack_sip` | 攻击 IP |
| `--attack-type` | `attack_type` | 告警类型 |
| `--ioc` | `ioc` | IOC / 规则 ID |
| `--threat-name` | `threat_name` | 威胁名称 |
| `--attack-stage` | `attack_stage` | 攻击阶段 |
| `--proto` | `proto` | 协议 |
| `--xff` | `x_forwarded_for` | XFF 代理 |
| `--attack-dimension` | `attack_dimension` | 攻击维度 |
| `--is-web-attack` | `is_web_attack` | 是否 WEB 攻击 |
| `--host` | `host` | 域名 / Host |
| `--status-http` | `status_http` | HTTP 状态码 |
| `--attck-org` | `attck_org` | ATT&CK 攻击组织 |
| `--attck` | `attck` | ATT&CK 技战术 |
| `--uri` | `uri` | URI |
| `--alert-rule` | `alert_rule` | 告警规则 |
| `--is-read` | `is_read` | 是否已读 |
| `--sport` | `sport` | 源端口 |
| `--dport` | `dport` | 目的端口 |
| `--src-mac` | `src_mac` | 源 MAC 地址 |
| `--dst-mac` | `dst_mac` | 目的 MAC 地址 |
| `--vlan-id` | `vlan_id` | VLAN |
| `--vxlan-id` | `vxlan_id` | VXLAN |
| `--gre-key` | `gre_key` | GRE KEY |
| `--marks` | `marks` | 告警标签 |
| `--ip-labels` | `ip_labels` | IP 资产标签 |
| `--start-update-time` | `start_update_time` | 规则更新时间起（毫秒时间戳） |
| `--end-update-time` | `end_update_time` | 规则更新时间止（毫秒时间戳） |
| `--alarm-source` | `alarm_source` | 告警来源 |
| `--pcap-filename` | `pcap_filename` | PCAP 文件名 |
| `--order-by` | `order_by` | 排序字段（仅 alarm list） |
| `--accurate` | `is_accurate=1` | 精确匹配（仅 alarm list） |

### 典型查询示例

```bash
# 最近 7 天，某源 IP 的告警
uv run python skyeye_sensor_cli.py alarm list --days 7 --sip "1.1.1.1"

# 最近 24 小时，高危 + 严重告警
uv run python skyeye_sensor_cli.py alarm list --hours 24 --hazard-level "3,2"

# 指定威胁类型 + 主机状态
uv run python skyeye_sensor_cli.py alarm list \
  --days 7 \
  --threat-type "2,3" \
  --host-state "0,1,2,-1"

# 只统计数量
uv run python skyeye_sensor_cli.py alarm count --days 1 --sip "1.1.1.1"
```

---

## 页面字段全量对照表

对应页面告警列表的筛选条件（以截图顺序为准）：

| 页面中文字段 | 接口字段名 | CLI 参数 |
| --- | --- | --- |
| 时间 | `start_time` / `end_time` | `--days` / `--hours` |
| 受害 IP | `alarm_sip` | `--alarm-sip` |
| 攻击 IP | `attack_sip` | `--attack-sip` |
| 告警类型 | `attack_type` | `--attack-type` |
| 威胁级别 | `hazard_level` | `--hazard-level` |
| 攻击结果 | `attack_result` | `--attack-result` |
| 处理状态 | `status` | `--status` |
| IOC / 规则 ID | `ioc` | `--ioc` |
| 威胁名称 | `threat_name` | `--threat-name` |
| 攻击阶段 | `attack_stage` | `--attack-stage` |
| 协议 | `proto` | `--proto` |
| XFF 代理 | `x_forwarded_for` | `--xff` |
| 攻击维度 | `attack_dimension` | `--attack-dimension` |
| WEB 攻击 | `is_web_attack` | `--is-web-attack` |
| 域名 / Host | `host` | `--host` |
| HTTP 状态码 | `status_http` | `--status-http` |
| 攻击组织 | `attck_org` | `--attck-org` |
| ATT&CK 技术 | `attck` | `--attck` |
| URI | `uri` | `--uri` |
| 告警规则 | `alert_rule` | `--alert-rule` |
| 是否已读 | `is_read` | `--is-read` |
| 源 IP | `sip` | `--sip` |
| 目的 IP | `dip` | `--dip` |
| 源端口 | `sport` | `--sport` |
| 目的端口 | `dport` | `--dport` |
| 源 MAC 地址 | `src_mac` | `--src-mac` |
| 目的 MAC 地址 | `dst_mac` | `--dst-mac` |
| VLAN | `vlan_id` | `--vlan-id` |
| VXLAN | `vxlan_id` | `--vxlan-id` |
| GRE KEY | `gre_key` | `--gre-key` |
| 告警标签 | `marks` | `--marks` |
| IP 资产标签 | `ip_labels` | `--ip-labels` |
| 规则更新时间 | `start_update_time` / `end_update_time` | `--start-update-time` / `--end-update-time` |
| 告警来源 | `alarm_source` / `alert_source` | `--alarm-source` |
| PCAP 文件名 | `pcap_filename` | `--pcap-filename` |

其他 API 已支持字段（页面无对应筛选项，但可编程过滤）：

| 接口字段名 | 说明 |
| --- | --- |
| `alarm_id` | 告警 ID |
| `file_name` | 文件名 |
| `file_md5` | 文件 MD5 |
| `file_type` | 文件类型 |
| `pcap_id` | 报文文件 ID |
| `user_label` | 用户标签（已标记） |

---

## 直接调用 API Client 示例

当需要使用"CLI 未暴露"的字段时，直接调用 Python API：

```python
from api_client import SkyeyeSensorClient

client = SkyeyeSensorClient()

# 按受害IP + 告警类型 + 攻击结果过滤
result = client.get_alarm_list(
    days=7,
    page=1,
    page_size=20,
    alarm_sip="192.168.1.100",
    attack_type="代码执行",
    attack_result="攻击成功",
)
print(result)
```

```python
# 按威胁名称、URI、协议过滤
result = client.get_alarm_list(
    days=7,
    threat_name="webshell管理工具",
    uri="/login",
    proto="http",
    attack_sip="1.1.1.1",
)
print(result)
```

```python
# 按端口过滤
result = client.get_alarm_list(
    days=7,
    sport="45332",
    dport="8080",
)
print(result)
```

```python
# 文件相关告警
result = client.get_alarm_list(
    days=7,
    file_name="cmd.exe",
    file_md5="d41d8cd98f00b204e9800998ecf8427e",
    file_type="exe",
)
print(result)
```

---

## 字段值域说明

| 字段 | 常见值 / 说明 |
| --- | --- |
| `hazard_level` | 逗号拼接，如 `3,2,1,0`（3=严重，2=高危，1=中危，0=低危） |
| `threat_type` | ID 串，不一定是中文文案 |
| `host_state` | 枚举编码，多值逗号分隔 |
| `user_label` | 常见值为 `1` |
| `attack_result` | 如 `攻击成功`、`攻击失败`、`0`（数值或字符串视版本而定） |
| `attack_type` | 如 `代码执行`、`webshell上传`、`僵尸网络` 等，动态适配 |
| `is_read` | `0`=未读，`1`=已读 |
| `is_web_attack` | `0`=否，`1`=是 |

## 推荐查询顺序

1. 先判断字段是否在"CLI 直接支持"列表
2. 如支持，直接用 `skyeye_sensor_cli.py alarm list / alarm count`
3. 如不支持，但字段在全量对照表中，直接调用 `api_client.py`
4. 如字段两处都没有，先补代码再查，不要猜

## 维护说明

如需把 API 已支持字段暴露到 CLI，修改：

- `skyeye_sensor_cli.py` 中的 `build_alarm_filters()`
- `skyeye_sensor_cli.py` 里 `alarm list` / `alarm count` 的 `click.option()`
