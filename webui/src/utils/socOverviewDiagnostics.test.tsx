import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Page from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_overview/src/index';

const get = vi.fn();
beforeEach(() => {
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = { React, api: {
    get, page: { get: vi.fn().mockResolvedValue({ data: {} }) },
  } };
  get.mockReset();
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:diagnostic'), revokeObjectURL: vi.fn() }));
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('downloads the live SOC support bundle without executing a workflow', async () => {
  get.mockResolvedValue({ data: { component: 'soc-workspace', schema: 1 } });
  render(<Page />);
  fireEvent.click(screen.getByRole('button', { name: '导出 SOC 诊断' }));
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce());
  expect(get).toHaveBeenCalledExactlyOnceWith('/api/soc-workspace/diagnostics');
  expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
});

it('shows the recovery path when export fails and allows retry', async () => {
  get.mockRejectedValue(new Error('403'));
  render(<Page />);
  fireEvent.click(screen.getByRole('button', { name: '导出 SOC 诊断' }));
  expect(await screen.findByText(/SOC 诊断导出失败/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出 SOC 诊断' })).toBeEnabled();
});
