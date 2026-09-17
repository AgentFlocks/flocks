import type {
  CatalogScope,
  Collection,
  FacetDefinition,
  LocalizedText,
  PluginItem,
  ScopeConfig,
} from './types';

const text = (zh: string, en: string): LocalizedText => ({ zh, en });
const facet = (
  key: string,
  zh: string,
  en: string,
  options: [value: string, zh: string, en: string][],
): FacetDefinition => ({
  key,
  label: text(zh, en),
  options: options.map(([value, optionZh, optionEn]) => ({
    value,
    label: text(optionZh, optionEn),
  })),
});
const enabled = (): FacetDefinition =>
  facet('enabled', '启用状态', 'Enabled', [
    ['yes', '已启用', 'Enabled'],
    ['no', '已停用', 'Disabled'],
  ]);
const source = (): FacetDefinition =>
  facet('source', '来源', 'Source', [
    ['project', '项目级', 'Project'],
    ['global', '全局', 'Global'],
  ]);

export const SCOPE_CONFIG: Record<CatalogScope, ScopeConfig> = {
  workflow: {
    scope: 'workflow',
    type: 'workflow',
    title: text('工作流', 'Workflows'),
    description: text(
      '按业务组织安全自动化流程；此处仅展示模拟配置。',
      'Organize security automation by business purpose. All configurations are mock data.',
    ),
    facets: [
      facet('status', '状态', 'Status', [
        ['active', '已启用', 'Active'],
        ['draft', '草稿', 'Draft'],
        ['archived', '已归档', 'Archived'],
      ]),
      facet('trigger', '触发方式', 'Trigger', [
        ['manual', '手动', 'Manual'],
        ['schedule', '定时', 'Schedule'],
        ['webhook', 'Webhook', 'Webhook'],
      ]),
      source(),
    ],
  },
  agent: {
    scope: 'agent',
    type: 'agent',
    title: text('智能体', 'Agents'),
    description: text(
      '整理安全协作角色，分组不改变权限或委派能力。',
      'Organize security roles without changing permissions or delegation capabilities.',
    ),
    facets: [
      facet('role', '角色', 'Role', [
        ['primary', '主智能体', 'Primary'],
        ['subagent', '子智能体', 'Subagent'],
      ]),
      facet('native', '内置', 'Native', [
        ['yes', '内置', 'Native'],
        ['no', '自定义', 'Custom'],
      ]),
      facet('delegatable', '可委派', 'Delegatable', [
        ['yes', '可委派', 'Delegatable'],
        ['no', '不可委派', 'Not delegatable'],
      ]),
    ],
  },
  skill: {
    scope: 'skill',
    type: 'skill',
    title: text('技能', 'Skills'),
    description: text(
      '查看技能与依赖状态；未知状态不代表已就绪。',
      'Browse skills and dependency readiness. Unknown does not mean ready.',
    ),
    facets: [
      facet('eligibility', '依赖状态', 'Readiness', [
        ['ready', '已就绪', 'Ready'],
        ['missing', '缺少依赖', 'Missing dependencies'],
        ['unknown', '未知', 'Unknown'],
      ]),
      enabled(),
      source(),
    ],
  },
  tool: {
    scope: 'tool',
    type: 'tool',
    title: text('工具', 'Tools'),
    description: text(
      '统一浏览工具来源与确认策略；原型不执行工具。',
      'Browse tool sources and confirmation policies. This prototype never executes tools.',
    ),
    facets: [
      facet('source', '来源', 'Source', [
        ['builtin', '内置', 'Built-in'],
        ['mcp', 'MCP', 'MCP'],
        ['api', 'API', 'API'],
        ['device', '设备', 'Device'],
        ['plugin_py', 'Python 插件', 'Python plugin'],
        ['plugin_yaml', 'YAML 插件', 'YAML plugin'],
      ]),
      enabled(),
      facet('confirmation', '执行确认', 'Confirmation', [
        ['required', '需要确认', 'Required'],
        ['not-required', '无需确认', 'Not required'],
      ]),
    ],
  },
  'device-template': {
    scope: 'device-template',
    type: 'device',
    title: text('设备模板', 'Device templates'),
    description: text(
      '模板安装状态与实例连接状态相互独立。所有厂商均为虚构。',
      'Template installation and instance connectivity are independent. All vendors are fictional.',
    ),
    facets: [
      facet('state', '安装状态', 'Installation', [
        ['installed', '已安装', 'Installed'],
        ['available', '可安装', 'Available'],
        ['updateAvailable', '可更新', 'Update available'],
        ['localOnly', '仅本地', 'Local only'],
        ['broken', '已损坏', 'Broken'],
      ]),
      facet('vendor', '厂商', 'Vendor', [
        ['aurora-net', '极光网络（虚构）', 'Aurora Net (fictional)'],
        ['cedar-sec', '雪松安全（虚构）', 'Cedar Security (fictional)'],
        [
          'harbor-observability',
          '港湾观测（虚构）',
          'Harbor Observability (fictional)',
        ],
        ['lattice-edge', '晶格边缘（虚构）', 'Lattice Edge (fictional)'],
      ]),
      facet('source', '来源', 'Source', [
        ['bundled', '随应用提供', 'Bundled'],
        ['project', '项目级', 'Project'],
        ['global', '全局', 'Global'],
      ]),
    ],
  },
  'device-instance': {
    scope: 'device-instance',
    type: 'device',
    title: text('设备实例', 'Device instances'),
    description: text(
      '业务分组不改变原机房；连接状态为模拟数据，并未实际探测。',
      'Business collections do not change the original room. Connection states are simulated, not probed.',
    ),
    facets: [
      facet('status', '连接状态', 'Connection status', [
        ['connected', '已连接（模拟）', 'Connected (mock)'],
        ['error', '连接错误（模拟）', 'Error (mock)'],
        ['unchecked', '未检查', 'Unchecked'],
      ]),
      facet('room', '原机房', 'Original room', [
        ['dc-primary', '主数据中心', 'Primary data center'],
        ['dc-backup', '备份数据中心', 'Backup data center'],
        ['lab', '安全实验室', 'Security lab'],
        ['cloud', '云环境', 'Cloud environment'],
      ]),
      facet('connection', '连接方式', 'Connection method', [
        ['api', 'API', 'API'],
        ['webcli', 'Web CLI', 'Web CLI'],
        ['workflow', '工作流', 'Workflow'],
      ]),
    ],
  },
};

