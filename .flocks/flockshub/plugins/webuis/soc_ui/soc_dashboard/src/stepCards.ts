// A bounded presentation state machine. It never starts or cancels a workflow.
export const STEP_CARD_LIMIT = 6;
export const STEP_DISPLAY_MS = 2000;
export const STEP_BATCH_DISPLAY_MS = 3200;
export const STEP_EXIT_MS = 360;
export const STEP_REPLAY_WINDOW_MS = 30000;
export const STEP_NODES = {
  denoise: ['receive_alert', 'normalize', 'filter_logs', 'dedup_and_write'],
  triage: ['load_dedup_file', 'concurrent_triage', 'commit_cursor', 'summarize'],
};

export type StepCard = {
  task: any; nodeId: string; serial: number; enteredAt: number;
  // replay retains an observed step for presentation; it is not success evidence.
  phase: 'processing' | 'replay' | 'complete' | 'exit'; endAt: number;
  outcome: 'complete' | 'changed' | 'stopped' | 'yielded'; durationMs: number | null;
};
type BatchMemory = { nodes: string[]; serial: number; touchedAt: number; replaySelected?: boolean };
export type StepStream = { cards: StepCard[]; batches: Record<string, BatchMemory>; serial: number };
export const createStepStream = (): StepStream => ({ cards: [], batches: {}, serial: 0 });

function successful(task: any): boolean {
  return task?.state === 'completed' && task.event?.status === 'completed';
}
export function nodeDuration(task: any, nodeId: string): number | null {
  // A whole-batch duration is never used as an individual step's duration.
  const value = nodeId === 'batch'
    ? successful(task) ? task?.event?.result?.durationMs : null
    : task?.event?.live?.stepDurationsMs?.[nodeId];
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
  return successful(task) && age >= 0 && age < STEP_REPLAY_WINDOW_MS;
}
function knownNode(task: any, nodeId: unknown): boolean {
  const nodes = STEP_NODES[task?.stage as keyof typeof STEP_NODES] || [];
  return typeof nodeId === 'string' && nodes.includes(nodeId);
}
function nextNode(task: any, memory: BatchMemory | undefined, now: number): string | null {
  const live = taskIsLive(task);
  if (!live && !replayable(task, now) && !(successful(task) && memory?.replaySelected)) return null;
  const nodes = STEP_NODES[task?.stage as keyof typeof STEP_NODES];
  if (!nodes) return null;
  // Reconstruct only measured successful steps. Polls can miss very fast nodes.
  const current = live && knownNode(task, task.event?.live?.nodeId) ? task.event.live.nodeId : null;
  const measured = nodes.filter((id) => nodeDuration(task, id) !== null);
  const available = nodes.find((id) => !memory?.nodes.includes(id) && (measured.includes(id) || id === current));
  if (available) return available;
  // Missing step telemetry does not mean there is no real batch. Completed
  // batches with known steps finish their measured replay without an extra card.
  return (live || !measured.length) && !memory?.nodes.includes('batch') ? 'batch' : null;
}

