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

it('keeps the loaded log controls keyboard reachable inside the dialog', async () => {
  const onClose = vi.fn();
  render(<LogViewerModal open onClose={onClose} />);
  expect(await screen.findByText('service ready')).toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: 'logs.title' })).toHaveAttribute('aria-modal', 'true');
  const fileSelect = screen.getByRole('combobox', { name: 'logs.file' });
  const close = screen.getByRole('button', { name: 'common:button.close' });
  fileSelect.focus();
  fireEvent.keyDown(fileSelect, { key: 'Tab', shiftKey: true });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: 'Tab' });
  expect(fileSelect).toHaveFocus();
  fireEvent.keyDown(fileSelect, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);
});
