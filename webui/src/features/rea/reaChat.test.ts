import { describe, expect, it } from 'vitest';
import type { Message, MessagePart } from '@/types';
import { findLatestReaCardPartId, isReaCardPart, reaToolAction } from './reaChat';

function part(
  id: string,
  action: string,
  status: 'pending' | 'running' | 'completed' | 'error',
  { tool = 'rea_analysis', state }: { tool?: string; state?: string } = {},
): MessagePart {
  return {
    id,
    type: 'tool',
    tool,
    state: {
      status,
      input: { action },
      ...(state ? { metadata: { rea: { action, state } } } : {}),
    },
  } as MessagePart;
}

describe('reaToolAction', () => {
  it('only knows the actions of rea_analysis', () => {
    expect(reaToolAction(part('a', 'install', 'running'))).toBe('install');
    expect(reaToolAction(part('b', 'report', 'completed'))).toBe('report');
    expect(reaToolAction(part('c', 'verify', 'completed'))).toBeNull();
    expect(reaToolAction(part('d', 'status', 'completed', { tool: 'bash' }))).toBeNull();
  });
});

describe('isReaCardPart', () => {
  it('draws a status card only when REA was not usable at the time of the call', () => {
    for (const state of ['not_installed', 'install_failed', 'installing', 'validating', 'degraded', 'uninstalling', 'uninstall_failed']) {
      expect(isReaCardPart(part(state, 'status', 'completed', { state }))).toBe(true);
    }
    // Ready: an ordinary tool step, Rex just continues.
    expect(isReaCardPart(part('d', 'status', 'completed', { state: 'ready' }))).toBe(false);
    expect(isReaCardPart(part('e', 'status', 'running'))).toBe(false);
    expect(isReaCardPart(part('f', 'status', 'error'))).toBe(false);
  });

  it('draws every install call and finished reports', () => {
    expect(isReaCardPart(part('a', 'install', 'running'))).toBe(true);
    expect(isReaCardPart(part('b', 'install', 'error'))).toBe(true);
    expect(isReaCardPart(part('c', 'report', 'running'))).toBe(false);
    expect(isReaCardPart(part('d', 'report', 'completed'))).toBe(true);
  });
});

describe('findLatestReaCardPartId', () => {
  it('returns the newest status or install card', () => {
    const messages = [
      { id: 'm1', role: 'assistant', parts: [part('s1', 'status', 'completed', { state: 'not_installed' })] },
      { id: 'm2', role: 'assistant', parts: [part('i1', 'install', 'error'), part('r1', 'report', 'completed')] },
      { id: 'm3', role: 'assistant', parts: [part('s2', 'status', 'completed', { state: 'ready' })] },
    ] as unknown as Message[];
    expect(findLatestReaCardPartId(messages)).toBe('i1');
    expect(findLatestReaCardPartId([])).toBeNull();
  });
});
