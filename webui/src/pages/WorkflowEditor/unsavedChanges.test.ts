import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearWorkflowDraft, readWorkflowDraft, writeWorkflowDraft, type WorkflowDraft } from './unsavedChanges';

const draft: WorkflowDraft = {
  baseUpdatedAt: 1_792_000_000_000,
  workflowJson: {
    start: 'a',
    nodes: [{ id: 'a', type: 'python', code: "outputs['x'] = 1" }],
    edges: [],
  } as WorkflowDraft['workflowJson'],
};

describe('workflow editor drafts', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    clearWorkflowDraft('wf-1');
    window.sessionStorage.clear();
  });

  it('keeps the draft for this tab and drops it when cleared', () => {
    writeWorkflowDraft('wf-1', draft);
    expect(readWorkflowDraft('wf-1')).toEqual(draft);
    expect(JSON.parse(window.sessionStorage.getItem('flocks.workflowEditor.draft.wf-1') ?? 'null')).toEqual(draft);

    clearWorkflowDraft('wf-1');
    expect(readWorkflowDraft('wf-1')).toBeNull();
    expect(window.sessionStorage.getItem('flocks.workflowEditor.draft.wf-1')).toBeNull();
  });

  it('still finds the draft in this page when sessionStorage refuses it', () => {
    const full = {
      getItem: () => null,
      setItem: () => {
        throw new DOMException('quota', 'QuotaExceededError');
      },
      removeItem: () => undefined,
    } as unknown as Storage;
    vi.spyOn(window, 'sessionStorage', 'get').mockReturnValue(full);

    writeWorkflowDraft('wf-1', draft);

    expect(readWorkflowDraft('wf-1')).toEqual(draft);
  });

  it('reads a draft left in sessionStorage by an earlier page load', () => {
    window.sessionStorage.setItem('flocks.workflowEditor.draft.wf-2', JSON.stringify(draft));
    expect(readWorkflowDraft('wf-2')).toEqual(draft);
    window.sessionStorage.setItem('flocks.workflowEditor.draft.wf-3', '{"baseUpdatedAt":"x"}');
    expect(readWorkflowDraft('wf-3')).toBeNull();
  });
});
