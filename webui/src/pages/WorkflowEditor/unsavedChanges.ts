import { useEffect } from 'react';
import type { WorkflowJSON } from '@/api/workflow';

/**
 * Unsaved canvas edits, kept for this browser tab.
 *
 * A reload, or a route change the app's router cannot intercept (browser
 * history), unmounts the editor. The edits are kept here and offered back when
 * the same workflow is opened again in this tab, as long as nobody saved it in
 * the meantime.
 */
export interface WorkflowDraft {
  /** updatedAt of the saved version the edits started from. */
  baseUpdatedAt: number;
  workflowJson: WorkflowJSON;
}

const DRAFT_KEY_PREFIX = 'flocks.workflowEditor.draft.';
// The same drafts in memory: a remount in this page still finds them when
// sessionStorage is full or blocked (only a reload needs the stored copy).
const memoryDrafts = new Map<string, WorkflowDraft>();

function draftStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readWorkflowDraft(workflowId: string): WorkflowDraft | null {
  return memoryDrafts.get(workflowId) ?? readStoredDraft(workflowId);
}

function readStoredDraft(workflowId: string): WorkflowDraft | null {
  try {
    const raw = draftStorage()?.getItem(DRAFT_KEY_PREFIX + workflowId);
    if (!raw) return null;
    const draft = JSON.parse(raw) as Partial<WorkflowDraft> | null;
    const json = draft?.workflowJson;
    if (
      typeof draft?.baseUpdatedAt !== 'number'
      || !json
      || !Array.isArray(json.nodes)
      || !Array.isArray(json.edges)
    ) {
      return null;
    }
    return draft as WorkflowDraft;
  } catch {
    return null;
  }
}

export function writeWorkflowDraft(workflowId: string, draft: WorkflowDraft): void {
  memoryDrafts.set(workflowId, draft);
  try {
    draftStorage()?.setItem(DRAFT_KEY_PREFIX + workflowId, JSON.stringify(draft));
  } catch {
    // Storage full or blocked: the copy in memory still covers this page.
  }
}

export function clearWorkflowDraft(workflowId: string): void {
  memoryDrafts.delete(workflowId);
  try {
    draftStorage()?.removeItem(DRAFT_KEY_PREFIX + workflowId);
  } catch {
    // Nothing stored, or storage blocked.
  }
}

/** While ``active``, closing or reloading the tab asks the browser to confirm. */
export function useConfirmBeforeUnload(active: boolean): void {
  useEffect(() => {
    if (!active) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Older browsers only show the prompt when returnValue is set.
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [active]);
}
