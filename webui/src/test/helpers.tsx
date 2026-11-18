/**
 * Shared test rendering helpers.
 *
 * Wraps components in MemoryRouter (and other providers as needed) so
 * individual tests don't have to repeat the boilerplate.
 */

import React from 'react';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import { MemoryRouter, type MemoryRouterProps } from 'react-router-dom';

// ---------------------------------------------------------------------------
// Router-aware render helper
// ---------------------------------------------------------------------------

interface RenderWithRouterOptions extends RenderOptions {
  routerProps?: MemoryRouterProps;
}

/**
 * Render `ui` wrapped in a MemoryRouter.
 *
 * @example
 * const { getByText } = renderWithRouter(<MyPage />);
 */
export function renderWithRouter(
  ui: React.ReactElement,
  { routerProps = {}, ...renderOptions }: RenderWithRouterOptions = {},
): RenderResult {
  function Wrapper({ children }: { children: React.ReactNode }) {
    return <MemoryRouter {...routerProps}>{children}</MemoryRouter>;
  }
  return render(ui, { wrapper: Wrapper, ...renderOptions });
}

// ---------------------------------------------------------------------------
// Backend error factory
// ---------------------------------------------------------------------------

/**
 * An axios rejection carrying the body the backend's exception handler sends for an
 * `HTTPException`: `{ error: 'HTTPException', message: <detail> }`. There is no `detail`
 * field in it, and `err.message` is only axios's own "Request failed with status code N".
 *
 * `message` is whatever the route passed as `detail`: text, or an object such as
 * `{ code, message }`.
 */
export function backendError(status: number, message: unknown): Error {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { error: 'HTTPException', message } },
  });
}

// ---------------------------------------------------------------------------
// Async wait helpers
// ---------------------------------------------------------------------------

/** Wait for all pending microtasks/promises to settle. */
export function flushPromises(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** Sleep for `ms` milliseconds (useful in async tests). */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// SSE mock factory
// ---------------------------------------------------------------------------

/**
 * Build a mock EventSource class that can be controlled in tests.
 *
 * Usage:
 *   const { MockEventSource, simulateMessage, simulateError } = createMockEventSource();
 *   vi.stubGlobal('EventSource', MockEventSource);
 *   // ... render component ...
 *   simulateMessage({ type: 'ping', properties: {} });
 */
export interface MockEventSourceControls {
  MockEventSource: typeof EventSource;
  /** Trigger an `onmessage` event on the most recently created instance. */
  simulateMessage: (data: unknown) => void;
  /** Trigger an `onerror` event on the most recently created instance. */
  simulateError: () => void;
  /** Trigger an `onopen` event on the most recently created instance. */
  simulateOpen: () => void;
  /** Return the URL the most recent instance was opened with. */
  getLastUrl: () => string;
  /** Close / replace current instance reference. */
  reset: () => void;
}

export function createMockEventSource(): MockEventSourceControls {
  let lastInstance: any = null;

  class MockEventSourceClass {
    url: string;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    onopen: ((event: Event) => void) | null = null;
    readyState: number = 0; // CONNECTING
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;

    constructor(url: string) {
      this.url = url;
      lastInstance = this;
    }

    close() {
      this.readyState = 2; // CLOSED
    }

    addEventListener() {}
    removeEventListener() {}
    dispatchEvent() { return true; }
  }

  return {
    MockEventSource: MockEventSourceClass as unknown as typeof EventSource,

    simulateMessage(data: unknown) {
      if (!lastInstance?.onmessage) return;
      lastInstance.readyState = 1; // OPEN
      lastInstance.onmessage(
        new MessageEvent('message', { data: JSON.stringify(data) }),
      );
    },

    simulateError() {
      if (!lastInstance?.onerror) return;
      lastInstance.onerror(new Event('error'));
    },

    simulateOpen() {
      if (!lastInstance?.onopen) return;
      lastInstance.readyState = 1; // OPEN
      lastInstance.onopen(new Event('open'));
    },

    getLastUrl() {
      return lastInstance?.url ?? '';
    },

    reset() {
      lastInstance = null;
    },
  };
}
