import random
from datetime import datetime, timezone

DASHBOARD = {
    "securityScore": 85,
    "scoreTrend": [82, 84, 81, 86, 85, 83, 85],
    "alertsToday": {"total": 47, "critical": 5, "high": 12, "medium": 18, "low": 12},
    "investigations": {"active": 8, "resolved": 119, "total": 127},
    "mttr": {"value": 23, "unit": "min", "trend": "down"},
    "mttd": {"value": 4, "unit": "min", "trend": "down"},
    "killChain": [
        {"phase": "侦察", "active": 12, "blocked": 45, "color": "#6366f1"},
        {"phase": "初始入侵", "active": 5, "blocked": 32, "color": "#ef4444"},
        {"phase": "执行", "active": 3, "blocked": 28, "color": "#f97316"},
        {"phase": "持久化", "active": 2, "blocked": 19, "color": "#eab308"},
        {"phase": "C2 通信", "active": 4, "blocked": 15, "color": "#8b5cf6"},
        {"phase": "横向移动", "active": 2, "blocked": 11, "color": "#06b6d4"},
        {"phase": "数据渗出", "active": 0, "blocked": 7, "color": "#ec4899"},
    ],
    "activeCases": [
        {"id": "CS-0701-001", "title": "疑似 Webshell + 内网横向", "host": "web-prod-01", "ip": "10.10.15.22",
         "progress": 77, "severity": "critical", "checklist": "48/62", "elapsed": "25min"},
        {"id": "CS-0702-002", "title": "DNS 隧道 + 数据外传", "host": "db-replica-02", "ip": "10.10.12.88",
         "progress": 19, "severity": "high", "checklist": "12/62", "elapsed": "3h12min"},
        {"id": "CS-0702-003", "title": "异常 SSH 暴力破解", "host": "app-backend-03", "ip": "10.10.8.45",
         "progress": 45, "severity": "medium", "checklist": "28/62", "elapsed": "1h45min"},
        {"id": "CS-0702-004", "title": "可疑 PowerShell 执行", "host": "dc-admin-01", "ip": "10.10.3.201",
         "progress": 31, "severity": "high", "checklist": "19/62", "elapsed": "45min"},
    ],
    "deviceHealth": [
        {"name": "NDR 流量检测", "id": "ndr", "health": 85, "status": "healthy", "alerts24h": 23, "latency": "12ms", "uptime": "99.97%",
         "color": "#6366f1"},
        {"name": "HIDS 主机安全", "id": "hids", "health": 72, "status": "warning", "alerts24h": 15, "latency": "45ms", "uptime": "99.82%",
         "color": "#10b981", "warning": "Agent 覆盖率 72% · 3 台主机未部署"},
        {"name": "防火墙", "id": "firewall", "health": 90, "status": "healthy", "alerts24h": 8, "latency": "5ms", "uptime": "99.99%",
         "color": "#8b5cf6"},
        {"name": "EDR 终端响应", "id": "edr", "health": 78, "status": "healthy", "alerts24h": 19, "latency": "28ms", "uptime": "99.91%",
         "color": "#06b6d4"},
    ],
    "hotTargets": [
        {"ip": "10.10.15.22", "hostname": "web-prod-01", "attacks": 47, "severity": "critical", "trend": "up"},
        {"ip": "10.10.12.88", "hostname": "db-replica-02", "attacks": 32, "severity": "high", "trend": "up"},
        {"ip": "10.10.8.45", "hostname": "app-backend-03", "attacks": 28, "severity": "medium", "trend": "stable"},
        {"ip": "10.10.3.201", "hostname": "dc-admin-01", "attacks": 21, "severity": "high", "trend": "down"},
        {"ip": "10.10.19.7", "hostname": "file-srv-05", "attacks": 15, "severity": "medium", "trend": "stable"},
    ],
    "recentIocs": [
        {"type": "ip", "value": "109.233.42.1", "label": "C2 服务器", "threat": "critical", "status": "blocked"},
        {"type": "domain", "value": "update.remote-c2.com", "label": "C2 域名 DGA", "threat": "critical", "status": "blocked"},
        {"type": "ip", "value": "198.51.100.23", "label": "C2 服务器", "threat": "high", "status": "monitoring"},
        {"type": "hash", "value": "a1b2c3d4e5f6", "label": "木马 MD5", "threat": "high", "status": "signatured"},
        {"type": "url", "value": "http://185.220.101.x/payload", "label": "载荷分发 URL", "threat": "high", "status": "investigating"},
        {"type": "file", "value": "/tmp/.sysupdate", "label": "后门文件", "threat": "critical", "status": "quarantined"},
    ],
    "timeline": [
        {"time": "08:12", "event": "NDR 外联告警", "target": "10.10.15.22", "severity": "critical", "caseId": "CS-0701-001"},
        {"time": "08:15", "event": "HIDS Webshell 创建", "target": "10.10.15.22", "severity": "critical", "caseId": "CS-0701-001"},
        {"time": "08:23", "event": "EDR 横向移动告警", "target": "10.10.15.35", "severity": "critical", "caseId": "CS-0701-001"},
        {"time": "09:05", "event": "NDR DNS 异常检测", "target": "10.10.12.88", "severity": "high", "caseId": "CS-0702-002"},
        {"time": "10:22", "event": "HIDS SSH 暴力破解", "target": "10.10.8.45", "severity": "medium", "caseId": "CS-0702-003"},
        {"time": "11:08", "event": "EDR PowerShell 告警", "target": "10.10.3.201", "severity": "high", "caseId": "CS-0702-004"},
        {"time": "12:15", "event": "FW 端口扫描告警", "target": "10.10.15.22", "severity": "medium", "caseId": None},
        {"time": "13:42", "event": "NDR C2 外联检测", "target": "10.10.12.88", "severity": "high", "caseId": "CS-0702-002"},
    ],
    "lastUpdated": datetime.now(timezone.utc).isoformat(),
}

