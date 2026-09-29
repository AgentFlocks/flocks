// A bounded presentation state machine. It never starts or cancels a workflow.
export const STEP_CARD_LIMIT = 6;
export const STEP_DISPLAY_MS = 1600;
export const STEP_EXIT_MS = 360;
export const STEP_REPLAY_WINDOW_MS = 30000;
export const STEP_NODES = {
  denoise: ['receive_alert', 'normalize', 'filter_logs', 'dedup_and_write'],
  triage: ['load_dedup_file', 'concurrent_triage', 'commit_cursor', 'summarize'],
};

export type StepCard = {
  task: any; nodeId: string; serial: number; enteredAt: number;
  phase: 'processing' | 'complete' | 'exit'; endAt: number;
  outcome: 'complete' | 'changed' | 'stopped'; durationMs: number | null;
};
type BatchMemory = { nodes: string[]; serial: number; touchedAt: number };
export type StepStream = { cards: StepCard[]; batches: Record<string, BatchMemory>; serial: number };
export const createStepStream = (): StepStream => ({ cards: [], batches: {}, serial: 0 });

export function nodeDuration(task: any, nodeId: string): number | null {
  const value = task?.event?.live?.stepDurationsMs?.[nodeId];
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}
export function taskIsLive(task: any): boolean {
  return task?.state === 'processing' && !['success', 'completed', 'failed', 'error', 'cancelled', 'canceled', 'timeout'].includes(task.event?.live?.phase);
}
function taskTime(task: any): number {
  return Date.parse(task?.event?.updatedAt || task?.event?.occurredAt || '') || 0;
}
function replayable(task: any, now: number): boolean {
  const age = now - taskTime(task);
  return task?.state === 'completed' && task.event?.status === 'completed' && age >= 0 && age < STEP_REPLAY_WINDOW_MS;
}
function knownNode(task: any, nodeId: unknown): boolean {
  const nodes = STEP_NODES[task?.stage as keyof typeof STEP_NODES] || [];
  return typeof nodeId === 'string' && nodes.includes(nodeId);
}
function nodesFor(task: any, now: number): string[] {
  const live = taskIsLive(task);
  if (!live && !replayable(task, now)) return [];
  const nodes = STEP_NODES[task.stage as keyof typeof STEP_NODES] || [];
  // Reconstruct only measured successful steps. Polls can miss very fast nodes.
  const current = live && nodes.includes(task.event?.live?.nodeId) ? task.event.live.nodeId : null;
  const known = nodes.filter((id) => nodeDuration(task, id) !== null || id === current);
  return known;
}

export function advanceStepStream(previous: StepStream, tasks: any[], online: boolean, now: number): StepStream {
  const batches = { ...previous.batches };
  const state: StepStream = { cards: [], batches, serial: previous.serial };
  // Unknown connectivity/status must never keep a processing glow alive.
  if (!online) return { ...state, cards: [] };
  const byId = new Map(tasks.map((task) => [task.key, task]));
  const remember = (card: StepCard) => {
    const old = batches[card.task.key];
    batches[card.task.key] = { serial: card.serial, touchedAt: now,
      nodes: [...new Set([...(old?.nodes || []), card.nodeId])] };
  };
  const nextCard = (task: any): StepCard | null => {
    const memory = batches[task.key];
    const nodeId = nodesFor(task, now).find((id) => !memory?.nodes.includes(id));
    if (!nodeId) return null;
    const serial = memory?.serial || ++state.serial;
    batches[task.key] = { nodes: memory?.nodes || [], serial, touchedAt: now };
    const durationMs = nodeDuration(task, nodeId);
    return { task, nodeId, serial, enteredAt: now, durationMs,
      phase: durationMs !== null ? 'complete' : 'processing',
      outcome: durationMs !== null ? 'complete' : 'changed', endAt: now + STEP_DISPLAY_MS };
  };
  for (const prior of previous.cards) {
    const source = byId.get(prior.task.key);
    let card = { ...prior, task: source || prior.task };
    // Older presentation state may contain a placeholder. Drop it immediately
    // so it neither animates out nor occupies a slot for a real observed step.
    if (!knownNode(card.task, card.nodeId)) continue;
    const live = source && taskIsLive(source);
    const completed = source?.state === 'completed' && source.event?.status === 'completed';
    if (card.phase === 'exit') {
      if (now >= card.endAt) {
        // A missing/unconfirmed snapshot did not complete this node. Allow
        // confirmed live work to resume in the same step after it returns.
        if (card.outcome !== 'stopped') remember(card);
        const next = source && nextCard(source);
        if (next) state.cards.push(next);
        continue;
      }
    } else if (!source || (!live && !completed)) {
      card = { ...card, phase: 'exit', outcome: 'stopped', endAt: now + STEP_EXIT_MS };
    } else {
      const durationMs = nodeDuration(source, card.nodeId);
      if (card.phase === 'processing') {
        if (durationMs !== null) {
          card = { ...card, durationMs, phase: 'complete', outcome: 'complete',
            endAt: Math.max(card.enteredAt + STEP_DISPLAY_MS, now + 450) };
        } else if (!live || source.event?.live?.nodeId !== card.nodeId) {
          // A changed node alone cannot supply an invented successful duration.
          // Missing telemetry also must not consume the node's dedup memory;
          // the same real node can become observable again on the next poll.
          card = { ...card, phase: 'exit',
            outcome: live && !knownNode(source, source.event?.live?.nodeId) ? 'stopped' : 'changed',
            endAt: now + STEP_EXIT_MS };
        }
      } else if (now >= card.endAt) {
        card = { ...card, phase: 'exit', endAt: now + STEP_EXIT_MS };
      }
    }
    state.cards.push(card);
  }
  // Keep cards in their current slots; new batches fill vacancies, not an
  // ever-growing playback queue. Concurrent batches may share the same node.
  const occupied = new Set(state.cards.map((card) => card.task.key));
  for (const task of tasks) {
    if (state.cards.length >= STEP_CARD_LIMIT) break;
    if (occupied.has(task.key)) continue;
    const card = nextCard(task);
    if (card) { state.cards.push(card); occupied.add(task.key); }
  }
  // At most 128 small dedup memories; in-flight slots are never evicted.
  const retained = Object.keys(batches).filter((key) => !occupied.has(key))
    .sort((a, b) => batches[b].touchedAt - batches[a].touchedAt);
  for (const key of retained.slice(Math.max(0, 128 - occupied.size))) delete batches[key];
  return state;
}
