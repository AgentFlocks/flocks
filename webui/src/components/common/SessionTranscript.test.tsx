import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SessionTranscript from './SessionTranscript';

const timeline = vi.hoisted(() => vi.fn());
vi.mock('./SessionChat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./SessionChat')>();
  return { ...actual, ChatMessageTimeline: (props: unknown) => { timeline(props); return <div>timeline</div>; } };
});

describe('session transcript', () => {
  it('preserves thinking, tool timing and errors and merges consecutive assistant messages read-only', () => {
    render(<SessionTranscript running messages={[
      { id: 'a', role: 'assistant', parts: [{ id: 'r', type: 'reasoning', text: 'Check inputs', time: { start: 1, end: 2 } }] },
      { id: 'b', role: 'assistant', parts: [{ id: 't', type: 'tool', tool: 'read', status: 'error', error: 'File missing', input: { path: 'app.py' }, time: { start: 2, end: 5 } }] },
    ]} />);
    expect(screen.getByText('timeline')).toBeInTheDocument();
    const props = timeline.mock.lastCall![0];
    expect(props.items).toHaveLength(1);
    expect(props.items[0].isActive).toBe(true);
    expect(props.items[0].message.parts).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'r', type: 'reasoning', text: 'Check inputs', time: { start: 1, end: 2 } }),
      expect.objectContaining({ id: 't', state: expect.objectContaining({ status: 'error', error: 'File missing', time: { start: 2, end: 5 } }) }),
    ]));
    expect(props.collapseIntermediateSteps).toBe(true);
    expect(props.showActions).toBe(false);
    expect(props.onQuestionAnswer).toBeUndefined();
    expect(props.onRegenerate).toBeUndefined();
    act(() => props.onProcessGroupOpenChange('steps', true));
    expect(timeline.mock.lastCall![0].processGroupOpenState.steps).toBe(true);
    act(() => timeline.mock.lastCall![0].onProcessGroupOpenChange('steps', false));
    expect(timeline.mock.lastCall![0].processGroupOpenState.steps).toBe(false);
  });

  it('draws full chat messages by default and only their bodies inline', () => {
    const messages = [{ id: 'a', role: 'assistant' as const, parts: [{ id: 't', type: 'text' as const, text: 'Done' }] }];
    const { container, rerender } = render(<SessionTranscript messages={messages} />);
    expect(timeline.mock.lastCall![0]).toEqual(expect.objectContaining({ compact: false, bare: false }));
    expect(container.firstElementChild).toHaveClass('pl-10');

    rerender(<SessionTranscript messages={messages} variant="inline" />);
    expect(timeline.mock.lastCall![0]).toEqual(expect.objectContaining({ compact: true, bare: true, collapseIntermediateSteps: true }));
    expect(container.firstElementChild).not.toHaveClass('pl-10');
  });
});
