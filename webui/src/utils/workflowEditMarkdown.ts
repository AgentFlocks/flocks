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

function truncateText(text: string, maxLength: number): string {
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
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

function summarizeOriginalMarkdown(markdown?: string): string {
  const lines = (markdown || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 20);
  if (lines.length === 0) return '原始 workflow.md 暂无内容。';
  return lines.map((line) => `> ${line}`).join('\n');
}

export function buildWorkflowEditMarkdown(workflow: Workflow): string {
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

  const nodeTable = workflowJson.nodes.map((node) => (
    `| ${tableCell(node.id)} | ${tableCell(NODE_TYPE_LABELS[node.type] || node.type)} | ${tableCell(summarizeDescription(node.description))} | ${tableCell(describeNodeOutputs(node, workflowJson))} |`
  ));

  const nodeSections = orderedNodeIds.map((nodeId, index) => {
    const node = nodesById.get(nodeId);
    if (!node) return '';
    const incoming = incomingEdges(node.id, workflowJson.edges).map(describeEdge);
    const outgoing = outgoingEdges(node.id, workflowJson.edges).map(describeEdge);
    return [
      `### ${index + 1}. ${node.id}`,
      '',
      `- 类型: ${NODE_TYPE_LABELS[node.type] || node.type}`,
      `- 作用: ${summarizeDescription(node.description)}`,
      `- 输入来源: ${describeNodeInputs(node, workflowJson)}`,
      `- 输出去向: ${describeNodeOutputs(node, workflowJson)}`,
      `- 编辑关注点: ${inferEditFocus(node)}`,
      incoming.length > 0 ? `- 入边: ${formatList(incoming)}` : '- 入边: 起点输入',
      outgoing.length > 0 ? `- 出边: ${formatList(outgoing)}` : '- 出边: 终点输出',
    ].join('\n');
  }).filter(Boolean);

  return [
    `# ${workflow.name || workflow.id} 工作流编辑文档`,
    '',
    '> 这份文件面向人阅读和编辑，用来解释工作流意图、流程、节点职责和可调整点。机器执行定义仍以 `workflow.json` 为准。',
    '',
    '## 1. 快速理解',
    '',
    `- 工作流 ID: ${workflow.id}`,
    `- 工作流目录: \`${workflowDir}\``,
    `- 入口节点: ${nodeLabel(startNode)}`,
    `- 终点节点: ${formatList(terminalNodes.map(nodeLabel))}`,
    `- 规模: ${workflowJson.nodes.length} 个节点，${workflowJson.edges.length} 条边`,
    `- 分类: ${workflow.category || 'default'}`,
    `- 生成时间: ${generatedAt}`,
    '',
    '一句话说明:',
    '',
    `${workflow.description || workflow.markdownContent ? truncateText(summarizeDescription(workflow.description || workflow.markdownContent), 260) : '这个工作流用于按既定节点顺序处理输入数据，并输出最终处理结果。'}`,
    '',
    '## 2. 流程地图',
    '',
    '推荐按下面顺序阅读和修改:',
    '',
    `\`${orderedNodeIds.join(' -> ')}\``,
    '',
    '| 节点 | 类型 | 主要职责 | 下一步 |',
    '| --- | --- | --- | --- |',
    ...nodeTable,
    '',
    '## 3. 节点详解',
    '',
    ...nodeSections.flatMap((section) => [section, '']),
    '## 4. 数据流和字段约定',
    '',
    '请先确认每条边的字段映射，再修改节点输出字段。字段名变化通常需要同步更新下游节点。',
    '',
    ...(workflowJson.edges.length > 0
      ? workflowJson.edges.map((edge) => `- ${describeEdge(edge)}`)
      : ['- 暂无显式边配置。']),
    '',
    '## 5. 触发器和输入样例',
    '',
    triggers.length > 0 ? '触发器:' : '触发器: 暂无显式触发器，通常通过手动测试或外部调用传入 inputs。',
    '',
    ...(triggers.length > 0 ? triggers.map(summarizeTrigger) : []),
    '',
    sampleInputLines.length > 0 ? '样例输入:' : '样例输入: 暂无保存样例，可在「概览 > 测试运行」中补充。',
    '',
    ...sampleInputLines,
    '',
    '## 6. 关键可调点',
    '',
    '- 输入解析: 输入字段、日志格式、来源类型识别。',
    '- 归一化: 统一字段名、默认值、来源差异兼容。',
    '- 过滤策略: 哪些数据继续进入下游，哪些数据被丢弃。',
    '- 去重策略: 相似度阈值、状态保存位置、历史窗口、结果文件格式。',
    '- 输出契约: 最终结果字段、文件路径、错误处理和下游消费方式。',
    '',
    '## 7. 编辑流程建议',
    '',
    '1. 先在本文档写清楚要改的业务规则。',
    '2. 再修改 `workflow.md`，让自然语言说明和预期行为一致。',
    '3. 最后同步 `workflow.json` 或节点代码，保证流程图和执行定义一致。',
    '4. 用一条最小样例验证输入、关键中间字段和最终输出。',
    '5. 如果涉及阈值、状态文件或落盘格式，需要记录默认值和回滚方式。',
    '',
    '## 8. 验收清单',
    '',
    '- [ ] 每个节点的输入来源清楚。',
    '- [ ] 每个节点的输出去向清楚。',
    '- [ ] 关键字段名在上下游保持一致。',
    '- [ ] 测试样例覆盖正常输入和至少一个边界情况。',
    '- [ ] 输出结果可以被人读懂，也可以被下游系统稳定解析。',
    '',
    '## 9. 原始 workflow.md 摘要',
    '',
    summarizeOriginalMarkdown(workflow.markdownContent),
    '',
  ].join('\n');
}
