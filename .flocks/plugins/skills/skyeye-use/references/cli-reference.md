# SkyEye CLI 查询总览

执行前必须先进入 `skill_load` 输出的 Base directory，或把命令工具的 `workdir` 设置为该目录。CLI 默认复用 `~/.flocks/browser/skyeye/auth-state.json`，也支持 `--auto-login`、`--username/--password` 触发 OCR 图片验证码自动登录；提供账密后会自动保存到 Flocks secret。若登录页不是默认路径，可传 `--login-path /skyeye/home/login`。

## 查询策略

### 告警类请求

- 已知字段筛选时，优先使用 `alarm list` 或 `alarm count`
- 多条件筛选通过重复传 `--filter key=value`

### 日志类请求

- `log search` 是唯一日志入口
- 只支持 `advance_model`（Lucene 字段匹配）

## 常用查询示例

### 高危及以上告警

```bash
# 最近 7 天高危及以上告警列表
uv run python scripts/skyeye_cli.py alarm list --days 7 --filter hazard_level=3,2

# 最近 24 小时高危告警统计
uv run python scripts/skyeye_cli.py alarm count --hours 24 --filter hazard_level=3,2
```

### 攻击成功的告警

```bash
# 最近 7 天攻击成功告警
uv run python scripts/skyeye_cli.py alarm list --days 7 --filter attack_result=攻击成功

# 高危以上 + 攻击成功
uv run python scripts/skyeye_cli.py alarm list --days 7 \
  --filter hazard_level=3,2 \
  --filter attack_result=攻击成功
```

### 指定 IP 的告警

```bash
# 某 IP 作为攻击 IP 的告警
uv run python scripts/skyeye_cli.py alarm list --days 7 --filter attack_sip=1.1.1.1

# 某 IP 作为受害 IP 的告警
uv run python scripts/skyeye_cli.py alarm list --days 7 --filter alarm_sip=192.168.1.100
```

### 日志检索

```bash
# 搜索某 IP 相关日志
uv run python scripts/skyeye_cli.py log search 'alarm_sip:(10.0.0.1)' --days 1

# 搜索攻击成功的 Web 告警
uv run python scripts/skyeye_cli.py log search \
  'attack_result:(攻击成功) AND threat_type:(WEB攻击)' --days 3
```

### 快速示例

```bash
# 告警明细
uv run python scripts/skyeye_cli.py alarm list --days 1 --filter attack_sip=1.1.1.1

# 告警计数
uv run python scripts/skyeye_cli.py alarm count --days 1 --filter threat_name=暴力破解

# 日志搜索（仅支持高级模式 Lucene 语法）
uv run python scripts/skyeye_cli.py log search 'alarm_sip:(10.0.0.1)' --days 1
uv run python scripts/skyeye_cli.py log search 'attack_result:(攻击成功) AND threat_type:(WEB攻击)' --days 7
```

## 详细参考

- 告警查询详细说明、字段速查、筛选条件模板：
  [alarm-instruction.md](alarm-instruction.md)
- 日志搜索详细说明、Lucene 写法、字段速查、检索模板：
  [log-search-instruction.md](log-search-instruction.md)

## 注意事项

- 这个 skill 面向 SkyEye 分析平台，不是传感器侧接口
- 这个 skill 内的精简 CLI 不包含威胁挖掘、资产、设备和系统接口
