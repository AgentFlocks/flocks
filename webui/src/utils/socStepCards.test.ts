import { describe, expect, it } from 'vitest';

import {
  advanceStepStream, createStepStream, nodeDuration, STEP_CARD_LIMIT,
  STEP_DISPLAY_MS, STEP_EXIT_MS, STEP_REPLAY_WINDOW_MS, taskIsLive,
} from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/src/stepCards';

const NOW = Date.parse('2026-09-30T10:00:00Z');

function batch(key: string, options: {
  stage?: 'denoise' | 'triage'; state?: string; node?: string;
  phase?: string; durations?: Record<string, unknown>; updatedAt?: number;
  status?: string;
} = {}) {
  const { stage = 'denoise', state = 'processing', node, phase,
    durations = {}, updatedAt = NOW, status = state === 'completed' ? 'completed' : 'running' } = options;
  return { key, stage, state, event: {
    status, updatedAt: new Date(updatedAt).toISOString(),
    live: { nodeId: node, phase, stepDurationsMs: durations },
  } };
}

describe('SOC independent workflow step cards', () => {
  it('shows concurrent batches at the same node alongside a triage batch', () => {
    const tasks = [batch('a', { node: 'normalize' }), batch('b', { node: 'normalize' }),
      batch('c', { stage: 'triage', node: 'concurrent_triage' })];
    const state = advanceStepStream(createStepStream(), tasks, true, NOW);
    expect(state.cards.map(card => [card.task.key, card.nodeId, card.phase])).toEqual([
      ['a', 'normalize', 'processing'], ['b', 'normalize', 'processing'], ['c', 'concurrent_triage', 'processing'],
    ]);
    expect(new Set(state.cards.map(card => card.serial)).size).toBe(3);
  });

  it('keeps exactly one card per batch and advances only after the exit transition', () => {
    const task = batch('a', { state: 'completed', durations: {
      receive_alert: 20, normalize: 50, filter_logs: 31, dedup_and_write: 40,
    } });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    const serial = state.cards[0].serial;
    let now = NOW;
    for (const node of ['receive_alert', 'normalize', 'filter_logs', 'dedup_and_write']) {
      expect(state.cards).toHaveLength(1);
      expect(state.cards[0]).toMatchObject({ nodeId: node, serial, phase: 'complete', outcome: 'complete' });
      state = advanceStepStream(state, [task], true, now + STEP_DISPLAY_MS - 1);
      expect(state.cards[0].phase).toBe('complete');
      state = advanceStepStream(state, [task], true, now + STEP_DISPLAY_MS);
      expect(state.cards[0]).toMatchObject({ nodeId: node, phase: 'exit' });
      state = advanceStepStream(state, [task], true, now + STEP_DISPLAY_MS + STEP_EXIT_MS - 1);
      expect(state.cards[0].nodeId).toBe(node);
      now += STEP_DISPLAY_MS + STEP_EXIT_MS;
      state = advanceStepStream(state, [task], true, now);
    }
    expect(state.cards).toEqual([]);
    state = advanceStepStream(state, [task], true, now + 1);
    expect(state.cards).toEqual([]);
    expect(state.serial).toBe(1);
  });

  it('replays only measured nodes without filling gaps or changing 20ms and 50ms durations', () => {
    const task = batch('a', { state: 'completed', durations: { normalize: 20, dedup_and_write: 50 } });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', durationMs: 20 });
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS);
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'dedup_and_write', durationMs: 50 });
    expect(state.batches.a.nodes).toEqual(['normalize']);
  });

  it('does not replay a completed batch without measured steps', () => {
    const task = batch('a', { state: 'completed', node: 'dedup_and_write' });
    expect(advanceStepStream(createStepStream(), [task], true, NOW).cards).toEqual([]);
  });

  it.each(['failed', 'error', 'cancelled', 'canceled', 'timeout'])('never reconstructs a %s batch as successful steps', status => {
    const task = batch(status, { state: 'completed', status, durations: { normalize: 20 } });
    expect(advanceStepStream(createStepStream(), [task], true, NOW).cards).toEqual([]);
  });

  it.each([STEP_REPLAY_WINDOW_MS, STEP_REPLAY_WINDOW_MS + 1, -1])('does not replay completed timestamps outside the recent window (%s)', age => {
    const task = batch('a', { state: 'completed', updatedAt: NOW - age, durations: { normalize: 20 } });
    expect(advanceStepStream(createStepStream(), [task], true, NOW).cards).toEqual([]);
  });

  it('shows only a live unknown card when the actual step is unavailable', () => {
    const task = batch('a');
    const state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards).toHaveLength(1);
    expect(state.cards[0]).toMatchObject({ nodeId: 'unknown', phase: 'processing', durationMs: null });
  });

  it('ends an unknown live card when the task finishes without a node or timing', () => {
    let task = batch('a');
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    task = batch('a', { state: 'completed' });
    state = advanceStepStream(state, [task], true, NOW + 100);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'changed', durationMs: null });
    state = advanceStepStream(state, [task], true, NOW + 100 + STEP_EXIT_MS);
    expect(state.cards).toEqual([]);
  });

  it('never infers completion or a duration from a change of the live node', () => {
    let task = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    task = batch('a', { node: 'filter_logs' });
    state = advanceStepStream(state, [task], true, NOW + 50);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'exit', outcome: 'changed', durationMs: null });
    state = advanceStepStream(state, [task], true, NOW + 50 + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'filter_logs', phase: 'processing', durationMs: null });
    expect(state.cards[0].serial).toBe(1);
  });

  it('updates a live node to measured completion without substituting the animation duration', () => {
    let task = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    task = batch('a', { node: 'filter_logs', durations: { normalize: 20 } });
    state = advanceStepStream(state, [task], true, NOW + 20);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', durationMs: 20, phase: 'complete', outcome: 'complete' });
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS - 1);
    expect(state.cards[0].phase).toBe('complete');
  });

  it.each(['unconfirmed', 'waiting'])('stops a live card when the batch becomes %s', stateName => {
    let task = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    task = batch('a', { state: stateName, node: 'normalize' });
    state = advanceStepStream(state, [task], true, NOW + 100);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'stopped', durationMs: null });
    state = advanceStepStream(state, [task], true, NOW + 100 + STEP_EXIT_MS);
    expect(state.cards).toEqual([]);
  });

  it.each(['success', 'failed', 'cancelled', 'canceled', 'timeout'])('stops live motion when %s is received before persisted terminal state', phase => {
    let task = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    task = batch('a', { node: 'normalize', phase });
    expect(taskIsLive(task)).toBe(false);
    state = advanceStepStream(state, [task], true, NOW + 100);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'stopped' });
  });

  it('clears disconnected cards, resumes current work, and preserves finished-step deduplication', () => {
    const task = batch('a', { node: 'normalize', durations: { receive_alert: 20 } });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS);
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards[0].nodeId).toBe('normalize');
    state = advanceStepStream(state, [task], false, NOW + 2000);
    expect(state.cards).toEqual([]);
    state = advanceStepStream(state, [task], true, NOW + 2200);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'processing', serial: 1 });
    expect(state.batches.a.nodes).toEqual(['receive_alert']);
  });

  it.each(['unconfirmed', 'missing'])('resumes the same live step after a %s snapshot is confirmed again', interruption => {
    const task = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    const interrupted = interruption === 'missing' ? [] : [batch('a', { state: 'unconfirmed', node: 'normalize' })];
    state = advanceStepStream(state, interrupted, true, NOW + 50);
    state = advanceStepStream(state, interrupted, true, NOW + 50 + STEP_EXIT_MS);
    expect(state.cards).toEqual([]);
    state = advanceStepStream(state, [task], true, NOW + 100 + STEP_EXIT_MS);
    expect(state.cards).toHaveLength(1);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'processing', serial: 1 });
  });

  it('retains existing slots under burst traffic and caps concurrent cards at six', () => {
    const tasks = Array.from({ length: 20 }, (_, i) => batch(`a${i}`, { node: 'normalize' }));
    let state = advanceStepStream(createStepStream(), tasks, true, NOW);
    expect(state.cards).toHaveLength(STEP_CARD_LIMIT);
    const keys = state.cards.map(card => card.task.key);
    const burst = Array.from({ length: 30 }, (_, i) => batch(`b${i}`, { node: 'filter_logs' }));
    state = advanceStepStream(state, [...burst, ...tasks], true, NOW + 50);
    expect(state.cards.map(card => card.task.key)).toEqual(keys);
    expect(Object.keys(state.batches)).toHaveLength(STEP_CARD_LIMIT);
  });

  it('bounds memory during prolonged traffic and does not evict an in-flight batch', () => {
    const long = batch('long', { node: 'normalize' });
    let state = createStepStream();
    for (let i = 0; i < 180; i += 1) {
      const now = NOW + i * 2200;
      const fast = batch(`batch-${i}`, { state: 'completed', updatedAt: now, durations: { normalize: 20 } });
      state = advanceStepStream(state, [long, fast], true, now);
      state = advanceStepStream(state, [long, fast], true, now + STEP_DISPLAY_MS);
      state = advanceStepStream(state, [long, fast], true, now + STEP_DISPLAY_MS + STEP_EXIT_MS);
      expect(state.cards.length).toBeLessThanOrEqual(STEP_CARD_LIMIT);
      expect(Object.keys(state.batches).length).toBeLessThanOrEqual(128);
      expect(state.batches.long).toBeDefined();
    }
  });

  it.each([undefined, null, -1, NaN, Infinity, '20'])('preserves unknown or invalid step durations (%s)', duration => {
    const task = batch('a', { node: 'normalize', durations: { normalize: duration } });
    expect(nodeDuration(task, 'normalize')).toBeNull();
    const state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards[0]).toMatchObject({ phase: 'processing', durationMs: null });
  });

  it('does not mutate a previously rendered stream', () => {
    const task = batch('a', { state: 'completed', durations: { normalize: 20 } });
    const before = advanceStepStream(createStepStream(), [task], true, NOW);
    const snapshot = JSON.stringify(before);
    const exit = advanceStepStream(before, [task], true, NOW + STEP_DISPLAY_MS);
    advanceStepStream(exit, [task], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS);
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});
