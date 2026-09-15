import { useRef, type ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SessionComposerMenu, {
  SessionComposerMenuHeader,
  SessionModeOption,
  type SessionComposerMenuProps,
} from './SessionComposerMenu';

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return { left, top, right: left + width, bottom: top + height, width, height, x: left, y: top, toJSON: () => ({}) };
}

let anchorRect: DOMRect;
let contentHeight: number;
let headerHeight: number;
let footerHeight: number;
let frames: Map<number, FrameRequestCallback>;
let observers: TestResizeObserver[];
let anchorMeasurements: ReturnType<typeof vi.fn>;

class TestResizeObserver implements ResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  constructor(private callback: ResizeObserverCallback) {
    observers.push(this);
  }
  trigger() {
    this.callback([], this);
  }
}

function setViewport(width: number, height: number) {
  vi.stubGlobal('innerWidth', width);
  vi.stubGlobal('innerHeight', height);
}

function flushFrame() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback(0));
  });
}

function getMenu() {
  return screen.getByRole('menu', { name: 'Session settings' });
}

// jsdom has no layout engine. Model the root's actual constrained border box,
// while its inner content keeps its full height even when the body must scroll.
function menuRect(menu: HTMLElement) {
  const naturalHeight = contentHeight
    + (menu.querySelector('[data-testid="fixed-header"]') ? headerHeight : 0)
    + (menu.querySelector('[data-testid="fixed-footer"]') ? footerHeight : 0)
    + 2;
  const height = Math.max(2, Math.min(naturalHeight, Number.parseFloat(menu.style.maxHeight)));
  const top = menu.style.top && menu.style.top !== 'auto'
    ? Number.parseFloat(menu.style.top)
    : window.innerHeight - Number.parseFloat(menu.style.bottom) - height;
  return rect(Number.parseFloat(menu.style.left), top, Number.parseFloat(menu.style.width), height);
}

function expectWithinViewport(menu: HTMLElement, padding = 16) {
  const bounds = menuRect(menu);
  expect(bounds.left).toBeGreaterThanOrEqual(padding);
  expect(bounds.right).toBeLessThanOrEqual(window.innerWidth - padding);
  expect(bounds.top).toBeGreaterThanOrEqual(padding);
  expect(bounds.bottom).toBeLessThanOrEqual(window.innerHeight - padding);
}

type FixtureProps = Omit<SessionComposerMenuProps, 'anchorRef' | 'children'> & { children?: ReactNode };

function MenuFixture({ children, ...props }: FixtureProps) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  return (
    <div data-testid="ancestor" style={{ overflow: 'hidden', transform: 'translateZ(0)' }}>
      <button ref={anchorRef} data-testid="anchor">Open</button>
      <SessionComposerMenu anchorRef={anchorRef} role="menu" aria-label="Session settings" {...props}>
        <div data-testid="menu-content">{children ?? <button type="button">Choose mode</button>}</div>
      </SessionComposerMenu>
    </div>
  );
}

