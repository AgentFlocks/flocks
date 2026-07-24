import { describe, expect, it } from 'vitest';

import enSession from '@/locales/en-US/session.json';
import zhSession from '@/locales/zh-CN/session.json';

import {
  redactToolInput,
  resolveToolPresentation,
  type ToolPresentationTranslator,
} from './toolPresentation';

function translator(locale: Record<string, unknown>): ToolPresentationTranslator {
  return (key) => {
    const value = key.split('.').reduce<unknown>((current, segment) => {
      if (!current || typeof current !== 'object') return undefined;
      return (current as Record<string, unknown>)[segment];
    }, locale);
    return typeof value === 'string' ? value : key;
  };
}

const zh = translator(zhSession);
const en = translator(enSession);

const BUILT_IN_TOOLS = [
  'read',
  'write',
  'edit',
  'apply_patch',
  'glob',
  'doc_parser',
  'bash',
  'grep',
  'lsp',
  'websearch',
  'webfetch',
  'delegate_task',
  'task',
  'schedule_task_create',
  'schedule_task_list',
  'schedule_task_status',
  'schedule_task_update',
  'schedule_task_delete',
  'schedule_task_rerun',
  'todo',
  'run_workflow',
  'run_workflow_node',
  'workflow_config_manage',
  'ssh_host_cmd',
  'ssh_run_script',
  'question',
  'list_providers',
  'add_provider',
  'add_model',
  'memory_search',
  'memory_get',
  'memory_write',
  'flocks_mcp',
  'session_manage',
  'run_slash_command',
  'tool_search',
  'flocks_skills',
  'skill_load',
  'device_manage',
  'channel_message',
  'im_send_message',
  'wecom_mcp',
  'get_time',
];

describe('resolveToolPresentation', () => {
  it('provides localized action names for every user-facing built-in tool', () => {
    for (const toolName of BUILT_IN_TOOLS) {
      const zhPresentation = resolveToolPresentation(toolName, {}, zh);
      const enPresentation = resolveToolPresentation(toolName, {}, en);

      expect(zhPresentation.known, toolName).toBe(true);
      expect(enPresentation.known, toolName).toBe(true);
      expect(zhPresentation.label, toolName).not.toContain('chat.tool.');
      expect(enPresentation.label, toolName).not.toContain('chat.tool.');
    }
  });

  it('uses dynamic skill, MCP, session, and device action names', () => {
    expect(resolveToolPresentation(
      'flocks_skills',
      { input: { subcommand: 'install', args: 'agent-builder' } },
      zh,
    )).toMatchObject({ label: '安装技能', detail: 'agent-builder' });

    expect(resolveToolPresentation(
      'flocks_mcp',
      { input: { subcommand: 'connect', name: 'brave-search' } },
      zh,
    )).toMatchObject({ label: '连接 MCP 服务', detail: 'brave-search' });

    expect(resolveToolPresentation(
      'session_manage',
      { input: { action: 'archive', archive: false, session_id: 'ses-1' } },
      zh,
    )).toMatchObject({ label: '恢复任务', detail: 'ses-1' });

    expect(resolveToolPresentation(
      'device_manage',
      { input: { action: 'connectivity_test', device_name: 'SOC 主设备' } },
      zh,
    )).toMatchObject({ label: '检测设备连接', detail: 'SOC 主设备' });
  });

  it('extracts concise targets instead of exposing raw input summaries', () => {
    expect(resolveToolPresentation(
      'read',
      { input: { filePath: '/repo/SessionChat.tsx', offset: 5200 } },
      zh,
    )).toMatchObject({
      label: '读取文件',
      detail: '/repo/SessionChat.tsx · 5200',
    });

    expect(resolveToolPresentation(
      'webfetch',
      { input: { url: 'https://docs.example.com/releases/latest' } },
      zh,
    )).toMatchObject({
      label: '读取网页',
      detail: 'docs.example.com/releases/latest',
    });

    expect(resolveToolPresentation(
      'apply_patch',
      {
        input: {
          patchText: [
            '*** Begin Patch',
            '*** Update File: src/a.ts',
            '*** Add File: src/b.ts',
            '*** End Patch',
          ].join('\n'),
        },
      },
      zh,
    )).toMatchObject({
      label: '修改文件',
      detail: 'src/a.ts +1',
    });
  });

  it('keeps unknown plugin tools on the generic fallback', () => {
    expect(resolveToolPresentation('custom_plugin_action', {}, zh)).toEqual({
      known: false,
      label: 'custom plugin action',
      detail: '',
    });
  });
});

describe('redactToolInput', () => {
  it('redacts sensitive fields recursively without hiding ordinary parameters', () => {
    expect(redactToolInput({
      api_key: 'secret-api-key',
      config: {
        password: 'secret-password',
        base_url: 'https://example.com',
      },
      headers: [
        { Authorization: 'Bearer secret-token' },
      ],
      key_path: '/Users/rex/.ssh/id_ed25519',
    })).toEqual({
      api_key: '••••••',
      config: {
        password: '••••••',
        base_url: 'https://example.com',
      },
      headers: [
        { Authorization: '••••••' },
      ],
      key_path: '/Users/rex/.ssh/id_ed25519',
    });
  });
});
