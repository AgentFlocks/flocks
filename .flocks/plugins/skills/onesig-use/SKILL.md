---
name: onesig-use
description: 用于处理 OneSIG（安全互联网网关 / Secure Internet Gateway）相关任务，适合通过 API 或者结合浏览器进行以下任务：威胁监控（仪表盘、防护大屏、失陷主机、入站/出站威胁事件、报告管理）、防护策略（全局白/黑名单、多维封锁、IPS、HTTP 黑名单、高危端口防护、API 联动、Syslog 自动封禁、FTP/SFTP 联动）、资产管理、平台管理（告警/审计/用户、HTTPS 解密、网口路由 DNS、HA、OneCC、设备升级与备份、license、MDR、诊断）、登录会话与改密、帮助文档。只要用户提到 OneSIG、SIG、安全互联网网关、微步互联网网关等相关操作时，必须先加载本 skill。本 skill 是 OneSIG 平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `onesig_*` tool。
---

# OneSIG Use

## First

操作模式 API V.S Browser

### 何时使用 API

- 默认模式，默认使用 API
- !!! important: 如果已经进入了浏览器模式，就不要走 API 了
- 查询类请求与处置 / 配置写入类请求要严格区分；用户没有明确要求执行写操作时，默认只使用只读查询能力

### 何时使用浏览器

- 现有 API 没有覆盖目标能力
- 未检测到对应 API 工具
- API 当前不可用，例如未配置、未开通、无权限、认证失败、Cookie 过期、SSL 验证失败或服务不可达
- 任务必须查看页面级详情、攻击链、威胁图、报表预览或人工确认弹窗
- 页面需要人工登录、图形验证码、TOTP、强制改密或页面级确认
- 用户明确要求使用浏览器，或者已经在浏览器操作过程中

### 请求确认

除非是用户要求使用浏览器，否则提示用户 API 不可用，请检查 API 配置或直接使用浏览器模式。

当确定操作模式后：

- API 模式：请阅读 API 模式使用指南
- 浏览器模式：请阅读浏览器模式使用指南

## API 模式使用指南

OneSIG 一共 6 个 grouped tool，按用户语义分流：

- 用户说"仪表盘""威胁防护大屏""失陷主机""入站/出站威胁事件""设备状态""报告""导出报表"时，优先走 `onesig_monitoring`
- 用户说"白名单""黑名单""多维封锁""API 联动密钥""Syslog 自动封禁""FTP/SFTP 联动""IPS 规则""HTTP 黑名单""端口防护组""一键 bypass""防护策略"时，优先走 `onesig_strategy`
- 用户说"资产""资产组""资产类型""资产导入/导出"时，优先走 `onesig_assets`
- 用户说"告警/通知配置""管理日志/审计""用户管理/登录策略""HTTPS 解密""网口/部署引导""路由 / DNS / 代理""HA 高可用""集中管控/OneCC""设备升级/重启/备份/恢复""日志外发""license""MDR""coredump""pcap""设备诊断"时，优先走 `onesig_device`
- 用户说"登录""退出""改密""账户信息""图形验证码""TOTP""恢复码""产品动态红点"时，优先走 `onesig_login`
- 用户说"帮助文档""产品版本""提交问题反馈"时，优先走 `onesig_helper`

OneSIG 与 OneSEC 在调用约定上有几个**关键差异**，agent 不要混用：

- 时间字段是 `startTime` / `endTime`（不是 OneSEC 的 `time_from` / `time_to`），单位 **Unix 秒**
- 分页字段是 `pageNo` / `pageSize`（不是 `cur_page` / `page_size`，也不是 `page_items_num`）
- OneSIG 主要走 **Cookie 会话** + RSA-OAEP 加密密码登录，处理器会自动登录、会话过期会自动重登；只有当设备启用了图形验证码或 TOTP 时才需要显式调 `onesig_login` 的 `login` 动作，并把 `captcha` / `totp` 作为入参传入
- 每个 grouped tool 的 action 除了 `action` 都按业务键平铺；POST 类 action 多数走 passthrough body，把所有筛选 / 分页字段直接放在请求体顶层即可

高风险写操作要特别谨慎，例如：

- 设备级动作：`device_onekey_bypass` / `device_quick_bypass`（流量直通）、`device_reboot` / `device_shutdown` / `device_reinit`、`device_upgrade` / `device_upgrade_upload` / `system_upgrade`
- 用户与口令：`user_create` / `user_delete` / `user_update` / `user_secret_reset`、`change_password`、`regenerate_recovery_code`
- 黑白名单与封禁：`whitelist_*` / `blacklist_*` 系列写操作、`multiblock_rule_create` / `multiblock_rule_update` / `multiblock_rule_delete` / `multiblock_rule_active`、`auto_blacklist_create` / `auto_blacklist_update` / `auto_blacklist_delete`
- IPS / HTTP / 端口防护：`ips_rule_create` / `ips_rule_apply` / `ips_rule_all`、`ips_ruleset_create` / `ips_ruleset_update` / `ips_ruleset_delete`、`http_blacklist_*` 写操作、`port_protect_group_*` / `port_protect_port_*` 写操作、`protection_policy_update` / `protection_policy_delete`
- HTTPS 解密：`tls_decrypt_policy_create` / `_update` / `_enable` / `_delete` / `_batch`、`tls_cert_create` / `_update` / `_delete` / `_set_default`、`tls_detect_delete` / `set_decrypt_config`
- 网口 / 路由 / 网络：`interface_update`（涉及启停时需 password）、`interface_*_create/update/delete`（virtualLine / listen / bridge）、`route_static_*` / `ipv6_route_static_*` 写操作、`set_dns_config` / `set_proxy_config`、`hosts_create/update/delete`
- 高可用 / 集中管控：`set_ha_config` / `ha_switching` / `ha_sync_config`、`set_onecc_config` / `set_onecc_status`
- 备份与升级：`backup_create` / `backup_recover` / `backup_delete` / `backup_import` / `backup_update`、`device_download_package`
- 离线情报库 / 授权：`basic_information_import` / `basic_information_enable`、`basic_license_upload`、`mdr_service_enable`
- 诊断写操作：`device_pcap_set`、`device_coredump_delete` / `device_pcap_file_delete`
- 报表与日志外发：`report_form_create` / `report_form_delete`、`report_task_create` / `_update` / `_delete` / `_test`、`logaccess_create` / `_update` / `_delete`、`set_dnslog_config`、`set_clean_config` / `set_storage_config`
- API Key 与密钥：`apikey_create` / `apikey_update` / `apikey_delete`、`apikey_secret`（会回吐明文 secret，需当前用户密码二次校验）

必须阅读：

各 grouped tool 与 action 的详细说明、必填参数、最小调用示例见 [references/api-reference.md](references/api-reference.md)。

## 浏览器模式使用指南

- ⚠️ 如果 OneSIG 设备的访问地址不清楚，请先询问用户，不要擅自填写域名。
- ⚠️ 用 `--headed` 打开浏览器，人工完成登录（OneSIG 多数部署启用了图形验证码 / TOTP / 强制改密策略）。
- ⚠️ OneSIG 控制台与 OneSEC / 青藤是不同产品；不要把 OneSEC 的页面路径或 OneSEC 的 API 套用到 OneSIG。

只要进入浏览器模式，就请阅读并按照 browser-workflow 操作，不要直接跳过本 skill 去套用其他通用浏览器 skill。

请严格按照以下文档执行：

- [references/browser-workflow.md](references/browser-workflow.md)
