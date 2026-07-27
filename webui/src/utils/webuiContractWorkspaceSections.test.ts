import { describe, expect, it } from 'vitest';
import { buildWebUIContractWorkspaceSections, getLocalizedWebUIContractTitle } from './webuiContractWorkspaceSections';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';

const workspace: WebUIContractWorkspaceListItem = {
  id: 'soc_ui',
  title: 'SOC 工作区',
  titleEn: 'SOC Workspace',
  route: '/contracts/webui/workspaces/soc_ui',
  icon: 'ShieldCheck',
  order: 10,
  enabled: true,
  placement: 'sceneWorkspace',
  defaultPageId: 'soc-overview',
  sections: [
    {
      id: 'operations',
      label: '告警运营',
      labelEn: 'Alert Operations',
      pageIds: ['soc-overview', 'soc-alerts'],
      defaultPageId: 'soc-overview',
    },
  ],
  pages: [
    {
      id: 'soc-overview',
      title: 'SOC 总览',
      titleEn: 'SOC Overview',
      route: '/contracts/webui/soc-overview',
      icon: 'Shield',
      order: 10,
      enabled: true,
      placement: 'home.after',
      buildHash: 'ready',
      buildStatus: 'ready',
    },
    {
      id: 'soc-alerts',
      title: '告警调查',
      titleEn: 'Alert Investigation',
      route: '/contracts/webui/soc-alerts',
      icon: 'AlertTriangle',
      order: 20,
      enabled: true,
      placement: 'home.after',
      buildHash: 'ready',
      buildStatus: 'ready',
    },
  ],
};

describe('webuiContractWorkspaceSections localization', () => {
  it('uses English workspace, section, and page titles outside Chinese locales', () => {
    expect(getLocalizedWebUIContractTitle(workspace, 'en-US')).toBe('SOC Workspace');

    const sections = buildWebUIContractWorkspaceSections(workspace, 'en-US');

    expect(sections[0].label).toBe('Alert Operations');
    expect(sections[0].pages.map((page) => page.title)).toEqual([
      'SOC Overview',
      'Alert Investigation',
    ]);
  });

  it('keeps Chinese titles for Chinese locales', () => {
    expect(getLocalizedWebUIContractTitle(workspace, 'zh-CN')).toBe('SOC 工作区');

    const sections = buildWebUIContractWorkspaceSections(workspace, 'zh-CN');

    expect(sections[0].label).toBe('告警运营');
    expect(sections[0].pages.map((page) => page.title)).toEqual(['SOC 总览', '告警调查']);
  });
});
