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

  it('loads the latest file into the viewer and lists it in the file select', async () => {
    render(<SystemLogPage />);
    expect(await screen.findByText('INFO line')).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveValue('2026-09-15/flocks.log');
    expect(logsAPI.readLatest).toHaveBeenCalled();
  });
});
