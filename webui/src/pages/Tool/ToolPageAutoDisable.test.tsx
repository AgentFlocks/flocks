import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ToolPage from './index';

const {
  getToolMock,
  listFixturesMock,
  refetchMock,
  reloadMock,
  testToolMock,
} = vi.hoisted(() => ({
  getToolMock: vi.fn(),
  listFixturesMock: vi.fn(),
  refetchMock: vi.fn(),
  reloadMock: vi.fn(),
  testToolMock: vi.fn(),
}));

const enabledTool = {
  name: 'failing_custom_tool',
  description: 'Always fails',
  source: 'plugin_py',
  category: 'custom',
  enabled: true,
  parameters: [],
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      typeof options?.defaultValue === 'string' ? options.defaultValue : key,
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('@/hooks/useTools', () => ({
  useToolPage: () => ({
    tools: [enabledTool],
    total: 1,
    facets: {
      category: { custom: 1 },
      source: { plugin_py: 1 },
      source_groups: {},
      source_name: { Flocks: 1 },
      enabled: { true: 1 },
    },
    loading: false,
    error: null,
    initialized: true,
    refetch: refetchMock,
    reload: reloadMock,
  }),
}));

vi.mock('@/api/tool', () => ({
  canDirectlyTestTool: vi.fn(() => true),
  toolAPI: {
    get: getToolMock,
    listFixtures: listFixturesMock,
    test: testToolMock,
  },
}));

vi.mock('@/api/provider', () => ({
  providerAPI: {
    listApiServices: vi.fn(() => Promise.resolve({ data: [] })),
  },
}));

describe('ToolPage auto-disable synchronization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getToolMock.mockResolvedValue({ data: enabledTool });
    listFixturesMock.mockResolvedValue({ data: [] });
    reloadMock.mockResolvedValue(undefined);
    testToolMock.mockResolvedValue({
      data: {
        success: false,
        error: 'synthetic repeated failure',
        metadata: { disabled: true, disabled_reason: 'repeated_error' },
      },
    });
  });

  it('reloads list state without refreshing plugins after an auto-disable result', async () => {
    render(<ToolPage />);

    fireEvent.click(screen.getByRole('button', { name: enabledTool.name }));
    await screen.findByText(enabledTool.description);
    fireEvent.click(screen.getByRole('button', { name: 'toolDetail.tabTest' }));
    fireEvent.click(screen.getByRole('button', { name: 'toolDetail.runTest' }));

    await waitFor(() => expect(reloadMock).toHaveBeenCalledTimes(1));
    expect(refetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'toolDetail.runTest' })).toBeDisabled();
  });
});
