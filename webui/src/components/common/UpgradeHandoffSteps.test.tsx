import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import UpgradeHandoffSteps, { useHandoffFailureMessage } from './UpgradeHandoffSteps';
import type { UpgradeHandoffProgress } from '@/api/update';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === 'handoff.failed') return `failed at ${options?.step}: ${options?.error}`;
      if (key.startsWith('handoff.steps.')) return `label:${key.slice('handoff.steps.'.length)}`;
      return key;
    },
  }),
}));

const failed: UpgradeHandoffProgress = {
  phase: 'failed',
  step: 'build_webui',
  failed_step: 'build_webui',
  error: 'npm run build exited with code 1',
  steps: [
    { id: 'stop', status: 'done' },
    { id: 'check_runtime', status: 'skipped' },
    { id: 'build_webui', status: 'failed' },
    { id: 'start', status: 'pending' },
  ],
};

function FailureMessage({ progress }: { progress: UpgradeHandoffProgress }) {
  const describe = useHandoffFailureMessage();
  return <p>{describe(progress)}</p>;
}

describe('UpgradeHandoffSteps', () => {
  it('lists every step with its state', () => {
    render(<UpgradeHandoffSteps progress={failed} />);

    const list = screen.getByRole('list', { name: 'handoff.title' });
    const items = within(list).getAllByRole('listitem');
    expect(items.map((item) => item.getAttribute('data-status'))).toEqual(['done', 'skipped', 'failed', 'pending']);
    expect(items[2]).toHaveTextContent('label:build_webui');
    expect(items[2]).toHaveTextContent('handoff.statuses.failed');
  });

  it('describes the failed step, the reason and how to recover', () => {
    render(<FailureMessage progress={failed} />);

    expect(screen.getByText('failed at label:build_webui: npm run build exited with code 1 handoff.recoveryHint')).toBeInTheDocument();
  });

  it('says where the reason is when the handoff withheld it', () => {
    render(<FailureMessage progress={{ ...failed, error: undefined }} />);

    expect(screen.getByText('failed at label:build_webui: handoff.noDetail handoff.recoveryHint')).toBeInTheDocument();
  });
});