export function advanceStepStream(previous: StepStream, tasks: any[], online: boolean, now: number): StepStream {
  const batches = { ...previous.batches };
  const state: StepStream = { cards: [], batches, serial: previous.serial };
  // Unknown connectivity/status must never keep a processing glow alive.
  if (!online) return { ...state, cards: [] };
  const byId = new Map(tasks.map((task) => [task.key, task]));
  const continuations: any[] = [];
  const remember = (card: StepCard) => {
    const old = batches[card.task.key];
    batches[card.task.key] = { ...old, serial: card.serial, touchedAt: now,
      nodes: [...new Set([...(old?.nodes || []), card.nodeId])] };
  };
  const nextCard = (task: any): StepCard | null => {
    const memory = batches[task.key];
    const nodeId = nextNode(task, memory, now);
    if (!nodeId) return null;
    const serial = memory?.serial || ++state.serial;
    batches[task.key] = { ...memory, nodes: memory?.nodes || [], serial, touchedAt: now,
      replaySelected: memory?.replaySelected || successful(task) };
    const durationMs = nodeDuration(task, nodeId);
    const complete = durationMs !== null || nodeId === 'batch' && successful(task);
    return { task, nodeId, serial, enteredAt: now, durationMs,
      phase: complete ? 'complete' : 'processing',
      outcome: complete ? 'complete' : 'changed', endAt: now + (nodeId === 'batch' ? STEP_BATCH_DISPLAY_MS : STEP_DISPLAY_MS) };
  };
  for (const prior of previous.cards) {
    const observed = byId.get(prior.task.key);
    // Already-selected successful replay owns a snapshot: normal bounded-cache
    // eviction cannot turn success into a stopped task or truncate its steps.
    // An explicit failure/unconfirmed observation always takes precedence.
    const source = observed || (successful(prior.task) ? prior.task : null);
    let card = { ...prior, task: source || prior.task };
    if (!STEP_NODES[card.task?.stage as keyof typeof STEP_NODES]
      || card.nodeId !== 'batch' && !knownNode(card.task, card.nodeId)) continue;
    const live = source && taskIsLive(source);
    const completed = successful(source);
    if (completed) {
      const memory = batches[card.task.key];
      if (memory) batches[card.task.key] = { ...memory, replaySelected: true };
    }
    if (card.phase === 'exit') {
      if (now >= card.endAt) {
        // Missing telemetry or a fair-share rotation does not finish a node.
        if (card.outcome === 'complete' || card.outcome === 'changed' && card.nodeId !== 'batch') remember(card);
        if (source) continuations.push(source);
        continue;
      }
    } else if (!source || (!live && !completed)) {
      card = { ...card, phase: 'exit', outcome: 'stopped', endAt: now + STEP_EXIT_MS };
    } else {
      const durationMs = nodeDuration(source, card.nodeId);
      const available = nextNode(source, batches[card.task.key], now);
      const minimumEndAt = card.enteredAt + (card.nodeId === 'batch' ? STEP_BATCH_DISPLAY_MS : STEP_DISPLAY_MS);
      if ((card.phase === 'processing' || card.phase === 'replay')
        && (durationMs !== null || card.nodeId === 'batch' && completed)) {
        card = { ...card, durationMs, phase: 'complete', outcome: 'complete',
          endAt: Math.max(minimumEndAt, now + 450) };
      } else if (card.phase === 'processing') {
        const batchHasSteps = card.nodeId === 'batch' && available && available !== 'batch';
        const stepChanged = card.nodeId !== 'batch' && (!live || source.event?.live?.nodeId !== card.nodeId);
        if (stepChanged && live && !knownNode(source, source.event?.live?.nodeId)) {
          card = { ...card, phase: 'exit', outcome: 'stopped', endAt: now + STEP_EXIT_MS };
        } else if (batchHasSteps || stepChanged) {
          // A fast normal transition must not flash the observed card away.
          // Keep its presentation, without inventing completion or elapsed time.
          card = { ...card, phase: 'replay', outcome: 'changed', endAt: minimumEndAt };
        }
      }
      if ((card.phase === 'complete' || card.phase === 'replay') && now >= card.endAt) {
        card = { ...card, phase: 'exit', endAt: now + STEP_EXIT_MS };
      }
    }
    state.cards.push(card);
  }
  const candidates = [...continuations, ...tasks];
  const availableStages = new Set([
    ...state.cards.map((card) => card.task.stage),
    ...candidates.filter((task) => nextNode(task, batches[task.key], now)).map((task) => task.stage),
  ]);
  const stageLimit = availableStages.size > 1 ? STEP_CARD_LIMIT / 2 : STEP_CARD_LIMIT;
  const keptByStage: Record<string, number> = {};
  state.cards = state.cards.map((card) => {
    const stage = card.task.stage;
    keptByStage[stage] = (keptByStage[stage] || 0) + 1;
    if (keptByStage[stage] > stageLimit && card.phase !== 'exit') {
      return { ...card, phase: 'exit', outcome: 'yielded', endAt: now + STEP_EXIT_MS };
    }
    return card;
  });
  const occupied = new Set(state.cards.map((card) => card.task.key));
  const counts = state.cards.reduce((all, card) => {
    all[card.task.stage] = (all[card.task.stage] || 0) + 1; return all;
  }, {} as Record<string, number>);
  for (const task of candidates) {
    if (state.cards.length >= STEP_CARD_LIMIT) break;
    if (occupied.has(task.key) || (counts[task.stage] || 0) >= stageLimit) continue;
    const card = nextCard(task);
    if (card) {
      state.cards.push(card); occupied.add(task.key);
      counts[task.stage] = (counts[task.stage] || 0) + 1;
    }
  }
  // At most 128 small dedup memories; in-flight slots are never evicted.
  const retained = Object.keys(batches).filter((key) => !occupied.has(key))
    .sort((a, b) => batches[b].touchedAt - batches[a].touchedAt);
  for (const key of retained.slice(Math.max(0, 128 - occupied.size))) delete batches[key];
  return state;
}
