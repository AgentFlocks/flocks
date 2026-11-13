import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ToolDetailDrawer } from './index';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === 'toolDetail.params' && typeof options?.count === 'number') {
        return `参数 (${options.count}个)`;
      }
      if (key === 'toolDetail.serviceDisabledApi') return `所属 API 服务「${options?.service}」未启用`;
      if (key === 'toolDetail.serviceDisabledDevice') return `所属设备插件「${options?.service}」还没有启用的设备`;

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
        'toolDetail.runTest': '执行测试',
        'toolDetail.enabled': '启用',
        'toolDetail.disabled': '禁用',
        'toolDetail.customized': '已自定义',
        'toolDetail.resetSetting': '恢复默认',
        'toolDetail.goToApiService': '去启用服务',
        'toolDetail.goToDevices': '前往设备接入',
        'toolDetail.enableNotApplied': '工具没有启用成功',
        'alert.unknownError': '未知错误',
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
    // ToolDetailDrawer mounts a fixtures-loading effect on every render that
    // pulls test fixtures via this endpoint.  Without a mock the call throws
    // `toolAPI.listFixtures is not a function` during the test's async tail
    // and unrelated assertions fail before they ever run.
    listFixtures: vi.fn(() => Promise.resolve({ data: [] })),
  },
}));

