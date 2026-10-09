import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import NotificationModal from './NotificationModal';
import type { UserNotification } from '@/api/notifications';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const benefit: UserNotification = {
  id: 'holiday-benefits-2026-09-23',
  kind: 'benefit',
  title: '10 月 Token 政策调整',
  summary: '双节前做好切换，业务运行更安心。',
  body: '每日保留 1000 万免费 Token，可登录[微步大模型平台](https://portal.agentflocks.com)充值。\n\n### 双节过渡保障\n\n临时额度 **1亿Token**（2026年10月15日到期）。',
  highlights: [],
  priority: 10,
  qr_code: {
    src: '/notifications/holiday-transition-20260923.jpg',
    alt: '微步在线过渡保障申请小程序码',
    caption: '扫码申请过渡保障',
  },
};

function renderBenefit(acknowledgingIds: string[] = []) {
  const onAcknowledge = vi.fn();
  const onClose = vi.fn();
  const onDismissForever = vi.fn();
  return {
    ...render(<NotificationModal notifications={[benefit]} acknowledgingIds={acknowledgingIds} onAcknowledge={onAcknowledge} onClose={onClose} onDismissForever={onDismissForever} />),
    onAcknowledge,
    onClose,
    onDismissForever,
  };
}

describe('NotificationModal', () => {
  it('renders the holiday benefit with a complete local code, Markdown and safe top-up link', () => {
    renderBenefit();
    expect(screen.getByRole('heading', { name: '双节过渡保障' })).toBeInTheDocument();
    expect(screen.getByText('1亿Token').tagName).toBe('STRONG');
    const image = screen.getByRole('img', { name: benefit.qr_code!.alt });
    expect(image).toHaveAttribute('src', benefit.qr_code!.src);
    expect(image).toHaveAttribute('width', '128');
    expect(image).toHaveAttribute('height', '128');
    expect(screen.getByText(benefit.qr_code!.caption!)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '微步大模型平台' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('region')).toHaveClass('min-h-0', 'overflow-y-auto');
  });

  it('shows a readable fallback when the code fails to load', () => {
    renderBenefit();
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByRole('status')).toHaveTextContent('qrUnavailable');
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'gotIt' })).toBeEnabled();
  });

  it('keeps ordinary dismissal separate from permanent acknowledgement', () => {
    const { onAcknowledge, onDismissForever, onClose } = renderBenefit();
    fireEvent.click(screen.getByRole('button', { name: 'gotIt' }));
    expect(onAcknowledge).toHaveBeenCalledTimes(1);
    expect(onDismissForever).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'close' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(document.querySelector('[aria-hidden="true"].fixed')!);
    expect(onClose).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole('button', { name: 'dismissThis' }));
    expect(onDismissForever).toHaveBeenCalledTimes(1);
  });

  it('blocks repeat dismissal, Escape and backdrop clicks while saving', () => {
    const { onAcknowledge, onClose, onDismissForever } = renderBenefit([benefit.id]);
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(document.querySelector('[aria-hidden="true"].fixed')!);
    expect(onClose).not.toHaveBeenCalled();
    expect(onAcknowledge).not.toHaveBeenCalled();
    expect(onDismissForever).not.toHaveBeenCalled();
  });

  it('traps focus, locks page scrolling and restores both on close', () => {
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    const before = document.body.style.overflow;
    const { unmount } = renderBenefit();
    expect(screen.getByRole('dialog')).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    const close = screen.getByRole('button', { name: 'close' });
    const confirm = screen.getByRole('button', { name: 'gotIt' });
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(close).toHaveFocus();
    unmount();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe(before);
    trigger.remove();
  });

  it('keeps older benefits without a code as plain text', () => {
    render(<NotificationModal notifications={[{ ...benefit, body: '**Original plain text**', qr_code: null }]} onAcknowledge={vi.fn()} onClose={vi.fn()} onDismissForever={vi.fn()} />);
    expect(screen.getByText('**Original plain text**')).toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('recaptures focus after a busy-state transition moves it outside the dialog', () => {
    const { rerender, onAcknowledge, onClose, onDismissForever } = renderBenefit();
    screen.getByRole('button', { name: 'dismissThis' }).focus();
    rerender(<NotificationModal notifications={[benefit]} acknowledgingIds={[benefit.id]} onAcknowledge={onAcknowledge} onClose={onClose} onDismissForever={onDismissForever} />);
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(screen.getByRole('region')).toHaveFocus();
    outside.remove();
  });

  it('accepts a QR code without an optional caption', () => {
    render(<NotificationModal notifications={[{ ...benefit, qr_code: { ...benefit.qr_code!, caption: null } }]} onAcknowledge={vi.fn()} onClose={vi.fn()} onDismissForever={vi.fn()} />);
    expect(screen.getByRole('img')).toBeInTheDocument();
    expect(document.querySelector('figcaption')).toBeNull();
  });

  it('does not render or lock scrolling for an empty notification list', () => {
    const before = document.body.style.overflow;
    render(<NotificationModal notifications={[]} onAcknowledge={vi.fn()} onClose={vi.fn()} onDismissForever={vi.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe(before);
  });

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
