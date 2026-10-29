import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { InstallationResultBanner } from './InstallationResultBanner';

const { getResult, auth } = vi.hoisted(() => ({ getResult: vi.fn(), auth: { user: { role: 'admin' } } }));
vi.mock('@/api/installation', () => ({ getInstallationResult: getResult }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ i18n: { language: 'zh-CN' } }) }));

const restored = {
  id: 'attempt-1', phase: 'failed', recovery: 'restored', updated_at: '',
  title: '升级未完成', message: '已恢复原来的 SQLite 安装，原数据已保留，可以继续使用。',
  retry_hint: '处理原因后重新运行原升级命令。', detail: '外部数据库安装包校验失败',
};
beforeEach(() => { vi.clearAllMocks(); auth.user.role = 'admin'; getResult.mockResolvedValue(null); });

describe('installation recovery notice', () => {
  it('shows that the old installation is usable, preserves detail, and can be dismissed', async () => {
    getResult.mockResolvedValue(restored);
    render(<InstallationResultBanner />);
    expect(await screen.findByRole('alert')).toHaveTextContent('可以继续使用');
    expect(screen.getByText(restored.retry_hint)).toBeInTheDocument();
    expect(screen.getByText(restored.detail)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭安装提示' }));
    expect(screen.queryByRole('alert')).toBeNull();
    getResult.mockResolvedValue({ ...restored, id: 'attempt-2' });
    fireEvent.focus(window);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('does not query or display private installation errors to a member', () => {
    auth.user.role = 'member';
    render(<InstallationResultBanner />);
    expect(getResult).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('leaves ordinary and older installations without a banner', async () => {
    getResult.mockRejectedValue(new Error('404'));
    render(<InstallationResultBanner />);
    await waitFor(() => expect(getResult).toHaveBeenCalledOnce());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('does not describe a retained external database installation as restored SQLite', async () => {
    getResult.mockResolvedValue({ ...restored, recovery: 'retained', message: '现有外部数据库的数据已保留，没有切回旧 SQLite。' });
    render(<InstallationResultBanner />);
    expect(await screen.findByRole('alert')).toHaveTextContent('没有切回旧 SQLite');
    expect(screen.queryByText(restored.message)).toBeNull();
  });
});