beforeEach(() => {
  setViewport(1200, 800);
  anchorRect = rect(200, 650, 112, 28);
  contentHeight = 200;
  headerHeight = 36;
  footerHeight = 40;
  frames = new Map();
  observers = [];
  anchorMeasurements = vi.fn();
  let nextFrame = 0;
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    const id = ++nextFrame;
    frames.set(id, callback);
    return id;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => { frames.delete(id); });
  vi.stubGlobal('ResizeObserver', TestResizeObserver);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.dataset.testid === 'anchor') {
      anchorMeasurements();
      return anchorRect;
    }
    if (this.firstElementChild?.getAttribute('data-testid') === 'menu-content') {
      return rect(0, 0, 500, contentHeight);
    }
    if (this.firstElementChild?.getAttribute('data-testid') === 'fixed-header') {
      return rect(0, 0, 500, headerHeight);
    }
    if (this.firstElementChild?.getAttribute('data-testid') === 'fixed-footer') {
      return rect(0, 0, 500, footerHeight);
    }
    if (this.getAttribute('role') === 'menu') return menuRect(this);
    return rect(0, 0, 0, 0);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('SessionModeOption', () => {
  it.each([
    ['自动允许全部', '未命中黑白名单时自动放行；Plugins 与代码目录固定 readonly。'],
    ['Require confirmation before execution', 'Ask for approval before changing files or accessing the network.'],
    ['/workspace/plugins/extremely-long-unbroken-model-name', '/workspace/plugins/custom/security/policy/configuration.json'],
  ])('keeps the complete label and explanation for %s in a native title', (label, description) => {
    render(<SessionModeOption selected={false} label={label} description={description} />);
    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('title', `${label}\n${description}`);
    expect(screen.getByText(label, { exact: true })).toHaveTextContent(label);
    expect(screen.getByText(description, { exact: true })).toHaveTextContent(description);
    expect(screen.getByText(label, { exact: true })).toHaveClass('truncate', 'whitespace-nowrap', 'min-w-0');
    expect(screen.getByText(description, { exact: true })).toHaveClass('truncate', 'whitespace-nowrap', 'min-w-0', 'text-[12px]', 'leading-[18px]');
    expect(button.querySelector('[title]')).toBeNull();
  });

  it('uses one row with an inline icon, a 112px title, flexible description and a reserved check column', () => {
    const { rerender } = render(
      <SessionModeOption selected={false} label="开发模式" description="Plugins 与代码目录按当前会话权限模式处理" icon={<svg data-testid="mode-icon" />} />,
    );
    const button = screen.getByRole('button');
    const [title, description, check] = [...button.children];
    expect(button).toHaveClass('grid', 'grid-cols-[minmax(0,112px)_minmax(0,1fr)_16px]', 'items-center');
    expect(button).not.toHaveClass('flex-col', 'flex-wrap');
    expect(button.children).toHaveLength(3);
    expect(title).toContainElement(screen.getByText('开发模式'));
    expect(title).toContainElement(screen.getByTestId('mode-icon'));
    expect(title).toHaveClass('flex', 'min-w-0', 'whitespace-nowrap', 'text-sm', 'leading-[14px]');
    expect(description).toHaveClass('min-w-0', 'truncate', 'whitespace-nowrap');
    expect(check).toHaveClass('h-4', 'w-4', 'invisible');
    expect(check).toHaveAttribute('aria-hidden', 'true');
    expect(check).not.toHaveClass('hidden');

    rerender(<SessionModeOption selected label="开发模式" description="Plugins 与代码目录按当前会话权限模式处理" />);
    expect(button.children).toHaveLength(3);
    expect(button.lastElementChild).toBe(check);
    expect(check).not.toHaveClass('invisible');
    expect(button).toHaveClass('border-blue-300', 'bg-blue-50', 'dark:bg-blue-500/15');
  });

  it('forwards radio-menu attributes and button behavior without imposing a role', () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <SessionModeOption selected label="Auto" description="Automatically choose" role="menuitemradio" aria-checked onClick={onClick} className="custom-option" />,
    );
    const option = screen.getByRole('menuitemradio');
    expect(option).toHaveAttribute('aria-checked', 'true');
    expect(option).toHaveAttribute('type', 'button');
    expect(option).toHaveClass('custom-option', 'focus-visible:ring-2', 'focus-visible:ring-blue-500');
    fireEvent.click(option);
    expect(onClick).toHaveBeenCalledOnce();

    rerender(<SessionModeOption selected={false} label="Auto" description="Automatically choose" disabled onClick={onClick} type="submit" />);
    const button = screen.getByRole('button');
    expect(button).not.toHaveAttribute('role');
    expect(button).not.toHaveAttribute('aria-checked');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('type', 'submit');
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });
});

describe('SessionComposerMenuHeader', () => {
  it.each([
    ['执行模式', '为当前对话选择默认执行方式'],
    ['Execution mode', 'Choose how this session should execute'],
    ['/workspace/unbroken-header-path', '/workspace/plugins/security/configuration.json'],
  ])('keeps %s and its hint in aligned, single-line columns with full titles', (title, hint) => {
    render(<SessionComposerMenuHeader title={title} hint={hint} />);
    const titleElement = screen.getByText(title);
    const hintElement = screen.getByText(hint);
    expect(titleElement).toHaveAttribute('title', title);
    expect(hintElement).toHaveAttribute('title', hint);
    expect(titleElement).toHaveClass('min-w-0', 'truncate', 'whitespace-nowrap');
    expect(hintElement).toHaveClass('min-w-0', 'truncate', 'whitespace-nowrap');
    expect(titleElement.parentElement).toBe(hintElement.parentElement);
    expect(titleElement.parentElement).toHaveClass('grid', 'grid-cols-[minmax(0,112px)_minmax(0,1fr)]', 'border-b', 'px-4');
    expect(titleElement.parentElement).not.toHaveClass('flex-col');
  });
});

