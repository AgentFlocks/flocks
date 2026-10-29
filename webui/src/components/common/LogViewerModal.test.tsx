import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import LogViewerModal from './LogViewerModal';

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