const businessGroups = ['response', 'hunting', 'exposure', 'audit'] as const;
type BusinessGroup = (typeof businessGroups)[number];
const businessGroupNames: Record<
  CatalogScope,
  Record<BusinessGroup, string>
> = {
  workflow: {
    response: '告警研判',
    hunting: '威胁调查',
    exposure: '资产巡检',
    audit: '报告自动化',
  },
  agent: {
    response: '事件响应',
    hunting: '威胁狩猎',
    exposure: '风险治理',
    audit: '合规审计',
  },
  skill: {
    response: '告警分析',
    hunting: '情报与狩猎',
    exposure: '基线与资产',
    audit: '审计与报告',
  },
  tool: {
    response: '调查取证',
    hunting: '情报检索',
    exposure: '资产治理',
    audit: '证据与报表',
  },
  'device-template': {
    response: '检测与防护',
    hunting: '日志与数据',
    exposure: '资产与身份',
    audit: '审计与合规',
  },
  'device-instance': {
    response: '核心业务防护',
    hunting: '监测与调查',
    exposure: '资产治理',
    audit: '合规证据',
  },
};
const scopes = Object.keys(SCOPE_CONFIG) as CatalogScope[];
const groupId = (scope: CatalogScope, group: BusinessGroup): string =>
  `group-${scope}-${group}`;

export const INITIAL_GROUPS: Collection[] = scopes.flatMap((scope) =>
  businessGroups.map((group) => ({
    id: groupId(scope, group),
    scope,
    name: businessGroupNames[scope][group],
  })),
);

type Seed = {
  slug: string;
  name: string;
  description: string;
  tags: string[];
  group: BusinessGroup | null;
  attributes: Record<string, string>;
  detail: { label: LocalizedText; value: string };
  templateSlug?: string;
};
const seed = (
  slug: string,
  name: string,
  description: string,
  tags: string[],
  group: BusinessGroup | null,
  attributes: Record<string, string>,
  detail: [zh: string, en: string, value: string],
  templateSlug?: string,
): Seed => ({
  slug,
  name,
  description,
  tags,
  group,
  attributes,
  detail: { label: text(detail[0], detail[1]), value: detail[2] },
  templateSlug,
});

