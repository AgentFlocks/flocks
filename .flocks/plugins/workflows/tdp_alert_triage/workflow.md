# NDR/TDP 告警调查工作流

## 业务场景

对 NDR/TDP 告警进行自动化研判分析。当前工作流重点兼容 TDP 检索结果这类嵌套结构输入，例如顶层 `data` 数组、`net.http` 请求响应字段以及 `threat` 判定信息，并生成结构化分析报告。

## 流程步骤

### 1. 接收告警数据
- **描述**: 接收并解析 NDR/TDP 告警或网络流量日志，提取关键字段（源IP、目的IP、端口、协议、HTTP 请求/响应、IOC 等）
- **工具/模型**: Tool-driven
- **输入**: `alert_data` - 告警 JSON 数据，支持扁平结构或 TDP 风格的 `{ "data": [ ... ] }`
- **输出**: `parsed_alert` - 解析后的告警数据字典
- **处理逻辑**:
  - 自动展开顶层 `data[0]`，兼容直接传入单条告警
  - 优先从 `threat`、`net.http`、`attacker/victim`、`external_ip/server_ip` 等 TDP 字段提取 src/dst、URL、请求与响应
  - 从 `threat.msg`、`threat.tag` 等字段中识别标准漏洞编号（如 `CVE-*`）
  - 提取 IOC（IP、域名、URL），并保留原始告警用于后续分析

### 2. 威胁情报查询（并行）
- **描述**: 使用多源威胁情报查询告警中涉及的外部 IP、域名、URL 等指标
- **工具/模型**: Tool-driven
- **输入**: `parsed_alert` - 解析后的告警数据
- **输出**: `intel_results` - 威胁情报查询结果汇总
- **处理逻辑**:
  - 遍历告警中的 IOC（IP、域名、URL）
  - 自动去重，并跳过内网/保留地址，避免对 `127.0.0.1`、RFC1918 地址做无意义情报查询
  - 使用 `threatbook_ip_query`、`threatbook_domain_query`、`threatbook_url_query` 查询
  - 使用 `virustotal_ip_query`、`virustotal_domain_query`、`virustotal_url_query` 做补充查询
  - 汇总所有情报结果

### 3. 漏洞信息查询（并行）
- **描述**: 仅在识别到标准漏洞编号时查询漏洞信息（CVE/CNVD/CNNVD/XVE）
- **工具/模型**: Tool-driven
- **输入**: `parsed_alert` - 可能包含漏洞ID
- **输出**: `vuln_info` - 漏洞详细信息
- **处理逻辑**:
  - 从 `threat.msg`、`threat.tag`、URL 等文本中提取漏洞ID（如 `CVE-2021-xxx`）
  - 仅当存在标准漏洞编号时才调用 `__mcp_vuln_query`
  - 获取漏洞描述、影响产品、修复方案、POC 等信息
  - 无漏洞ID时返回空结果

### 4. 攻击负载分析（并行）
- **描述**: 使用 LLM 分析 HTTP 请求负载，识别攻击/扫描手法与意图
- **工具/模型**: LLM-driven
- **输入**: `parsed_alert` - 包含 payload
- **输出**: `payload_analysis` - 攻击负载分析结果
- **处理逻辑**:
  - 提取 HTTP 请求行、请求头、请求体
  - 使用 LLM 分析该流量更像攻击、扫描、误报还是正常请求
  - 识别具体攻击/扫描方式和意图
  - **必须落盘**: 将 LLM 分析结果写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/artifacts/payload_analysis_llm_output.md`

### 5. 响应包分析与攻击成功判定（并行）
- **描述**: 结合服务器响应包和 TDP 判定字段，判断攻击是否成功
- **工具/模型**: LLM-driven
- **输入**: `parsed_alert` - 包含请求和响应
- **输出**: `response_analysis` - 响应分析结果, `attack_success` - 攻击是否成功
- **处理逻辑**:
  - 提取请求包和响应包内容
  - 将 `HTTP status`、`threat.result`、`threat.failed_by` 一并作为判定信号
  - 优先让 LLM 结构化输出成功/失败结论，解析失败时再使用规则兜底
  - **必须落盘**: 将分析结果写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/artifacts/response_analysis_llm_output.md`

### 6. 汇聚并行结果
- **描述**: 使用 `join=true` 等待并行节点全部完成，再把结果归一化后传给报告节点
- **工具/模型**: Tool-driven
- **输入**: `intel_results`、`vuln_info`、`payload_analysis`、`response_analysis`、`attack_success`
- **输出**: 归一化后的统一上下文
- **处理逻辑**:
  - 等待 4 个并行节点全部完成
  - 透传并规整报告节点所需字段
  - 避免多个并行分支直接汇聚到写文件节点，满足 workflow 引擎约束

### 7. 生成分析报告
- **描述**: 综合以上分析结果，生成结构化分析报告
- **工具/模型**: LLM-driven
- **输入**: 所有前序步骤的输出（intel_results, vuln_info, payload_analysis, response_analysis, attack_success）
- **输出**: `final_report` - 完整分析报告
- **处理逻辑**:
  - 汇总情报查询结果
  - 汇总漏洞信息
  - 汇总攻击负载分析和响应分析
  - 根据 `attack_success` 和 TDP 失败信号生成风险等级
  - 生成结构化报告，包含：摘要、IOC、情报、漏洞、分析、风险评估、建议
  - **必须落盘**: 将报告写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/artifacts/final_report.md`

## 并行执行设计

步骤 2、3、4、5 为并行节点，同时执行以提升效率：
- query_threat_intel: 威胁情报查询
- query_vuln: 漏洞信息查询
- analyze_payload: 攻击负载分析
- analyze_response: 响应包分析与攻击成功判定

所有并行节点执行完成后，先汇聚到 `join_results`，再进入 `generate_report` 生成最终报告。

## 报告结构

### 执行摘要
- 告警概述
- 主要发现
- 风险等级

### 详细分析
- 告警详情
- 威胁情报结果
- 漏洞信息（如有）
- 攻击负载分析
- 响应分析

### 关键发现
- IOC 列表
- 攻击手法描述
- 是否成功判定

### 风险评估
- 风险等级
- 影响范围

### 建议与行动项
- 紧急处置建议
- 长期加固建议
- 需要关联分析的系统
