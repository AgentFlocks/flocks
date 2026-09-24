import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import AutomaticStatusControl from './AutomaticStatusControl';
const mocks = vi.hoisted(() => ({ setAutomaticStatus: vi.fn() }));
vi.mock('@/api/securityMonitoring', () => ({ monitoringApi: mocks }));
beforeEach(() => vi.resetAllMocks());

it('explains rules and enables once without event confirmations', async () => {
  let finish!: () => void;
  mocks.setAutomaticStatus.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
  const refresh = vi.fn(async () => {});
  render(<AutomaticStatusControl enabled={false} ready refresh={refresh} />);
  expect(screen.getByText(/需跟进或证据不足/)).toHaveTextContent('处置中');
  expect(screen.getByText(/病毒事件恶意文件/)).toHaveTextContent('处置完成');
  const button = screen.getByRole('button', { name: '启用自动标记' });
  fireEvent.click(button); fireEvent.click(button);
  expect(mocks.setAutomaticStatus).toHaveBeenCalledTimes(1);
  expect(mocks.setAutomaticStatus).toHaveBeenCalledWith(true);
  expect(screen.getByRole('button', { name: '正在保存…' })).toBeDisabled();
  await act(async () => finish());
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
});

it('can disable automatic writes even while installation is unready', async () => {
  mocks.setAutomaticStatus.mockResolvedValue({});
  render(<AutomaticStatusControl enabled ready={false} refresh={vi.fn(async () => {})} />);
  fireEvent.click(screen.getByRole('button', { name: '关闭自动标记' }));
  await waitFor(() => expect(mocks.setAutomaticStatus).toHaveBeenCalledWith(false));
});

it('does not enable an unready installation and shows request failures', async () => {
  const refresh = vi.fn(async () => {});
  const view = render(<AutomaticStatusControl enabled={false} ready={false} refresh={refresh} />);
  expect(screen.getByRole('button', { name: '启用自动标记' })).toBeDisabled();
  view.rerender(<AutomaticStatusControl enabled={false} ready refresh={refresh} />);
  mocks.setAutomaticStatus.mockRejectedValue({ response: { data: { detail: '设备绑定已变化' } } });
  fireEvent.click(screen.getByRole('button', { name: '启用自动标记' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('设备绑定已变化');
  expect(screen.getByText('自动标记状态：已关闭')).toBeInTheDocument();
});
