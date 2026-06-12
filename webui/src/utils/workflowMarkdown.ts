import type { Workflow, WorkflowEdge, WorkflowJSON, WorkflowNode, WorkflowTrigger } from '@/api/workflow';

const NODE_TYPE_LABELS: Record<string, string> = {
  python: 'Python',
  logic: '逻辑',
  branch: '分支',
  loop: '循环',
  tool: '工具',
  llm: 'LLM',
  http_request: 'HTTP',
  subworkflow: '子工作流',
};

function cleanText(value?: string | null): string {
  return (value || '').replace(/\s+/g, ' ').trim();
}

function tableCell(value: unknown): string {
  const text = value === undefined || value === null || value === ''
    ? '-'
    : String(value);
  return text.replace(/\|/g, '\\|').replace(/\n+/g, '<br>');
}

function formatList(items: string[]): string {
  const useful = items.map(cleanText).filter(Boolean);
  return useful.length > 0 ? useful.join('、') : '-';
}

function nodeLabel(node?: WorkflowNode): string {
  if (!node) return '-';
  return `${node.id} (${NODE_TYPE_LABELS[node.type] || node.type})`;
}

function summarizeDescription(text?: string): string {
  const value = cleanText(text);
  if (!value) return '暂无描述。';
  return value;
}