describe('ToolDetailDrawer', () => {
  const enabledTool = {
    name: 'failing_custom_tool',
    description: 'Always fails',
    source: 'plugin_py',
    category: 'custom',
    enabled: true,
    parameters: [],
  } as any;

  const drawerProps = {
    tool: enabledTool,
    testParams: '{}',
    testResult: null,
    testing: false,
    onClose: vi.fn(),
    onTestParamsChange: vi.fn(),
    onTest: vi.fn(),
  };

  it('disables test execution as soon as the result reports auto-disable', async () => {
    const { rerender } = render(<ToolDetailDrawer {...drawerProps} />);
    fireEvent.click(screen.getByRole('button', { name: '测试' }));
    expect(screen.getByRole('button', { name: '执行测试' })).toBeEnabled();

    rerender(
      <ToolDetailDrawer
        {...drawerProps}
        testResult={{
          success: false,
          error: 'synthetic repeated failure',
          metadata: { disabled: true, disabled_reason: 'repeated_error' },
        }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '执行测试' })).toBeDisabled();
    });
  });

  it('wraps long descriptions and parameter text without clipping', () => {
    const longDescription = 'OneSEC DNS grouped tool. dns_search_blocked_queries_by_super_long_keyword_with_no_breaks_and_more_details_to_force_wrapping';
    const longParamDescription = 'DNS 分组动作名 dns_search_blocked_queries_by_super_long_keyword_with_no_breaks_and_more_details_to_force_wrapping';

    render(
      <ToolDetailDrawer
        tool={{
          name: 'onesec_dns',
          description: longDescription,
          source: 'device',
          source_name: 'TDP',
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
    expect(screen.getByText('Device')).toBeInTheDocument();

    const table = screen.getByRole('table');
    expect(table).toHaveClass('table-fixed');

    const paramDescriptionCell = screen.getByText(longParamDescription).closest('td');
    expect(paramDescriptionCell).not.toBeNull();
    expect(paramDescriptionCell?.className).toContain('whitespace-pre-wrap');
    expect(paramDescriptionCell?.className).toContain('break-words');
    expect(paramDescriptionCell?.className).toContain('align-top');
  });

  it('passes the reset payload to onEnabledChange so the parent cannot restore a stale badge', async () => {
    const { toolAPI } = await import('@/api/tool');
    (toolAPI.resetSetting as any).mockResolvedValue({
      data: { ...enabledTool, enabled: true, enabled_customized: false, enabled_default: true },
    });
    const onEnabledChange = vi.fn();
    const customizedTool = { ...enabledTool, enabled: false, enabled_customized: true, enabled_default: true };
    const { rerender } = render(
      <ToolDetailDrawer {...drawerProps} tool={customizedTool} onEnabledChange={onEnabledChange} />,
    );
    expect(screen.getByText('已自定义')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));
    await waitFor(() => expect(onEnabledChange).toHaveBeenCalled());
    const [, newEnabled, updated] = onEnabledChange.mock.calls[0];
    expect(newEnabled).toBe(true);
    expect(updated.enabled_customized).toBe(false);

    // parent re-renders with the merged tool (enabled + overlay flags) → badge stays gone
    rerender(
      <ToolDetailDrawer
        {...drawerProps}
        tool={{ ...customizedTool, enabled: true, enabled_customized: updated.enabled_customized, enabled_default: updated.enabled_default }}
        onEnabledChange={onEnabledChange}
      />,
    );
    await waitFor(() => expect(screen.queryByText('已自定义')).toBeNull());
  });

  it('takes focus, closes on Escape and returns focus to the row that opened it', () => {
    const onClose = vi.fn();

    function Harness() {
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>read</button>
          <button type="button">background group</button>
          {open && (
            <ToolDetailDrawer
              {...drawerProps}
              onClose={() => {
                onClose();
                setOpen(false);
              }}
            />
          )}
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole('button', { name: 'read' });
    trigger.focus();
    fireEvent.click(trigger);

    const drawer = screen.getByRole('dialog');
    expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(drawer).toContainElement(document.activeElement as HTMLElement);

    // Tab from the last control wraps inside the drawer instead of reaching the page.
    const background = screen.getByRole('button', { name: 'background group' });
    background.focus();
    expect(drawer).toContainElement(document.activeElement as HTMLElement);

    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('names the close button for screen readers', () => {
    render(<ToolDetailDrawer {...drawerProps} />);
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });

  it('surfaces the backend message when toggling is rejected', async () => {
    const { toolAPI } = await import('@/api/tool');
    (toolAPI.setEnabled as any).mockRejectedValue({
      response: { status: 403, data: { error: 'HTTPException', message: '仅管理员可执行该操作' } },
      message: 'Request failed with status code 403',
    });
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const { container } = render(<ToolDetailDrawer {...drawerProps} />);

    // The enable toggle is the h-6 w-11 pill.
    const toggle = container.ownerDocument.querySelector('button.h-6.w-11') as HTMLButtonElement;
    expect(toggle).toBeTruthy();
    fireEvent.click(toggle);
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('仅管理员可执行该操作'));
    expect(screen.getByText('启用')).toBeInTheDocument();
    alertSpy.mockRestore();
  });

  // INC-F5C08C1C: the backend never opens a tool whose API service is off; it
  // answers 200 with enabled=false, and the switch used to do nothing visible.
  describe('when the service that owns the tool is disabled', () => {
    const blockedApiTool = {
      name: 'af_api_app_traffic_top',
      description: 'AF app traffic top',
      source: 'api',
      source_name: 'af_api',
      category: 'custom',
      enabled: false,
      enabled_default: true,
      enabled_customized: false,
      service_enabled: false,
      parameters: [],
    } as any;

    async function mockedToolAPI() {
      const { toolAPI } = await import('@/api/tool');
      const setEnabled = toolAPI.setEnabled as any;
      setEnabled.mockReset();
      return { setEnabled };
    }

    it('says why the tool is off, locks the switch and offers the way to the service', async () => {
      const { setEnabled } = await mockedToolAPI();
      const onOpenApiService = vi.fn();
      render(<ToolDetailDrawer {...drawerProps} tool={blockedApiTool} onOpenApiService={onOpenApiService} />);

      const notice = screen.getByRole('note');
      expect(notice).toHaveTextContent('所属 API 服务「af_api」未启用');
      const toggle = screen.getByRole('switch', { name: '状态' });
      expect(toggle).toBeDisabled();
      expect(toggle).toHaveAttribute('aria-checked', 'false');
      expect(toggle).toHaveAttribute('aria-describedby', notice.id);

      fireEvent.click(toggle);
      expect(setEnabled).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: '去启用服务' }));
      expect(onOpenApiService).toHaveBeenCalledExactlyOnceWith('af_api');
    });

    it('shows the reason after a refused enable when the opened row did not carry it', async () => {
      const { setEnabled } = await mockedToolAPI();
      const { service_enabled: _unknown, ...staleRow } = blockedApiTool;
      setEnabled.mockResolvedValue({ data: { ...blockedApiTool } });
      const onEnabledChange = vi.fn();
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
      render(<ToolDetailDrawer {...drawerProps} tool={staleRow} onEnabledChange={onEnabledChange} />);

      expect(screen.queryByRole('note')).toBeNull();
      const toggle = screen.getByRole('switch', { name: '状态' });
      expect(toggle).toBeEnabled();
      fireEvent.click(toggle);

      expect(await screen.findByRole('note')).toHaveTextContent('所属 API 服务「af_api」未启用');
      expect(setEnabled).toHaveBeenCalledExactlyOnceWith('af_api_app_traffic_top', true);
      expect(screen.getByRole('switch', { name: '状态' })).toBeDisabled();
      // The parent gets the flag too, or its prop sync would hide the notice again.
      expect(onEnabledChange.mock.calls[0][2].service_enabled).toBe(false);
      expect(alertSpy).not.toHaveBeenCalled();
      alertSpy.mockRestore();
    });

    it('never lets a refused enable pass silently, whatever the reason', async () => {
      const { setEnabled } = await mockedToolAPI();
      const plainTool = { ...enabledTool, enabled: false };
      setEnabled.mockResolvedValue({ data: { ...plainTool } });
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
      render(<ToolDetailDrawer {...drawerProps} tool={plainTool} />);

      fireEvent.click(screen.getByRole('switch', { name: '状态' }));
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('工具没有启用成功'));
      expect(screen.queryByRole('note')).toBeNull();
      alertSpy.mockRestore();
    });

    it('sends device tools to the device page and closes the drawer on the way', async () => {
      await mockedToolAPI();
      const onClose = vi.fn();
      const onOpenApiService = vi.fn();
      render(
        <MemoryRouter>
          <ToolDetailDrawer
            {...drawerProps}
            onClose={onClose}
            onOpenApiService={onOpenApiService}
            tool={{ ...blockedApiTool, name: 'ngsoc_alarms', source: 'device', source_name: 'ngsoc_api_v4_15_1', service_integration_type: 'device' }}
          />
        </MemoryRouter>,
      );

      expect(screen.getByRole('note')).toHaveTextContent('所属设备插件「ngsoc_api_v4_15_1」还没有启用的设备');
      expect(screen.getByRole('switch', { name: '状态' })).toBeDisabled();
      expect(screen.queryByRole('button', { name: '去启用服务' })).toBeNull();
      const link = screen.getByRole('link', { name: '前往设备接入' });
      expect(link).toHaveAttribute('href', '/devices');
      fireEvent.click(link);
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(onOpenApiService).not.toHaveBeenCalled();
    });

    it('goes by where the service is managed, not by the directory the plugin was loaded from', async () => {
      await mockedToolAPI();
      const onOpenApiService = vi.fn();
      // A device plugin installed under tools/api: an "api" tool whose
      // service the API tab does not list.
      const { rerender } = render(
        <MemoryRouter>
          <ToolDetailDrawer
            {...drawerProps}
            onOpenApiService={onOpenApiService}
            tool={{ ...blockedApiTool, source: 'api', source_name: 'legacy_device', service_integration_type: 'device' }}
          />
        </MemoryRouter>,
      );
      expect(screen.getByRole('note')).toHaveTextContent('所属设备插件「legacy_device」还没有启用的设备');
      expect(screen.getByRole('link', { name: '前往设备接入' })).toHaveAttribute('href', '/devices');
      expect(screen.queryByRole('button', { name: '去启用服务' })).toBeNull();

      // The reverse: loaded from tools/device without declaring a device
      // integration, so the API tab lists its service and can enable it.
      rerender(
        <MemoryRouter>
          <ToolDetailDrawer
            {...drawerProps}
            onOpenApiService={onOpenApiService}
            tool={{ ...blockedApiTool, source: 'device', source_name: 'plain_service', service_integration_type: null }}
          />
        </MemoryRouter>,
      );
      expect(screen.getByRole('note')).toHaveTextContent('所属 API 服务「plain_service」未启用');
      expect(screen.queryByRole('link', { name: '前往设备接入' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: '去启用服务' }));
      expect(onOpenApiService).toHaveBeenCalledExactlyOnceWith('plain_service');
    });

    it('takes where the service is managed from the answer to a toggle or a reset', async () => {
      const { setEnabled } = await mockedToolAPI();
      const { toolAPI } = await import('@/api/tool');
      const resetSetting = toolAPI.resetSetting as any;
      resetSetting.mockReset();
      // The drawer was opened with no word about the service; the refused
      // enable then says it is off and managed on the device page.
      const { service_enabled: _flag, ...staleRow } = blockedApiTool;
      setEnabled.mockResolvedValue({ data: { ...blockedApiTool, service_integration_type: 'device' } });
      const { unmount } = render(
        <MemoryRouter>
          <ToolDetailDrawer {...drawerProps} tool={staleRow} onOpenApiService={vi.fn()} />
        </MemoryRouter>,
      );
      fireEvent.click(screen.getByRole('switch', { name: '状态' }));
      expect(await screen.findByRole('note')).toHaveTextContent('所属设备插件「af_api」还没有启用的设备');
      expect(screen.getByRole('link', { name: '前往设备接入' })).toHaveAttribute('href', '/devices');
      expect(screen.queryByRole('button', { name: '去启用服务' })).toBeNull();
      unmount();

      // Same for 恢复默认: the drawer believed the API tab lists the service.
      const customized = { ...blockedApiTool, enabled_customized: true, service_integration_type: null };
      resetSetting.mockResolvedValue({ data: { ...customized, enabled_customized: false, service_integration_type: 'device' } });
      render(
        <MemoryRouter>
          <ToolDetailDrawer {...drawerProps} tool={customized} onOpenApiService={vi.fn()} />
        </MemoryRouter>,
      );
      expect(screen.getByRole('button', { name: '去启用服务' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));
      expect(await screen.findByRole('link', { name: '前往设备接入' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '去启用服务' })).toBeNull();
    });

    it('still lets an enabled tool be switched off, then locks the switch', async () => {
      const { setEnabled } = await mockedToolAPI();
      // Only api/device tools have their state ANDed with the service, so
      // another gated tool can still read enabled until the registry syncs.
      const enabledButGated = { ...blockedApiTool, name: 'yaml_plugin_tool', source: 'plugin_yaml', enabled: true };
      setEnabled.mockResolvedValue({ data: { ...enabledButGated, enabled: false, enabled_customized: true } });
      render(<ToolDetailDrawer {...drawerProps} tool={enabledButGated} onOpenApiService={vi.fn()} />);

      expect(screen.getByRole('note')).toBeInTheDocument();
      // Only api tools have a service panel on the API tab to jump to.
      expect(screen.queryByRole('button', { name: '去启用服务' })).toBeNull();
      const toggle = screen.getByRole('switch', { name: '状态' });
      expect(toggle).toBeEnabled();
      fireEvent.click(toggle);

      await waitFor(() => expect(screen.getByRole('switch', { name: '状态' })).toBeDisabled());
      expect(setEnabled).toHaveBeenCalledExactlyOnceWith('yaml_plugin_tool', false);
      expect(screen.getByText('已自定义')).toBeInTheDocument();
    });

    it('leaves the switch alone when the service is enabled', async () => {
      const { setEnabled } = await mockedToolAPI();
      const offTool = { ...blockedApiTool, service_enabled: true };
      setEnabled.mockResolvedValue({ data: { ...offTool, enabled: true } });
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
      render(<ToolDetailDrawer {...drawerProps} tool={offTool} onOpenApiService={vi.fn()} />);

      expect(screen.queryByRole('note')).toBeNull();
      const toggle = screen.getByRole('switch', { name: '状态' });
      expect(toggle).toBeEnabled();
      expect(toggle).not.toHaveAttribute('aria-describedby');
      fireEvent.click(toggle);

      await waitFor(() => expect(screen.getByRole('switch', { name: '状态' })).toHaveAttribute('aria-checked', 'true'));
      expect(setEnabled).toHaveBeenCalledExactlyOnceWith('af_api_app_traffic_top', true);
      expect(screen.queryByRole('note')).toBeNull();
      expect(alertSpy).not.toHaveBeenCalled();
      alertSpy.mockRestore();
    });
  });
});
