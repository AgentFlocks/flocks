import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ToolDetailDrawer } from './index';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === 'toolDetail.params' && typeof options?.count === 'number') {
        return `参数 (${options.count}个)`;
      }

      const translations: Record<string, string> = {
        'toolDetail.tabInfo': '详情',
        'toolDetail.tabTest': '测试',
        'toolDetail.description': '描述',
        'toolDetail.status': '状态',
        'toolDetail.security': '安全',
        'toolDetail.requiresConfirmation': '执行需确认',
        'toolDetail.paramName': '名称',
        'toolDetail.paramType': '类型',
        'toolDetail.paramRequired': '必填',
        'toolDetail.paramDesc': '描述',
        'toolDetail.yes': '是',
        'toolDetail.no': '否',
        'toolDetail.close': '关闭',
        'toolDetail.testTool': '测试工具',
        'toolDetail.enabled': '启用',
        'toolDetail.disabled': '禁用',
      };

      return translations[key] ?? key;
    },
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/api/tool', () => ({
  canDirectlyTestTool: vi.fn(() => true),
  toolAPI: {
    setEnabled: vi.fn(),
    resetSetting: vi.fn(),
  },
}));

describe('ToolDetailDrawer', () => {
  it('wraps long descriptions and parameter text without clipping', () => {
    const longDescription = 'OneSEC DNS grouped tool. dns_search_blocked_queries_by_super_long_keyword_with_no_breaks_and_more_details_to_force_wrapping';
    const longParamDescription = 'DNS 分组动作名 dns_search_blocked_queries_by_super_long_keyword_with_no_breaks_and_more_details_to_force_wrapping';

    render(
      <ToolDetailDrawer
        tool={{
          name: 'onesec_dns',
          description: longDescription,
          source: 'custom',
          source_name: 'OneSEC',
          category: 'custom',
          enabled: true,
          parameters: [
            {
              name: 'action',
              type: 'string',
              required: true,
              description: longParamDescription,
            },
          ],
        } as any}
        testParams="{}"
        testResult={null}
        testing={false}
        onClose={vi.fn()}
        onTestParamsChange={vi.fn()}
        onTest={vi.fn()}
      />,
    );

    const description = screen.getByText(longDescription);
    expect(description).toHaveClass('whitespace-pre-wrap');
    expect(description).toHaveClass('break-words');

    const table = screen.getByRole('table');
    expect(table).toHaveClass('table-fixed');

    const paramDescriptionCell = screen.getByText(longParamDescription).closest('td');
    expect(paramDescriptionCell).not.toBeNull();
    expect(paramDescriptionCell?.className).toContain('whitespace-pre-wrap');
    expect(paramDescriptionCell?.className).toContain('break-words');
    expect(paramDescriptionCell?.className).toContain('align-top');
  });
});
