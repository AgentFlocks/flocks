import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import LogViewerModal from './LogViewerModal';
import { logsAPI } from '@/api/logs';
import { backendError } from '@/test/helpers';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/api/logs', () => ({
  logsAPI: {
    list: vi.fn().mockResolvedValue({ data: { files: [{ name: 'service.log', size: 12 }] } }),
    readLatest: vi.fn().mockResolvedValue({ data: { filename: 'service.log', content: 'service ready', total_lines: 1 } }),
  },
}));

it('loads the latest log and closes from the backdrop', async () => {
  const onClose = vi.fn();
  render(<LogViewerModal open onClose={onClose} />);
  expect(await screen.findByText('service ready')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'logs.title' })).toBeInTheDocument();
  fireEvent.click(document.querySelector('div.fixed.inset-0.z-40')!);
  expect(onClose).toHaveBeenCalledTimes(1);
});

it('says why the log could not be read instead of the bare status code', async () => {
  vi.mocked(logsAPI.readLatest).mockRejectedValueOnce(backendError(403, '只有管理员可以查看系统日志'));
  render(<LogViewerModal open onClose={vi.fn()} />);
  expect(await screen.findByText('只有管理员可以查看系统日志')).toBeInTheDocument();
  expect(screen.queryByText(/status code 403/)).not.toBeInTheDocument();
});
