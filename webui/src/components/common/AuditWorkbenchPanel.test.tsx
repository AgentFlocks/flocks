import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AuditWorkbenchPanel from './AuditWorkbenchPanel';

describe('AuditWorkbenchPanel', () => {
  it('preserves the conversation and draft while closed and restores focus', () => {
    const close = vi.fn();
    const panel = (open: boolean) => <>
      <button>工作台</button>
      <AuditWorkbenchPanel open={open} title="审计工作台" closeLabel="关闭工作台" resizeLabel="调整宽度" onClose={close}>
        <p>已有回复</p><textarea aria-label="追问" />
      </AuditWorkbenchPanel>
    </>;
    const view = render(panel(false));
    const trigger = screen.getByRole('button', { name: '工作台' });
    trigger.focus();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    view.rerender(panel(true));
    expect(screen.getByRole('button', { name: '关闭工作台' })).toHaveFocus();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('dialog').closest('.code-security-workspace')).toBeNull();
    expect(screen.getByTestId('audit-workbench-backdrop')).toBeVisible();
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '继续分析' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(close).toHaveBeenCalledOnce();
    view.rerender(panel(false));
    expect(trigger).toHaveFocus();
    view.rerender(panel(true));
    expect(screen.getByRole('textbox')).toBe(input);
    expect(input).toHaveValue('继续分析');
    expect(screen.getByText('已有回复')).toBeVisible();
    fireEvent.click(screen.getByTestId('audit-workbench-backdrop'));
    expect(close).toHaveBeenCalledTimes(2);
    const separator = screen.getByRole('separator');
    const width = Number(separator.getAttribute('aria-valuenow'));
    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(Number(separator.getAttribute('aria-valuenow'))).toBeGreaterThan(width);
  });
});
