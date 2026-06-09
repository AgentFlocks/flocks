import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useReasoningToggle } from './useReasoningToggle';

describe('useReasoningToggle', () => {
  it('keeps active reasoning expanded by default for normal chat', () => {
    const { result } = renderHook(() => useReasoningToggle([
      { id: 'reason-1', type: 'reasoning', text: 'thinking' },
    ]));

    expect(result.current.getPartExpanded('reason-1')).toBe(true);
  });

  it('can default-collapse active reasoning for embedded workflow panels and expand on click', () => {
    const { result } = renderHook(() => useReasoningToggle(
      [{ id: 'reason-1', type: 'reasoning', text: 'thinking' }],
      undefined,
      { expandWhileActive: false },
    ));

    expect(result.current.getPartExpanded('reason-1')).toBe(false);

    act(() => {
      result.current.togglePart('reason-1');
    });

    expect(result.current.getPartExpanded('reason-1')).toBe(true);
  });

  it('keeps completed reasoning collapsed by default but expandable', () => {
    const { result } = renderHook(() => useReasoningToggle([
      { id: 'reason-1', type: 'reasoning', text: 'thinking' },
      { id: 'text-1', type: 'text', text: 'answer' },
    ]));

    expect(result.current.getPartExpanded('reason-1')).toBe(false);

    act(() => {
      result.current.togglePart('reason-1');
    });

    expect(result.current.getPartExpanded('reason-1')).toBe(true);
  });
});
