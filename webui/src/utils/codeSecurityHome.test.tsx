import React from 'react';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AuditHome } from '../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/AuditHome';
beforeEach(() => { (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = { useLanguage: () => 'zh-CN' }; });
afterEach(() => { delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__; });
const projects = Array.from({ length: 41 }, (_, i) => ({ id: `p${i}`, name: `Project ${i + 1}`, worktree: `/source/p${i}` }));
const props = { projects, scans: [], canCreate: true, onNewAudit: () => {}, onProjectSelect: () => {}, onProjectAdded: () => {}, hasMore: false, onLoadMore: () => {}, loadingMore: false };
it('limits pages to 20 actual project rows and resets pagination when searching', () => {
  render(<AuditHome {...props} />);
  expect(screen.getAllByRole('row')).toHaveLength(21);
  fireEvent.click(screen.getByRole('button', { name: '第 3 页' }));
  expect(screen.getAllByRole('row')).toHaveLength(2);
  expect(screen.getByText('Project 41')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Project 2' } });
  expect(screen.getByRole('button', { name: '第 1 页' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getAllByRole('row')).toHaveLength(12);
});
it('opens project registration as a labelled modal and supports cancel', () => {
  render(<AuditHome {...props} />);
  const trigger = screen.getByRole('button', { name: '添加项目' });
  fireEvent.click(trigger);
  const dialog = screen.getByRole('dialog', { name: '添加项目' });
  expect(within(dialog).getByLabelText('源码目录')).toBeRequired();
  fireEvent.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});


it.each(['git', 'zip', 'url'] as const)('submits %s import as multipart and adds the returned project', async (kind) => {
  const project = { id: 'imported', name: 'Demo', worktree: '/workspace/code-audit/imports/id/source' };
  const post = vi.fn().mockResolvedValue({ data: project });
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__.api = { post };
  const onProjectAdded = vi.fn();
  render(<AuditHome {...props} onProjectAdded={onProjectAdded} />);
  fireEvent.click(screen.getByRole('button', { name: '添加项目' }));
  const dialog = screen.getByRole('dialog');
  const labels = { git: 'Git 仓库', zip: 'ZIP 文件', url: '源码 URL' };
  fireEvent.click(within(dialog).getByRole('tab', { name: labels[kind] }));
  fireEvent.change(within(dialog).getByLabelText('项目名称'), { target: { value: 'Demo' } });
  const file = new File(['zip content'], 'demo.zip', { type: 'application/zip' });
  if (kind === 'zip') {
    fireEvent.change(within(dialog).getByLabelText(/上传源码 ZIP/), { target: { files: [file] } });
  } else {
    fireEvent.change(within(dialog).getByLabelText(kind === 'git' ? 'Git 仓库地址' : '源码下载地址'), { target: { value: 'https://host/repo.zip' } });
  }
  fireEvent.submit(dialog.querySelector('form')!);
  await waitFor(() => expect(onProjectAdded).toHaveBeenCalledWith(project));
  const [url, data, config] = post.mock.calls[0];
  expect(url).toBe('/api/code-security/v1/projects/import');
  expect(data).toBeInstanceOf(FormData);
  expect(data.get('kind')).toBe(kind);
  expect(data.get('name')).toBe('Demo');
  if (kind === 'zip') expect(data.get('file')).toBe(file);
  expect(config.headers['Content-Type']).toBe('multipart/form-data');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('shows source types and project names without exposing source paths in rows', () => {
  render(<AuditHome {...props} projects={[
    { id: 'git', name: 'Repository', worktree: '/private/source/repo', sourceKind: 'git' },
    { id: 'zip', name: 'Archive', worktree: '/private/source/archive', sourceKind: 'zip' },
  ]} />);
  expect(screen.getByRole('columnheader', { name: '源码来源' })).toBeInTheDocument();
  expect(screen.getByText('Git 仓库')).toBeInTheDocument();
  expect(screen.getByText('ZIP 上传')).toBeInTheDocument();
  expect(screen.queryByText('/private/source/repo')).not.toBeInTheDocument();
});

it.each([true, false])('confirms deletion and updates the list only on success (%s)', async (success) => {
  const remove = success ? vi.fn().mockResolvedValue({ data: true }) : vi.fn().mockRejectedValue(new Error('删除失败'));
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__.api = { delete: remove };
  const onProjectDeleted = vi.fn();
  render(<AuditHome {...props} projects={[projects[0]]} onProjectDeleted={onProjectDeleted} />);
  fireEvent.click(screen.getByRole('button', { name: '删除', exact: true }));
  expect(remove).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole('dialog', { name: '删除项目' })).getByRole('button', { name: '确定' }));
  if (success) {
    await waitFor(() => expect(onProjectDeleted).toHaveBeenCalledWith('p0'));
    expect(remove).toHaveBeenCalledWith('/api/code-security/v1/projects/p0');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  } else {
    await screen.findByRole('alert');
    expect(onProjectDeleted).not.toHaveBeenCalled();
    expect(screen.getByText('Project 1')).toBeInTheDocument();
  }
});
