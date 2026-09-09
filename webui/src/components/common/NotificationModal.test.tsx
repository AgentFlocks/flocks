import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import NotificationModal from './NotificationModal';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('NotificationModal', () => {
  it('renders release notes as Markdown with safe links', () => {
    render(
      <NotificationModal
        notifications={[{
          id: 'whats-new-2026.8.17',
          kind: 'whats_new',
          title: 'Flocks v2026.8.17 更新内容',
          body: '### 记忆与自进化\n\n* 支持 **Dream** 和 `/dream`\n\n[项目地址](https://github.com/AgentFlocks/flocks)\n\n[危险链接](javascript:alert(1))\n\n<script>alert(1)</script>',
          highlights: [],
          priority: 100,
        }]}
        onAcknowledge={vi.fn()}
        onClose={vi.fn()}
        onDismissForever={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { level: 3, name: '记忆与自进化' })).toBeInTheDocument();
    expect(screen.getByRole('listitem')).toHaveTextContent('支持 Dream 和 /dream');
    expect(screen.getByText('Dream').tagName).toBe('STRONG');
    expect(screen.getByText('/dream').tagName).toBe('CODE');
    const link = screen.getByRole('link', { name: '项目地址' });
    expect(link).toHaveAttribute('href', 'https://github.com/AgentFlocks/flocks');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText('危险链接')).not.toHaveAttribute('href', 'javascript:alert(1)');
    expect(document.querySelector('script')).toBeNull();
  });
});
