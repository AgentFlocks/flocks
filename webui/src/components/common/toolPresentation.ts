import type { ToolState } from '@/types';

export type ToolPresentationTranslator = (
  key: string,
  options?: Record<string, unknown>,
) => string;

export interface ToolPresentation {
  known: boolean;
  label: string;
  detail: string;
}

const STATIC_LABEL_KEYS: Record<string, string> = {
  read: 'chat.tool.actions.readFile',
  write: 'chat.tool.actions.writeFile',
  edit: 'chat.tool.actions.editFile',
  apply_patch: 'chat.tool.actions.editFile',
  glob: 'chat.tool.actions.findFiles',
  doc_parser: 'chat.tool.actions.parseDocument',
  bash: 'chat.tool.actions.executeCommand',
  grep: 'chat.tool.actions.searchContent',
  lsp: 'chat.tool.actions.analyzeCode',
  websearch: 'chat.tool.actions.searchWeb',
  webfetch: 'chat.tool.actions.fetchWeb',
  delegate_task: 'chat.tool.actions.delegateTask',
  task: 'chat.tool.actions.delegateTask',
  schedule_task_create: 'chat.tool.actions.createScheduledTask',
  schedule_task_list: 'chat.tool.actions.listScheduledTasks',
  schedule_task_status: 'chat.tool.actions.viewScheduledTask',
  schedule_task_update: 'chat.tool.actions.updateScheduledTask',
  schedule_task_delete: 'chat.tool.actions.deleteScheduledTask',
  schedule_task_rerun: 'chat.tool.actions.rerunScheduledTask',
  todo: 'chat.tool.todoUpdated',
  run_workflow: 'chat.tool.actions.runWorkflow',
  run_workflow_node: 'chat.tool.actions.runWorkflowNode',
  question: 'chat.tool.actions.askQuestion',
  memory_search: 'chat.tool.actions.searchMemory',
  memory_get: 'chat.tool.actions.readMemory',
  memory_write: 'chat.tool.actions.writeMemory',
  list_providers: 'chat.tool.actions.listProviders',
  add_provider: 'chat.tool.actions.addProvider',
  add_model: 'chat.tool.actions.addModel',
  run_slash_command: 'chat.tool.actions.runSlashCommand',
  tool_search: 'chat.tool.actions.searchTools',
  skill_load: 'chat.tool.loadSkill',
  flocks_skills: 'chat.tool.actions.manageSkills',
  flocks_mcp: 'chat.tool.actions.manageMcp',
  session_manage: 'chat.tool.actions.manageTasks',
  workflow_config_manage: 'chat.tool.actions.manageWorkflowConfig',
  device_manage: 'chat.tool.actions.manageDevices',
  ssh_host_cmd: 'chat.tool.actions.runRemoteCommand',
  ssh_run_script: 'chat.tool.actions.runRemoteScript',
  channel_message: 'chat.tool.actions.sendMessage',
  im_send_message: 'chat.tool.actions.sendMessage',
  wecom_mcp: 'chat.tool.actions.useWeCom',
  get_time: 'chat.tool.actions.getTime',
};