const seeds: Record<CatalogScope, Seed[]> = {
  workflow: [
    seed(
      'phishing-triage',
      '邮件钓鱼告警研判',
      '关联上报邮件、沙箱结论与威胁情报，生成告警研判和分析员交接草稿。',
      ['email', 'triage'],
      'response',
      { status: 'active', trigger: 'webhook', source: 'project' },
      [
        '节点数',
        'Workflow nodes',
        '8 nodes · analyst approval before containment',
      ],
    ),
    seed(
      'endpoint-containment',
      '终端隔离审批',
      '整理终端告警证据并起草隔离申请，任何处置必须经过人工审批。',
      ['endpoint', 'containment'],
      'response',
      { status: 'draft', trigger: 'manual', source: 'project' },
      ['节点数', 'Workflow nodes', '6 nodes · draft, never executed'],
    ),
    seed(
      'dns-beacon-review',
      'DNS 异常通信调查',
      '结合威胁情报与已审批应用基线，复核周期性 DNS 通信摘要。',
      ['dns', 'network'],
      'hunting',
      { status: 'active', trigger: 'schedule', source: 'global' },
      ['节点数', 'Workflow nodes', '9 nodes · synthetic DNS evidence'],
    ),
    seed(
      'identity-anomaly',
      '身份异常告警证据复核',
      '汇总异常登录告警证据，仅供复核，不修改账号权限。',
      ['identity', 'triage'],
      'hunting',
      { status: 'active', trigger: 'webhook', source: 'project' },
      ['节点数', 'Workflow nodes', '7 nodes · read-only review'],
    ),
    seed(
      'external-assets',
      '外部资产台账核对',
      '对照已授权资产台账与模拟暴露面快照，标记资产归属差异。',
      ['asset', 'inventory'],
      'exposure',
      { status: 'active', trigger: 'schedule', source: 'global' },
      ['节点数', 'Workflow nodes', '5 nodes · no network scanning'],
    ),
    seed(
      'patch-exceptions',
      '补丁豁免续期审查',
      '将即将到期的修复豁免交由业务负责人确认，保留审批责任链。',
      ['vulnerability', 'governance'],
      'exposure',
      { status: 'draft', trigger: 'schedule', source: 'project' },
      ['节点数', 'Workflow nodes', '4 nodes · owner sign-off required'],
    ),
    seed(
      'audit-evidence',
      '季度控制项证据归集',
      '整理控制项证明、证据来源与保留期限，生成季度审计材料包。',
      ['audit', 'evidence'],
      'audit',
      { status: 'active', trigger: 'manual', source: 'global' },
      ['节点数', 'Workflow nodes', '11 nodes · mock evidence only'],
    ),
    seed(
      'access-certification',
      '特权访问季度复核',
      '为季度治理会议准备权限复核工作表，保留已归档流程作参考。',
      ['identity', 'audit'],
      'audit',
      { status: 'archived', trigger: 'schedule', source: 'project' },
      ['节点数', 'Workflow nodes', '6 nodes · superseded review procedure'],
    ),
    seed(
      'ransomware-recovery',
      '勒索事件恢复准备度检查——跨地域证据保全、隔离备份验证及关键业务负责人联合审批与分阶段恢复演练',
      '在桌面演练中协调备份验证记录与分阶段恢复决策，不执行恢复操作。',
      ['recovery', 'tabletop'],
      'response',
      { status: 'draft', trigger: 'manual', source: 'project' },
      ['节点数', 'Workflow nodes', '14 nodes · tabletop exercise only'],
    ),
    seed(
      'certificate-review',
      '证书续期责任人核对',
      '将证书到期通知与应用归属台账关联，确认续期责任人。',
      ['certificate', 'inventory'],
      'exposure',
      { status: 'active', trigger: 'schedule', source: 'global' },
      ['节点数', 'Workflow nodes', '5 nodes · no certificate changes'],
    ),
    seed(
      'legacy-alert-routing',
      '旧版告警路由迁移参考',
      '保留已退役的告警路由图，作为迁移前后对照与审查材料。',
      ['migration', 'triage'],
      null,
      { status: 'archived', trigger: 'webhook', source: 'project' },
      ['节点数', 'Workflow nodes', '3 nodes · archived reference'],
    ),
    seed(
      'third-party-intake',
      '第三方安全事件接报',
      '在确定响应负责人之前，起草供应商安全事件接报清单。',
      ['vendor', 'intake'],
      null,
      { status: 'draft', trigger: 'manual', source: 'global' },
      ['节点数', 'Workflow nodes', '4 nodes · ownership not assigned'],
    ),
  ],
  agent: [
    seed(
      'incident-coordinator',
      '安全事件协作负责人',
      '维护安全事件时间线，为已授权的专业角色准备协作任务。',
      ['incident', 'coordination'],
      'response',
      { role: 'primary', native: 'yes', delegatable: 'no' },
      ['工具数', 'Available tools', '8 mock tool bindings · no execution'],
    ),
    seed(
      'email-analyst',
      '邮件告警证据分析员',
      '解析模拟邮件头与邮件告警证据，为复核人员提供摘要。',
      ['email', 'evidence'],
      'response',
      { role: 'subagent', native: 'no', delegatable: 'yes' },
      ['工具数', 'Available tools', '5 mock read-only tool bindings'],
    ),
    seed(
      'hunting-lead',
      '威胁情报狩猎负责人',
      '依据威胁情报组织假设驱动的狩猎，使用授权数据集并设置复核节点。',
      ['hunting', 'hypothesis'],
      'hunting',
      { role: 'primary', native: 'no', delegatable: 'no' },
      ['工具数', 'Available tools', '7 mock tool bindings'],
    ),
    seed(
      'identity-reviewer',
      '身份行为复核员',
      '复核会话异常与身份告警，不跨越账号权限和身份边界。',
      ['identity', 'hunting'],
      'hunting',
      { role: 'subagent', native: 'yes', delegatable: 'yes' },
      ['工具数', 'Available tools', '4 mock read-only tool bindings'],
    ),
    seed(
      'exposure-owner',
      '暴露面修复规划员',
      '按业务重要性和责任人，对模拟风险发现进行修复优先级排序。',
      ['vulnerability', 'planning'],
      'exposure',
      { role: 'primary', native: 'no', delegatable: 'no' },
      ['工具数', 'Available tools', '6 mock tool bindings'],
    ),
    seed(
      'cloud-posture',
      '云安全态势复核员',
      '审核导出的云控制项快照，不接入真实租户或云账号。',
      ['cloud', 'posture'],
      'exposure',
      { role: 'subagent', native: 'no', delegatable: 'yes' },
      ['工具数', 'Available tools', '3 mock snapshot readers'],
    ),
    seed(
      'audit-coordinator',
      '控制项保障协调员',
      '组织控制项审查，跟踪模拟证据的完整性与责任归属。',
      ['audit', 'governance'],
      'audit',
      { role: 'primary', native: 'yes', delegatable: 'no' },
      ['工具数', 'Available tools', '5 mock tool bindings'],
    ),
    seed(
      'evidence-curator',
      '证据来源与保留管理员',
      '标注证据来源、保留策略与复核责任人，便于审计追溯。',
      ['evidence', 'retention'],
      'audit',
      { role: 'subagent', native: 'no', delegatable: 'yes' },
      ['工具数', 'Available tools', '4 mock metadata tools'],
    ),
    seed(
      'network-triage',
      '网络遥测告警研判专家——分区环境证据关联、受限数据处理及跨团队审查交接与全流程来源追溯',
      '根据脱敏桌面演练记录归纳网络告警与情报线索。',
      ['network', 'triage'],
      'response',
      { role: 'subagent', native: 'yes', delegatable: 'yes' },
      ['工具数', 'Available tools', '6 mock telemetry tools'],
    ),
    seed(
      'detection-reviewer',
      '检测规则覆盖复核员',
      '对照模拟检测规则台账与狩猎假设，识别覆盖缺口。',
      ['detection', 'coverage'],
      'hunting',
      { role: 'subagent', native: 'no', delegatable: 'no' },
      [
        '工具数',
        'Available tools',
        '2 mock inventory tools · manual assignment only',
      ],
    ),
    seed(
      'tabletop-facilitator',
      '安全桌面演练主持人',
      '围绕虚构安全事件起草演练问题与讨论纪要。',
      ['tabletop', 'training'],
      null,
      { role: 'primary', native: 'no', delegatable: 'no' },
      ['工具数', 'Available tools', '0 tool bindings · discussion-only role'],
    ),
    seed(
      'handoff-reviewer',
      '值班交接质量复核员',
      '检查分析员交接中是否包含负责人、时间戳和后续行动。',
      ['handoff', 'quality'],
      null,
      { role: 'subagent', native: 'yes', delegatable: 'yes' },
      ['工具数', 'Available tools', '2 mock note readers'],
    ),
  ],
  skill: [
    seed(
      'email-headers',
      '邮件告警头部解析',
      '解析脱敏邮件头样本中的路由异常与告警线索。',
      ['email', 'triage'],
      'response',
      { eligibility: 'ready', enabled: 'yes', source: 'global' },
      [
        '依赖说明',
        'Dependency notes',
        'Bundled text parser present in the mock manifest.',
      ],
    ),
    seed(
      'memory-timeline',
      '内存取证时间线复核',
      '为已导出的内存分析结果建立复核清单，展示缺少依赖时的行为。',
      ['forensics', 'timeline'],
      'response',
      { eligibility: 'missing', enabled: 'yes', source: 'project' },
      [
        '依赖说明',
        'Dependency notes',
        'Missing dependency: memory-report-reader. No installation has been attempted.',
      ],
    ),
    seed(
      'dns-hypotheses',
      'DNS 威胁情报狩猎假设构建',
      '结合威胁情报，将模拟 DNS 摘要转化为可复核的狩猎假设。',
      ['dns', 'hunting'],
      'hunting',
      { eligibility: 'ready', enabled: 'yes', source: 'global' },
      [
        '依赖说明',
        'Dependency notes',
        'Requires only the bundled sample dataset.',
      ],
    ),
    seed(
      'sigma-review',
      '告警检测规则质量复核',
      '检查提供的检测规则字段映射与证据假设，依赖未检查时保持未知。',
      ['detection', 'quality'],
      'hunting',
      { eligibility: 'unknown', enabled: 'yes', source: 'project' },
      [
        '依赖说明',
        'Dependency notes',
        'Dependency discovery has not run; rule-linter availability is unknown.',
      ],
    ),
    seed(
      'cloud-controls',
      '云安全控制项映射',
      '将导出的态势发现映射至内部控制项清单，不使用云凭据。',
      ['cloud', 'posture'],
      'exposure',
      { eligibility: 'ready', enabled: 'yes', source: 'global' },
      [
        '依赖说明',
        'Dependency notes',
        'Mock control catalog is bundled; no cloud credentials are used.',
      ],
    ),
    seed(
      'sbom-review',
      '软件物料清单审查',
      '审查示例 SBOM 中的软件包归属与修复说明，明确缺少依赖。',
      ['supply-chain', 'vulnerability'],
      'exposure',
      { eligibility: 'missing', enabled: 'no', source: 'project' },
      [
        '依赖说明',
        'Dependency notes',
        'Missing dependency: sbom-normalizer. Skill is disabled.',
      ],
    ),
    seed(
      'evidence-retention',
      '证据保留策略检查',
      '在证据进入审查材料包之前，准备保留期限与合规性问题。',
      ['audit', 'retention'],
      'audit',
      { eligibility: 'ready', enabled: 'yes', source: 'global' },
      [
        '依赖说明',
        'Dependency notes',
        'No external dependencies in the mock manifest.',
      ],
    ),
    seed(
      'access-review',
      '访问权限复核工作表',
      '将匿名化角色导出整理为复核工作表，示例依赖就绪但技能已停用。',
      ['identity', 'audit'],
      'audit',
      { eligibility: 'ready', enabled: 'no', source: 'project' },
      [
        '依赖说明',
        'Dependency notes',
        'Dependencies are present; intentionally disabled pending owner approval.',
      ],
    ),
    seed(
      'disk-artifacts',
      '磁盘取证痕迹解释——证据来源核验、保管链记录、跨团队交接复核及受限环境下的依赖可用性说明',
      '解释取证痕迹元数据，不读取真实磁盘，也不采集文件。',
      ['forensics', 'evidence'],
      'response',
      { eligibility: 'unknown', enabled: 'no', source: 'project' },
      [
        '依赖说明',
        'Dependency notes',
        'Optional artifact-indexer has not been checked in this environment.',
      ],
    ),
    seed(
      'asset-ownership',
      '资产归属一致性核对',
      '核对模拟资产标签与业务归属记录，标记需要负责人确认的差异。',
      ['asset', 'inventory'],
      'exposure',
      { eligibility: 'ready', enabled: 'yes', source: 'global' },
      [
        '依赖说明',
        'Dependency notes',
        'Bundled CSV reader declared ready in the fixture.',
      ],
    ),
    seed(
      'container-baseline',
      '容器安全基线复核',
      '对照安全基线复核静态工作负载清单；已启用不代表依赖已就绪。',
      ['container', 'baseline'],
      null,
      { eligibility: 'missing', enabled: 'yes', source: 'project' },
      [
        '依赖说明',
        'Dependency notes',
        'Missing dependency: manifest-policy-pack. Enabled does not mean ready.',
      ],
    ),
    seed(
      'vendor-questionnaire',
      '供应商安全保障问卷',
      '根据导入的供应商服务说明起草审查问题，保留未知依赖状态。',
      ['vendor', 'governance'],
      null,
      { eligibility: 'unknown', enabled: 'yes', source: 'global' },
      [
        '依赖说明',
        'Dependency notes',
        'Imported manifest has no dependency declaration; readiness remains unknown.',
      ],
    ),
  ],
  tool: [
    seed(
      'case-note-reader',
      '告警事件笔记读取器',
      '读取内置演示工作区中的脱敏告警事件笔记。',
      ['incident', 'evidence'],
      'response',
      { source: 'builtin', enabled: 'yes', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'Read-only · case reference input · sample text output',
      ],
    ),
    seed(
      'containment-request',
      '终端处置申请草拟器',
      '准备终端处置审批材料，不隔离任何真实主机。',
      ['endpoint', 'containment'],
      'response',
      { source: 'api', enabled: 'yes', confirmation: 'required' },
      [
        '调用约定',
        'Invocation contract',
        'Mock adapter only · human approval required',
      ],
    ),
    seed(
      'event-search',
      '安全告警与情报检索连接器',
      '检索模拟安全事件集合中的告警与情报记录，不连接外部服务。',
      ['hunting', 'search'],
      'hunting',
      { source: 'mcp', enabled: 'yes', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'Mock MCP binding · no server connection configured',
      ],
    ),
    seed(
      'flow-summary',
      '网络流量摘要读取器',
      '读取虚构网络传感器的预录制流量摘要，不捕获实时流量。',
      ['network', 'hunting'],
      'hunting',
      { source: 'device', enabled: 'yes', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'Sample device response · no traffic capture',
      ],
    ),
    seed(
      'asset-normalizer',
      '资产标签标准化工具',
      '将示例资产清单标准化为稳定的负责人和业务标签。',
      ['asset', 'inventory'],
      'exposure',
      { source: 'plugin_py', enabled: 'yes', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'Python plugin manifest · mock CSV input and output',
      ],
    ),
    seed(
      'exception-register',
      '修复豁免登记工具',
      '为治理复核人员起草修复豁免登记变更，当前示例已停用。',
      ['vulnerability', 'governance'],
      'exposure',
      { source: 'plugin_yaml', enabled: 'no', confirmation: 'required' },
      [
        '调用约定',
        'Invocation contract',
        'YAML plugin manifest · disabled pending review',
      ],
    ),
    seed(
      'evidence-manifest',
      '审计证据清单导出预览',
      '构建模拟证据标签与保留标记清单，仅提供预览，不写入文件。',
      ['audit', 'evidence'],
      'audit',
      { source: 'builtin', enabled: 'yes', confirmation: 'required' },
      [
        '调用约定',
        'Invocation contract',
        'Preview-only export · no file or browser APIs invoked',
      ],
    ),
    seed(
      'control-catalog',
      '控制项目录查询',
      '在虚构的保障目录中查询控制项标签，不配置真实接口或凭据。',
      ['audit', 'control'],
      'audit',
      { source: 'api', enabled: 'no', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'Disabled mock API binding · no endpoint or credentials',
      ],
    ),
    seed(
      'timeline-merge',
      '安全事件时间线合并——跨项目告警排序、证据来源保留、时区差异复核及人工确认后的全流程审查与变更预览',
      '合并示例时间戳并保留证据来源，不写回原始事件。',
      ['incident', 'timeline'],
      'response',
      { source: 'plugin_py', enabled: 'yes', confirmation: 'required' },
      [
        '调用约定',
        'Invocation contract',
        'Preview-only Python plugin · review before applying changes',
      ],
    ),
    seed(
      'rule-catalog',
      '检测规则情报目录连接器',
      '读取虚构检测规则的归属与情报标签目录，连接器处于停用状态。',
      ['detection', 'inventory'],
      'hunting',
      { source: 'mcp', enabled: 'no', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'Mock MCP binding disabled · server is not configured',
      ],
    ),
    seed(
      'device-health',
      '设备健康快照读取器',
      '读取模拟设备快照，不检测当前连通性；未检查不代表离线。',
      ['device', 'health'],
      null,
      { source: 'device', enabled: 'no', confirmation: 'required' },
      [
        '调用约定',
        'Invocation contract',
        'Stored sample only · unchecked is not offline',
      ],
    ),
    seed(
      'handoff-checklist',
      '值班交接清单渲染器',
      '基于本地演示格式渲染分析员交接清单。',
      ['handoff', 'quality'],
      null,
      { source: 'plugin_yaml', enabled: 'yes', confirmation: 'not-required' },
      [
        '调用约定',
        'Invocation contract',
        'YAML plugin manifest · deterministic mock checklist',
      ],
    ),
  ],
  'device-template': [
    seed(
      'edge-firewall',
      'Aurora Edge 防火墙连接器',
      '为虚构边界网关描述只读防火墙证据字段，模板安装不代表设备在线。',
      ['network', 'firewall'],
      'response',
      { state: 'installed', vendor: 'aurora-net', source: 'bundled' },
      [
        '模板版本',
        'Template version',
        '2.4.0 · installed locally; instance connectivity is independent',
      ],
    ),
    seed(
      'endpoint-hub',
      'Cedar 终端响应中心',
      '为虚构终端响应记录提供需要审批的结构定义。',
      ['endpoint', 'response'],
      'response',
      { state: 'installed', vendor: 'cedar-sec', source: 'global' },
      [
        '模板版本',
        'Template version',
        '1.8.2 · installed manifest only, not a connection check',
      ],
    ),
    seed(
      'flow-sensor',
      'Harbor 网络流量传感器',
      '将模拟流量遥测字段映射至证据浏览器，示例包含可更新版本。',
      ['network', 'telemetry'],
      'hunting',
      {
        state: 'updateAvailable',
        vendor: 'harbor-observability',
        source: 'global',
      },
      [
        '模板版本',
        'Template version',
        '3.1.0 installed · 3.2.0 available in the mock catalog',
      ],
    ),
    seed(
      'dns-resolver',
      'Aurora DNS 可视性适配器',
      '描述虚构解析器的导出格式，不采集实时 DNS 查询。',
      ['dns', 'telemetry'],
      'hunting',
      { state: 'installed', vendor: 'aurora-net', source: 'bundled' },
      ['模板版本', 'Template version', '1.6.1 · bundled fixture schema'],
    ),
    seed(
      'cloud-posture',
      'Lattice 云态势桥接器',
      '描述虚构云态势服务的快照导入格式，模板尚未安装。',
      ['cloud', 'posture'],
      'exposure',
      { state: 'available', vendor: 'lattice-edge', source: 'global' },
      [
        '模板版本',
        'Template version',
        '2.0.0 · not installed; no instance is provisioned',
      ],
    ),
    seed(
      'asset-registry',
      'Cedar 资产台账适配器',
      '将模拟业务归属记录映射至资产复核格式，模板仅保存在项目本地。',
      ['asset', 'inventory'],
      'exposure',
      { state: 'localOnly', vendor: 'cedar-sec', source: 'project' },
      [
        '模板版本',
        'Template version',
        '0.5.0-local · project manifest, not published',
      ],
    ),
    seed(
      'audit-vault',
      'Harbor 审计证据保管库',
      '为虚构证据保管库描述保留标签与复核标记。',
      ['audit', 'retention'],
      'audit',
      { state: 'installed', vendor: 'harbor-observability', source: 'global' },
      ['模板版本', 'Template version', '4.0.1 · installed read-only schema'],
    ),
    seed(
      'access-gateway',
      'Lattice 特权访问网关',
      '声明虚构权限复核字段，不包含任何凭据材料。',
      ['identity', 'audit'],
      'audit',
      { state: 'updateAvailable', vendor: 'lattice-edge', source: 'bundled' },
      [
        '模板版本',
        'Template version',
        '2.2.0 installed · 2.3.0 available in the mock catalog',
      ],
    ),
    seed(
      'recovery-console',
      'Cedar 恢复编排控制台——隔离备份验证、证据保全审批与跨业务联合演练中的模板结构完整性诊断',
      '保留故意缺少连接结构的模拟模板，以演示损坏诊断；不可创建实例。',
      ['recovery', 'diagnostics'],
      'response',
      { state: 'broken', vendor: 'cedar-sec', source: 'project' },
      [
        '模板诊断',
        'Template diagnostics',
        '0.9.0 · broken fixture: required connection schema is missing; cannot instantiate',
      ],
    ),
    seed(
      'certificate-hub',
      'Aurora 证书生命周期中心',
      '为未来资产集成描述证书责任人导出格式，模板可安装但尚未安装。',
      ['certificate', 'inventory'],
      'exposure',
      { state: 'available', vendor: 'aurora-net', source: 'global' },
      [
        '模板版本',
        'Template version',
        '1.0.0 · available mock package, not installed',
      ],
    ),
    seed(
      'lab-collector',
      'Harbor 实验室遥测采集器',
      '描述仅供项目离线训练样本使用的采集器。',
      ['lab', 'telemetry'],
      null,
      { state: 'localOnly', vendor: 'harbor-observability', source: 'project' },
      ['模板版本', 'Template version', '0.3.0-local · training manifest only'],
    ),
    seed(
      'branch-gateway',
      'Lattice 分支网关适配器',
      '描述尚未分配业务分组的虚构分支网关，模板已安装但不推断连通性。',
      ['network', 'branch'],
      null,
      { state: 'installed', vendor: 'lattice-edge', source: 'bundled' },
      [
        '模板版本',
        'Template version',
        '1.2.0 · installed; no connectivity can be inferred',
      ],
    ),
  ],
  'device-instance': [
    seed(
      'edge-gateway',
      '主站边界证据网关',
      '虚构防火墙实例，来源模板虽已安装，但从未进行连接检查。',
      ['network', 'firewall'],
      'response',
      { status: 'unchecked', room: 'dc-primary', connection: 'api' },
      [
        '连接说明',
        'Connection notes',
        'Unchecked: no connection test has run. Installed template does not mean online.',
      ],
      'edge-firewall',
    ),
    seed(
      'endpoint-response',
      '终端响应证据中心',
      '虚构终端中心，展示模拟适配器超时，并非真实故障告警。',
      ['endpoint', 'response'],
      'response',
      { status: 'error', room: 'dc-primary', connection: 'api' },
      [
        '连接说明',
        'Connection notes',
        'Simulated timeout fixture. Template is installed; connectivity is a separate state.',
      ],
      'endpoint-hub',
    ),
    seed(
      'flow-observer',
      '骨干网络流量证据观察点',
      '虚构网络流量采集器的已连接状态快照，仅为模拟记录。',
      ['network', 'telemetry'],
      'hunting',
      { status: 'connected', room: 'dc-primary', connection: 'api' },
      [
        '连接说明',
        'Connection notes',
        'Mock connected snapshot recorded 2026-09-12; no current probe.',
      ],
      'flow-sensor',
    ),
    seed(
      'dns-observer',
      '备站 DNS 证据观察点',
      '虚构 DNS 适配器，展示已保存的成功演示响应，不执行浏览器自动化。',
      ['dns', 'telemetry'],
      'hunting',
      { status: 'connected', room: 'dc-backup', connection: 'webcli' },
      [
        '连接说明',
        'Connection notes',
        'Sanitized Web CLI transcript only; no browser automation.',
      ],
      'dns-resolver',
    ),
    seed(
      'asset-bridge',
      '云业务归属桥接实例',
      '项目本地适配器，等待首次经过审批的资产连接检查。',
      ['cloud', 'inventory'],
      'exposure',
      { status: 'unchecked', room: 'cloud', connection: 'workflow' },
      [
        '连接说明',
        'Connection notes',
        'Workflow binding has not been checked; no service endpoint is configured.',
      ],
      'asset-registry',
    ),
    seed(
      'recovery-asset-bridge',
      '恢复站点资产归属桥接实例',
      '虚构备站资产适配器，展示模拟响应格式不匹配，不联系远端服务。',
      ['asset', 'recovery'],
      'exposure',
      { status: 'error', room: 'dc-backup', connection: 'api' },
      [
        '连接说明',
        'Connection notes',
        'Mock schema mismatch in the response; no remote service was contacted.',
      ],
      'asset-registry',
    ),
    seed(
      'audit-archive',
      '云审计证据归档实例',
      '虚构证据保管实例，展示成功的只读演示响应，不上传证据。',
      ['audit', 'retention'],
      'audit',
      { status: 'connected', room: 'cloud', connection: 'api' },
      [
        '连接说明',
        'Connection notes',
        'Stored mock response only; no evidence is uploaded.',
      ],
      'audit-vault',
    ),
    seed(
      'access-review-gateway',
      '特权访问复核网关',
      '等待首次人工连接检查的权限复核实例，不保存任何真实凭据。',
      ['identity', 'audit'],
      'audit',
      { status: 'unchecked', room: 'dc-primary', connection: 'webcli' },
      [
        '连接说明',
        'Connection notes',
        'Unchecked Web CLI binding; there are no stored credentials.',
      ],
      'access-gateway',
    ),
    seed(
      'recovery-edge',
      '恢复网络证据网关——隔离备用环境、分析员审批连接检查及跨业务证据保全协作中的原机房只读展示',
      '虚构备用网关，展示模拟证书校验错误；来源模板保持已安装状态。',
      ['network', 'recovery'],
      'response',
      { status: 'error', room: 'dc-backup', connection: 'workflow' },
      [
        '连接说明',
        'Connection notes',
        'Simulated certificate mismatch. Installed template remains unchanged.',
      ],
      'edge-firewall',
    ),
    seed(
      'lab-flow-observer',
      '实验室流量回放观察点',
      '实验室实例，使用离线遥测回放产生成功演示响应，并非实时在线检测。',
      ['lab', 'telemetry'],
      'hunting',
      { status: 'connected', room: 'lab', connection: 'workflow' },
      [
        '连接说明',
        'Connection notes',
        'Offline replay fixture; connected is illustrative, not a live status.',
      ],
      'lab-collector',
    ),
    seed(
      'training-collector',
      '培训遥测采集实例',
      '尚未分组的实验室采集实例；修改业务分组必须保留原机房。',
      ['lab', 'training'],
      null,
      { status: 'unchecked', room: 'lab', connection: 'api' },
      [
        '连接说明',
        'Connection notes',
        'No connection test; collection assignment never changes the original room.',
      ],
      'lab-collector',
    ),
    seed(
      'branch-evidence',
      '分支证据网关实例',
      '尚未分组的虚构分支适配器，等待资产责任人确认，连接状态未检查。',
      ['branch', 'network'],
      null,
      { status: 'unchecked', room: 'cloud', connection: 'webcli' },
      [
        '连接说明',
        'Connection notes',
        'Template installed, instance unchecked; no online state is inferred.',
      ],
      'branch-gateway',
    ),
  ],
};