CHECKLIST = [
    {"id": "ph1", "label": "告警接入与优先级判定", "icon": "alert",
     "children": [
         {"id": "ph1-1", "label": "告警源确认与去重", "children": [
             {"id": "ph1-1-1", "label": "确认告警触发设备类型（NDR / HIDS / FW / EDR）", "status": "passed"},
             {"id": "ph1-1-2", "label": "核实告警时间戳与日志完整性", "status": "passed"},
             {"id": "ph1-1-3", "label": "检查是否存在重复告警（去重）", "status": "passed"},
             {"id": "ph1-1-4", "label": "验证告警原始日志是否存在篡改痕迹", "status": "passed"},
         ]},
         {"id": "ph1-2", "label": "告警关联与上下文分析", "children": [
             {"id": "ph1-2-1", "label": "查询同时间窗口（±30min）内其他设备告警", "status": "passed", "note": "NDR 2+HIDS 1"},
             {"id": "ph1-2-2", "label": "提取并关联关键实体：IP、域名、进程名、用户账号", "status": "passed"},
             {"id": "ph1-2-3", "label": "检索历史同类告警记录，判断是否为已知误报", "status": "passed"},
             {"id": "ph1-2-4", "label": "查询告警源 IP 的历史行为基线", "status": "passed"},
         ]},
         {"id": "ph1-3", "label": "威胁评分与优先级判定", "children": [
             {"id": "ph1-3-1", "label": "基于 MITRE ATT&CK 进行技战术预标注", "status": "passed"},
             {"id": "ph1-3-2", "label": "结合资产关键度计算威胁评分", "status": "passed", "note": "78"},
             {"id": "ph1-3-3", "label": "判定调查优先级（P0-P3）", "status": "passed", "note": "P0"},
         ]},
     ]},
    {"id": "ph2", "label": "多设备并发取证", "icon": "server",
     "children": [
         {"id": "ph2-1", "label": "取证范围定义", "children": [
             {"id": "ph2-1-1", "label": "确定需查询的设备类型与覆盖范围", "status": "passed"},
             {"id": "ph2-1-2", "label": "设定取证时间窗口（含前置回溯窗口）", "status": "passed", "note": "±30min"},
             {"id": "ph2-1-3", "label": "定义取证关键字段", "status": "passed"},
         ]},
         {"id": "ph2-2", "label": "网络层取证 NDR", "children": [
             {"id": "ph2-2-1", "label": "检索目标 IP 的全量会话记录", "status": "passed"},
             {"id": "ph2-2-2", "label": "导出关键会话 PCAP（不可篡改原始证据）", "status": "passed"},
             {"id": "ph2-2-3", "label": "解析 TLS/HTTP/DNS/SMB 协议载荷", "status": "passed"},
             {"id": "ph2-2-4", "label": "提取 JA3/JARM 指纹并与威胁情报比对", "status": "passed", "note": "命中 C2"},
         ]},
         {"id": "ph2-3", "label": "终端层取证 HIDS / EDR", "children": [
             {"id": "ph2-3-1", "label": "提取目标主机进程树与父子关系", "status": "passed"},
             {"id": "ph2-3-2", "label": "检索文件系统变更记录", "status": "passed"},
             {"id": "ph2-3-3", "label": "提取网络连接与 DNS 查询记录", "status": "passed"},
             {"id": "ph2-3-4", "label": "查询计划任务 / 服务 / 启动项变更", "status": "passed"},
         ]},
         {"id": "ph2-4", "label": "边界层取证 防火墙", "children": [
             {"id": "ph2-4-1", "label": "检索出站/入站会话日志", "status": "passed"},
             {"id": "ph2-4-2", "label": "逐跳还原 NAT 转换链路", "status": "passed"},
             {"id": "ph2-4-3", "label": "审查涉及的安全策略与放行规则", "status": "warning", "note": "POL-1024 过宽"},
         ]},
         {"id": "ph2-5", "label": "取证数据质量校验", "children": [
             {"id": "ph2-5-1", "label": "检查各设备时间戳 NTP 偏移量", "status": "passed"},
             {"id": "ph2-5-2", "label": "验证日志字段完整性（8 核心字段）", "status": "passed"},
             {"id": "ph2-5-3", "label": "识别数据缺失设备并标注覆盖率盲区", "status": "warning", "note": "10.10.15.35 无 Agent"},
         ]},
     ]},
    {"id": "ph3", "label": "时间线重建与因果分析", "icon": "clock",
     "children": [
         {"id": "ph3-1", "label": "时间线构建", "children": [
             {"id": "ph3-1-1", "label": "统一所有设备时区至 UTC+8", "status": "passed"},
             {"id": "ph3-1-2", "label": "按时间戳正序排列所有事件", "status": "passed"},
             {"id": "ph3-1-3", "label": "标记关键时间节点", "status": "passed"},
         ]},
         {"id": "ph3-2", "label": "因果链推断", "children": [
             {"id": "ph3-2-1", "label": "分析事件先后关系与触发依赖", "status": "passed"},
             {"id": "ph3-2-2", "label": "识别父进程衍生链", "status": "passed"},
             {"id": "ph3-2-3", "label": "检测时间序列异常", "status": "passed"},
         ]},
         {"id": "ph3-3", "label": "Kill Chain 阶段判定", "children": [
             {"id": "ph3-3-1", "label": "标注 Initial Access", "status": "passed"},
             {"id": "ph3-3-2", "label": "标注 Execution", "status": "passed"},
             {"id": "ph3-3-3", "label": "标注 C2 通信", "status": "passed"},
             {"id": "ph3-3-4", "label": "标注 Lateral Movement", "status": "passed"},
             {"id": "ph3-3-5", "label": "标注 Persistence", "status": "passed"},
         ]},
     ]},
    {"id": "ph4", "label": "攻击链路还原与 IOC 提取", "icon": "link",
     "children": [
         {"id": "ph4-1", "label": "完整攻击路径重建", "children": [
             {"id": "ph4-1-1", "label": "定位初始入侵向量", "status": "passed", "note": "PHP 文件上传"},
             {"id": "ph4-1-2", "label": "还原横向移动跳板路径", "status": "passed"},
             {"id": "ph4-1-3", "label": "绘制完整攻击链路图", "status": "passed"},
         ]},
         {"id": "ph4-2", "label": "IOC 提取与分类", "children": [
             {"id": "ph4-2-1", "label": "提取网络层 IOC：IP / 域名 / URL", "status": "passed"},
             {"id": "ph4-2-2", "label": "提取主机层 IOC：文件路径 / 哈希", "status": "passed"},
             {"id": "ph4-2-3", "label": "IOC 威胁等级评估与情报关联", "status": "passed"},
             {"id": "ph4-2-4", "label": "IOC 入库并推送至边界设备黑名单", "status": "pending"},
         ]},
         {"id": "ph4-3", "label": "MITRE ATT&CK 完整映射", "children": [
             {"id": "ph4-3-1", "label": "映射所有已知技战术 ID", "status": "passed", "note": "8 条"},
             {"id": "ph4-3-2", "label": "评估每项技战术的置信度", "status": "passed"},
             {"id": "ph4-3-3", "label": "识别检测盲区（未覆盖 ATT&CK 技战术）", "status": "passed"},
         ]},
     ]},
    {"id": "ph5", "label": "影响范围评估与风险定级", "icon": "target",
     "children": [
         {"id": "ph5-1", "label": "受影响资产盘点", "children": [
             {"id": "ph5-1-1", "label": "确定已失陷主机清单", "status": "passed", "note": "1 台"},
             {"id": "ph5-1-2", "label": "确定疑似受影响主机清单", "status": "passed", "note": "2 台"},
             {"id": "ph5-1-3", "label": "标注每台资产的关键度与业务归属", "status": "passed"},
         ]},
         {"id": "ph5-2", "label": "业务影响分析", "children": [
             {"id": "ph5-2-1", "label": "评估对核心业务的可用性影响", "status": "warning", "note": "高风险"},
             {"id": "ph5-2-2", "label": "评估数据泄露风险（含数据流向追踪）", "status": "warning", "note": "中风险"},
             {"id": "ph5-2-3", "label": "计算 Blast Radius 影响半径", "status": "passed"},
         ]},
         {"id": "ph5-3", "label": "风险定级", "children": [
             {"id": "ph5-3-1", "label": "综合威胁评分与业务影响进行风险定级", "status": "passed", "note": "78"},
             {"id": "ph5-3-2", "label": "输出风险报告摘要", "status": "passed"},
         ]},
     ]},
    {"id": "ph6", "label": "处置建议与闭环反馈", "icon": "zap",
     "children": [
         {"id": "ph6-1", "label": "紧急处置 Containment", "children": [
             {"id": "ph6-1-1", "label": "边界防火墙封禁 C2 IP 与域名", "status": "pending"},
             {"id": "ph6-1-2", "label": "隔离已失陷主机网络通信", "status": "pending"},
         ]},
         {"id": "ph6-2", "label": "根除与恢复", "children": [
             {"id": "ph6-2-1", "label": "委派主机取证 Agent 到失陷主机", "status": "pending"},
             {"id": "ph6-2-2", "label": "清除 Webshell 与后门文件", "status": "pending"},
             {"id": "ph6-2-3", "label": "修补 PHP 文件上传漏洞", "status": "pending"},
         ]},
         {"id": "ph6-3", "label": "加固与预防", "children": [
             {"id": "ph6-3-1", "label": "创建东西向 SMB 隔离策略", "status": "pending"},
             {"id": "ph6-3-2", "label": "收窄防火墙出站策略", "status": "pending"},
             {"id": "ph6-3-3", "label": "补充未覆盖主机 Agent 部署", "status": "pending"},
         ]},
         {"id": "ph6-4", "label": "闭环反馈", "children": [
             {"id": "ph6-4-1", "label": "更新 SIEM 检测规则（IOC + 行为特征）", "status": "pending"},
             {"id": "ph6-4-2", "label": "更新 MITRE ATT&CK 覆盖矩阵", "status": "pending"},
             {"id": "ph6-4-3", "label": "归档威胁狩猎 Hypothesis", "status": "pending"},
             {"id": "ph6-4-4", "label": "输出标准化溯源报告", "status": "pending"},
         ]},
     ]},
]

