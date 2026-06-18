import { describe, expect, it } from 'vitest';

import type { Workflow } from '@/api/workflow';
import { getWorkflowDisplayName } from './workflowDisplay';

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: 'wf-1',
    name: 'stable_workflow_name',
    category: 'default',
    workflowJson: {
      start: '',
      nodes: [],
      edges: [],
    },
    status: 'draft',
    createdAt: 0,
    updatedAt: 0,
    stats: {
      callCount: 0,
      successCount: 0,
      errorCount: 0,
      totalRuntime: 0,
      avgRuntime: 0,
      thumbsUp: 0,
      thumbsDown: 0,
    },
    ...overrides,
  };
}

describe('getWorkflowDisplayName', () => {
  it('uses localized workflow response names for the active language', () => {
    const workflow = makeWorkflow({
      nameI18n: {
        'zh-CN': '中文工作流名称',
        'en-US': 'English Workflow Name',
      },
    });

    expect(getWorkflowDisplayName(workflow, 'zh-CN')).toBe('中文工作流名称');
    expect(getWorkflowDisplayName(workflow, 'en-US')).toBe('English Workflow Name');
  });

  it('can read names from workflow metadata', () => {
    const workflow = makeWorkflow({
      workflowJson: {
        start: '',
        nodes: [],
        edges: [],
        metadata: {
          nameI18n: {
            zh: '元数据中文名',
            en: 'Metadata English Name',
          },
        },
      },
    });

    expect(getWorkflowDisplayName(workflow, 'zh-Hans')).toBe('元数据中文名');
    expect(getWorkflowDisplayName(workflow, 'en')).toBe('Metadata English Name');
  });

  it('falls back to stable name when no localized name exists', () => {
    expect(getWorkflowDisplayName(makeWorkflow(), 'zh-CN')).toBe('stable_workflow_name');
  });
});