// Each variant represents a different project/environment, not duplicate live resources.
const contexts = [
  { id: 'commerce', name: '电商生产', environment: 'production' },
  { id: 'research', name: '研究预发布', environment: 'staging' },
] as const;
const chineseTags: Record<string, string> = {
  email: '邮件安全',
  triage: '告警研判',
  hunting: '威胁情报',
  detection: '检测规则',
  response: '告警响应',
  incident: '事件响应',
  evidence: '证据',
  network: '网络',
  inventory: '资产清单',
  audit: '审计',
  identity: '身份',
  cloud: '云安全',
  vulnerability: '漏洞治理',
  recovery: '恢复演练',
  governance: '安全治理',
};

export const MOCK_ITEMS: PluginItem[] = scopes.flatMap((scope, scopeIndex) =>
  seeds[scope].flatMap((entry, seedIndex) =>
    contexts.map((context, contextIndex): PluginItem => {
      const details = [
        { label: text('项目环境', 'Project environment'), value: context.name },
        { label: { ...entry.detail.label }, value: entry.detail.value },
        {
          label: text('数据性质', 'Data provenance'),
          value: '固定模拟数据 · 未执行真实检查，不包含凭据或真实服务地址。',
        },
      ];
      if (scope === 'device-template' && entry.attributes.state === 'broken') {
        details.push({
          label: text('模板版本', 'Template version'),
          value: '0.9.0 · intentionally broken mock manifest',
        });
      }
      if (scope === 'device-instance') {
        const room = SCOPE_CONFIG[scope].facets
          .find((definition) => definition.key === 'room')
          ?.options.find((option) => option.value === entry.attributes.room);
        details.push(
          {
            label: text('原机房（只读）', 'Original room (read-only)'),
            value: `${room?.label.zh ?? entry.attributes.room} · ${entry.attributes.room}`,
          },
          {
            label: text('来源模板', 'Source template'),
            value: `device-template-${entry.templateSlug}-${context.id}`,
          },
        );
      }
      return {
        id: `${scope}-${entry.slug}-${context.id}`,
        scope,
        name: `${entry.name} · ${context.name}`,
        identifier: `${context.id}/${scope}/${entry.slug}`,
        description: `模拟：${entry.description} 用于${context.name}项目，不执行真实操作或连通性检查。`,
        tags: [
          ...entry.tags.flatMap((tag) =>
            chineseTags[tag] ? [chineseTags[tag], tag] : [tag],
          ),
          context.id,
          context.environment,
        ],
        collectionId: entry.group === null ? null : groupId(scope, entry.group),
        favorite: (seedIndex + contextIndex) % 4 === 0,
        updatedAt: new Date(
          Date.UTC(2026, 8, 16, 12) -
            (scopeIndex * 24 + seedIndex * 2 + contextIndex) * 3_600_000,
        ).toISOString(),
        attributes: { ...entry.attributes },
        details,
      };
    }),
  ),
);