CASES = [
    {"id": "CS-0701-001", "title": "疑似 Webshell + 内网横向移动", "status": "resolved", "severity": "critical",
     "createdAt": "2026-07-01T08:05:00+08:00",
     "target": {"ip": "10.10.15.22", "hostname": "web-prod-01", "role": "Web 服务器", "sector": "核心业务"},
     "summary": {"threatScore": 78, "affectedHosts": 3},
     "checklist": CHECKLIST, "checklistProgress": {"total": 62, "passed": 48, "warning": 4, "pending": 10}},
    {"id": "CS-0702-002", "title": "DNS 隧道 + 数据外传可疑", "status": "investigating", "severity": "high",
     "createdAt": "2026-07-02T14:10:00+08:00",
     "target": {"ip": "10.10.12.88", "hostname": "db-replica-02", "role": "数据库从库", "sector": "数据平台"},
     "summary": {"threatScore": 62, "affectedHosts": 1},
     "checklist": CHECKLIST, "checklistProgress": {"total": 62, "passed": 12, "warning": 2, "pending": 48}},
]

CASES_STORE = {c["id"]: c for c in CASES}


async def get_dashboard(ctx, request):
    return DASHBOARD


async def get_cases(ctx, request):
    return {"cases": [{"id": c["id"], "title": c["title"], "status": c["status"],
            "severity": c["severity"], "createdAt": c["createdAt"],
            "target": c["target"], "summary": c.get("summary")} for c in CASES]}


async def get_case(ctx, request):
    case_id = None
    try:
        if hasattr(request, 'path_params') and request.path_params:
            case_id = request.path_params.get("id")
    except Exception:
        pass
    if not case_id:
        try:
            case_id = str(request.url.path).rstrip('/').split('/')[-1]
        except Exception:
            pass
    c = CASES_STORE.get(case_id) if case_id else None
    if c and not c.get("checklist"):
        c["checklist"] = CHECKLIST
    return {"ok": True, "case": c} if c else {"ok": False, "error": "案件不存在"}