import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRef } from 'react';
import { MonitoringDeferredDetails, MonitoringMessageViewport, MonitoringViewportContext } from './MonitoringViewport';

const callbacks = new Map<Element, IntersectionObserverCallback>();
function installObservers() {
  vi.stubGlobal('IntersectionObserver', class {
    target?: Element;
    constructor(private callback: IntersectionObserverCallback) {}
    observe(target: Element) { this.target = target; callbacks.set(target, this.callback); }
    disconnect() { if (this.target) callbacks.delete(this.target); }
  });
}
function visibility(target: Element, visible: boolean) {
  act(() => callbacks.get(target)?.([{ target, isIntersecting: visible } as IntersectionObserverEntry], {} as IntersectionObserver));
}
const root = createRef<HTMLDivElement>();
afterEach(() => { vi.unstubAllGlobals(); callbacks.clear(); });

describe('monitoring viewport', () => {
  it('keeps only near-screen bodies while retaining lightweight anchors for 500 messages', () => {
    installObservers();
    const body = vi.fn((id: number) => <span>record {id}</span>);
    function Body({ id }: { id: number }) { return body(id); }
    const { container } = render(<MonitoringViewportContext.Provider value={{ root, active: true }}>
      {Array.from({ length: 500 }, (_, id) => <MonitoringMessageViewport key={id} id={String(id)} active={id === 499}><Body id={id} /></MonitoringMessageViewport>)}
    </MonitoringViewportContext.Provider>);
    expect(body).toHaveBeenCalledTimes(1);
    expect(container.querySelectorAll('[data-message-id]')).toHaveLength(500);
    const message = container.querySelector('[data-message-id="250"]')!;
    visibility(message, true);
    expect(screen.getByText('record 250')).toBeInTheDocument();
    visibility(message, false);
    expect(screen.queryByText('record 250')).not.toBeInTheDocument();
    expect(screen.getByText('record 499')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-monitor-placeholder]')).toHaveLength(499);
  });

  it('does not build closed details and keeps their expansion through viewport eviction', () => {
    installObservers();
    const body = vi.fn(() => <pre>large packet</pre>);
    const { container } = render(<MonitoringViewportContext.Provider value={{ root, active: true }}>
      <MonitoringMessageViewport id="packet" active={false}><MonitoringDeferredDetails cacheKey="evidence" summary="packet details" renderBody={body} /></MonitoringMessageViewport>
    </MonitoringViewportContext.Provider>);
    const message = container.querySelector('[data-message-id]')!;
    visibility(message, true);
    expect(body).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('packet details'));
    expect(screen.getByText('large packet')).toBeInTheDocument();
    visibility(message, false);
    expect(screen.queryByText('large packet')).not.toBeInTheDocument();
    visibility(message, true);
    expect(screen.getByText('large packet')).toBeInTheDocument();
    expect(container.querySelector('details')).toHaveAttribute('open');
  });

  it('retains measured placeholder height, ignores hidden-tab resize and disconnects observers', () => {
    installObservers();
    let resize: (() => void) | undefined;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { resize = callback; } observe() {} disconnect() {} });
    const children = <MonitoringMessageViewport id="a" active={false}><p>message</p></MonitoringMessageViewport>;
    const { container, rerender } = render(<MonitoringViewportContext.Provider value={{ root, active: true }}>{children}</MonitoringViewportContext.Provider>);
    const node = container.querySelector('[data-message-id]') as HTMLElement;
    vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({ height: 800 } as DOMRect);
    visibility(node, true);
    act(() => resize?.());
    visibility(node, false);
    expect(node.style.height).toBe('800px');
    rerender(<MonitoringViewportContext.Provider value={{ root, active: false }}>{children}</MonitoringViewportContext.Provider>);
    expect(callbacks.size).toBe(0);
    expect(node.style.height).toBe('800px');
  });

  it('renders a focused source-message anchor immediately and leaves workbench rendering alone', () => {
    installObservers();
    const { rerender } = render(<MonitoringViewportContext.Provider value={{ root, active: true, focusMessageId: 'source' }}>
      <MonitoringMessageViewport id="merged" sourceIds={['source']} active={false}><p>focused evidence</p></MonitoringMessageViewport>
    </MonitoringViewportContext.Provider>);
    expect(screen.getByText('focused evidence')).toBeInTheDocument();
    rerender(<MonitoringMessageViewport id="normal" active={false}><p>ordinary chat</p></MonitoringMessageViewport>);
    expect(screen.getByText('ordinary chat')).toBeInTheDocument();
  });
});
