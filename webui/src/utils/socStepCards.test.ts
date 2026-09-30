import { describe, expect, it } from 'vitest';

import {
  advanceStepStream, createStepStream, nodeDuration, STEP_CARD_LIMIT,
  STEP_DISPLAY_MS, STEP_BATCH_DISPLAY_MS, STEP_EXIT_MS, STEP_REPLAY_WINDOW_MS, taskIsLive,
} from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/src/stepCards';

const NOW = Date.parse('2026-09-30T10:00:00Z');

function batch(key: string, options: {
  stage?: 'denoise' | 'triage'; state?: string; node?: unknown;
  phase?: string; durations?: Record<string, unknown>; updatedAt?: number;
  status?: string; durationMs?: unknown;
} = {}) {
  const { stage = 'denoise', state = 'processing', node, phase,
    durations = {}, updatedAt = NOW, status = state === 'completed' ? 'completed' : 'running', durationMs } = options;
  return { key, stage, state, event: {
    status, updatedAt: new Date(updatedAt).toISOString(), result: { durationMs },
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

  it.each([undefined, 230])('shows a real completed batch without inventing step timings (duration %s)', durationMs => {
    const task = batch('a', { state: 'completed', node: 'dedup_and_write', durationMs });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards).toHaveLength(1);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'complete', durationMs: durationMs ?? null });
    state = advanceStepStream(state, [task], true, NOW + STEP_BATCH_DISPLAY_MS - 1);
    expect(state.cards[0].phase).toBe('complete');
    state = advanceStepStream(state, [task], true, NOW + STEP_BATCH_DISPLAY_MS);
    state = advanceStepStream(state, [task], true, NOW + STEP_BATCH_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards).toEqual([]);
    expect(nodeDuration(task, 'dedup_and_write')).toBeNull();
  });

  it.each(['failed', 'error', 'cancelled', 'canceled', 'timeout'])('never reconstructs a %s batch as successful steps', status => {
    const task = batch(status, { state: 'completed', status, durations: { normalize: 20 } });
    expect(advanceStepStream(createStepStream(), [task], true, NOW).cards).toEqual([]);
  });

  it.each([STEP_REPLAY_WINDOW_MS, STEP_REPLAY_WINDOW_MS + 1, -1])('does not replay completed timestamps outside the recent window (%s)', age => {
    const task = batch('a', { state: 'completed', updatedAt: NOW - age, durations: { normalize: 20 } });
    expect(advanceStepStream(createStepStream(), [task], true, NOW).cards).toEqual([]);
  });

  it.each([undefined, null, '', 'unknown', 'not-a-step', 1, {}])('uses a real batch card when current-step telemetry is unavailable (%s)', node => {
    const task = batch('a', { node });
    const state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards).toHaveLength(1);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'processing', durationMs: null });
    expect(state.serial).toBe(1);
  });

  it('keeps a batch card visible for its minimum before switching to an observed real step', () => {
    let state = advanceStepStream(createStepStream(), [batch('a')], true, NOW);
    const task = batch('a', { node: 'normalize' });
    state = advanceStepStream(state, [task], true, NOW + 1);
    expect(state.cards).toHaveLength(1);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'replay', outcome: 'changed', durationMs: null });
    state = advanceStepStream(state, [task], true, NOW + STEP_BATCH_DISPLAY_MS - 1);
    expect(state.cards[0].phase).toBe('replay');
    state = advanceStepStream(state, [task], true, NOW + STEP_BATCH_DISPLAY_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'exit', outcome: 'changed' });
    state = advanceStepStream(state, [task], true, NOW + STEP_BATCH_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'processing', serial: 1 });
    expect(state.batches.a.nodes).not.toContain('batch');
  });

  it('bounds real batch fallbacks at six without creating unknown-step placeholders', () => {
    const unknown = Array.from({ length: 20 }, (_, i) => batch(`unknown-${i}`));
    const known = Array.from({ length: STEP_CARD_LIMIT }, (_, i) => batch(`known-${i}`, { node: 'normalize' }));
    const state = advanceStepStream(createStepStream(), [...unknown, ...known], true, NOW);
    expect(state.cards).toHaveLength(STEP_CARD_LIMIT);
    expect(state.cards.every(card => card.nodeId === 'batch')).toBe(true);
    expect(Object.keys(state.batches)).toHaveLength(STEP_CARD_LIMIT);
    expect(state.serial).toBe(STEP_CARD_LIMIT);
  });

  it.each(['processing', 'completed'])('preserves measured successful steps despite missing current node (%s)', stateName => {
    const task = batch('a', { state: stateName, durations: { normalize: 20 } });
    const state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards).toHaveLength(1);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'complete', durationMs: 20 });
  });

  it.each(['processing', 'replay', 'complete', 'exit'] as const)('drops a legacy unknown card in %s phase without an exit animation or slot delay', phase => {
    const original = batch('a', { node: 'normalize' });
    const legacy = advanceStepStream(createStepStream(), [original], true, NOW);
    legacy.cards[0] = { ...legacy.cards[0], nodeId: 'unknown', phase, endAt: NOW + 9999 };
    const known = Array.from({ length: STEP_CARD_LIMIT - 1 }, (_, i) => batch(`known-${i}`, { node: 'normalize' }));
    const state = advanceStepStream(legacy, [original, ...known], true, NOW + 1);
    expect(state.cards).toHaveLength(STEP_CARD_LIMIT);
    expect(state.cards.every(card => card.nodeId === 'normalize' && card.phase === 'processing')).toBe(true);
    expect(state.cards[0]).toMatchObject({ serial: 1, enteredAt: NOW + 1 });
    expect(state.batches.a.nodes).not.toContain('unknown');
  });

  it('can resume a previously visible step after its current-node telemetry disappears', () => {
    const original = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [original], true, NOW);
    state = advanceStepStream(state, [batch('a')], true, NOW + 50);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'exit', outcome: 'stopped' });
    state = advanceStepStream(state, [batch('a')], true, NOW + 50 + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'processing' });
    state = advanceStepStream(state, [original], true, NOW + 51 + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'replay', outcome: 'changed' });
    state = advanceStepStream(state, [original], true, NOW + 50 + STEP_EXIT_MS + STEP_BATCH_DISPLAY_MS);
    state = advanceStepStream(state, [original], true, NOW + 50 + STEP_EXIT_MS * 2 + STEP_BATCH_DISPLAY_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'processing', serial: 1 });
  });

  it('never infers completion or a duration from a change of the live node', () => {
    let task = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    task = batch('a', { node: 'filter_logs' });
    state = advanceStepStream(state, [task], true, NOW + 50);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'replay', outcome: 'changed', durationMs: null });
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS - 1);
    expect(state.cards[0].phase).toBe('replay');
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'exit', outcome: 'changed' });
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS);
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

  it('uses a two-second minimum for step presentation without restarting it at every poll', () => {
    expect(STEP_DISPLAY_MS).toBe(2000);
    let state = advanceStepStream(createStepStream(), [batch('a', { node: 'normalize' })], true, NOW);
    const next = batch('a', { node: 'filter_logs' });
    for (const elapsed of [20, 40, 120, 700, 1999]) {
      state = advanceStepStream(state, [next], true, NOW + elapsed);
      expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'replay',
        outcome: 'changed', durationMs: null, enteredAt: NOW, endAt: NOW + 2000 });
    }
    state = advanceStepStream(state, [next], true, NOW + 2000);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'changed' });
  });

  it('keeps an observed step after successful batch completion without claiming an unmeasured step succeeded', () => {
    let state = advanceStepStream(createStepStream(), [batch('a', { node: 'normalize' })], true, NOW);
    const completed = batch('a', { state: 'completed', node: 'dedup_and_write', durationMs: 83 });
    state = advanceStepStream(state, [completed], true, NOW + 50);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'replay', outcome: 'changed', durationMs: null });
    state = advanceStepStream(state, [], true, NOW + STEP_DISPLAY_MS - 1);
    expect(state.cards[0].phase).toBe('replay');
    state = advanceStepStream(state, [], true, NOW + STEP_DISPLAY_MS);
    state = advanceStepStream(state, [], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'complete', durationMs: 83 });
    expect(state.batches.a.nodes).toEqual(['normalize']);
    expect(state.batches.a.nodes).not.toContain('dedup_and_write');
  });

  it('upgrades a retained step only when its actual duration arrives', () => {
    let state = advanceStepStream(createStepStream(), [batch('a', { node: 'normalize' })], true, NOW);
    state = advanceStepStream(state, [batch('a', { node: 'filter_logs' })], true, NOW + 20);
    expect(state.cards[0].phase).toBe('replay');
    const measured = batch('a', { node: 'filter_logs', durations: { normalize: 50 }, durationMs: 9999 });
    state = advanceStepStream(state, [measured], true, NOW + 100);
    expect(state.cards[0]).toMatchObject({ phase: 'complete', outcome: 'complete', durationMs: 50,
      endAt: NOW + STEP_DISPLAY_MS });
  });

  it('preserves a measured batch duration while switching from a batch card to successful steps', () => {
    let state = advanceStepStream(createStepStream(), [batch('a')], true, NOW);
    const completed = batch('a', { state: 'completed', durationMs: 70, durations: { normalize: 20, dedup_and_write: 50 } });
    state = advanceStepStream(state, [completed], true, NOW + 70);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'complete', outcome: 'complete', durationMs: 70 });
    state = advanceStepStream(state, [completed], true, NOW + STEP_BATCH_DISPLAY_MS - 1);
    expect(state.cards[0].phase).toBe('complete');
    state = advanceStepStream(state, [completed], true, NOW + STEP_BATCH_DISPLAY_MS);
    state = advanceStepStream(state, [completed], true, NOW + STEP_BATCH_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'normalize', phase: 'complete', durationMs: 20 });
  });

  it('does not extend a normal transition once the minimum presentation has already elapsed', () => {
    const initial = batch('a', { node: 'normalize' });
    let state = advanceStepStream(createStepStream(), [initial], true, NOW);
    state = advanceStepStream(state, [initial], true, NOW + STEP_DISPLAY_MS + 1000);
    expect(state.cards[0].phase).toBe('processing');
    state = advanceStepStream(state, [batch('a', { node: 'filter_logs' })], true, NOW + STEP_DISPLAY_MS + 1100);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'changed', durationMs: null });
  });

  it.each(['failed', 'unconfirmed', 'missing', 'offline'])('stops a retained step immediately on %s instead of waiting for its display minimum', interruption => {
    let state = advanceStepStream(createStepStream(), [batch('a', { node: 'normalize' })], true, NOW);
    const next = batch('a', { node: 'filter_logs' });
    state = advanceStepStream(state, [next], true, NOW + 20);
    expect(state.cards[0].phase).toBe('replay');
    const tasks = interruption === 'missing' ? [] : interruption === 'failed'
      ? [batch('a', { state: 'completed', status: 'failed', node: 'filter_logs' })]
      : interruption === 'unconfirmed' ? [batch('a', { state: 'unconfirmed', node: 'filter_logs' })] : [next];
    state = advanceStepStream(state, tasks, interruption !== 'offline', NOW + 21);
    if (interruption === 'offline') expect(state.cards).toEqual([]);
    else expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'stopped', endAt: NOW + 21 + STEP_EXIT_MS });
  });

  it('does not delay a failure during a batch-to-step presentation transition', () => {
    let state = advanceStepStream(createStepStream(), [batch('a')], true, NOW);
    state = advanceStepStream(state, [batch('a', { node: 'normalize' })], true, NOW + 20);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'replay' });
    state = advanceStepStream(state, [batch('a', { node: 'normalize', state: 'completed', status: 'failed' })], true, NOW + 21);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'stopped', durationMs: null });
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
    state = advanceStepStream(state, [task], false, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS + 100);
    expect(state.cards).toEqual([]);
    state = advanceStepStream(state, [task], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS + 200);
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

  it('reserves three slots per category when long-running triage and completed denoise both need display', () => {
    const triage = Array.from({ length: 6 }, (_, i) => batch(`t${i}`, { stage: 'triage', node: 'concurrent_triage' }));
    const denoise = Array.from({ length: 10 }, (_, i) => batch(`d${i}`, { state: 'completed' }));
    const state = advanceStepStream(createStepStream(), [...triage, ...denoise], true, NOW);
    expect(state.cards).toHaveLength(6);
    expect(state.cards.filter(card => card.task.stage === 'triage')).toHaveLength(3);
    expect(state.cards.filter(card => card.task.stage === 'denoise')).toHaveLength(3);
  });

  it('rotates surplus cards when the other category arrives without marking the running nodes complete', () => {
    const triage = Array.from({ length: 6 }, (_, i) => batch(`t${i}`, { stage: 'triage', node: 'concurrent_triage' }));
    const denoise = Array.from({ length: 3 }, (_, i) => batch(`d${i}`, { state: 'completed' }));
    let state = advanceStepStream(createStepStream(), triage, true, NOW);
    expect(state.cards).toHaveLength(6);
    state = advanceStepStream(state, [...triage, ...denoise], true, NOW + 100);
    expect(state.cards.filter(card => card.outcome === 'yielded')).toHaveLength(3);
    state = advanceStepStream(state, [...triage, ...denoise], true, NOW + 100 + STEP_EXIT_MS);
    expect(state.cards.filter(card => card.task.stage === 'triage')).toHaveLength(3);
    expect(state.cards.filter(card => card.task.stage === 'denoise')).toHaveLength(3);
    expect(state.batches.t3.nodes).toEqual([]);
    expect(state.batches.t4.nodes).toEqual([]);
    expect(state.batches.t5.nodes).toEqual([]);
  });

  it('continues a selected successful replay using its own evidence after source-cache eviction', () => {
    const task = batch('a', { state: 'completed', durations: {
      receive_alert: 20, normalize: 50, filter_logs: 30, dedup_and_write: 40,
    } });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    let now = NOW;
    for (const node of ['receive_alert', 'normalize', 'filter_logs', 'dedup_and_write']) {
      expect(state.cards[0]).toMatchObject({ nodeId: node, phase: 'complete', outcome: 'complete' });
      state = advanceStepStream(state, [], true, now + STEP_DISPLAY_MS);
      state = advanceStepStream(state, [], true, now + STEP_DISPLAY_MS + STEP_EXIT_MS);
      now += STEP_DISPLAY_MS + STEP_EXIT_MS;
    }
    expect(state.cards).toEqual([]);
    expect(state.batches.a.nodes).toEqual(['receive_alert', 'normalize', 'filter_logs', 'dedup_and_write']);
  });

  it('finishes an already-selected recent replay even when later steps cross the admission window', () => {
    const task = batch('a', { state: 'completed', updatedAt: NOW - STEP_REPLAY_WINDOW_MS + 1,
      durations: { normalize: 20, dedup_and_write: 50 } });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    state = advanceStepStream(state, [], true, NOW + STEP_DISPLAY_MS);
    state = advanceStepStream(state, [], true, NOW + STEP_DISPLAY_MS + STEP_EXIT_MS);
    expect(state.cards[0]).toMatchObject({ nodeId: 'dedup_and_write', phase: 'complete' });
  });

  it.each(['failed', 'unconfirmed'])('stops retained successful replay when explicitly observed as %s', status => {
    const task = batch('a', { state: 'completed', durations: { normalize: 20, dedup_and_write: 50 } });
    let state = advanceStepStream(createStepStream(), [task], true, NOW);
    const stopped = batch('a', { state: status === 'failed' ? 'completed' : status, status,
      durations: { normalize: 20, dedup_and_write: 50 } });
    state = advanceStepStream(state, [stopped], true, NOW + 1);
    expect(state.cards[0]).toMatchObject({ phase: 'exit', outcome: 'stopped' });
    state = advanceStepStream(state, [stopped], true, NOW + 1 + STEP_EXIT_MS);
    expect(state.cards).toEqual([]);
  });

  it('stops successful replay immediately while offline', () => {
    const task = batch('a', { state: 'completed', durations: { normalize: 20 } });
    const state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(advanceStepStream(state, [], false, NOW + 1).cards).toEqual([]);
  });

  it('updates a running batch to successful completion with unknown duration without inventing milliseconds', () => {
    let state = advanceStepStream(createStepStream(), [batch('a')], true, NOW);
    state = advanceStepStream(state, [batch('a', { state: 'completed' })], true, NOW + 50);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'complete', outcome: 'complete', durationMs: null });
  });

  it('does not use a supplied whole-batch duration for any live step', () => {
    const task = batch('a', { durationMs: 12000 });
    expect(nodeDuration(task, 'batch')).toBeNull();
    const state = advanceStepStream(createStepStream(), [task], true, NOW);
    expect(state.cards[0]).toMatchObject({ nodeId: 'batch', phase: 'processing', durationMs: null });
  });

  it('keeps both categories visible under repeated three-second denoise bursts without unbounded playback queues', () => {
    const triage = Array.from({ length: 6 }, (_, i) => batch(`t${i}`, { stage: 'triage', node: 'concurrent_triage' }));
    let state = advanceStepStream(createStepStream(), triage, true, NOW);
    let denoise: ReturnType<typeof batch>[] = [];
    const seenDenoise = new Set<string>();
    for (let elapsed = 0; elapsed < 120000; elapsed += 120) {
      if (elapsed % 3000 === 0) {
        denoise = Array.from({ length: 10 }, (_, i) => batch(`d${elapsed}-${i}`, {
          state: 'completed', updatedAt: NOW + elapsed, durationMs: 30,
        }));
      }
      state = advanceStepStream(state, [...triage, ...denoise], true, NOW + elapsed);
      expect(state.cards.length).toBeLessThanOrEqual(STEP_CARD_LIMIT);
      expect(Object.keys(state.batches).length).toBeLessThanOrEqual(128);
      expect(new Set(state.cards.map(card => card.task.key)).size).toBe(state.cards.length);
      if (elapsed >= STEP_EXIT_MS) {
        expect(state.cards.filter(card => card.task.stage === 'triage')).toHaveLength(3);
        expect(state.cards.filter(card => card.task.stage === 'denoise')).toHaveLength(3);
      }
      state.cards.filter(card => card.task.stage === 'denoise').forEach(card => seenDenoise.add(card.task.key));
    }
    expect(seenDenoise.size).toBeGreaterThan(60);
  });

  it('bounds memory during prolonged traffic and does not evict an in-flight batch', () => {
    const long = batch('long', { node: 'normalize' });
    let state = createStepStream();
    for (let i = 0; i < 180; i += 1) {
      const now = NOW + i * (STEP_DISPLAY_MS + STEP_EXIT_MS + 1);
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
