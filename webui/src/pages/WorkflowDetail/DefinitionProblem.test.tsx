import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Workflow } from '@/api/workflow';
import DefinitionProblem from './DefinitionProblem';

const { restoreDefinition, confirm } = vi.hoisted(() => ({ restoreDefinition: vi.fn(), confirm: vi.fn() }));
vi.mock('@/api/workflow', () => ({ workflowAPI: { restoreDefinition } }));
vi.mock('@/components/common/ConfirmDialog', () => ({ useConfirm: () => confirm }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

const workflow = {
  id: 'broken', definitionError: {
    code: 'WORKFLOW_DEFINITION_INVALID', file: 'workflow.json', line: 10, column: 3,
    message: 'workflow.json (line 10, column 3): Invalid control character', canRestore: true,
  },
} as Workflow;

beforeEach(() => {
  vi.clearAllMocks();
  confirm.mockResolvedValue(true);
  restoreDefinition.mockResolvedValue({ data: {} });
});

describe('invalid workflow definition recovery', () => {
  it('shows the parse error and restores the named file after confirmation', async () => {
    const reload = vi.fn();
    render(<DefinitionProblem workflow={workflow} onReload={reload} onBack={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('line 10, column 3');
    fireEvent.click(screen.getByRole('button', { name: 'definition.restore' }));
    await waitFor(() => expect(restoreDefinition).toHaveBeenCalledExactlyOnceWith('broken', 'workflow.json'));
    expect(reload).toHaveBeenCalledOnce();
  });

  it('does not restore when confirmation is cancelled', async () => {
    confirm.mockResolvedValue(false);
    render(<DefinitionProblem workflow={workflow} onReload={vi.fn()} onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'definition.restore' }));
    await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
    expect(restoreDefinition).not.toHaveBeenCalled();
  });

  it('keeps retry available when no backup exists', () => {
    const reload = vi.fn();
    render(<DefinitionProblem workflow={{ ...workflow, definitionError: { ...workflow.definitionError!, canRestore: false } }} onReload={reload} onBack={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'definition.restore' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'common:button.retry' }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it('shows backend restoration errors without reloading a broken definition', async () => {
    restoreDefinition.mockRejectedValue({ response: { data: { detail: 'Backup is invalid' } } });
    const reload = vi.fn();
    render(<DefinitionProblem workflow={workflow} onReload={reload} onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'definition.restore' }));
    expect(await screen.findByText('Backup is invalid')).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });
});
