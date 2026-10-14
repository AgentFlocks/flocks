import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import WorkflowStatusBadge from './WorkflowStatusBadge';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? '',
  }),
}));

describe('WorkflowStatusBadge', () => {
  it('renders running status as a healthy green badge', () => {
    render(<WorkflowStatusBadge status="running" />);

    expect(screen.getByText('running')).toHaveClass('bg-green-100', 'text-green-700');
  });
});
