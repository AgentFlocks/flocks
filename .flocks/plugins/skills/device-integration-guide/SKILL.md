---
name: device-integration-guide
description: 指导 Flocks 新建、添加和接入安全设备。Use when the user asks to create, add, onboard, or connect a new security device.
---

# Device Integration Guide

用于处理 Flocks 设备接入相关对话。目标是把用户带到正确路径：设备创建、配置写入和敏感凭证走设备接入页面。

## 适用场景

当用户提到以下意图时使用本 skill：

- 新建设备实例。
- 添加安全设备到 Flocks。
- 接入一个还没有出现在设备列表里的安全设备。
- 用户想把一个没有现成模板的安全设备做成 Flocks 可用设备。

## 核心原则

- 先确认用户是在**新建设备实例**、**页面配置**，还是**测试连通性**。
- 不要要求用户在聊天里粘贴密码、Token、Cookie、API Key 等敏感凭证。
- 独立会话中，已安装模板的新设备优先通过 `device_manage(action="create")` 创建；只有敏感凭证需要回到设备接入页面填写。
- 每次修改后，优先用标准连通性测试验证结果。
- 保持回答简短，给出当前动作、结果和下一步。

## 决策流程

1. 已有 `device_id`：启停设备或更新模板声明的非密码字段时使用 `device_manage(action="update")`，测试或排障用 `connectivity_test`；密码字段回到设备接入页面填写。
2. 没有 `device_id`：先用 `device_manage(action="list")` 排除已有实例，再调用 `device_manage(action="list_templates")` 查询模板。设备实例为空不代表模板不存在。
3. 按名称、厂商、`plugin_id`、`service_id`、`storage_key` 和描述匹配模板：
   - `installed=true`：按 `credential_schema` 整理非敏感字段，然后进入创建流程。
   - `installed=false`：引导用户在 FlockHub 安装返回的 `plugin_id`，安装后重新查询；不要创建自定义模板。
   - 没有匹配模板：进入自定义 API、浏览器或 Workflow 接入。

## 使用模板直接创建设备

只有 `list_templates` 已返回匹配模板且 `installed=true` 时，才能调用 `device_manage(action="create")`。`create` 会再次校验模板状态，不能用 `update` 代替创建。

从模板收集设备名称、机房、SSL 偏好和已声明的非敏感字段；不询问或传递 `storage=secret`、`sensitive=true`、`input_type=password` 的字段。

创建成功后：

- 记录并报告返回的 `device_id`，后续操作始终使用它。
- 如果返回 `sensitive_fields_to_complete`，告诉用户前往该设备的配置表单填写这些字段，不要在聊天中索要真实值。
- 用户确认敏感字段已填写后，调用 `device_manage(action="connectivity_test", device_id="<device_id>")`。
- 同一轮会话已经拿到成功返回的 `device_id` 后，不要因重试或继续对话再次创建。

## 自定义接入路由

没有合适已安装模板时，按用户描述选择路径：

- 设备提供 API 文档或开放接口：选择「API 接入」。需要创建 device 插件时，先使用 `tool-builder`，目标是设备插件，不是普通 API 服务。
- 设备主要通过 Web 控制台操作，没有开放 API：选择「浏览器接入」。需要捕获页面能力时，先使用 `web2cli`，生成可维护的设备能力。
- 数据通过 Syslog、Kafka 或 Webhook 上报：选择「Workflow 接入」。不要创建 device 插件，引导用户走工作流发布/接入配置。

如果用户已经明确选择 API、浏览器或 Workflow，不要重复询问接入方式。只有无法判断时，才用一句话澄清。

## 页面配置与更新

如果用户正在设备接入页面配置设备，帮助确认需要填写的表单字段，让页面负责保存。独立会话中的已有设备使用 `device_manage(action="update")`：设备启停通过一级参数 `enabled` 更新，`fields` 只能包含目标模板 `credential_schema` 声明的字段。

`fields` 的具体可更新项以目标模板为准，常见字段包括：

- `base_url`
- `host`
- `port`
- `scheme`
- `timeout`
- `tenant`
- `region`

模板没有声明的字段不要传入 `fields`。

不要在聊天中索要或回显敏感字段：

- `api_key`
- `secret`
- `password`
- `token`
- `cookie`

如果用户的目标是补填密钥、修改密码、刷新 Token 或重新登录，只说明应该在设备接入页面对应字段中处理。

不要把 `enabled` 写入 `fields`。密码及 `input_type=password` 的字段不通过工具更新，应在页面表单内填写。

## 连通性与冒烟验证

设备通过 `create` 返回 `device_id`，或在设备接入页面保存后，除非用户明确不需要，继续调用：

```python
device_manage(action="connectivity_test", device_id="<device_id>")
```

连通性测试成功后，再选择少量只读、低风险的设备工具做基础冒烟验证。必须继续使用同一个 `device_id`。不要为了验证而执行写操作或高风险操作。

完成后汇报：

- 目标设备和 `device_id`。
- 页面中已整理或保存的字段名，不回显敏感值。
- 标准连通性测试结果。
- 只读冒烟验证结果。

## 失败排查顺序

连通性或冒烟失败时，按最小排查顺序给建议：

1. 地址或端口是否正确，Base URL 是否包含协议。
2. 设备侧网络、代理、防火墙或白名单是否允许 Flocks 访问。
3. `verify_ssl` 是否与设备证书状态匹配。
4. 页面里的凭证字段是否已填写且权限足够。
5. 设备版本、模板版本或工具集是否匹配。
6. 如果是浏览器接入，登录态是否过期，是否需要用户重新完成验证码、MFA 或人工确认。

## 不要做

- 不要在聊天中索要、保存或复述真实密钥。
- 不要把自定义设备误做成普通 API 服务。
- 不要跳过 `device_manage(action="connectivity_test")` 就声称设备已可用。
- 不要把卡片状态建立在普通业务工具结果上；卡片状态以标准连通性测试写入结果为准。