function outgoingEdges(nodeId: string, edges: WorkflowEdge[]): WorkflowEdge[] {
  return edges
    .filter((edge) => edge.from === nodeId)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

function incomingEdges(nodeId: string, edges: WorkflowEdge[]): WorkflowEdge[] {
  return edges
    .filter((edge) => edge.to === nodeId)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

function describeEdge(edge: WorkflowEdge): string {
  const extras: string[] = [];
  if (edge.label) extras.push(`分支: ${edge.label}`);
  if (edge.mapping && Object.keys(edge.mapping).length > 0) {
    extras.push(`映射: ${Object.entries(edge.mapping).map(([k, v]) => `${k} <- ${v}`).join(', ')}`);
  }
  if (edge.const && Object.keys(edge.const).length > 0) {
    extras.push(`常量: ${Object.entries(edge.const).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')}`);
  }
  return extras.length > 0 ? `${edge.from} -> ${edge.to} (${extras.join('; ')})` : `${edge.from} -> ${edge.to}`;
}

function buildLinearFlow(workflowJson: WorkflowJSON): string[] {
  const nodesById = new Map(workflowJson.nodes.map((node) => [node.id, node]));
  const visited = new Set<string>();
  const result: string[] = [];
  let current = workflowJson.start || workflowJson.nodes[0]?.id;

  while (current && !visited.has(current)) {
    const node = nodesById.get(current);
    if (!node) break;
    visited.add(current);
    result.push(node.id);
    const next = outgoingEdges(current, workflowJson.edges)[0]?.to;
    current = next;
  }

  workflowJson.nodes.forEach((node) => {
    if (!visited.has(node.id)) result.push(node.id);
  });

  return result;
}

function describeNodeInputs(node: WorkflowNode, workflowJson: WorkflowJSON): string {
  const incoming = incomingEdges(node.id, workflowJson.edges);
  if (node.id === workflowJson.start || incoming.length === 0) return '工作流输入 / 触发器输入';
  return incoming.map((edge) => edge.from).join('、');
}

function describeNodeOutputs(node: WorkflowNode, workflowJson: WorkflowJSON): string {
  const outgoing = outgoingEdges(node.id, workflowJson.edges);
  if (outgoing.length === 0) return '工作流最终输出';
  return outgoing.map((edge) => edge.to).join('、');
}

function inferEditFocus(node: WorkflowNode): string {
  const haystack = `${node.id} ${node.description || ''}`.toLowerCase();
  if (haystack.includes('dedup') || haystack.includes('minhash') || haystack.includes('lsh')) {
    return '修改去重阈值、状态保存、结果落盘路径或输出格式时，优先编辑这里。';
  }
  if (haystack.includes('normalize')) {
    return '修改统一字段、字段重命名、来源差异兼容时，优先编辑这里。';
  }
  if (haystack.includes('filter')) {
    return '修改保留/丢弃规则、方向判断、告警类型分类时，优先编辑这里。';
  }
  if (haystack.includes('receive') || haystack.includes('incoming') || haystack.includes('syslog')) {
    return '修改输入来源、日志格式识别、TDP/SkyEye 自动识别规则时，优先从这里开始。';
  }
  if (node.type === 'tool') return '修改外部工具名称、参数映射或工具返回值处理时，优先检查这里。';
  if (node.type === 'llm') return '修改提示词、模型或结构化输出要求时，优先检查这里。';
  return '修改此步骤的输入、输出或执行逻辑时，先确认上下游字段是否同步变化。';
}

function summarizeTrigger(trigger: WorkflowTrigger): string {
  const enabled = trigger.enabled === false ? '关闭' : '启用';
  const name = trigger.name || trigger.id;
  return `- ${name}: ${trigger.type}，${enabled}${trigger.description ? `，${trigger.description}` : ''}`;
}

function summarizeSampleInputs(workflowJson: WorkflowJSON): string[] {
  const sampleInputs = workflowJson.metadata?.sampleInputs;
  if (!sampleInputs || typeof sampleInputs !== 'object') return [];
  return Object.entries(sampleInputs).map(([key, value]) => {
    const preview = typeof value === 'string'
      ? value
      : JSON.stringify(value);
    return `- ${key}: ${preview.length > 120 ? `${preview.slice(0, 120)}...` : preview}`;
  });
}

export function buildWorkflowMarkdown(workflow: Workflow): string {
  const workflowJson = workflow.workflowJson;
  const orderedNodeIds = buildLinearFlow(workflowJson);
  const nodesById = new Map(workflowJson.nodes.map((node) => [node.id, node]));
  const startNode = nodesById.get(workflowJson.start);
  const terminalNodes = workflowJson.nodes.filter((node) => outgoingEdges(node.id, workflowJson.edges).length === 0);
  const triggers = workflowJson.triggers || [];
  const sampleInputLines = summarizeSampleInputs(workflowJson);
  const workflowDir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;
  const generatedAt = new Date().toLocaleString();

  const nodeTable = orderedNodeIds.map((nodeId, index) => {
    const node = nodesById.get(nodeId);
    if (!node) return '';
    return `| ${index + 1} | ${tableCell(node.id)} | ${tableCell(summarizeDescription(node.description))} | ${tableCell(describeNodeOutputs(node, workflowJson))} |`;
  }).filter(Boolean);

  const nodeSections = orderedNodeIds.map((nodeId, index) => {
    const node = nodesById.get(nodeId);
    if (!node) return '';
    const incoming = incomingEdges(node.id, workflowJson.edges).map(describeEdge);
    const outgoing = outgoingEdges(node.id, workflowJson.edges).map(describeEdge);
    return [
      `### 4.${index + 1} ${node.id}`,
      '',
      `职责: ${summarizeDescription(node.description)}`,
      '',
      `- 节点类型: ${NODE_TYPE_LABELS[node.type] || node.type}`,
      `- 输入来源: ${describeNodeInputs(node, workflowJson)}`,
      `- 输出去向: ${describeNodeOutputs(node, workflowJson)}`,
      `- 编辑重点: ${inferEditFocus(node)}`,
      incoming.length > 0 ? `- 上游关系: ${formatList(incoming)}` : '- 上游关系: 从工作流输入开始',
      outgoing.length > 0 ? `- 下游关系: ${formatList(outgoing)}` : '- 下游关系: 输出工作流结果',
    ].join('\n');
  }).filter(Boolean);

  return [
    `# ${workflow.name || workflow.id}`,
    '',
    '这份 `workflow.md` 是工作流的人类可编辑说明。它用来解释工作流的功能、处理原理、输入输出和可修改位置；机器执行仍以 `workflow.json` 为准。',
    '',
    '## 1. 功能概览',
    '',
    `一句话说明: ${workflow.description ? summarizeDescription(workflow.description) : '这个工作流会按固定步骤处理输入，并整理出稳定、可验证的输出结果。'}`,
    '',
    '基本信息:',
    '',
    `- 工作流 ID: \`${workflow.id}\``,
    `- 工作流目录: \`${workflowDir}\``,
    `- 分类: \`${workflow.category || 'default'}\``,
    `- 状态: \`${workflow.status || 'draft'}\``,
    `- 入口节点: ${nodeLabel(startNode)}`,
    `- 终点节点: ${formatList(terminalNodes.map(nodeLabel))}`,
    `- 生成时间: ${generatedAt}`,
    '',
    '适合在这里写清楚:',
    '',
    '- 这个工作流解决什么问题。',
    '- 适合处理什么输入。',
    '- 不负责处理什么边界场景。',
    '',
    '## 2. 原理和总体流程',
    '',
    '核心原理是把输入按节点顺序逐步加工，每个节点只负责一个清晰职责。流程顺序如下:',
    '',
    '```text',
    orderedNodeIds.join(' -> '),
    '```',
    '',
    '流程表:',
    '',
    '| 顺序 | 节点 | 做什么 | 下一步 |',
    '| --- | --- | --- | --- |',
    ...nodeTable,
    '',
    '编辑流程结构时，要同时确认节点顺序、边关系、字段映射和最终输出是否仍然一致。',
    '',
    '## 3. 输入说明',
    '',
    '本章用于说明工作流接受什么输入，以及入口节点如何理解这些输入。',
    '',
    sampleInputLines.length > 0
      ? '当前工作流保存了这些样例输入，可以先照着这些字段测试:'
      : '当前工作流还没有保存样例输入。建议先补一条最小可运行输入，方便后续测试。',
    '',
    ...(sampleInputLines.length > 0 ? sampleInputLines : ['- 待补充。']),
    '',
    '修改输入时，至少同步检查:',
    '',
    '- 入口节点是否能读取新字段。',
    '- 样例输入是否覆盖主要场景。',
    '- 下游节点是否还在引用旧字段名。',
    '- 发布方式中的参数说明是否需要更新。',
    '',
    '## 4. 模块逻辑',
    '',
    '本章按执行顺序解释每个节点。修改内部逻辑时，优先定位到对应节点，再检查它的上下游关系。',
    '',
    ...nodeSections.flatMap((section) => [section, '']),
    '## 5. 输出说明',
    '',
    '本章用于维护工作流最终返回什么，以及是否产生额外副作用。',
    '',
    '输出说明建议包含:',
    '',
    '- 返回给用户或调用方的核心字段。',
    '- 给下游系统继续消费的结构化字段。',
    '- 是否写文件、发通知、调用外部系统或更新状态。',
    '- 没有结果、部分失败、完全失败时分别返回什么。',
    '',
    '如果还不确定输出格式，先用一条样例跑通，再把真实返回字段补到这里。',
    '',
    '## 6. 发布方式',
    '',
    '发布页会根据 `config.json` 模板和运行时状态决定展示哪些能力；`workflow.md` 只负责解释这些能力的用途。',
    '',
    triggers.length > 0 ? '当前 `workflow.json` 里配置了这些触发器:' : '当前 `workflow.json` 里还没有显式触发器。',
    '',
    ...(triggers.length > 0 ? triggers.map(summarizeTrigger) : ['- 可以通过发布页配置 API、Syslog、Kafka、Schedule 或 Webhook 等方式。']),
    '',
    '发布相关编辑原则:',
    '',
    '- 改展示模板: 修改 `config.json`。',
    '- 改运行启停状态: 通过发布页或后端运行时状态处理。',
    '- 改参数语义: 同步更新本章、输入说明和相关节点。',
    '- 不要把明文密钥、长期 token 或私人路径写进 `workflow.md` 或 `config.json`。',
    '',
    '## 7. 编辑指南',
    '',
    '先判断你要改哪一类内容，再去找对应位置:',
    '',
    '| 修改目标 | 优先查看 |',
    '| --- | --- |',
    '| 输入格式、来源、样例 | 第 3 章和入口节点 |',
    '| 字段映射、清洗、分类 | 第 4 章对应节点 |',
    '| 分支、循环、节点增删 | `workflow.json` 和第 2 章流程表 |',
    '| 输出字段、落盘、通知 | 第 5 章和终点节点 |',
    '| API、Syslog、Kafka 等发布方式 | `config.json` 和第 6 章 |',
    '| 字段重命名 | 所有上下游节点、样例输入和输出说明 |',
    '',
    '编辑后建议把改动说明写回相应章节，让下一个人可以直接看懂为什么这样改。',
    '',
    '## 8. 验证方式',
    '',
    '最小验收清单:',
    '',
    '- [ ] 用一条正常样例能跑通。',
    '- [ ] 输出字段符合你的预期。',
    '- [ ] 如果改了字段名，下游节点没有继续引用旧字段。',
    '- [ ] 如果改了发布方式，发布页只展示应该出现的能力。',
    '- [ ] 没有明文密钥、长期 token 或私人路径写进工作流目录。',
    '',
  ].join('\n');
}
