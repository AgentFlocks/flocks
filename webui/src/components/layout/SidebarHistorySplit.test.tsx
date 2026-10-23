import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SidebarHistorySplit from './SidebarHistorySplit';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

let availableHeight = 800;
let contentHeight = 240;
let notifyResize: () => void;

beforeEach(() => {
  availableHeight = 800;
  contentHeight = 240;
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => availableHeight);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const requested = this.style.height ? Number.parseFloat(this.style.height) : contentHeight;
    return { height: Math.min(requested, availableHeight / 2 - 8) } as DOMRect;
  });
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { notifyResize = callback; }
    observe() {}
    disconnect() {}
  });
  vi.stubGlobal('PointerEvent', class extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  });
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup() {
  const props = { enabled: true, navigation: <nav>Navigation</nav>, history: <section>History</section> };
  const view = render(<SidebarHistorySplit {...props} />);
  const handle = screen.getByRole('separator');
  Object.assign(handle, { setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn() });
  const navigation = screen.getByRole('navigation').parentElement!;
  return { ...view, props, handle, navigation };
}

function drag(handle: HTMLElement, start: number, end: number) {
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientY: start });
  fireEvent.pointerMove(handle, { buttons: 1, pointerId: 1, clientY: end });
  fireEvent.pointerUp(handle, { pointerId: 1, clientY: end });
  act(() => notifyResize());
}

describe('SidebarHistorySplit', () => {
  it('automatically follows content height and caps navigation at half the shared region', () => {
    const { handle, navigation } = setup();
    expect(navigation.style.height).toBe('');
    expect(handle).toHaveAttribute('aria-valuenow', '240');
    contentHeight = 500;
    act(() => notifyResize());
    expect(navigation.style.height).toBe('');
    expect(navigation.style.maxHeight).toBe('calc(50% - 8px)');
    expect(handle).toHaveAttribute('aria-valuenow', '392');
    expect(handle).toHaveAttribute('aria-valuemax', '392');
  });

  it('resizes by pointer movement and keeps at least half for history', () => {
    const { handle, navigation } = setup();
    handle.focus();
    drag(handle, 250, 310);
    expect(handle).not.toHaveFocus();
    expect(navigation.style.height).toBe('300px');
    drag(handle, 310, 1500);
    expect(navigation.style.height).toBe('392px');
    drag(handle, 400, -500);
    expect(navigation.style.height).toBe('64px');
  });

  it('clamps to a smaller window and starts the next drag from the visible divider', () => {
    const { handle, navigation } = setup();
    drag(handle, 250, 900);
    availableHeight = 400;
    act(() => notifyResize());
    expect(handle).toHaveAttribute('aria-valuemax', '192');
    expect(handle).toHaveAttribute('aria-valuenow', '192');
    drag(handle, 200, 180);
    expect(navigation.style.height).toBe('172px');
  });

  it.each(['window blur', 'missed pointerup'])('ends a drag on %s and allows a new drag', (interruption) => {
    const { handle, navigation } = setup();
    fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientY: 250 });
    fireEvent.pointerMove(handle, { buttons: 1, pointerId: 1, clientY: 310 });
    expect(navigation.style.height).toBe('300px');
    expect(navigation.parentElement).toHaveClass('cursor-row-resize', 'select-none');

    if (interruption === 'window blur') fireEvent.blur(window);
    else fireEvent.pointerMove(handle, { buttons: 0, pointerId: 1, clientY: 400 });

    expect(navigation.parentElement).not.toHaveClass('cursor-row-resize');
    expect(navigation.parentElement).not.toHaveClass('select-none');
    expect(handle.releasePointerCapture).toHaveBeenCalledWith(1);
    fireEvent.pointerMove(handle, { buttons: 1, pointerId: 1, clientY: 400 });
    expect(navigation.style.height).toBe('300px');

    drag(handle, 310, 280);
    expect(navigation.style.height).toBe('270px');
  });

  it('supports keyboard adjustment and restores content sizing on double-click or Enter', () => {
    const { handle, navigation } = setup();
    fireEvent.keyDown(handle, { key: 'ArrowUp' });
    expect(navigation.style.height).toBe('224px');
    fireEvent.keyDown(handle, { key: 'End' });
    expect(navigation.style.height).toBe('392px');
    fireEvent.doubleClick(handle);
    expect(navigation.style.height).toBe('');
    fireEvent.keyDown(handle, { key: 'ArrowDown' });
    expect(navigation.style.height).toBe('256px');
    fireEvent.keyDown(handle, { key: 'Enter' });
    expect(navigation.style.height).toBe('');
  });

  it('ends cancelled drags and removes the splitter outside expanded workbench navigation', () => {
    const { handle, navigation, rerender, props } = setup();
    fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientY: 250 });
    fireEvent.pointerCancel(handle, { pointerId: 1 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientY: 400 });
    expect(navigation.style.height).toBe('');
    rerender(<SidebarHistorySplit {...props} enabled={false} />);
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
    expect(navigation.style.maxHeight).toBe('');
  });
});
