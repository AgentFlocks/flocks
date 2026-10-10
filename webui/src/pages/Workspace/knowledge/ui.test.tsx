import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import en from '@/locales/en-US/workspace.json';
import zh from '@/locales/zh-CN/workspace.json';
import { Button, CreatedAt, fileType, Modal, Pagination } from './ui';

const i18n = createInstance();
beforeAll(async () => {
  await i18n.init({ lng: 'en-US', resources: { 'en-US': { workspace: en } }, interpolation: { escapeValue: false } });
});
function Providers({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

describe('opt-in knowledge visuals', () => {
  it('leaves existing buttons unchanged and uses explicit variants for the knowledge skin', () => {
    render(<><Button>Existing</Button><Button variant="primary" size="small" disabled>Create set</Button></>);
    const existing = screen.getByRole('button', { name: 'Existing' });
    expect(existing).toHaveClass('border-gray-200', 'dark:border-zinc-700');
    expect(existing).not.toHaveClass('kb-button');
    const primary = screen.getByRole('button', { name: 'Create set' });
    expect(primary).toHaveClass('kb-button', 'kb-button--primary', 'kb-button--small');
    expect(primary).not.toHaveAttribute('variant');
    expect(primary).not.toHaveAttribute('size');
    expect(primary).toBeDisabled();
  });

  it('keeps knowledge modal styling opt-in with an accessible close action', async () => {
    const onClose = vi.fn();
    render(<Modal variant="knowledge" title="Add source files" onClose={onClose}>File selection</Modal>, { wrapper: Providers });
    const dialog = screen.getByRole('dialog', { name: 'Add source files' });
    expect(dialog).toHaveClass('kb-modal--knowledge');
    await userEvent.setup().click(screen.getByRole('button', { name: en.knowledge.close }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('knowledge table metadata', () => {
  it.each([
    ['report.pdf', 'PDF'], ['NOTES.Md', 'MD'], ['archive.tar.gz', 'GZ'],
    ['README', '—'], ['.env', '—'], ['file.', '—'], ['', '—'], ['folder.with.dot/README', '—'],
  ])('derives a text type only from the name extension: %s', (name, expected) => {
    expect(fileType(name)).toBe(expected);
  });

  it.each([
    undefined, null, '', 'not-a-date', '2026', '1700000000000', '2026-09-28',
    '2026-02-30T10:00:00Z', '2026-13-01T00:00:00Z', '2026-09-28T24:00:00Z',
  ])('uses a dash for missing or invalid ISO timestamps: %s', value => {
    const { container } = render(<CreatedAt value={value} />, { wrapper: Providers });
    expect(container).toHaveTextContent(/^—$/);
    expect(container.querySelector('time')).toBeNull();
  });

  it.each(['2026-09-28T10:00:00Z', '2026-09-28T18:00:00.123+08:00'])('keeps valid ISO metadata on a localized time element: %s', value => {
    const { container } = render(<CreatedAt value={value} />, { wrapper: Providers });
    const time = container.querySelector('time');
    expect(time).toHaveAttribute('datetime', value);
    expect(time).toHaveTextContent(new Date(value).toLocaleString('en-US'));
  });
});

describe('knowledge pagination and copy', () => {
  it('shows only the page number but still uses total to disable next', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<Pagination page={1} total={21} onChange={onChange} />, { wrapper: Providers });
    expect(screen.getByText('Page 1')).toBeInTheDocument();
    expect(screen.queryByText(/21|Items:/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: en.knowledge.previousPage })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: en.knowledge.nextPage }));
    expect(onChange).toHaveBeenCalledWith(2);
    rerender(<Pagination page={2} total={21} onChange={onChange} />);
    expect(screen.getByRole('button', { name: en.knowledge.nextPage })).toBeDisabled();
    expect(screen.getByRole('button', { name: en.knowledge.previousPage })).toBeEnabled();
    rerender(<Pagination page={1} total={20} onChange={onChange} />);
    expect(screen.getByRole('button', { name: en.knowledge.nextPage })).toBeDisabled();
  });

  it('keeps English and Chinese add-file labels and page-only copy aligned', () => {
    expect(en.knowledge.datasets.linkFiles).toBe('Add files');
    expect(zh.knowledge.datasets.linkFiles).toBe('添加文件');
    expect(en.knowledge.datasets.gridView).toBe('Grid view');
    expect(zh.knowledge.datasets.gridView).toBe('网格视图');
    expect(en.knowledge.datasets.listView).toBe('List view');
    expect(zh.knowledge.datasets.listView).toBe('列表视图');
    expect(en.knowledge.files.empty).toMatch(/folder/i);
    expect(zh.knowledge.files.empty).toMatch(/目录|文件夹/);
    expect(en.knowledge.files.search).toMatch(/folder/i);
    expect(zh.knowledge.files.search).toMatch(/目录|文件夹/);
    expect(en.knowledge.pagination).not.toContain('{{total}}');
    expect(zh.knowledge.pagination).not.toContain('{{total}}');
    expect(Object.keys(en.knowledge.files).sort()).toEqual(Object.keys(zh.knowledge.files).sort());
    expect(Object.keys(en.knowledge.datasets).sort()).toEqual(Object.keys(zh.knowledge.datasets).sort());
  });
});