const ACTION_LABEL_KEYS: Record<string, Record<string, string>> = {
  lsp: {
    goToDefinition: 'chat.tool.actions.findDefinition',
    findReferences: 'chat.tool.actions.findReferences',
    hover: 'chat.tool.actions.inspectSymbol',
    documentSymbol: 'chat.tool.actions.listSymbols',
    workspaceSymbol: 'chat.tool.actions.searchSymbols',
    goToImplementation: 'chat.tool.actions.findImplementations',
    prepareCallHierarchy: 'chat.tool.actions.analyzeCallHierarchy',
    incomingCalls: 'chat.tool.actions.findCallers',
    outgoingCalls: 'chat.tool.actions.findCallees',
  },
  flocks_skills: {
    find: 'chat.tool.actions.searchSkills',
    install: 'chat.tool.actions.installSkill',
    status: 'chat.tool.actions.checkSkills',
    'install-deps': 'chat.tool.actions.installSkillDependencies',
    remove: 'chat.tool.actions.removeSkill',
  },
  flocks_mcp: {
    list: 'chat.tool.actions.listMcpServers',
    add: 'chat.tool.actions.addMcpServer',
    remove: 'chat.tool.actions.removeMcpServer',
    connect: 'chat.tool.actions.connectMcpServer',
    disconnect: 'chat.tool.actions.disconnectMcpServer',
  },
  session_manage: {
    list: 'chat.tool.actions.listTasks',
    get: 'chat.tool.actions.viewTask',
    create: 'chat.tool.actions.createTask',
    update: 'chat.tool.actions.updateTask',
    delete: 'chat.tool.actions.deleteTask',
    archive: 'chat.tool.actions.archiveTask',
  },
  workflow_config_manage: {
    get: 'chat.tool.actions.readWorkflowConfig',
    status: 'chat.tool.actions.viewWorkflowConfigStatus',
    sync: 'chat.tool.actions.syncWorkflowConfig',
    diff: 'chat.tool.actions.compareWorkflowConfig',
    put: 'chat.tool.actions.updateWorkflowConfig',
  },
  device_manage: {
    list: 'chat.tool.actions.listDevices',
    list_templates: 'chat.tool.actions.listDeviceTemplates',
    create: 'chat.tool.actions.createDevice',
    update: 'chat.tool.actions.updateDevice',
    connectivity_test: 'chat.tool.actions.testDeviceConnection',
  },
  wecom_mcp: {
    list: 'chat.tool.actions.listWeComTools',
    call: 'chat.tool.actions.useWeCom',
  },
};

const SENSITIVE_KEY_PATTERN =
  /(?:api[_-]?key|password|passwd|token|secret|authorization|credential|private[_-]?key|access[_-]?key|cookie)/i;