describe('SessionComposerMenu', () => {
  it.each(['data-execution-mode-selector', 'data-model-selector', 'data-permission-mode-selector'])(
    'portals the fixed root to body and preserves %s for native closest handlers',
    (marker) => {
      const onClick = vi.fn();
      const nativeClick = vi.fn((event: Event) => (event.target as Element).closest(`[${marker}]`));
      document.addEventListener('click', nativeClick);
      try {
        const { container } = render(<MenuFixture {...{ [marker]: true }} id="composer-menu" className="custom-menu" style={{ color: 'red' }} onClick={onClick} />);
        const menu = getMenu();
        expect(menu.parentElement).toBe(document.body);
        expect(container).not.toContainElement(menu);
        expect(menu).toHaveAttribute(marker, 'true');
        expect(menu).toHaveAttribute('id', 'composer-menu');
        expect(menu).toHaveStyle({ position: 'fixed', color: 'rgb(255, 0, 0)', visibility: 'visible' });
        expect(menu).toHaveClass('custom-menu', 'z-50', 'flex', 'flex-col', 'overflow-hidden', 'rounded-lg', 'border-zinc-200', 'dark:border-zinc-800', 'bg-white', 'dark:bg-zinc-900');
        fireEvent.click(screen.getByRole('button', { name: 'Choose mode' }));
        expect(onClick).toHaveBeenCalledOnce();
        expect(nativeClick).toHaveReturnedWith(menu);
      } finally {
        document.removeEventListener('click', nativeClick);
      }
    },
  );

  it('places a 520px-wide menu 8px above its anchor by default', () => {
    render(<MenuFixture />);
    const menu = getMenu();
    expect(menu).toHaveStyle({ width: '520px', left: '200px', maxHeight: '480px' });
    expect(menuRect(menu).bottom).toBe(anchorRect.top - 8);
    expectWithinViewport(menu);
  });

  it.each([
    [320, 200, 288, 16],
    [1200, -100, 520, 16],
    [1200, 1100, 520, 664],
  ])('clamps width and horizontal placement in a %ipx viewport with anchor left %i', (viewportWidth, anchorLeft, width, left) => {
    setViewport(viewportWidth, 800);
    anchorRect = rect(anchorLeft, 650, 100, 28);
    render(<MenuFixture />);
    const menu = getMenu();
    expect(menu).toHaveStyle({ width: `${width}px`, left: `${left}px` });
    expectWithinViewport(menu);
  });

  it('flips below only when above is insufficient and below offers more room', () => {
    anchorRect = rect(200, 120, 112, 28);
    contentHeight = 300;
    render(<MenuFixture />);
    const menu = getMenu();
    expect(menu).toHaveStyle({ top: '156px', bottom: 'auto', maxHeight: '480px' });
    expect(menuRect(menu).top).toBe(anchorRect.bottom + 8);
    expectWithinViewport(menu);
  });

  it('stays above if the content fits, even when there is more space below', () => {
    anchorRect = rect(200, 200, 112, 28);
    contentHeight = 100;
    render(<MenuFixture />);
    const menu = getMenu();
    expect(menuRect(menu).bottom).toBe(192);
    expect(menu).toHaveStyle({ maxHeight: '176px' });
    expectWithinViewport(menu);
  });

  it('limits a tall menu to the larger available side of a short viewport', () => {
    setViewport(320, 240);
    anchorRect = rect(200, 126, 100, 28);
    contentHeight = 1000;
    render(<MenuFixture />);
    const menu = getMenu();
    expect(menu).toHaveStyle({ width: '288px', left: '16px', maxHeight: '102px' });
    expect(menuRect(menu).bottom).toBe(118);
    expectWithinViewport(menu);
  });

  it('keeps a tie above and caps the popup to its available height', () => {
    setViewport(800, 400);
    anchorRect = rect(100, 186, 112, 28);
    contentHeight = 800;
    render(<MenuFixture />);
    expect(getMenu()).toHaveStyle({ maxHeight: '162px' });
    expect(menuRect(getMenu()).bottom).toBe(178);
    expectWithinViewport(getMenu());
  });

  it.each([-200, 900])('clamps an offscreen vertical anchor at %ipx to the viewport', (top) => {
    anchorRect = rect(200, top, 112, 28);
    contentHeight = 1000;
    render(<MenuFixture />);
    expectWithinViewport(getMenu());
  });

  it('does not overflow even when the viewport is smaller than its normal padding', () => {
    setViewport(24, 24);
    anchorRect = rect(0, 12, 20, 10);
    render(<MenuFixture />);
    expect(getMenu()).toHaveStyle({ width: '0px', maxHeight: '0px' });
    expectWithinViewport(getMenu(), 0);
  });

  it('keeps header and footer outside one scrolling body and includes them in placement', () => {
    setViewport(800, 600);
    anchorRect = rect(200, 160, 112, 28);
    contentHeight = 80;
    render(
      <MenuFixture
        header={<div data-testid="fixed-header">Models</div>}
        footer={<div data-testid="fixed-footer"><button>Add model</button></div>}
        contentClassName="p-1.5 space-y-0.5"
      >
        <div className="sticky top-0">Provider group</div>
      </MenuFixture>,
    );
    const menu = getMenu();
    const body = menu.querySelector('.overflow-y-auto');
    const header = screen.getByTestId('fixed-header').parentElement;
    const footer = screen.getByTestId('fixed-footer').parentElement;
    const content = screen.getByTestId('menu-content').parentElement;
    expect(menu.querySelectorAll('.overflow-y-auto')).toHaveLength(1);
    expect(body).toHaveClass('min-h-0', 'overflow-x-hidden');
    expect(body).toHaveStyle({ scrollbarGutter: 'stable' });
    expect(body).toContainElement(content);
    expect(content).toHaveClass('p-1.5', 'space-y-0.5');
    expect(header).toHaveClass('shrink-0');
    expect(footer).toHaveClass('shrink-0');
    expect(header?.parentElement).toBe(menu);
    expect(footer?.parentElement).toBe(menu);
    expect(body).not.toContainElement(header);
    expect(body).not.toContainElement(footer);
    expect(body).toContainElement(screen.getByText('Provider group'));
    expect(screen.getByText('Provider group')).toHaveClass('sticky', 'top-0');
    expect(menu).toHaveStyle({ top: '196px' }); // 80px body fits above, but header + footer do not.
    expectWithinViewport(menu);
    for (const element of [header, footer, content, menu, screen.getByTestId('anchor')]) {
      expect(observers[0].observe).toHaveBeenCalledWith(element);
    }
  });

  it('remeasures resize using full content, not the previously clipped popup height', () => {
    setViewport(800, 300);
    anchorRect = rect(200, 180, 112, 28);
    contentHeight = 500;
    render(<MenuFixture />);
    const menu = getMenu();
    expect(menu).toHaveStyle({ maxHeight: '156px' });
    expect(menuRect(menu).bottom).toBe(172);

    setViewport(320, 600);
    anchorRect = rect(220, 220, 80, 28);
    fireEvent.resize(window);
    flushFrame();
    // The old rendered height (156) fits above (196), but the full content does not.
    expect(menu).toHaveStyle({ width: '288px', left: '16px', top: '256px', maxHeight: '328px' });
    expectWithinViewport(menu);
    for (let i = 0; i < 4; i += 1) {
      observers[0].trigger();
      flushFrame();
      expect(menu).toHaveStyle({ top: '256px', maxHeight: '328px' });
      expect(frames.size).toBe(0);
    }
  });

  it('follows relevant non-bubbling ancestor/document scroll but ignores body and unrelated scrolling', () => {
    const { container } = render(<MenuFixture />);
    const ancestor = screen.getByTestId('ancestor');
    anchorRect = rect(500, 600, 112, 28);
    fireEvent.scroll(ancestor, { bubbles: false });
    flushFrame();
    expect(getMenu()).toHaveStyle({ left: '500px' });
    expect(menuRect(getMenu()).bottom).toBe(592);

    anchorMeasurements.mockClear();
    fireEvent.scroll(getMenu().querySelector('.overflow-y-auto')!, { bubbles: false });
    const unrelated = document.createElement('div');
    container.appendChild(unrelated);
    fireEvent.scroll(unrelated, { bubbles: false });
    expect(frames.size).toBe(0);
    expect(anchorMeasurements).not.toHaveBeenCalled();

    anchorRect = rect(400, 500, 112, 28);
    fireEvent.scroll(document, { bubbles: false });
    flushFrame();
    expect(getMenu()).toHaveStyle({ left: '400px' });
    expect(menuRect(getMenu()).bottom).toBe(492);
  });

  it('coalesces geometry events and responds to observed anchor/content changes', () => {
    anchorRect = rect(200, 220, 112, 28);
    contentHeight = 100;
    render(<MenuFixture />);
    expect(menuRect(getMenu()).bottom).toBe(212);
    expect(observers[0].observe).toHaveBeenCalledWith(screen.getByTestId('ancestor'));
    anchorMeasurements.mockClear();

    contentHeight = 700;
    anchorRect = rect(400, 220, 150, 32);
    observers[0].trigger();
    observers[0].trigger();
    fireEvent.resize(window);
    fireEvent.scroll(screen.getByTestId('ancestor'));
    expect(frames.size).toBe(1);
    expect(anchorMeasurements).not.toHaveBeenCalled();
    flushFrame();
    expect(anchorMeasurements).toHaveBeenCalledOnce();
    expect(getMenu()).toHaveStyle({ left: '400px', top: '260px', maxHeight: '480px' });
  });

  it('remeasures on parent content/header/footer updates and observes new fixed slots', () => {
    anchorRect = rect(200, 220, 112, 28);
    contentHeight = 100;
    const { rerender } = render(<MenuFixture />);
    const firstObserver = observers[0];
    expect(menuRect(getMenu()).bottom).toBe(212);
    contentHeight = 300;
    rerender(<MenuFixture header={<div data-testid="fixed-header">Models</div>} footer={<div data-testid="fixed-footer">Add model</div>} />);
    expect(getMenu()).toHaveStyle({ top: '256px' });
    expect(firstObserver.disconnect).toHaveBeenCalledOnce();
    expect(observers[1].observe).toHaveBeenCalledWith(screen.getByTestId('fixed-header').parentElement);
    expect(observers[1].observe).toHaveBeenCalledWith(screen.getByTestId('fixed-footer').parentElement);
  });

  it('removes the portal, listeners, observer and pending animation frame on unmount', () => {
    const addListener = vi.spyOn(window, 'addEventListener');
    const removeListener = vi.spyOn(window, 'removeEventListener');
    const { unmount } = render(<MenuFixture />);
    const observer = observers[0];
    const resizeListener = addListener.mock.calls.find(([type]) => type === 'resize')![1];
    const scrollListener = addListener.mock.calls.find(([type]) => type === 'scroll')![1];
    const ancestor = screen.getByTestId('ancestor');
    const menu = getMenu();
    observers[0].trigger();
    expect(frames.size).toBe(1);
    const pendingFrame = [...frames.keys()][0];
    unmount();
    expect(menu).not.toBeInTheDocument();
    expect(observer.disconnect).toHaveBeenCalledOnce();
    expect(removeListener).toHaveBeenCalledWith('resize', resizeListener);
    expect(removeListener).toHaveBeenCalledWith('scroll', scrollListener, true);
    expect(window.cancelAnimationFrame).toHaveBeenCalledWith(pendingFrame);
    expect(frames.size).toBe(0);
    anchorMeasurements.mockClear();
    fireEvent.resize(window);
    fireEvent.scroll(ancestor);
    observer.trigger(); // A queued observer delivery must not resurrect an update.
    flushFrame();
    expect(frames.size).toBe(0);
    expect(anchorMeasurements).not.toHaveBeenCalled();
  });
});
