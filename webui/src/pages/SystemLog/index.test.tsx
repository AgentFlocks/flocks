import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import SystemLogPage from './index';

const { logsAPI } = vi.hoisted(() => ({
  logsAPI: {
    list: vi.fn(),
    readLatest: vi.fn(),
    read: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@/api/logs', () => ({ logsAPI }));

describe('SystemLogPage toolbar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    logsAPI.list.mockResolvedValue({
      data: { files: [{ name: '2026-09-15/flocks.log', size: 1, modified: 0 }], log_dir: '/logs' },
    });
    logsAPI.readLatest.mockResolvedValue({
      data: { filename: '2026-09-15/flocks.log', content: 'INFO line', total_lines: 1, truncated: false },
    });
  });

  // At 375px a fixed-width select pushes the auto-scroll label into a
  // character-per-line wrap and the refresh button off screen; the select has
  // to yield width, the label must not wrap, and the button must stay pinned.
  it('lets the file select shrink and keeps the auto-scroll label and refresh button in the row', async () => {
    render(<SystemLogPage />);

    const select = await screen.findByRole('combobox');
    expect(select.className).toContain('min-w-0');
    expect(select.className).toContain('truncate');

    const label = screen.getByText('logs.autoScroll').closest('label') as HTMLElement;
    expect(label.className).toContain('shrink-0');
    expect(label.className).toContain('whitespace-nowrap');

    const refresh = screen.getByTitle('logs.refresh');
    expect(refresh.parentElement?.className).toContain('shrink-0');
    expect(refresh.parentElement?.className).toContain('ml-auto');
  });
});