function stringValue(
  input: Record<string, unknown>,
  ...keys: string[]
): string {
  for (const key of keys) {
    const value = input[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function joinDetail(...parts: Array<string | undefined>): string {
  return parts.filter((part): part is string => Boolean(part?.trim())).join(' · ');
}

function urlDetail(value: string): string {
  if (!value) return '';
  try {
    const url = new URL(value);
    return `${url.hostname}${url.pathname === '/' ? '' : url.pathname}`;
  } catch {
    return value;
  }
}

function workflowName(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return '';
  const normalized = value.trim().replace(/\\/g, '/');
  const lastSegment = normalized.split('/').filter(Boolean).pop() || normalized;
  return lastSegment.replace(/\.json$/i, '');
}

function patchDetail(value: unknown): string {
  if (typeof value !== 'string') return '';
  const paths = Array.from(
    value.matchAll(/^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$/gm),
    (match) => match[1]?.trim(),
  ).filter((path): path is string => Boolean(path));
  if (paths.length === 0) return '';
  if (paths.length === 1) return paths[0];
  return `${paths[0]} +${paths.length - 1}`;
}

function resolveLabelKey(toolName: string, input: Record<string, unknown>): string | undefined {
  if (toolName === 'session_manage' && input.action === 'archive' && input.archive === false) {
    return 'chat.tool.actions.restoreTask';
  }
  const action = stringValue(input, 'action', 'subcommand', 'operation');
  return ACTION_LABEL_KEYS[toolName]?.[action] || STATIC_LABEL_KEYS[toolName];
}

function buildDetail(
  toolName: string,
  state: Partial<ToolState>,
): string {
  const input = state.input || {};
  const metadata = state.metadata || {};

  switch (toolName) {
    case 'read':
      return joinDetail(
        stringValue(input, 'filePath'),
        stringValue(input, 'offset', 'from_line'),
      );
    case 'write':
    case 'edit':
      return stringValue(input, 'filePath');
    case 'apply_patch':
      return patchDetail(input.patchText);
    case 'glob':
      return joinDetail(stringValue(input, 'pattern'), stringValue(input, 'path'));
    case 'grep':
      return joinDetail(stringValue(input, 'pattern'), stringValue(input, 'path'));
    case 'doc_parser':
      return stringValue(input, 'input_path');
    case 'bash':
      return stringValue(input, 'description', 'command');
    case 'lsp':
      return joinDetail(
        stringValue(input, 'filePath'),
        input.line === undefined ? '' : `L${String(input.line)}`,
      );
    case 'websearch':
    case 'tool_search':
    case 'memory_search':
      return stringValue(input, 'query');
    case 'webfetch':
      return urlDetail(stringValue(input, 'url'));
    case 'skill_load':
      return stringValue(input, 'name', 'skill_name');
    case 'flocks_skills':
      return stringValue(input, 'args');
    case 'memory_get':
    case 'memory_write':
      return stringValue(input, 'path');
    case 'schedule_task_create':
      return joinDetail(
        stringValue(input, 'title'),
        stringValue(input, 'cron_description', 'run_at', 'cron', 'schedule'),
      );
    case 'schedule_task_list':
      return stringValue(input, 'status', 'type');
    case 'schedule_task_status':
    case 'schedule_task_delete':
    case 'schedule_task_rerun':
      return stringValue(input, 'task_id');
    case 'schedule_task_update':
      return stringValue(input, 'title', 'task_id');
    case 'run_workflow':
    case 'run_workflow_node':
      return joinDetail(
        stringValue(metadata, 'workflow_name') || workflowName(input.workflow),
        stringValue(input, 'node_id'),
      );
    case 'workflow_config_manage':
      return joinDetail(
        stringValue(input, 'workflow_id'),
        stringValue(input, 'config_type'),
      );
    case 'flocks_mcp':
      return stringValue(input, 'name');
    case 'session_manage':
      return stringValue(input, 'title', 'session_id', 'project_id');
    case 'device_manage':
      return stringValue(input, 'device_name', 'device_id', 'storage_key');
    case 'ssh_host_cmd':
      return joinDetail(stringValue(input, 'host'), stringValue(input, 'command'));
    case 'ssh_run_script':
      return joinDetail(stringValue(input, 'host'), stringValue(input, 'script_path'));
    case 'channel_message':
      return joinDetail(
        stringValue(input, 'channel_type'),
        stringValue(input, 'chat_id', 'session_id'),
      );
    case 'im_send_message':
      return joinDetail(
        stringValue(input, 'channel_type'),
        stringValue(input, 'target', 'session_id'),
      );
    case 'wecom_mcp':
      return joinDetail(stringValue(input, 'category'), stringValue(input, 'method'));
    case 'list_providers':
      return stringValue(input, 'provider_id');
    case 'add_provider':
      return stringValue(input, 'name');
    case 'add_model':
      return joinDetail(stringValue(input, 'name', 'model_id'), stringValue(input, 'provider_id'));
    case 'run_slash_command':
      return joinDetail(stringValue(input, 'command'), stringValue(input, 'arguments'));
    default:
      return '';
  }
}

export function resolveToolPresentation(
  rawToolName: string,
  state: Partial<ToolState>,
  t: ToolPresentationTranslator,
): ToolPresentation {
  const toolName = rawToolName === 'load_skill' ? 'skill_load' : rawToolName;
  const input = state.input || {};
  const labelKey = resolveLabelKey(toolName, input);
  return {
    known: Boolean(labelKey),
    label: labelKey ? t(labelKey) : rawToolName.replace(/_/g, ' '),
    detail: buildDetail(toolName, state),
  };
}

export function redactToolInput(value: unknown, key = ''): unknown {
  if (key && SENSITIVE_KEY_PATTERN.test(key)) return '••••••';
  if (Array.isArray(value)) {
    return value.map((item) => redactToolInput(item));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([childKey, childValue]) => [
          childKey,
          redactToolInput(childValue, childKey),
        ]),
    );
  }
  return value;
}
