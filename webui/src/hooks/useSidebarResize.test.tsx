import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSidebarResize } from './useSidebarResize';

function Harness({ enabled = true }: { enabled?: boolean }) {
  const resize = useSidebarResize(enabled);
  return <div role="separator" tabIndex={0} style={{ width: resize.width }} aria-valuemax={resize.maximum} {...resize.handleProps} />;
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('innerWidth', 1440);
  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  });
});

afterEach(() => { vi.unstubAllGlobals(); });

function setup() {
  const view = render(<Harness />);
  const handle = screen.getByRole('separator');
  Object.assign(handle, { setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn() });
  return { ...view, handle };
}

function start(handle: HTMLElement, x = 192) {
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: x });
}

function move(handle: HTMLElement, x: number) {
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: x });
}

describe('useSidebarResize', () => {
  it('drags from the visible width and restores the saved size after remount', () => {
    const { handle, unmount } = setup();
    expect(handle.style.width).toBe('192px');
    handle.focus();
    start(handle);
    expect(handle).not.toHaveFocus();
    move(handle, 310);
    expect(handle.style.width).toBe('310px');
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(handle).not.toHaveFocus();
    move(handle, 400);
    expect(handle.style.width).toBe('310px');
    expect(document.body.style.cursor).toBe('');
    unmount();
    render(<Harness />);
    expect(screen.getByRole('separator').style.width).toBe('310px');
  });

  it('limits the width, supports keyboard adjustment and resets to the default', () => {
    const { handle } = setup();
    start(handle);
    move(handle, 1200);
    expect(handle.style.width).toBe('480px');
    move(handle, -100);
    expect(handle.style.width).toBe('192px');
    fireEvent.pointerUp(handle, { pointerId: 1 });
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(handle.style.width).toBe('208px');
    fireEvent.keyDown(handle, { key: 'End' });
    expect(handle.style.width).toBe('480px');
    fireEvent.doubleClick(handle);
    expect(handle.style.width).toBe('192px');
    fireEvent.keyDown(handle, { key: 'End' });
    fireEvent.keyDown(handle, { key: 'Enter' });
    expect(handle.style.width).toBe('192px');
  });

  it.each(['pointerCancel', 'lostPointerCapture', 'blur', 'disable'])('cleans up an interrupted drag: %s', (reason) => {
    const { handle, rerender } = setup();
    start(handle);
    expect(document.body.style.userSelect).toBe('none');
    if (reason === 'blur') fireEvent.blur(window);
    else if (reason === 'disable') rerender(<Harness enabled={false} />);
    else if (reason === 'pointerCancel') fireEvent.pointerCancel(handle, { pointerId: 1 });
    else fireEvent.lostPointerCapture(handle, { pointerId: 1 });
    move(handle, 400);
    expect(handle.style.width).toBe('192px');
    expect(document.body.style.cursor).toBe('');
    expect(document.body.style.userSelect).toBe('');
  });

  it('fits a narrower window without losing the preferred width', () => {
    localStorage.setItem('flocks_layout_sidebar_width', '480');
    const { handle } = setup();
    act(() => { vi.stubGlobal('innerWidth', 1000); window.dispatchEvent(new Event('resize')); });
    expect(handle.style.width).toBe('400px');
    expect(handle).toHaveAttribute('aria-valuemax', '400');
    act(() => { vi.stubGlobal('innerWidth', 1440); window.dispatchEvent(new Event('resize')); });
    expect(handle.style.width).toBe('480px');
  });
});
