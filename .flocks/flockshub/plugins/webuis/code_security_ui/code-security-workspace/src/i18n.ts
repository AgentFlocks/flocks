import { useCallback } from "react";

type TranslationValues = Record<string, string | number>;

interface ContractRuntime {
  useLanguage?: () => string;
}

const englishMessages: Record<string, string> = {
  准备源码快照: "Prepare source snapshot",
  威胁建模: "Threat modeling",
  威胁模型: "Threat model",
  基线扫描: "Baseline scan",
  静态验证: "Static validation",
  动态验证: "Dynamic validation",
  主智能体裁决: "Primary agent adjudication",
  主智能体: "Primary agent",
  覆盖度: "Coverage",
  覆盖证明: "Coverage attestation",
  覆盖证明统计: "Coverage attestation counts",
  最近一次提交拒绝: "Latest submission rejection",
  错误码: "Error code",
  违规项: "Violations",
  可在当前执行中修正: "Correctable in the current attempt",
  不可重试: "Not retryable",
  策略: "Policy",
  已分配: "Assigned",
  完整读取: "Read complete",
  未检查: "Unexamined",
  可信部分覆盖: "Trusted partial coverage",
  "这些数字由宿主根据当前执行尝试的源码证据回执计算。":
    "These counts are computed by the host from source-evidence receipts for the current attempt.",
  定向复扫: "Targeted rescan",
  产物封装: "Artifact packaging",
  准备中: "Preparing",
  运行中: "Running",
  正在取消: "Cancelling",
  已完成: "Completed",
  执行失败: "Failed",
  已取消: "Cancelled",
  已中断: "Interrupted",
  等待中: "Pending",
  部分完成: "Partially completed",
  已跳过: "Skipped",
  无法动态执行: "Not runnable",
  待校验: "Pending validation",
  校验通过: "Validation passed",
  校验失败: "Validation failed",
  待评估: "Pending assessment",
  部分覆盖: "Partial coverage",
  覆盖受阻: "Coverage blocked",
  完整覆盖: "Complete coverage",
  未知: "Unknown",
  威胁建模员: "Threat modeler",
  基线分析员: "Baseline analyst",
  定向调查员: "Targeted investigator",
  静态验证员: "Static verifier",
  动态探测员: "Dynamic prober",
  严重: "Critical",
  高危: "High",
  中危: "Medium",
  低危: "Low",
  已确认: "Confirmed",
  已驳回: "Rejected",
  证据不足: "Insufficient evidence",
  等待结论: "Pending conclusion",
  清单: "Inventory",
  搜索: "Search",
  读取: "Read",
  等待生成: "Pending",
  生成中: "Generating",
  可查看: "Available",
  执行状态: "Execution status",
  阶段状态: "Phase status",
  工作单元状态: "Work unit status",
  执行模型: "Execution model",
  等待执行: "Waiting to run",
  模型信息不可用: "Model information unavailable",
  恢复中: "Recovering",
  "第 {{ordinal}} 次执行": "Attempt {{ordinal}}",
  "续跑 {{count}} 次": "{{count}} resumes",
  "查看执行记录（{{count}}）": "View execution history ({{count}})",
  "{{context}}：{{label}}": "{{context}}: {{label}}",
  审计完成后确定: "Available after the audit completes",
  最终结果不可用: "Final result unavailable",
  动态验证复现: "Reproduced by dynamic validation",
  静态验证确认: "Confirmed by static validation",
  "{{hours}}时{{minutes}}分{{seconds}}秒":
    "{{hours}}h {{minutes}}m {{seconds}}s",
  "{{minutes}}分{{seconds}}秒": "{{minutes}}m {{seconds}}s",
  "{{seconds}}秒": "{{seconds}}s",
  刚刚: "Just now",
  "{{minutes}}分钟前": "{{minutes}}m ago",
  "{{hours}}小时前": "{{hours}}h ago",
  "{{days}}天前": "{{days}}d ago",
  "总耗时 ": "Total duration ",
  代码审计: "Code audit",
  "{{phase}}新增 {{count}} 条事件。": "{{count}} new events in {{phase}}.",
  无法加载更多审计记录: "Unable to load more audit records",
  无法加载代码审计工作区: "Unable to load the code audit workspace",
  无法加载可审计项目列表: "Unable to load auditable projects",
  无法加载扫描详情: "Unable to load scan details",
  无法加载更早的可信事件: "Unable to load earlier trusted events",
  "确定取消本次审计吗？已产生的中间数据会保留。":
    "Cancel this audit? Existing intermediate data will be retained.",
  取消审计失败: "Unable to cancel the audit",
  "审计已创建，但扫描列表刷新失败":
    "The audit was created, but the audit list could not be refreshed",
  "已删除 {{name}} 的审计记录。": "Deleted the audit record for {{name}}.",
  删除审计失败: "Unable to delete the audit",
  正在加载代码审计工作区: "Loading code audit workspace",
  关闭审计列表: "Close audit list",
  "实时连接仍未恢复，当前内容可继续查看。":
    "The live connection is still unavailable. You can continue viewing the current content.",
  "实时连接已中断，正在重连。当前内容仍可查看。":
    "The live connection was interrupted and is reconnecting. The current content remains available.",
  立即重试: "Retry now",
  关闭: "Close",
  切换审计: "Switch audit",
  "正在加载…": "Loading…",
  加载更多审计记录: "Load more audits",
  打开审计列表: "Open audit list",
  动态审计: "Dynamic audit",
  静态审计: "Static audit",
  等待阶段信息: "Waiting for phase information",
  新建审计: "New audit",
  "正在取消…": "Cancelling…",
  取消审计: "Cancel audit",
  下载报告: "Download report",
  查看产物: "View artifacts",
  审计未正常完成: "Audit did not complete normally",
  关闭产物检查器: "Close artifact inspector",
  还没有代码审计: "No code audits yet",
  "创建一次基于不可变源码快照的安全审计，查看威胁模型、验证过程和最终报告。":
    "Create a security audit from an immutable source snapshot to review the threat model, validation process, and final report.",
  "当前版本仅允许管理员启动审计。":
    "Only administrators can start audits in the current edition.",
  正在加载扫描详情: "Loading scan details",
  审计历史: "Audit history",
  代码安全: "Code security",
  审计记录: "Audit records",
  搜索审计记录: "Search audit records",
  "搜索目标或 scan_id": "Search target or scan_id",
  状态筛选: "Status filter",
  全部状态: "All statuses",
  失败: "Failed",
  扫描列表: "Scan list",
  漏洞待确认: "Findings pending confirmation",
  "{{count}} 个漏洞": "{{count}} findings",
  动态: "Dynamic",
  "删除审计 {{name}}": "Delete audit {{name}}",
  "审计 {{name}} 仍在运行，需先取消后才能删除":
    "Audit {{name}} is still running. Cancel it before deleting it.",
  删除审计: "Delete audit",
  "请先取消审计，待其停止后再删除":
    "Cancel the audit and wait for it to stop before deleting it",
  没有匹配的审计记录: "No matching audit records",
  不可恢复的操作: "Irreversible action",
  "删除这条审计记录？": "Delete this audit record?",
  将永久删除: "Permanently delete",
  "的审计记录、事件、快照和审计产物。":
    "and all of its audit records, events, snapshots, and artifacts.",
  取消: "Cancel",
  "正在删除…": "Deleting…",
  永久删除: "Delete permanently",
  取消删除审计: "Cancel audit deletion",
  "表单中有尚未提交的内容，确定关闭吗？":
    "The form contains unsaved changes. Close it anyway?",
  "目标目录属于 Flocks 运行数据，不能作为审计目标。请选择源码项目目录。":
    "The target directory contains Flocks runtime data and cannot be audited. Select a source project directory.",
  "目标目录不存在或不是文件夹，请检查相对路径。":
    "The target directory does not exist or is not a folder. Check the relative path.",
  "目标目录不在所选工作区内，请重新选择。":
    "The target directory is outside the selected workspace. Select another directory.",
  "所选工作区不可用，请刷新后重新选择。":
    "The selected workspace is unavailable. Refresh and select it again.",
  "目标目录必须是工作区内的相对路径。":
    "The target directory must be a relative path inside the workspace.",
  "请选择工作区。": "Select a workspace.",
  "固定模型必须使用 provider/model 格式。":
    "The fixed model must use the provider/model format.",
  "请确认已理解动态验证的执行边界。":
    "Confirm that you understand the dynamic validation execution boundary.",
  创建审计失败: "Unable to create the audit",
  "请求 ID：{{id}}": "Request ID: {{id}}",
  关闭新建审计: "Close new audit",
  不可变源码快照: "Immutable source snapshot",
  直接源码审计: "Direct source audit",
  新建代码审计: "New code audit",
  请修正以下问题: "Fix the following issues",
  目标: "Target",
  工作区: "Workspace",
  请选择工作区: "Select a workspace",
  目标目录: "Target directory",
  "相对于所选工作区，例如": "Relative to the selected workspace, for example",
  范围: "Scope",
  包含路径: "Included paths",
  "每行一个快照相对路径。": "Enter one snapshot-relative path per line.",
  排除模式: "Exclusion patterns",
  "每行一个 glob；留空使用插件默认排除规则。":
    "Enter one glob per line, or leave blank to use the plugin defaults.",
  高级设置: "Advanced settings",
  复制源码到只读快照: "Copy source into a read-only snapshot",
  "默认开启；审计使用固定的源码副本。":
    "Enabled by default. The audit uses a fixed source copy.",
  "已关闭；直接读取源目录，文件变化会导致校验失败，且不能启用动态验证。":
    "Disabled. The audit reads the source directory directly; file changes fail integrity checks, and dynamic validation is unavailable.",
  "单文件上限（字节）": "Per-file limit (bytes)",
  覆盖策略: "Coverage policy",
  "可信部分覆盖（默认）": "Trusted partial coverage (default)",
  穷尽覆盖: "Exhaustive coverage",
  "穷尽覆盖会阻止仍有未检查文件或阻塞问题的工作单元完成。":
    "Exhaustive coverage prevents work units with unexamined files or blocking questions from completing.",
  独立复核票数: "Independent verification votes",
  "每个候选漏洞独立复核；多票时按严格多数决裁定。":
    "Each candidate is reviewed independently; multiple votes use a strict majority.",
  模型: "Model",
  固定模型: "Fixed model",
  留空使用系统默认模型: "Leave blank to use the system default",
  "可选；格式为": "Optional; format:",
  验证方式: "Validation method",
  "默认关闭；开启后执行受限 Docker 探测。":
    "Disabled by default. When enabled, restricted Docker probes are executed.",
  "直接源码审计不支持动态验证；重新开启源码复制后可用。":
    "Dynamic validation is unavailable for direct source audits. Re-enable source copying to use it.",
  "动态验证将在本地 Docker 中构建并运行受限探测":
    "Dynamic validation builds and runs restricted probes in local Docker",
  "无网络 · 无主机挂载 · 只读根文件系统 · 无 capabilities · 资源受限 · 仅使用本地已有镜像":
    "No network · no host mounts · read-only root filesystem · no capabilities · resource limits · local images only",
  "需要 Docker CLI、可用的本地 daemon，以及快照中受支持的测试框架。":
    "Requires the Docker CLI, an available local daemon, and a supported test framework in the snapshot.",
  "需要 Docker CLI、可用的本地 daemon，以及快照中受支持的 Dockerfile。":
    "Requires the Docker CLI, an available local daemon, and a supported Dockerfile in the snapshot.",
  "我理解动态验证会执行快照中的受限代码，并同意继续。":
    "I understand that dynamic validation executes restricted code from the snapshot and agree to continue.",
  "正在创建不可变快照…": "Creating immutable snapshot…",
  "正在准备直接源码审计…": "Preparing direct source audit…",
  启动动态审计: "Start dynamic audit",
  启动静态审计: "Start static audit",
  阶段与实时事件: "Phases and live events",
  "漏洞数，{{basis}}": "Finding count, {{basis}}",
  "漏洞数 {{count}} 个，{{basis}}": "{{count}} findings, {{basis}}",
  漏洞数: "Findings",
  审计阶段: "Audit phases",
  "{{phase}}阶段": "{{phase}} phase",
  "{{done}}/{{total}} 个工作单元 · ": "{{done}}/{{total}} work units · ",
  当前查看: "Currently viewing",
  开始时间: "Start time",
  结束时间: "End time",
  阶段耗时: "Phase duration",
  快照大小: "Snapshot size",
  已完成产物: "Completed artifacts",
  裁决轮次: "Adjudication round",
  "{{count}} 个": "{{count}} items",
  个: "findings",
  "第 {{round}} 轮": "Round {{round}}",
  "启动审计时未启用动态验证。":
    "Dynamic validation was not enabled when this audit started.",
  "该阶段仅部分完成，请结合覆盖度与限制项判断结果。":
    "This phase completed only partially. Review coverage and limitations before interpreting the result.",
  "阶段信息将在快照创建后出现。":
    "Phase information will appear after the snapshot is created.",
  "快照可信边界将在源码快照创建后出现。":
    "The trusted snapshot boundary will appear after the source snapshot is created.",
  "封装结果将在最终产物生成后出现。":
    "Packaging results will appear after the final artifacts are generated.",
  "当前扫描任务缺少标识，无法读取证据。":
    "This scan has no identifier, so evidence cannot be loaded.",
  无法加载候选漏洞证据: "Unable to load candidate finding evidence",
  裁决内容与结果: "Adjudication details and results",
  "主智能体已完成{{round}}裁决并形成最终结论":
    "The primary agent completed {{round}} adjudication and reached a final conclusion",
  "主智能体已完成{{round}}裁决并要求补充验证":
    "The primary agent completed {{round}} adjudication and requested additional validation",
  主智能体未能提交有效裁决:
    "The primary agent could not submit a valid adjudication",
  主智能体正在审阅候选漏洞与验证结论:
    "The primary agent is reviewing candidate findings and validation conclusions",
  裁决结果: "Adjudication result",
  完成审计定稿: "Finalize the audit",
  裁决对象: "Candidates reviewed",
  "{{count}} 个候选漏洞": "{{count}} candidate findings",
  接受: "Accepted",
  驳回: "Rejected",
  纳入漏洞: "Accepted findings",
  "没有候选漏洞被纳入最终报告。":
    "No candidate findings were accepted into the final report.",
  已驳回的候选漏洞: "Rejected candidate findings",
  "没有候选漏洞被驳回。": "No candidate findings were rejected.",
  需要定向复扫后再形成最终结论:
    "A targeted rescan is required before a final conclusion",
  裁决依据: "Decision rationale",
  复扫范围: "Rescan scope",
  待确认问题: "Open questions",
  "裁决执行失败，请结合阶段事件查看失败原因。":
    "Adjudication failed. Review phase events for the failure reason.",
  "裁决提交后，这里将显示接受、驳回或定向复扫的具体内容。":
    "After adjudication is submitted, accepted findings, rejections, or targeted rescan details will appear here.",
  "当前任务没有保存结构化裁决详情，可在审计产物面板中查看完整裁决记录。":
    "No structured adjudication details were saved for this audit. Review the complete record in the artifact panel.",
  "{{title}}，{{count}} 个": "{{title}}, {{count}} items",
  "{{candidate}}，{{action}}": "{{candidate}}, {{action}}",
  "经主智能体裁决，纳入最终报告":
    "Accepted into the final report by the primary agent",
  "经主智能体裁决，不纳入最终报告":
    "Excluded from the final report by the primary agent",
  收起证据: "Hide evidence",
  查看证据: "View evidence",
  收起依据: "Hide rationale",
  查看依据: "View rationale",
  已纳入: "Accepted",
  "{{candidate}}的纳入证据": "Evidence for accepting {{candidate}}",
  "{{candidate}}的驳回详情": "Rejection details for {{candidate}}",
  驳回依据: "Rejection rationale",
  独立验证结论: "Independent validation conclusion",
  代码证据: "Code evidence",
  相关代码证据: "Related code evidence",
  "{{count}} 条": "{{count}} records",
  "正在加载纳入证据…": "Loading acceptance evidence…",
  "正在加载驳回详情…": "Loading rejection details…",
  重新加载: "Reload",
  "证据 {{index}}": "Evidence {{index}}",
  "代码证据 {{index}}，{{path}} 第 {{start}} 至 {{end}} 行":
    "Code evidence {{index}}, {{path}}, lines {{start}}–{{end}}",
  "证据内容已截断。": "Evidence content was truncated.",
  "该候选漏洞没有可展示的代码证据，可在审计产物中查看完整记录。":
    "No displayable code evidence is available for this candidate finding. Review the complete record in audit artifacts.",
  快照可信边界: "Trusted snapshot boundary",
  后续审计仅基于此不可变源码快照:
    "All subsequent analysis uses only this immutable source snapshot",
  直接源码校验边界: "Direct source validation boundary",
  后续审计直接读取源目录并校验文件摘要:
    "Subsequent analysis reads the source directory directly and verifies file digests",
  纳入文件: "Included files",
  遗漏文件: "Omitted files",
  "工作区后续发生的文件变化不会影响本次审计结果，所有智能体共享同一份固定内容与版本标识。":
    "Subsequent workspace file changes do not affect this audit. All agents share the same fixed content and revision identifier.",
  "审计工具直接读取源目录；如果已纳入文件的内容或大小发生变化，摘要校验将失败并停止使用变化后的内容。":
    "Audit tools read the source directory directly. If an included file's contents or size changes, digest validation fails and the changed content is not used.",
  等待校验: "Pending validation",
  封装结果: "Packaging results",
  最终产物已经固定并通过摘要校验:
    "Final artifacts are sealed and passed digest validation",
  最终产物未通过完整性校验: "Final artifacts failed integrity validation",
  正在生成并校验最终产物: "Generating and validating final artifacts",
  产物大小: "Artifact size",
  完整性: "Integrity",
  最终报告: "Final report",
  "报告、SARIF、漏洞清单、覆盖度和审计清单等产物已完成，可从审计产物面板查看或下载。":
    "The report, SARIF, findings, coverage, and audit manifest are complete and available in the artifact panel.",
  "当前封装结果不可作为可信最终输出，请结合审计产物面板中的校验信息重新发起审计。":
    "The current package cannot be trusted as final output. Review validation details in the artifact panel and start a new audit.",
  "产物仍在生成中，完整性校验完成后会自动更新封装状态。":
    "Artifacts are still being generated. Packaging status will update after integrity validation completes.",
  工作单元: "Work units",
  验证对象: "Validation target",
  开始: "Started",
  耗时: "Duration",
  全量源码快照: "Full source snapshot",
  "{{count}} 个路径": "{{count}} paths",
  源码证据回执: "Source evidence receipts",
  源码证据回执统计: "Source evidence receipt summary",
  清单条目: "Inventory entries",
  搜索命中回执: "Search-match receipts",
  读取片段: "Read excerpts",
  "关联候选漏洞 ID": "Related candidate finding IDs",
  查看验证结论: "View validation conclusion",
  验证结论详情: "Validation conclusion details",
  查看分配范围: "View assigned scope",
  "仅显示前 {{count}} 条路径。": "Only the first {{count}} paths are shown.",
  "该阶段没有工作单元。": "This phase has no work units.",
  阶段事件: "Phase events",
  全部阶段: "All phases",
  "{{phase}} · 正在加载事件…": "{{phase}} · Loading events…",
  "{{phase}} · 显示 {{groups}} 组（{{filtered}} 条）/ {{total}} 条":
    "{{phase}} · {{groups}} groups ({{filtered}} events) / {{total}} total",
  "{{phase}} · 显示 {{visible}} / {{total}} 条":
    "{{phase}} · {{visible}} / {{total}} events",
  按阶段筛选事件: "Filter events by phase",
  按工作单元筛选事件: "Filter events by work unit",
  全部工作单元: "All work units",
  按级别筛选事件: "Filter events by level",
  全部级别: "All levels",
  信息: "Info",
  警告: "Warning",
  错误: "Error",
  "正在加载更早事件…": "Loading earlier events…",
  加载更早事件: "Load earlier events",
  审计事件列表: "Audit event list",
  "正在加载审计事件…": "Loading audit events…",
  "当前阶段与筛选条件下还没有事件。":
    "No events match the current phase and filters.",
  "有 {{count}} 条新事件 · 回到最新": "{{count}} new events · Return to latest",
  "已合并 {{count}} 条相同状态事件，时间范围 {{start}} 至 {{end}}":
    "Merged {{count}} identical status events from {{start}} to {{end}}",
  "合并 {{count}} 条 · {{start}}–{{end}}":
    "Merged {{count}} · {{start}}–{{end}}",
  "复制合并事件摘要，共 {{count}} 条":
    "Copy summary of {{count}} merged events",
  "复制事件 {{seq}} 摘要": "Copy event {{seq}} summary",
  "复制合并的 {{count}} 条事件摘要": "Copy summary of {{count}} merged events",
  "复制事件 #{{seq}} 摘要": "Copy event #{{seq}} summary",
  "合并 {{count}} 条相同状态事件：{{start}}–{{end}}":
    "Merged {{count}} identical status events: {{start}}–{{end}}",
  "{{count}} 失败": "{{count}} failed",
  "{{completed}}/{{total}} 已完成": "{{completed}}/{{total}} completed",
  "{{count}} 个已完成": "{{count}} completed",
  "{{count}} 运行中": "{{count}} running",
  "{{count}} 已取消": "{{count}} cancelled",
  "{{count}} 已启动": "{{count}} started",
  已启动: "Started",
  "{{verified}}/{{candidates}} 已验证": "{{verified}}/{{candidates}} verified",
  "{{candidates}} 个候选漏洞中已有 {{verified}} 个验证记录":
    "{{verified}} of {{candidates}} candidate findings have validation records",
  "{{count}} 候选漏洞": "{{count}} candidate findings",
  "{{count}} 漏洞": "{{count}} findings",
  "{{level}}事件": "{{level}} event",
  "{{count}} 个工作单元执行失败": "{{count}} work units failed",
  "{{total}} 个工作单元中 {{completed}} 个已完成":
    "{{completed}} of {{total}} work units completed",
  "{{count}} 个工作单元正在运行": "{{count}} work units running",
  "{{count}} 个工作单元已取消": "{{count}} work units cancelled",
  "{{count}} 个工作单元已启动": "{{count}} work units started",
  不可变源码快照已创建: "Immutable source snapshot created",
  直接源码审计视图已准备: "Direct source audit view prepared",
  审计阶段已开始: "Audit phase started",
  审计阶段状态已更新: "Audit phase status updated",
  扫描状态已更新: "Scan status updated",
  动态验证已开始: "Dynamic validation started",
  动态验证预检已开始: "Dynamic validation preflight started",
  动态验证预检已通过: "Dynamic validation preflight passed",
  "受限 Docker 探测已开始": "Restricted Docker probe started",
  "受限 Docker 探测已完成": "Restricted Docker probe completed",
  动态验证已完成: "Dynamic validation completed",
  动态验证执行失败: "Dynamic validation failed",
  动态验证已取消: "Dynamic validation cancelled",
  主智能体裁决已开始: "Primary-agent adjudication started",
  主智能体裁决失败: "Primary-agent adjudication failed",
  主智能体已提交裁决: "Primary agent submitted its adjudication",
  最终产物已完成完整性校验: "Final artifacts passed integrity validation",
  代码审计已取消: "Code audit cancelled",
  概览: "Overview",
  产物检查器: "Artifact Inspector",
  审计产物: "Audit artifacts",
  中间产物检查器: "Intermediate artifact inspector",
  "刷新中…": "Refreshing…",
  刷新: "Refresh",
  "该产物未通过完整性校验，不能作为可信产物展示。":
    "This artifact failed integrity validation and cannot be displayed as trusted output.",
  暂时无法读取该产物: "The artifact is temporarily unavailable",
  产物未通过校验: "Artifact validation failed",
  产物暂不可用: "Artifact unavailable",
  "结构化中间数据仍可从其他标签查看；最终报告需要重新发起审计后生成。":
    "Structured intermediate data remains available in other tabs. Start a new audit to generate the final report.",
  "扫描继续运行时，可稍后点击“刷新”读取最新版本。":
    "While the scan continues, select Refresh later to load the latest version.",
  "刷新失败，仍显示上一次成功读取的内容：{{error}}":
    "Refresh failed. Showing the last successfully loaded content: {{error}}",
  产物完整性: "Artifact integrity",
  未启用: "Disabled",
  候选漏洞: "Candidate findings",
  "执行完成不等同于不存在漏洞，覆盖度也不等同于产物完整性。":
    "Execution completion does not mean no vulnerabilities exist, and coverage is independent from artifact integrity.",
  源码文件: "Source files",
  版本: "Revision",
  目录摘要: "Tree digest",
  "该产物尚未产生。扫描继续运行时会自动更新。":
    "This artifact has not been generated yet and will update automatically while the scan continues.",
  动态验证未启用: "Dynamic validation is disabled",
  "本次审计仅执行静态分析。若需要运行受限探测，请发起新的动态审计。":
    "This audit runs static analysis only. Start a new dynamic audit to run restricted probes.",
  最终审计报告: "Final audit report",
  无法读取代码证据: "Unable to load code evidence",
  当前阶段尚未发现候选漏洞: "No candidate findings in this phase",
  "扫描仍可能在其他范围继续运行。":
    "The scan may still be running in other areas.",
  关闭代码证据: "Close code evidence",
  "证据已按 64 KiB 上限截断。": "Evidence was truncated at the 64 KiB limit.",
  待定: "Undetermined",
  漏洞: "Finding",
  未命名候选漏洞: "Untitled candidate finding",
  验证状态: "Validation status",
  候选漏洞代码证据: "Candidate finding code evidence",
  "查看证据 · {{path}}:{{start}}-{{end}}":
    "View evidence · {{path}}:{{start}}-{{end}}",
  正在加载产物: "Loading artifact",
  "完整性清单未封装以下必需产物：{{artifacts}}。":
    "The integrity manifest does not include these required artifacts: {{artifacts}}.",
  "已完成扫描的产物目录不存在。":
    "The artifact directory for the completed scan does not exist.",
  "产物完整性清单不存在。": "The artifact integrity manifest does not exist.",
  是: "Yes",
  否: "No",
};

export type Translator = (
  message: string,
  values?: TranslationValues,
) => string;

export function isChineseLanguage(language: string): boolean {
  return language.toLowerCase().replace("_", "-").startsWith("zh");
}

export function translate(
  language: string,
  message: string,
  values: TranslationValues = {},
): string {
  const template = isChineseLanguage(language)
    ? message
    : englishMessages[message] || message;
  return template.replace(/\{\{(\w+)\}\}/g, (match, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key)
      ? String(values[key])
      : match,
  );
}

export function useCodeSecurityI18n(): {
  language: string;
  t: Translator;
} {
  const runtime = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ as
    | ContractRuntime
    | undefined;
  const language = runtime?.useLanguage
    ? runtime.useLanguage()
    : window.localStorage?.getItem("flocks-language") ||
      window.navigator?.language ||
      "en-US";
  const t = useCallback<Translator>(
    (message, values) => translate(language, message, values),
    [language],
  );
  return { language, t };
}

export function hasEnglishTranslation(message: string): boolean {
  return Object.prototype.hasOwnProperty.call(englishMessages, message);
}
