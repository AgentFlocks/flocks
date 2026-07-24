import { beforeEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_SESSION_EXECUTION_MODE,
  EXECUTION_MODE_DRAFT_STORAGE_KEY,
  EXECUTION_MODE_STORAGE_PREFIX,
  promoteDraftExecutionMode,
  readSessionExecutionMode,
  resetDraftExecutionMode,
  writeSessionExecutionMode,
} from './sessionExecutionMode';

describe('sessionExecutionMode storage', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults new and existing composers to Build', () => {
    expect(readSessionExecutionMode()).toBe(DEFAULT_SESSION_EXECUTION_MODE);
    expect(readSessionExecutionMode('session-1')).toBe(DEFAULT_SESSION_EXECUTION_MODE);
  });

  it('persists Plan independently by session', () => {
    writeSessionExecutionMode('session-1', 'plan');

    expect(readSessionExecutionMode('session-1')).toBe('plan');
  });

  it('promotes the draft mode to a newly created session', () => {
    writeSessionExecutionMode(null, 'plan');
    promoteDraftExecutionMode('session-new', 'plan');

    expect(readSessionExecutionMode('session-new')).toBe('plan');
    expect(localStorage.getItem(EXECUTION_MODE_DRAFT_STORAGE_KEY)).toBeNull();
  });

  it('stores Build by removing the override', () => {
    localStorage.setItem(`${EXECUTION_MODE_STORAGE_PREFIX}session-1`, 'plan');
    writeSessionExecutionMode('session-1', 'build');
    writeSessionExecutionMode(null, 'plan');
    resetDraftExecutionMode();

    expect(localStorage.getItem(`${EXECUTION_MODE_STORAGE_PREFIX}session-1`)).toBeNull();
    expect(localStorage.getItem(EXECUTION_MODE_DRAFT_STORAGE_KEY)).toBeNull();
  });

  it('ignores removed and one-shot persisted modes', () => {
    localStorage.setItem(`${EXECUTION_MODE_STORAGE_PREFIX}session-1`, 'ask');
    expect(readSessionExecutionMode('session-1')).toBe('build');

    localStorage.setItem(`${EXECUTION_MODE_STORAGE_PREFIX}session-1`, 'goal');
    expect(readSessionExecutionMode('session-1')).toBe('build');
  });
});
